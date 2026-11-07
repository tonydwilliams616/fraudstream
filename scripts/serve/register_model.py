"""scripts/serve/register_model.py - registers a published model run in the Model Registry.

What: reads metrics.json of a run published by scripts/train/package_model.py,
      checks its model.tar.gz exists, and registers it in the fs-fraud-detection
      model package group (as a new version). Registering the same run twice
      does nothing and prints the existing version.
Why:  the endpoint (terraform/stacks/40-serving) is built only from a registered,
      Approved package. Phase 3's training pipeline takes over this job; this
      script registers the Phase 1 baseline.
Approval: new versions start as PendingManualApproval - a person reviews the
      evidence and approves (console or CLI). --approve registers as Approved
      straight away. Deploying is a separate, reviewed change: put the ARN in
      terraform/stacks/40-serving/model.auto.tfvars and open a PR.

Usage (repo root, venv active, `source ./aws-login.sh` first):
  python scripts/serve/register_model.py baseline-20261106T120445Z
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3

from fraudstream.registry import package_request, register
from fraudstream.training.package import METRICS, MODEL_NAME, TARBALL

DEFAULT_BUCKET = "fs-artefacts-831547420318"
DEFAULT_GROUP = "fs-fraud-detection"
REGION = "us-east-1"


def main(argv: list[str] | None = None, s3=None, sagemaker=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("run_id", help="a published run, e.g. baseline-20261106T120445Z")
    p.add_argument("--approve", action="store_true", help="register as Approved")
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--group", default=DEFAULT_GROUP)
    args = p.parse_args(argv)

    s3 = s3 or boto3.client("s3", region_name=REGION)
    sagemaker = sagemaker or boto3.client("sagemaker", region_name=REGION)
    prefix = f"models/{MODEL_NAME}/{args.run_id}"
    metrics_obj = s3.get_object(Bucket=args.bucket, Key=f"{prefix}/{METRICS}")
    metrics = json.loads(metrics_obj["Body"].read())
    s3.head_object(Bucket=args.bucket, Key=f"{prefix}/{TARBALL}")  # fails if missing

    request = package_request(
        metrics,
        model_data_url=f"s3://{args.bucket}/{prefix}/{TARBALL}",
        metrics_url=f"s3://{args.bucket}/{prefix}/{METRICS}",
        group=args.group,
        region=REGION,
        approval="Approved" if args.approve else "PendingManualApproval",
    )
    arn, created = register(sagemaker, request)
    status = sagemaker.describe_model_package(ModelPackageName=arn)["ModelApprovalStatus"]
    print(("Registered " if created else "Already registered: ") + arn)
    print(f"Status: {status}")
    print(f'To deploy, set in model.auto.tfvars:  model_package_arn = "{arn}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
