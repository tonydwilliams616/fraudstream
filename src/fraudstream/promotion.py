"""fraudstream.promotion - promote an Approved model to the endpoint, and check one is.

What:
  python -m fraudstream.promotion promote --version 2
      checks fs-fraud-detection/2 is Approved, then rewrites
      terraform/stacks/40-serving/model.auto.tfvars to serve it (with a comment
      saying what it is and why it can be trusted). You then commit it and open
      a PR - the promotion IS that PR.
  python -m fraudstream.promotion check
      reads the version in model.auto.tfvars and exits 1 unless it is Approved.
      The deploy workflow runs this on the PR's plan and again right before it
      applies 40-serving.
  python -m fraudstream.promotion verify
      AFTER 40-serving is applied: follows the live endpoint to the model package
      it is really serving and exits 1 unless that is the one in model.auto.tfvars.
      Needed because a blue/green update that SageMaker abandons or rolls back
      can still be reported by Terraform as "Modifications complete" - the
      deploy then looks green while the old model keeps serving.
Why: the registry's approval and git's record of what's deployed are two separate
     facts. This keeps them in step: nothing reaches the endpoint unless a person
     approved it in the registry AND a reviewed commit put it in the file.
     Three layers refuse an unapproved model: the PR check, the check before
     apply, and the consumer at runtime.
Light on purpose: only the standard library plus boto3 (imported when needed),
     so the workflows can run it without installing the training libraries.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

GROUP = "fs-fraud-detection"
TFVARS = Path("terraform/stacks/40-serving/model.auto.tfvars")
_ARN_LINE = re.compile(r'^model_package_arn\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
_ARN = re.compile(
    r"^arn:aws:sagemaker:(?P<region>[a-z0-9-]+):(?P<account>\d{12}):"
    r"model-package/(?P<group>[\w-]+)/(?P<version>\d+)$"
)


class NotApproved(Exception):
    """The model package may not be deployed."""


class NotServing(Exception):
    """The endpoint is not serving the model package git says it should."""


# --- the tfvars file ----------------------------------------------------------------
def read_arn(text: str) -> str:
    """The model_package_arn in model.auto.tfvars (exactly one)."""
    found = _ARN_LINE.findall(text)
    if len(found) != 1:
        raise ValueError(f"expected one model_package_arn line, found {len(found)}")
    return found[0]


def parse_arn(arn: str) -> dict:
    match = _ARN.match(arn)
    if not match:
        raise ValueError(f"not a versioned model package ARN: {arn}")
    return {**match.groupdict(), "version": int(match["version"])}


def with_version(arn: str, version: int) -> str:
    """The same group's ARN with another version number."""
    p = parse_arn(arn)
    return f"arn:aws:sagemaker:{p['region']}:{p['account']}:model-package/{p['group']}/{version}"


def render(package: dict) -> str:
    """model.auto.tfvars for a described model package."""
    props = package.get("CustomerMetadataProperties", {})
    approved = package.get("LastModifiedTime") or package.get("CreationTime")
    version = package["ModelPackageVersion"]
    description = package.get("ModelPackageDescription", "")
    run = props.get("run_id", "?")
    commit = props.get("git_commit", "?")[:12]
    dataset = props.get("dataset", "?")
    xgboost = props.get("xgboost_version", "?")
    status = package["ModelApprovalStatus"]
    if hasattr(approved, "strftime"):
        status += f" ({approved:%d %b %Y})"
    if package.get("ApprovalDescription"):
        status += f": {package['ApprovalDescription']}"
    details = [
        f"# Version {version}: {description}",
        f"# Run {run}, commit {commit}, dataset {dataset}, XGBoost {xgboost}.",
        f"# Status {status}",
    ]
    return "\n".join(
        [
            "# " + "-" * 77,
            "# 40-serving/model.auto.tfvars - THE model in production.",
            "#",
            "# Promoting a model = a PR that changes this file, made with",
            "#   python -m fraudstream.promotion promote --version <N>",
            "# which refuses any version that isn't Approved in the registry. The PR's",
            "# plan shows exactly what changes; merging + approving the deploy rolls it",
            "# out (blue/green). Rolling back = reverting the commit. The nightly rebuild",
            "# always restores this version.",
            "#",
            *details,
            "# " + "-" * 77,
            f'model_package_arn = "{package["ModelPackageArn"]}"',
            "",
        ]
    )


