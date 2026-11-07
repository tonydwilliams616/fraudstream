"""tests/test_registry.py - checks model registration builds the right package, once.

What: the CreateModelPackage request points at the right container and model,
      carries lineage and results as metadata SageMaker will accept, refuses a
      model trained with an XGBoost the container can't serve, and registering
      the same run twice creates only one version. SageMaker and S3 are faked.
Why:  the registry entry is what the endpoint is built from. A wrong container,
      a missing threshold or a duplicate version would break the link between
      the model we evaluated and the model we serve.
"""

import io
import json
import runpy
from pathlib import Path

import pytest

from fraudstream.registry import XGBOOST_IMAGE_TAG, metadata, package_request, register

FEATURES = ["amount", "log_amount", "hour_of_day", "is_online"]
METRICS = {
    "run_id": "baseline-20261106T120445Z",
    "created_at": "2026-11-06T12:04:45+00:00",
    "code": {"git_commit": "a" * 40, "git_dirty": False},
    "data": {
        "dataset": "transactions/v2",
        "dataset_fingerprint": "26a6c068e5f9" + "0" * 52,
        "feature_schema_version": 1,
        "features": FEATURES,
    },
    "training": {"xgboost_version": "3.0.5", "trees_used": 117},
    "evaluation": {
        "alert_budget": {"alert_rate": 0.005, "threshold": 0.912345},
        "test": {
            "pr_auc": 0.795,
            "recall": 0.72,
            "precision": 0.77,
            "alerts_per_day": 79.2,
        },
    },
}
MODEL_URL = "s3://bucket/models/fraud-baseline/baseline-20261106T120445Z/model.tar.gz"
METRICS_URL = "s3://bucket/models/fraud-baseline/baseline-20261106T120445Z/metrics.json"


def request(**overrides):
    args = {
        "metrics": METRICS,
        "model_data_url": MODEL_URL,
        "metrics_url": METRICS_URL,
        "group": "fs-fraud-detection",
        "region": "us-east-1",
    }
    return package_request(**{**args, **overrides})


class FakeSageMaker:
    """Just enough of the SageMaker API for registration: an in-memory group."""

    def __init__(self):
        self.packages = {}

    def create_model_package(self, **req):
        group = req["ModelPackageGroupName"]
        arn = f"arn:aws:sagemaker:us-east-1:1:model-package/{group}/{len(self.packages) + 1}"
        self.packages[arn] = req
        return {"ModelPackageArn": arn}

    def describe_model_package(self, ModelPackageName):
        req = self.packages[ModelPackageName]
        return {
            "ModelApprovalStatus": req["ModelApprovalStatus"],
            "CustomerMetadataProperties": req["CustomerMetadataProperties"],
        }

    def get_paginator(self, name):
        assert name == "list_model_packages"
        fake = self

        class Paginator:
            def paginate(self, ModelPackageGroupName):
                yield {"ModelPackageSummaryList": [{"ModelPackageArn": a} for a in fake.packages]}

        return Paginator()


def test_package_points_at_the_matching_container_and_model():
    container = request()["InferenceSpecification"]["Containers"][0]
    assert container["Image"] == (
        f"683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:{XGBOOST_IMAGE_TAG}"
    )
    assert container["ModelDataUrl"] == MODEL_URL
    assert request()["InferenceSpecification"]["SupportedContentTypes"] == ["text/csv"]


def test_new_versions_wait_for_approval_unless_asked():
    assert request()["ModelApprovalStatus"] == "PendingManualApproval"
    assert request(approval="Approved")["ModelApprovalStatus"] == "Approved"
    with pytest.raises(ValueError):
        request(approval="Rejected")


def test_metadata_carries_lineage_threshold_and_feature_order():
    props = metadata(METRICS)
    assert props["alert_threshold"] == "0.912345"
    assert props["features"].split() == FEATURES  # the order the endpoint expects
    assert props["dataset"] == "transactions/v2"
    assert all(isinstance(v, str) for v in props.values())


def test_metadata_with_characters_sagemaker_rejects_fails_early():
    bad = json.loads(json.dumps(METRICS))
    bad["data"]["dataset"] = "transactions,v2"
    with pytest.raises(ValueError, match="dataset"):
        metadata(bad)


def test_model_from_another_xgboost_version_is_refused():
    newer = json.loads(json.dumps(METRICS))
    newer["training"]["xgboost_version"] = "3.4.1"
    with pytest.raises(ValueError, match="3.4.1"):
        request(metrics=newer)


def test_registering_the_same_run_twice_creates_one_version():
    sm = FakeSageMaker()
    first, created = register(sm, request())
    again, created_again = register(sm, request())
    assert created and not created_again
    assert first == again
    assert len(sm.packages) == 1


def test_script_registers_from_the_published_metrics(capsys):
    class FakeS3:
        def get_object(self, Bucket, Key):
            assert Key.endswith("/metrics.json")
            return {"Body": io.BytesIO(json.dumps(METRICS).encode())}

        def head_object(self, Bucket, Key):
            assert Key.endswith("/model.tar.gz")

    script = Path(__file__).parents[1] / "scripts" / "serve" / "register_model.py"
    main = runpy.run_path(str(script))["main"]
    sm = FakeSageMaker()
    assert main(["baseline-20261106T120445Z"], s3=FakeS3(), sagemaker=sm) == 0
    out = capsys.readouterr().out
    assert "Registered arn:aws:sagemaker" in out
    assert "Status: PendingManualApproval" in out
    assert 'model_package_arn = "arn:aws:sagemaker' in out
