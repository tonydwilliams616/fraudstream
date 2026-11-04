"""tests/test_package_model.py - checks the model card, the SageMaker package and the upload.

What: on a small trained run, the model card shows the real results; the
      tarball holds exactly one file named xgboost-model that scores identically
      to the trained model; a tampered package is caught; the upload sends
      metrics.json last; and the script refuses to publish a model trained from
      uncommitted code. S3 is faked.
Why:  the package is what the endpoint will load and the registry will point at.
      A package that loads but scores differently - or a card that describes a
      different model - would break the link between "what we tested" and
      "what we deployed", which is the whole point of the lifecycle.
"""

import json
import runpy
import tarfile
from pathlib import Path

import pytest

from fraudstream.generator.dataset import DatasetParams, generate_dataset
from fraudstream.storage import save_version
from fraudstream.training.package import (
    MODEL_CARD,
    SAGEMAKER_MODEL_FILE,
    TARBALL,
    build_model_tarball,
    check_packaged_model,
    upload_model,
    write_model_card,
)

SCRIPTS = Path(__file__).parents[1] / "scripts" / "train"


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("work")
    save_version(
        generate_dataset(DatasetParams(seed=5, n_cards=2000, n_merchants=300, days=10)),
        root / "data",
        "v1",
    )
    train = runpy.run_path(str(SCRIPTS / "train_baseline.py"))["main"]
    args = ["--dataset", str(root / "data" / "transactions" / "v1"), "--out", str(root / "models")]
    assert train([*args, "--train-days", "7", "--valid-days", "2"]) == 0
    (folder,) = (root / "models").iterdir()
    return folder


def test_model_card_shows_the_real_results(run_dir):
    card = write_model_card(run_dir).read_text()
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["run_id"] in card
    assert str(metrics["evaluation"]["test"]["pr_auc"]) in card
    assert str(metrics["evaluation"]["alert_budget"]["threshold"]) in card
    assert "## Limitations" in card and "SYNTHETIC" in card


def test_tarball_holds_one_xgboost_model_that_scores_identically(run_dir):
    tarball = build_model_tarball(run_dir)
    with tarfile.open(tarball) as tar:
        assert tar.getnames() == [SAGEMAKER_MODEL_FILE]
    check_packaged_model(run_dir)  # raises if scores differ
    assert not (run_dir / SAGEMAKER_MODEL_FILE).exists()  # staging file cleaned up


def test_a_different_model_in_the_package_is_caught(run_dir, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    for name in ("model.json", "metrics.json"):
        (other / name).write_bytes((run_dir / name).read_bytes())
    build_model_tarball(other)
    # make the trained model slightly different: nudge every leaf of the first tree
    model = json.loads((other / "model.json").read_text())
    tree = model["learner"]["gradient_booster"]["model"]["trees"][0]
    for node, left in enumerate(tree["left_children"]):
        if left == -1:  # a leaf: its value is stored in split_conditions
            tree["split_conditions"][node] += 0.5
    (other / "model.json").write_text(json.dumps(model))
    with pytest.raises(ValueError, match="scores differently"):
        check_packaged_model(other)


class FakeS3:
    def __init__(self):
        self.puts = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"KeyCount": sum(k.startswith(Prefix) for k in self.puts)}

    def put_object(self, Bucket, Key, Body, IfNoneMatch):
        assert IfNoneMatch == "*"
        self.puts.append(Key)


def test_upload_publishes_the_run_with_metrics_last(run_dir):
    write_model_card(run_dir)
    build_model_tarball(run_dir)
    s3 = FakeS3()
    keys = upload_model(run_dir, "bucket", s3=s3)
    prefix = f"models/fraud-baseline/{run_dir.name}/"
    assert keys == [prefix + f for f in (TARBALL, "model.json", MODEL_CARD, "metrics.json")]
    with pytest.raises(FileExistsError):
        upload_model(run_dir, "bucket", s3=s3)  # never overwritten


def test_script_refuses_to_publish_a_model_from_uncommitted_code(run_dir, tmp_path, capsys):
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    for name in ("model.json", "metrics.json"):
        (dirty / name).write_bytes((run_dir / name).read_bytes())
    metrics = json.loads((dirty / "metrics.json").read_text())
    metrics["code"]["git_dirty"] = True
    (dirty / "metrics.json").write_text(json.dumps(metrics))
    main = runpy.run_path(str(SCRIPTS / "package_model.py"))["main"]
    assert main([str(dirty), "--upload"]) == 1
    assert "uncommitted" in capsys.readouterr().out
    assert main([str(dirty)]) == 0  # packaging locally is still fine
    assert (dirty / TARBALL).exists() and (dirty / MODEL_CARD).exists()
