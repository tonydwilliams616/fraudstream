"""scripts/train/train_baseline.py - trains and evaluates the baseline model locally.

What: loads a dataset version (from S3, or a local folder), builds the features,
      splits by time, trains XGBoost, evaluates it, and writes a run folder:
        models/<run-id>/model.json     the trained model (XGBoost's own format)
        models/<run-id>/metrics.json   how it was trained and how well it did
Why:  one repeatable command for a baseline. Every number in metrics.json can
      be traced back to a dataset fingerprint, a code commit and fixed settings.
      Step 4 packages a run for SageMaker and the model registry.

Usage (repo root, venv active; S3 needs `source ./aws-login.sh` first):
  python scripts/train/train_baseline.py
  python scripts/train/train_baseline.py --dataset data/transactions/v1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from fraudstream.storage import download_version, git_state, load_version
from fraudstream.training.data import prepare
from fraudstream.training.evaluate import ALERT_RATE, evaluate
from fraudstream.training.train import best_booster, score, train

DEFAULT_DATASET = "s3://fs-datasets-831547420318/transactions/v2"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dataset", default=DEFAULT_DATASET, help="s3://... URI or a local folder")
    p.add_argument("--cache", type=Path, default=Path("data"), help="where S3 datasets are cached")
    p.add_argument("--out", type=Path, default=Path("models"), help="root folder for run folders")
    p.add_argument("--train-days", type=int, default=21)
    p.add_argument("--valid-days", type=int, default=4)
    p.add_argument("--alert-rate", type=float, default=ALERT_RATE)
    args = p.parse_args(argv)

    started = time.perf_counter()
    if args.dataset.startswith("s3://"):
        folder = download_version(args.dataset, args.cache)
    else:
        folder = Path(args.dataset)
    print(f"Loading {folder} (checking it against its manifest) ...")
    dataset, manifest = load_version(folder)

    print("Building features and splitting by day ...")
    data = prepare(dataset, manifest, args.train_days, args.valid_days)

    print("Training ...")
    model, training = train(data)
    report = evaluate(data, score(model, data.valid), score(model, data.test), args.alert_rate)

    run_id = "baseline-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    best_booster(model).save_model(run_dir / "model.json")  # only the trees we evaluated
    metrics = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": git_state(),
        "data": data.info,
        "training": training,
        "evaluation": report,
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")

    _print_summary(run_dir, training, report, time.perf_counter() - started)
    return 0


def _print_summary(run_dir: Path, training: dict, report: dict, seconds: float) -> None:
    test, budget = report["test"], report["alert_budget"]
    low, high = test["pr_auc_95ci"]
    print(f"\nSaved {run_dir}/ in {seconds:.0f}s - {training['trees_used']} trees")
    print(
        f"  Test PR-AUC {test['pr_auc']}  (95% range {low}-{high}; random = {test['baseline_pr_auc']})"
    )
    print(f"  Test ROC-AUC {test['roc_auc']}")
    print(
        f"  Alert threshold {budget['threshold']} -> {test['alerts_per_day']} alerts/day on test: "
        f"precision {test['precision']}, recall {test['recall']}"
    )
    for style, r in test["recall_by_fraud_type"].items():
        print(f"    {style:<17} recall {r['recall']:.2f}  ({r['fraud_rows']} fraud rows)")
    top = list(training["feature_importance"].items())[:5]
    print("  Clues the model leaned on most: " + ", ".join(f"{f} {v:.0%}" for f, v in top))


if __name__ == "__main__":
    sys.exit(main())
