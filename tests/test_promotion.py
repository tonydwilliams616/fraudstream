"""tests/test_promotion.py - checks that only Approved models can be promoted.

What: reading and rewriting model.auto.tfvars (round trip, exactly one ARN), the
      approval check (Approved passes; Pending, Rejected and another group's
      package are refused), and the command line end to end with a fake registry:
      promote rewrites the file only for an Approved version, check exits 1 for
      an unapproved one.
Why:  this is the guard between "a model passed the gate" and "customers are
      scored by it". A bug here would let an unreviewed model into production.
"""

from datetime import UTC, datetime

import pytest

from fraudstream.promotion import NotApproved, check, main, parse_arn, read_arn, render

ARN1 = "arn:aws:sagemaker:us-east-1:123456789012:model-package/fs-fraud-detection/1"
ARN2 = ARN1[:-1] + "2"


class FakeRegistry:
    def __init__(self, statuses):
        self.statuses = statuses  # version -> status

    def describe_model_package(self, ModelPackageName):
        version = parse_arn(ModelPackageName)["version"]
        return {
            "ModelPackageArn": ModelPackageName,
            "ModelPackageVersion": version,
            "ModelApprovalStatus": self.statuses[version],
            "ModelPackageDescription": "run-x: test PR-AUC 0.81, recall 0.72 at 77 alerts/day",
            "LastModifiedTime": datetime(2026, 11, 13, tzinfo=UTC),
            "CustomerMetadataProperties": {"run_id": "run-x", "git_commit": "abc123" * 6},
        }


def tfvars(tmp_path, arn=ARN1):
    path = tmp_path / "model.auto.tfvars"
    path.write_text(f'# header\nmodel_package_arn = "{arn}"\n')
    return path


def test_render_round_trips_and_explains_the_version():
    package = FakeRegistry({2: "Approved"}).describe_model_package(ARN2)
    text = render(package)
    assert read_arn(text) == ARN2
    assert "# Version 2: run-x: test PR-AUC 0.81" in text
    assert "13 Nov 2026" in text


def test_exactly_one_arn_line_is_required():
    with pytest.raises(ValueError):
        read_arn("# no arn here\n")
    with pytest.raises(ValueError):
        read_arn(f'model_package_arn = "{ARN1}"\nmodel_package_arn = "{ARN2}"\n')


def test_only_approved_packages_pass():
    registry = FakeRegistry({1: "Approved", 2: "PendingManualApproval", 3: "Rejected"})
    assert check(registry, ARN1)["ModelPackageVersion"] == 1
    for arn in (ARN2, ARN1[:-1] + "3"):
        with pytest.raises(NotApproved):
            check(registry, arn)
    with pytest.raises(NotApproved):
        check(registry, ARN1.replace("fs-fraud-detection", "someone-else"))


def test_promote_rewrites_the_file_only_when_approved(tmp_path):
    path = tfvars(tmp_path)
    pending = FakeRegistry({1: "Approved", 2: "PendingManualApproval"})
    assert main(["promote", "--version", "2", "--file", str(path)], pending) == 1
    assert read_arn(path.read_text()) == ARN1  # untouched

    approved = FakeRegistry({1: "Approved", 2: "Approved"})
    assert main(["promote", "--version", "2", "--file", str(path)], approved) == 0
    assert read_arn(path.read_text()) == ARN2


def test_check_fails_for_an_unapproved_deployed_version(tmp_path):
    path = tfvars(tmp_path, ARN2)
    assert main(["check", "--file", str(path)], FakeRegistry({2: "Approved"})) == 0
    assert main(["check", "--file", str(path)], FakeRegistry({2: "Rejected"})) == 1