# --- the registry --------------------------------------------------------------------
def check(sagemaker, arn: str) -> dict:
    """Describe the package; raise NotApproved unless it may be deployed."""
    if parse_arn(arn)["group"] != GROUP:
        raise NotApproved(f"{arn} is not in the {GROUP} group")
    package = sagemaker.describe_model_package(ModelPackageName=arn)
    status = package["ModelApprovalStatus"]
    if status != "Approved":
        raise NotApproved(
            f"{GROUP}/{package['ModelPackageVersion']} is {status} - "
            "approve it in the registry before it can be deployed"
        )
    return package


def serving(sagemaker, endpoint_name: str) -> str:
    """The model package ARN the endpoint is ACTUALLY serving right now."""
    from botocore.exceptions import ClientError

    endpoint = sagemaker.describe_endpoint(EndpointName=endpoint_name)
    status, failure = endpoint["EndpointStatus"], endpoint.get("FailureReason")
    if status != "InService":
        raise NotServing(
            f"endpoint {endpoint_name} is {status}" + (f": {failure}" if failure else "")
        )
    config_name = endpoint["EndpointConfigName"]
    try:
        config = sagemaker.describe_endpoint_config(EndpointConfigName=config_name)
        model_name = config["ProductionVariants"][0]["ModelName"]
        container = sagemaker.describe_model(ModelName=model_name)["PrimaryContainer"]
    except ClientError as err:
        raise NotServing(
            f"endpoint {endpoint_name} runs config {config_name}, which (or whose model) no "
            "longer exists - a deployment that did not complete was reported as complete"
            + (f". Last failure: {failure}" if failure else "")
        ) from err
    return container.get("ModelPackageName", "")


def verify(sagemaker, endpoint_name: str, expected_arn: str) -> str:
    """Raise NotServing unless the endpoint serves expected_arn."""
    actual = serving(sagemaker, endpoint_name)
    if actual != expected_arn:
        failure = sagemaker.describe_endpoint(EndpointName=endpoint_name).get("FailureReason")
        raise NotServing(
            f"endpoint serves {actual or 'an unregistered model'}, but model.auto.tfvars says "
            f"{expected_arn}" + (f". Last failure: {failure}" if failure else "")
        )
    return actual


# --- command line --------------------------------------------------------------------
def parse(argv):
    p = argparse.ArgumentParser(prog="python -m fraudstream.promotion")
    sub = p.add_subparsers(dest="command", required=True)
    pr = sub.add_parser("promote", help="point model.auto.tfvars at an Approved version")
    pr.add_argument("--version", type=int, required=True)
    ck = sub.add_parser("check", help="exit 1 unless the deployed version is Approved")
    vf = sub.add_parser("verify", help="exit 1 unless the endpoint serves the version in the file")
    vf.add_argument("--endpoint", default="fs-fraud")
    for parser in (pr, ck, vf):
        parser.add_argument("--file", type=Path, default=TFVARS)
    return p.parse_args(argv)


def main(argv: list[str] | None = None, sagemaker=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    current = read_arn(args.file.read_text())
    target = with_version(current, args.version) if args.command == "promote" else current
    if sagemaker is None:
        import boto3

        sagemaker = boto3.client("sagemaker", region_name=parse_arn(target)["region"])

    if args.command == "verify":
        try:
            verify(sagemaker, args.endpoint, target)
        except NotServing as err:
            print(f"NOT SERVING: {err}")
            return 1
        print(f"OK: {args.endpoint} is serving {GROUP}/{parse_arn(target)['version']}")
        return 0

    try:
        package = check(sagemaker, target)
    except NotApproved as err:
        print(f"REFUSED: {err}")
        return 1

    version = package["ModelPackageVersion"]
    if args.command == "check":
        print(f"OK: {GROUP}/{version} is Approved")
        return 0

    if target == current:
        print(f"{GROUP}/{version} is already the deployed version - nothing to change")
        return 0
    args.file.write_text(render(package))
    print(f"Updated {args.file}: {GROUP}/{parse_arn(current)['version']} -> {version}")
    print("Next: commit it on a branch and open a PR - the plan shows the endpoint change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
