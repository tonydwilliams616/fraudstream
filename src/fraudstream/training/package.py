"""fraudstream.training.package - turns a training run into a publishable model.

What: for a run folder (models/<run-id>/, made by scripts/train/train_baseline.py):
        write_model_card()   model_card.md - the one-page description of the model
        build_model_tarball() model.tar.gz - the model packed the way SageMaker's
                              built-in XGBoost container expects
        check_packaged_model() proves the packed model gives exactly the same
                              scores as the trained one
        upload_model()       publishes the run to the artefacts bucket
Why:  a model is only usable by the rest of the system once it's (a) described,
      (b) in a format the serving container can load, and (c) stored somewhere
      permanent with its evidence. The model registry (Phase 3) and the
      endpoint (Phase 2) both point at the uploaded model.tar.gz.
Concepts:
  - MODEL CARD: like the label on a medicine box - what it's for, what went in,
    how well it works, and when NOT to use it. Generated from metrics.json, so
    it can never disagree with the actual results.
  - SageMaker's XGBoost container unpacks model.tar.gz and loads a file called
    exactly "xgboost-model" from it. It receives rows of numbers (CSV, no
    header) in the FEATURE_COLUMNS order and returns one score per row - the
    reason the feature contract fixes the order of the clues.
  - The serving container must run the same major XGBoost version that trained
    the model; the model card records the training version so Phase 2 can pick
    the matching container image.
"""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path

import numpy as np
import xgboost as xgb

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.storage import upload_files

MODEL_NAME = "fraud-baseline"
SAGEMAKER_MODEL_FILE = "xgboost-model"  # the file name the container looks for
TARBALL = "model.tar.gz"
MODEL_CARD = "model_card.md"
METRICS = "metrics.json"
TRAINED_MODEL = "model.json"

LIMITATIONS = [
    (
        "Trained on SYNTHETIC data. It has partly learned the generator's own rules "
        "(e.g. which shop types fraudsters favour), so real-world performance would be lower."
    ),
    (
        "Recall is counted per transaction. Blocking a card usually needs only one alert per "
        "episode, so the share of fraud episodes caught is higher than the recall shown."
    ),
    (
        "The test period has a few hundred fraud rows, so results carry real uncertainty "
        "(see the 95% range)."
    ),
    (
        "History clues start empty when the live consumer restarts, so cards look 'new' for a "
        "while; card_txn_count tells the model how much history it had."
    ),
    (
        "The threshold is tied to an alert budget on the validation days. If fraud levels or "
        "spending patterns drift, both the model and the threshold must be re-checked."
    ),
]


def write_model_card(run_dir: Path) -> Path:
    """Write model_card.md from the run's metrics.json and return its path."""
    run_dir = Path(run_dir)
    m = json.loads((run_dir / METRICS).read_text())
    data, training, ev = m["data"], m["training"], m["evaluation"]
    test, split = ev["test"], data["split"]
    commit = m["code"]["git_commit"] or "unknown"
    dirty = " (uncommitted changes!)" if m["code"]["git_dirty"] else ""
    low, high = test["pr_auc_95ci"]
    cm = test["confusion_matrix"]

    lines = [
        f"# Model card - {MODEL_NAME} `{m['run_id']}`",
        "",
        "| | |",
        "|---|---|",
        f"| Model | XGBoost {training['xgboost_version']}, {training['trees_used']} trees |",
        f"| Created | {m['created_at']} |",
        f"| Code | commit `{commit[:12]}`{dirty} |",
        f"| Training data | `{data['dataset']}` (fingerprint `{data['dataset_fingerprint'][:12]}`) |",
        (
            f"| Feature schema | version {data['feature_schema_version']}, "
            f"{len(data['features'])} features |"
        ),
        "",
        "## Intended use",
        "Scores card transactions from 0 (normal) to 1 (looks like fraud) so a fraud team can",
        "review the most suspicious ones. Demonstration only: trained on synthetic data and",
        "not for decisions about real customers.",
        "",
        "## Inputs and output",
        "Input: one row per transaction with these features, in this order (empty = unknown):",
        "",
        ", ".join(f"`{f}`" for f in data["features"]),
        "",
        (
            f"Output: a fraud score. **Alert when score >= {ev['alert_budget']['threshold']}** - "
            "chosen on the validation days so that about "
            f"{ev['alert_budget']['alert_rate']:.1%} of transactions are flagged."
        ),
        "",
        "## Training",
        "| Part | Days | Rows | Fraud rows |",
        "|---|---|---|---|",
        *(
            f"| {name} | {s['days'][0]}-{s['days'][1]} | {s['rows']:,} | {s['fraud_rows']:,} |"
            for name, s in split.items()
        ),
        "",
        (
            f"Class weight (`scale_pos_weight`) {training['scale_pos_weight']}; early stopping "
            f"after {training['early_stopping_rounds']} rounds without validation improvement."
        ),
        "",
        "## Results (test days - used once, never to choose anything)",
        "| Measure | Value |",
        "|---|---|",
        (
            f"| PR-AUC (headline) | **{test['pr_auc']}** (95% range {low}-{high}; "
            f"random = {test['baseline_pr_auc']}) |"
        ),
        f"| ROC-AUC | {test['roc_auc']} |",
        f"| Alerts per day | {test['alerts_per_day']} |",
        f"| Precision (alerts that were fraud) | {test['precision']} |",
        f"| Recall (fraud that got an alert) | {test['recall']} |",
        (
            f"| Confusion matrix | {cm['true_pos']} caught, {cm['false_neg']} missed, "
            f"{cm['false_pos']} false alarms, {cm['true_neg']:,} correctly left alone |"
        ),
        "",
        "Recall by fraud style:",
        "",
        "| Style | Fraud rows | Recall |",
        "|---|---|---|",
        *(
            f"| {style} | {r['fraud_rows']} | {r['recall']:.2f} |"
            for style, r in test["recall_by_fraud_type"].items()
        ),
        "",
        "Clues the model leaned on most (share of total gain): "
        + ", ".join(f"`{f}` {v:.0%}" for f, v in list(training["feature_importance"].items())[:5]),
        "",
        "## Limitations",
        *(f"- {item}" for item in LIMITATIONS),
        "",
    ]
    path = run_dir / MODEL_CARD
    path.write_text("\n".join(lines))
    return path


