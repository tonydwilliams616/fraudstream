"""tests/test_train.py - checks the baseline model trains sensibly and reproducibly.

What: on a small generated dataset, the model trains, stops early, uses the
      right class weight, scores fraud far above normal purchases, gives
      identical scores when trained twice, and the training script writes a
      complete run folder.
Why:  "it trained without an error" proves nothing about a model. These checks
      catch the quiet failures: a label that got lost, a model that learned
      nothing, or training that isn't repeatable (which would make every later
      comparison between models meaningless).
"""

import json
import runpy
from pathlib import Path

import numpy as np
import pytest
import xgboost as xgb
from sklearn.metrics import roc_auc_score

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.generator.dataset import DatasetParams, generate_dataset
from fraudstream.storage import load_version, save_version
from fraudstream.training.data import LABEL, prepare
from fraudstream.training.train import best_booster, score, train

PARAMS = DatasetParams(seed=5, n_cards=2000, n_merchants=300, days=10)


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    root = tmp_path_factory.mktemp("data")
    manifest = save_version(generate_dataset(PARAMS), root, "v1")
    return root / "transactions" / "v1", manifest


@pytest.fixture(scope="module")
def data(saved):
    dataset, manifest = load_version(saved[0])
    return prepare(dataset, manifest, train_days=7, valid_days=2)


@pytest.fixture(scope="module")
def trained(data):
    return train(data)


def test_class_weight_is_normal_over_fraud(data, trained):
    _, record = trained
    y = data.train[LABEL]
    assert record["scale_pos_weight"] == pytest.approx((len(y) - y.sum()) / y.sum(), abs=0.01)


def test_early_stopping_keeps_fewer_trees_than_the_limit(trained):
    _, record = trained
    assert 1 <= record["trees_used"] < record["params"]["n_estimators"]


def test_model_ranks_fraud_far_above_normal(data, trained):
    # ROC-AUC: the chance a random fraud row scores above a random normal row.
    # It checks the ranking, not the raw score values - those depend on how
    # XGBoost calibrates its starting point, which differs between versions.
    model, _ = trained
    assert roc_auc_score(data.valid[LABEL], score(model, data.valid)) > 0.9


def test_saved_model_scores_exactly_like_the_evaluated_model(data, trained, tmp_path):
    # The file we publish must hold exactly the model whose results we report.
    model, _ = trained
    best_booster(model).save_model(tmp_path / "model.json")
    reloaded = xgb.Booster()
    reloaded.load_model(tmp_path / "model.json")
    matrix = xgb.DMatrix(data.test[FEATURE_COLUMNS])
    np.testing.assert_allclose(reloaded.predict(matrix), score(model, data.test), rtol=1e-6)


def test_training_twice_gives_identical_scores(data, trained):
    model, _ = trained
    again, _ = train(data)
    np.testing.assert_array_equal(score(model, data.test), score(again, data.test))


def test_feature_importance_covers_every_feature(trained):
    _, record = trained
    assert sum(record["feature_importance"].values()) == pytest.approx(1.0, abs=0.01)


def test_training_script_writes_a_run_folder(saved, tmp_path, capsys):
    script = Path(__file__).parents[1] / "scripts" / "train" / "train_baseline.py"
    main = runpy.run_path(str(script))["main"]
    args = ["--dataset", str(saved[0]), "--out", str(tmp_path), "--train-days", "7"]
    assert main([*args, "--valid-days", "2"]) == 0
    (run_dir,) = tmp_path.iterdir()
    assert sorted(p.name for p in run_dir.iterdir()) == ["metrics.json", "model.json"]
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["data"]["dataset"] == "transactions/v1"
    assert metrics["evaluation"]["test"]["pr_auc"] is not None
    assert "Test PR-AUC" in capsys.readouterr().out
    reloaded = xgb.Booster()
    reloaded.load_model(run_dir / "model.json")  # the saved model can be read back ...
    assert reloaded.num_boosted_rounds() == metrics["training"]["trees_used"]  # ... best trees only
