"""tests/test_pipeline_steps.py - runs the pipeline's steps end to end on a small dataset.

What: prepare writes train/valid/test and info.json; the training entry point
      (as SageMaker's XGBoost container runs it) saves an xgboost-model holding
      only the best trees; evaluate scores challenger and champion on the same
      days and writes metrics.json, evaluation.json and a model card; a weak
      challenger loses to a good champion; the registry entry is built from the
      evaluated run and refused if the gate failed. AWS is faked.
Why:  the same functions run inside SageMaker. If they work here on real data
      and a real model, a pipeline run can only fail on wiring, not on logic.
"""

import io
import json
import tarfile

import pytest

from fraudstream.generator.dataset import DatasetParams, generate_dataset
from fraudstream.pipeline import train_entry
from fraudstream.pipeline.steps import (
    evaluate_model,
    find_champion,
    load_booster,
    prepare_data,
    register_challenger,
)
from fraudstream.storage import save_version

PARAMS = DatasetParams(seed=5, n_cards=2000, n_merchants=300, days=10)
NO_FLOOR = {"min_pr_auc": 0.0}  # a 10-day toy dataset can't promise a real-world PR-AUC


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline")
    save_version(generate_dataset(PARAMS), root / "datasets", "v1")
    out = root / "data"
    prepare_data(root / "datasets" / "transactions" / "v1", out, train_days=7, valid_days=2)
    return root, out


def train_model(root, data_dir, name, argv=()):
    model_dir = root / name
    env = {"SM_CHANNEL_DATA": str(data_dir), "SM_MODEL_DIR": str(model_dir)}
    assert train_entry.main(list(argv), env=env) == 0
    tar_path = root / f"{name}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:  # what SageMaker does with /opt/ml/model
        tar.add(model_dir / train_entry.MODEL_FILE, arcname=train_entry.MODEL_FILE)
    return tar_path


@pytest.fixture(scope="module")
def good_model(prepared):
    root, data = prepared
    return train_model(root, data, "good")


def test_prepare_writes_the_three_parts_and_info(prepared):
    _, data = prepared
    assert sorted(p.name for p in data.iterdir()) == [
        "info.json",
        "test.csv",
        "train.csv",
        "valid.csv",
    ]
    info = json.loads((data / "info.json").read_text())
    assert info["dataset"] == "transactions/v1"
    assert info["split"]["train"]["days"] == [1, 7]


def test_training_saves_only_the_best_trees(good_model):
    booster, version = load_booster(good_model)
    assert version.startswith("3.0.")
    assert 1 <= booster.num_boosted_rounds() < 500  # early stopping cut it short


def test_first_model_is_evaluated_and_documented(prepared, good_model, tmp_path):
    _, data = prepared
    summary = evaluate_model(
        good_model, data, tmp_path, "pipeline-test1", "abc123", gate_rules=NO_FLOOR
    )
    assert summary["gate"]["passed"] and summary["champion"] is None
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "evaluation.json",
        "metrics.json",
        "model_card.md",
    ]
    metrics = json.loads((tmp_path / "metrics.json").read_text())
    assert metrics["training"]["trees_used"] == load_booster(good_model)[0].num_boosted_rounds()
    assert metrics["evaluation"]["alert_budget"]["threshold"] > 0
    assert json.loads((tmp_path / "evaluation.json").read_text()) == summary


def test_weak_challenger_loses_to_a_good_champion(prepared, good_model, tmp_path):
    root, data = prepared
    weak = train_model(root, data, "weak", ["--n_estimators", "1", "--max_depth", "1"])
    champion = {"version": 1, "booster": load_booster(good_model)[0]}
    summary = evaluate_model(
        weak, data, tmp_path, "pipeline-weak", "abc123", params={"n_estimators": 1, "max_depth": 1},
        champion=champion, gate_rules=NO_FLOOR,
    )  # fmt: skip
    assert not summary["gate"]["passed"]
    assert summary["champion"]["version"] == 1
    assert summary["challenger"]["pr_auc"] < summary["champion"]["pr_auc"]


def test_same_model_as_champion_passes(prepared, good_model, tmp_path):
    _, data = prepared
    champion = {"version": 1, "booster": load_booster(good_model)[0]}
    summary = evaluate_model(
        good_model,
        data,
        tmp_path,
        "pipeline-same",
        "abc123",
        champion=champion,
        gate_rules=NO_FLOOR,
    )
    assert summary["gate"]["passed"]
    assert summary["challenger"]["pr_auc"] == summary["champion"]["pr_auc"]


class FakeSageMaker:
    def __init__(self, approved=None):
        self.approved = approved
        self.created = []

    def list_model_packages(self, **kwargs):
        assert kwargs["ModelApprovalStatus"] == "Approved"
        return {
            "ModelPackageSummaryList": [{"ModelPackageArn": "arn:pkg/1"}] if self.approved else []
        }

    def describe_model_package(self, ModelPackageName):
        if ModelPackageName == "arn:pkg/1":
            return {
                "ModelPackageVersion": 1,
                "InferenceSpecification": {"Containers": [{"ModelDataUrl": self.approved}]},
            }
        req = self.created[-1]
        return {"ModelApprovalStatus": req["ModelApprovalStatus"],
                "CustomerMetadataProperties": req["CustomerMetadataProperties"]}  # fmt: skip

    def get_paginator(self, _name):
        class Pager:
            def paginate(self, **_):
                yield {"ModelPackageSummaryList": []}

        return Pager()

    def create_model_package(self, **req):
        self.created.append(req)
        return {"ModelPackageArn": "arn:pkg/2"}


class FakeS3:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[f"s3://{Bucket}/{Key}"])}


def test_champion_is_the_newest_approved_package():
    assert find_champion(FakeSageMaker(), "fs-fraud-detection") is None
    found = find_champion(FakeSageMaker("s3://b/models/v1/model.tar.gz"), "fs-fraud-detection")
    assert found == {"version": 1, "model_data_url": "s3://b/models/v1/model.tar.gz"}


def test_registration_uses_the_evaluated_run_and_waits_for_approval(prepared, good_model, tmp_path):
    _, data = prepared
    evaluate_model(good_model, data, tmp_path, "pipeline-reg", "abc123", gate_rules=NO_FLOOR)
    metrics_uri = "s3://art/models/fraud-pipeline/pipeline-reg/evaluation/metrics.json"
    s3 = FakeS3({metrics_uri: (tmp_path / "metrics.json").read_bytes()})
    sm = FakeSageMaker()
    arn, created = register_challenger(
        sm, s3, metrics_uri, "s3://art/models/x/model.tar.gz", "fs-fraud-detection", "us-east-1"
    )
    assert created and arn == "arn:pkg/2"
    req = sm.created[0]
    assert req["ModelApprovalStatus"] == "PendingManualApproval"
    assert req["CustomerMetadataProperties"]["run_id"] == "pipeline-reg"
    assert req["ModelMetrics"]["ModelQuality"]["Statistics"]["S3Uri"] == metrics_uri


def test_registration_refuses_a_run_that_failed_the_gate(prepared, good_model, tmp_path):
    _, data = prepared
    evaluate_model(
        good_model, data, tmp_path, "pipeline-fail", "abc123", gate_rules={"min_pr_auc": 1.1}
    )
    uri = "s3://art/metrics.json"
    with pytest.raises(RuntimeError, match="gate did not pass"):
        register_challenger(
            FakeSageMaker(), FakeS3({uri: (tmp_path / "metrics.json").read_bytes()}),
            uri, "s3://art/model.tar.gz", "fs-fraud-detection", "us-east-1",
        )  # fmt: skip
