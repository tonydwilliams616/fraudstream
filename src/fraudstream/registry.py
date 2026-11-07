"""fraudstream.registry - registers a published model in the SageMaker Model Registry.

What: package_request() turns a published run (its metrics.json and the S3
      location of its model.tar.gz) into a CreateModelPackage request;
      find_package() looks for a run that's already registered; register()
      does both, so the same run is never registered twice.
Why:  the endpoint is built from a REGISTERED, APPROVED package, never from a
      loose file in S3. The package ties together everything needed to trust
      and serve a model: which container runs it, where its weights are, what
      data and code made it, how well it scored, and its alert threshold.
Concepts:
  - MODEL REGISTRY: the approved-medicines list. Each model package is one
    entry (version 1, 2, ...) in a group (fs-fraud-detection), with a status:
    PendingManualApproval -> Approved (or Rejected). Only Approved versions
    may be deployed.
  - The package records the serving container (SageMaker's built-in XGBoost
    3.0-5, matching the XGBoost 3.0 the model was trained with) and the input
    format (CSV rows of features in FEATURE_COLUMNS order, no header), so
    whoever deploys it can't pair the weights with the wrong container.
  - Metadata (CustomerMetadataProperties) carries the lineage and results; the
    full metrics.json is linked as the package's model-quality report, so the
    console shows the evidence next to the version.
"""

from __future__ import annotations

import re

XGBOOST_IMAGE_ACCOUNT = {"us-east-1": "683313688378", "us-west-2": "246618743249"}
XGBOOST_IMAGE_TAG = "3.0-5"  # built-in container release for XGBoost 3.0 (training pins 3.0.x)
CONTENT_TYPE = "text/csv"
INSTANCE_TYPES = ["ml.m5.large", "ml.m5.xlarge", "ml.c5.large", "ml.c5.xlarge"]

# SageMaker only accepts letters, numbers, spaces and _ . : / = + - @ in
# metadata, values up to 1024 characters.
_METADATA_OK = re.compile(r"^[\w\s.:/=+\-@]*$")


def xgboost_image(region: str) -> str:
    """URI of SageMaker's built-in XGBoost container in a region."""
    account = XGBOOST_IMAGE_ACCOUNT[region]
    return f"{account}.dkr.ecr.{region}.amazonaws.com/sagemaker-xgboost:{XGBOOST_IMAGE_TAG}"


def metadata(metrics: dict) -> dict[str, str]:
    """Lineage and headline results from metrics.json, as registry metadata."""
    data, training, ev = metrics["data"], metrics["training"], metrics["evaluation"]
    test = ev["test"]
    props = {
        "run_id": metrics["run_id"],
        "created_at": metrics["created_at"],
        "git_commit": metrics["code"]["git_commit"] or "unknown",
        "dataset": data["dataset"],
        "dataset_fingerprint": data["dataset_fingerprint"],
        "feature_schema_version": str(data["feature_schema_version"]),
        "features": " ".join(data["features"]),  # the column order the endpoint expects
        "xgboost_version": training["xgboost_version"],
        "trees": str(training["trees_used"]),
        "alert_threshold": str(ev["alert_budget"]["threshold"]),
        "alert_rate": str(ev["alert_budget"]["alert_rate"]),
        "test_pr_auc": str(test["pr_auc"]),
        "test_recall": str(test["recall"]),
        "test_precision": str(test["precision"]),
    }
    for key, value in props.items():
        if len(value) > 1024 or not _METADATA_OK.match(value):
            raise ValueError(f"metadata {key}={value!r} has characters SageMaker rejects")
    return props


def package_request(
    metrics: dict,
    model_data_url: str,
    metrics_url: str,
    group: str,
    region: str,
    approval: str = "PendingManualApproval",
) -> dict:
    """The CreateModelPackage request for a published run."""
    if approval not in ("PendingManualApproval", "Approved"):
        raise ValueError("approval must be PendingManualApproval or Approved")
    if not metrics["training"]["xgboost_version"].startswith("3.0."):
        raise ValueError(
            f"model was trained with XGBoost {metrics['training']['xgboost_version']}; "
            f"the {XGBOOST_IMAGE_TAG} container serves 3.0.x"
        )
    test = metrics["evaluation"]["test"]
    return {
        "ModelPackageGroupName": group,
        "ModelPackageDescription": (
            f"{metrics['run_id']}: test PR-AUC {test['pr_auc']}, recall {test['recall']} "
            f"at {test['alerts_per_day']} alerts/day"
        ),
        "ModelApprovalStatus": approval,
        "Domain": "MACHINE_LEARNING",
        "Task": "CLASSIFICATION",
        "InferenceSpecification": {
            "Containers": [
                {
                    "Image": xgboost_image(region),
                    "ModelDataUrl": model_data_url,
                    "Framework": "XGBOOST",
                    "FrameworkVersion": "3.0",
                }
            ],
            "SupportedContentTypes": [CONTENT_TYPE],
            "SupportedResponseMIMETypes": [CONTENT_TYPE],
            "SupportedRealtimeInferenceInstanceTypes": INSTANCE_TYPES,
        },
        "ModelMetrics": {
            "ModelQuality": {
                "Statistics": {"ContentType": "application/json", "S3Uri": metrics_url}
            }
        },
        "CustomerMetadataProperties": metadata(metrics),
    }


def find_package(sagemaker, group: str, run_id: str) -> str | None:
    """ARN of the package already registered for this run, if any."""
    pages = sagemaker.get_paginator("list_model_packages").paginate(ModelPackageGroupName=group)
    for page in pages:
        for summary in page["ModelPackageSummaryList"]:
            arn = summary["ModelPackageArn"]
            props = sagemaker.describe_model_package(ModelPackageName=arn).get(
                "CustomerMetadataProperties", {}
            )
            if props.get("run_id") == run_id:
                return arn
    return None


def register(sagemaker, request: dict) -> tuple[str, bool]:
    """Register the run unless it already is. Returns (ARN, created now?)."""
    group = request["ModelPackageGroupName"]
    existing = find_package(sagemaker, group, request["CustomerMetadataProperties"]["run_id"])
    if existing:
        return existing, False
    return sagemaker.create_model_package(**request)["ModelPackageArn"], True
