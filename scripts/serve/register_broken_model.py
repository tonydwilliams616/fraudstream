"""scripts/serve/register_broken_model.py - registers a DELIBERATELY BROKEN model for the rollback test.

What: trains a tiny XGBoost model on random data with 13 features (the real
      model has 14), packs it exactly like a real one, uploads it under
      models/broken-demo/<timestamp>/ and registers it in fs-fraud-detection as
      PendingManualApproval, clearly labelled as broken.
Why:  Phase 4 must prove that a bad model is rolled back automatically. This one
      is broken in the most useful way: it loads and passes the container's
      health check, so the canary starts and takes traffic - and then fails on
      EVERY real request, because the consumer sends 14 columns to a model that
      expects 13. The endpoint answers with errors, the 4xx/5xx alarm fires during
      the 5-minute bake, and SageMaker returns all traffic to the last-known-good
      model. (A corrupt file would fail at start-up instead, and never exercise
      the alarms.)
After the test: reject the package so it can never be deployed again:
  aws sagemaker update-model-package --model-package-arn <arn> \
    --model-approval-status Rejected --approval-description "rollback test done"

Usage (repo root, venv active, `source ./aws-login.sh` first):
  python scripts/serve/register_broken_model.py
"""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime

import boto3
import numpy as np
import xgboost as xgb

from fraudstream.registry import CONTENT_TYPE, INSTANCE_TYPES, xgboost_image

BUCKET = "fs-artefacts-831547420318"
GROUP = "fs-fraud-detection"
REGION = "us-east-1"
WRONG_FEATURE_COUNT = 13  # the endpoint will be sent 14


def broken_model_tar() -> bytes:
    """model.tar.gz holding a valid XGBoost model that expects 13 features."""
    rng = np.random.default_rng(0)
    x = rng.random((200, WRONG_FEATURE_COUNT))
    y = rng.integers(0, 2, 200)
    booster = xgb.train({"objective": "binary:logistic", "max_depth": 2}, xgb.DMatrix(x, y), 5)
    raw = bytes(booster.save_raw("json"))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("xgboost-model")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def main() -> int:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key = f"models/broken-demo/{stamp}/model.tar.gz"
    boto3.client("s3", region_name=REGION).put_object(
        Bucket=BUCKET, Key=key, Body=broken_model_tar()
    )
    arn = boto3.client("sagemaker", region_name=REGION).create_model_package(
        ModelPackageGroupName=GROUP,
        ModelPackageDescription=(
            f"DELIBERATELY BROKEN broken-demo-{stamp}: expects {WRONG_FEATURE_COUNT} features, "
            "fails every real request. For the canary rollback test only."
        ),
        ModelApprovalStatus="PendingManualApproval",
        InferenceSpecification={
            "Containers": [
                {
                    "Image": xgboost_image(REGION),
                    "ModelDataUrl": f"s3://{BUCKET}/{key}",
                    "Framework": "XGBOOST",
                    "FrameworkVersion": "3.0",
                }
            ],
            "SupportedContentTypes": [CONTENT_TYPE],
            "SupportedResponseMIMETypes": [CONTENT_TYPE],
            "SupportedRealtimeInferenceInstanceTypes": INSTANCE_TYPES,
        },
        CustomerMetadataProperties={
            "run_id": f"broken-demo-{stamp}",
            "purpose": "canary rollback test - never deploy",
            "xgboost_version": xgb.__version__,
        },
    )["ModelPackageArn"]
    print(f"Registered {arn}")
    print("It is PendingManualApproval. Approving it is a deliberate step of the test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