def build_model_tarball(run_dir: Path) -> Path:
    """Pack the trained model as model.tar.gz containing a single file, xgboost-model."""
    run_dir = Path(run_dir)
    booster = xgb.Booster()
    booster.load_model(run_dir / TRAINED_MODEL)
    raw = booster.save_raw("json")  # the same model, in XGBoost's JSON format, as bytes
    staged = run_dir / SAGEMAKER_MODEL_FILE
    staged.write_bytes(bytes(raw))
    path = run_dir / TARBALL
    with tarfile.open(path, "w:gz") as tar:
        tar.add(staged, arcname=SAGEMAKER_MODEL_FILE)
    staged.unlink()
    return path


def check_packaged_model(run_dir: Path, rows: int = 2000, seed: int = 0) -> None:
    """Unpack model.tar.gz as the container would and check it scores exactly like model.json.

    Uses random rows shaped like real features (including empty values), so the
    check needs no dataset: two copies of the same model must agree on ANY input.
    """
    run_dir = Path(run_dir)
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(rows, len(FEATURE_COLUMNS))) * 50
    x[rng.random(x.shape) < 0.1] = np.nan
    original = xgb.Booster()
    original.load_model(run_dir / TRAINED_MODEL)
    matrix = xgb.DMatrix(x, feature_names=FEATURE_COLUMNS, feature_types=original.feature_types)
    with tarfile.open(run_dir / TARBALL) as tar, tempfile.TemporaryDirectory() as tmp:
        names = tar.getnames()
        if names != [SAGEMAKER_MODEL_FILE]:
            raise ValueError(f"{TARBALL} must contain only {SAGEMAKER_MODEL_FILE}, has {names}")
        tar.extract(SAGEMAKER_MODEL_FILE, tmp, filter="data")
        packed = xgb.Booster()
        packed.load_model(str(Path(tmp) / SAGEMAKER_MODEL_FILE))  # by path, like the container
    if not np.array_equal(original.predict(matrix), packed.predict(matrix)):
        raise ValueError("packaged model scores differently from the trained model")


def upload_model(run_dir: Path, bucket: str, s3=None) -> list[str]:
    """Publish a run to s3://<bucket>/models/fraud-baseline/<run-id>/; return the keys.

    metrics.json goes last: like a dataset's manifest, it marks the set complete.
    """
    run_dir = Path(run_dir)
    run_id = json.loads((run_dir / METRICS).read_text())["run_id"]
    files = [TARBALL, TRAINED_MODEL, MODEL_CARD, METRICS]
    return upload_files(run_dir, files, bucket, f"models/{MODEL_NAME}/{run_id}", s3)
