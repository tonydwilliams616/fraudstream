"""scripts/serve/check_endpoint.py - proves the endpoint scores exactly like the evaluated model.

What: takes transactions from the model's TEST days (all the fraud rows plus a
      random sample of the rest), builds their features, scores them twice -
      locally with the run's saved model and remotely on the fs-fraud endpoint -
      and compares. Passes only if every score matches within 1e-6 and no alert
      decision (score >= threshold) changes.
Why:  the model card's results and the alert threshold were measured on the
      local model. This check is the evidence that the container, its XGBoost
      version and our CSV encoding reproduce it - training/serving parity.
      Re-run it whenever the model, the container or the encoding changes.

Usage (repo root, venv active, `source ./aws-login.sh`, endpoint InService):
  python scripts/serve/check_endpoint.py baseline-20261106T120445Z
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import boto3
import numpy as np
import xgboost as xgb

from fraudstream.endpoint import compare, score
from fraudstream.features import FEATURE_COLUMNS
from fraudstream.storage import download_version, load_version
from fraudstream.training.data import LABEL, prepare
from fraudstream.training.package import METRICS, MODEL_NAME, TRAINED_MODEL

DEFAULT_BUCKET = "fs-artefacts-831547420318"
DEFAULT_DATASETS = "fs-datasets-831547420318"
DEFAULT_ENDPOINT = "fs-fraud"
REGION = "us-east-1"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("run_id", help="the published run the endpoint serves")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument(
        "--rows", type=int, default=3000, help="normal rows to sample (all fraud is added)"
    )
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--cache", type=Path, default=Path("data"))
    args = p.parse_args(argv)

    s3 = boto3.client("s3", region_name=REGION)
    prefix = f"models/{MODEL_NAME}/{args.run_id}"
    metrics = json.loads(
        s3.get_object(Bucket=args.bucket, Key=f"{prefix}/{METRICS}")["Body"].read()
    )
    data_info = metrics["data"]
    if data_info["features"] != FEATURE_COLUMNS:
        print(
            "Feature columns in this code differ from the model's - check out the model's commit."
        )
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        local_model = Path(tmp) / TRAINED_MODEL
        s3.download_file(args.bucket, f"{prefix}/{TRAINED_MODEL}", str(local_model))
        booster = xgb.Booster()
        booster.load_model(local_model)

    split = data_info["split"]
    train_days = split["train"]["days"][1] - split["train"]["days"][0] + 1
    valid_days = split["valid"]["days"][1] - split["valid"]["days"][0] + 1
    folder = download_version(f"s3://{DEFAULT_DATASETS}/{data_info['dataset']}", args.cache)
    dataset, manifest = load_version(folder)
    test = prepare(dataset, manifest, train_days, valid_days).test

    fraud = test[test[LABEL] == 1]
    normal = test[test[LABEL] == 0].sample(min(args.rows, (test[LABEL] == 0).sum()), random_state=0)
    rows = np.vstack(
        [fraud[FEATURE_COLUMNS].to_numpy(float), normal[FEATURE_COLUMNS].to_numpy(float)]
    )
    print(
        f"Scoring {len(rows)} test-day rows ({len(fraud)} fraud) locally and on {args.endpoint} ..."
    )

    matrix = xgb.DMatrix(rows, feature_names=FEATURE_COLUMNS, feature_types=booster.feature_types)
    local = booster.predict(matrix)
    remote = score(boto3.client("sagemaker-runtime", region_name=REGION), args.endpoint, rows)

    threshold = metrics["evaluation"]["alert_budget"]["threshold"]
    result = compare(local, remote, threshold)
    print(json.dumps(result, indent=2))
    print(
        "PASS: the endpoint serves the evaluated model"
        if result["match"]
        else "FAIL: scores differ"
    )
    return 0 if result["match"] else 1


if __name__ == "__main__":
    sys.exit(main())
