"""fraudstream.consumer.model - which model is the endpoint serving, and does it fit this code?

What: resolve_model() follows endpoint -> endpoint config -> model -> model
      package and returns that package's metadata: version, run, alert
      threshold, feature schema version and feature order. check_contract()
      refuses a model that expects different features from the ones this
      code builds, or that isn't Approved.
Why:  the threshold and the feature contract belong to the MODEL, not to the
      consumer's settings. Reading them from what's actually deployed means a
      promotion (new package version) brings its own threshold with it, every
      scored record can say which model version scored it, and a model built
      for different features is refused instead of quietly scoring garbage.
Concept: the FEATURE CONTRACT. A model learned from clues in one exact order
      and meaning (FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION). Sending it clues
      in another order is like swapping two answer columns on an exam - every
      number still looks plausible, and every answer is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from fraudstream.features import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION


class EndpointNotReady(Exception):
    """The endpoint doesn't exist yet or isn't InService - wait and try again."""


class ModelContractError(Exception):
    """The deployed model doesn't fit this code - never score with it."""


@dataclass(frozen=True)
class ModelInfo:
    package_arn: str
    version: int
    run_id: str
    approval: str
    threshold: float
    feature_schema_version: str
    features: tuple[str, ...]


def resolve_model(sagemaker, endpoint_name: str) -> ModelInfo:
    """The model package behind an InService endpoint, with its metadata."""
    try:
        endpoint = sagemaker.describe_endpoint(EndpointName=endpoint_name)
    except Exception as err:  # botocore ValidationException when it doesn't exist (yet)
        if "Could not find endpoint" in str(err):
            raise EndpointNotReady(f"endpoint {endpoint_name} doesn't exist yet") from err
        raise
    if endpoint["EndpointStatus"] != "InService":
        raise EndpointNotReady(f"endpoint {endpoint_name} is {endpoint['EndpointStatus']}")

    config = sagemaker.describe_endpoint_config(EndpointConfigName=endpoint["EndpointConfigName"])
    model_name = config["ProductionVariants"][0]["ModelName"]
    container = sagemaker.describe_model(ModelName=model_name)["PrimaryContainer"]
    package_arn = container.get("ModelPackageName")
    if not package_arn:
        raise ModelContractError(f"model {model_name} wasn't built from a registered package")

    package = sagemaker.describe_model_package(ModelPackageName=package_arn)
    props = package.get("CustomerMetadataProperties", {})
    try:
        return ModelInfo(
            package_arn=package_arn,
            version=int(package["ModelPackageVersion"]),
            run_id=props["run_id"],
            approval=package["ModelApprovalStatus"],
            threshold=float(props["alert_threshold"]),
            feature_schema_version=props["feature_schema_version"],
            features=tuple(props["features"].split()),
        )
    except KeyError as err:
        raise ModelContractError(f"package {package_arn} is missing metadata {err}") from err


def check_contract(model: ModelInfo) -> None:
    """Raise ModelContractError unless the model is Approved and expects exactly our features."""
    if model.approval != "Approved":
        raise ModelContractError(f"model v{model.version} is {model.approval}, not Approved")
    if model.feature_schema_version != FEATURE_SCHEMA_VERSION:
        raise ModelContractError(
            f"model v{model.version} expects feature schema {model.feature_schema_version}, "
            f"this code builds {FEATURE_SCHEMA_VERSION}"
        )
    if list(model.features) != FEATURE_COLUMNS:
        raise ModelContractError(f"model v{model.version} expects features in a different order")
