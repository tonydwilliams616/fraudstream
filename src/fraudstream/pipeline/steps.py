"""fraudstream.pipeline.steps - what the prepare, evaluate and register steps do.

What:
  prepare_data()       dataset version -> features + time split -> train/valid/test CSV
                       plus info.json (dataset, fingerprint, feature schema, split)
  evaluate_model()     scores the challenger (and the champion, if any) on the SAME
                       validation and test days, applies the gate, and writes
                       metrics.json (the same layout as Phase 1 runs), evaluation.json
                       (what the pipeline's condition step reads) and model_card.md
  find_champion()      the newest Approved model package in the registry group
  register_challenger() registers the challenger as PendingManualApproval
Why: each function is plain Python with files in and files out, so the same code
     runs in a SageMaker step, in CI's smoke test and in the unit tests.
Fair comparison: the champion is scored by the same evaluate() on the same days,
     and its alert threshold is chosen the same way (on the validation days), so
     the gate compares like with like - not today's challenger with numbers the
     champion got on a different test set months ago.
"""

from __future__ import annotations

import json
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import xgboost as xgb

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.pipeline.gate import decide
from fraudstream.pipeline.train_entry import MODEL_FILE
from fraudstream.registry import package_request, register
from fraudstream.storage import load_version
from fraudstream.training.data import TrainingData, prepare
from fraudstream.training.evaluate import evaluate
from fraudstream.training.package import write_model_card
from fraudstream.training.train import EARLY_STOPPING_ROUNDS, XGB_PARAMS

PARTS = ("train", "valid", "test")


# --- prepare --------------------------------------------------------------------
def prepare_data(dataset_dir: Path, out_dir: Path, train_days: int, valid_days: int) -> dict:
    """Write train/valid/test CSVs and info.json for a saved dataset version."""
    dataset, manifest = load_version(Path(dataset_dir))  # checks every file against the manifest
    data = prepare(dataset, manifest, train_days, valid_days)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in PARTS:
        getattr(data, name).to_csv(out_dir / f"{name}.csv", index=False)
    (out_dir / "info.json").write_text(json.dumps(data.info, indent=2) + "\n")
    return data.info


# --- evaluate -------------------------------------------------------------------
def load_booster(model_tar: Path) -> tuple[xgb.Booster, str]:
    """The model inside a model.tar.gz, and the XGBoost version that saved it."""
    with tarfile.open(model_tar) as tar:
        # SageMaker may store it as "xgboost-model" or "./xgboost-model".
        (member,) = [m for m in tar.getmembers() if Path(m.name).name == MODEL_FILE]
        raw = tar.extractfile(member).read()
    booster = xgb.Booster()
    booster.load_model(bytearray(raw))
    version = ".".join(str(v) for v in json.loads(raw)["version"])
    return booster, version


def _read(data_dir: Path) -> TrainingData:
    data_dir = Path(data_dir)
    valid = pd.read_csv(data_dir / "valid.csv")
    test = pd.read_csv(data_dir / "test.csv")
    info = json.loads((data_dir / "info.json").read_text())
    return TrainingData(valid.iloc[0:0], valid, test, info)


def _scores(booster: xgb.Booster, part: pd.DataFrame):
    return booster.predict(xgb.DMatrix(part[FEATURE_COLUMNS]))


def _report(booster: xgb.Booster, data: TrainingData) -> dict:
    return evaluate(data, _scores(booster, data.valid), _scores(booster, data.test))


def _training_record(booster: xgb.Booster, version: str, params: dict, info: dict) -> dict:
    """What a Phase 1 run records about training, worked out from the saved model."""
    train_split = info["split"]["train"]
    gain = booster.get_score(importance_type="total_gain")
    total = sum(gain.values()) or 1.0
    importance = {f: round(gain.get(f, 0.0) / total, 4) for f in FEATURE_COLUMNS}
    trees = booster.num_boosted_rounds()
    return {
        "algorithm": "xgboost",
        "xgboost_version": version,
        "params": {**XGB_PARAMS, **params},
        "scale_pos_weight": round(
            (train_split["rows"] - train_split["fraud_rows"]) / train_split["fraud_rows"], 2
        ),
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
        "best_iteration": trees - 1,
        "trees_used": trees,
        "feature_importance": dict(sorted(importance.items(), key=lambda kv: -kv[1])),
    }


def evaluate_model(
    model_tar: Path,
    data_dir: Path,
    out_dir: Path,
    run_id: str,
    git_commit: str,
    params: dict | None = None,
    champion: dict | None = None,
    gate_rules: dict | None = None,
) -> dict:
    """Evaluate the challenger (and champion) and write metrics, evaluation and model card.

    champion: {"version": int, "booster": xgb.Booster} or None if there isn't one yet.
    Returns the evaluation summary (also written as evaluation.json).
    """
    data = _read(data_dir)
    booster, version = load_booster(Path(model_tar))
    report = _report(booster, data)
    champion_report = _report(champion["booster"], data) if champion else None
    gate = decide(
        report["test"], champion_report["test"] if champion_report else None, **(gate_rules or {})
    )

    def headline(r):
        t = r["test"]
        return {
            "pr_auc": t["pr_auc"],
            "recall": t["recall"],
            "precision": t["precision"],
            "alerts_per_day": t["alerts_per_day"],
            "threshold": r["alert_budget"]["threshold"],
        }

    summary = {
        "run_id": run_id,
        "challenger": headline(report),
        "champion": (
            {"version": champion["version"], **headline(champion_report)} if champion else None
        ),
        "gate": gate,
    }
    metrics = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": {"git_commit": git_commit, "git_dirty": False},
        "data": data.info,
        "training": _training_record(booster, version, params or {}, data.info),
        "evaluation": report,
        "comparison": summary,
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    (out_dir / "evaluation.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_model_card(out_dir)
    return summary


# --- champion and registration ---------------------------------------------------
def find_champion(sagemaker, group: str) -> dict | None:
    """The newest Approved package in the group: {"version", "model_data_url"}, or None."""
    found = sagemaker.list_model_packages(
        ModelPackageGroupName=group,
        ModelApprovalStatus="Approved",
        SortBy="CreationTime",
        SortOrder="Descending",
        MaxResults=1,
    )["ModelPackageSummaryList"]
    if not found:
        return None
    package = sagemaker.describe_model_package(ModelPackageName=found[0]["ModelPackageArn"])
    return {
        "version": int(package["ModelPackageVersion"]),
        "model_data_url": package["InferenceSpecification"]["Containers"][0]["ModelDataUrl"],
    }


def _split_s3(uri: str) -> tuple[str, str]:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


def load_champion(sagemaker, s3, group: str) -> dict | None:
    """find_champion() plus its model, downloaded and loaded: {"version", "booster"} or None."""
    champion = find_champion(sagemaker, group)
    if champion is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "model.tar.gz"
        s3.download_file(*_split_s3(champion["model_data_url"]), str(local))
        booster, _ = load_booster(local)
    return {"version": champion["version"], "booster": booster}


def register_challenger(sagemaker, s3, metrics_uri: str, model_uri: str, group: str, region: str):
    """Register the evaluated challenger as PendingManualApproval. Returns (ARN, created now?)."""
    bucket, key = _split_s3(metrics_uri)
    metrics = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    if not metrics["comparison"]["gate"]["passed"]:
        raise RuntimeError("the gate did not pass - refusing to register")  # belt and braces
    request = package_request(metrics, model_uri, metrics_uri, group, region)
    return register(sagemaker, request)
