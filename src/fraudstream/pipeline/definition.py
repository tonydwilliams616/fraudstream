"""fraudstream.pipeline.definition - the SageMaker Pipeline, written as plain data.

What: build_definition() returns the pipeline's definition: the JSON document
      SageMaker stores and runs. Four steps, the gate deciding the ending:
        Prepare    (our image)  dataset version -> train/valid/test CSVs
        Train      (SageMaker's XGBoost 3.0-5 image, script mode, train_entry.py)
        Evaluate   (our image)  challenger vs champion on the same days -> evaluation.json
        GatePassed (condition)  reads gate.passed from evaluation.json
          yes -> Register      (our image)  PendingManualApproval in the registry
          no  -> GateRejected  (fail step)  the run ends as Failed, with the reason
Why plain data, not the SageMaker Python SDK: the definition is just JSON, so
      building it as a dict needs no SDK (whose API changed a lot in v3), is easy
      to unit-test, and shows every change to the pipeline in a PR diff.
References: {"Get": "Parameters.X"} is a run-time parameter, {"Get": "Steps.Y..."}
      is an output of an earlier step, and Std:Join builds a string at run time.
      SageMaker works out the step order from these references.
Outputs: every run writes under models/fraud-pipeline/runs/<execution id>/ in the
      artefacts bucket - the only place the pipeline's role may write.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fraudstream.training.train import XGB_PARAMS

PIPELINE_NAME = "fs-fraud-training"
PREFIX = "models/fraud-pipeline"  # in the artefacts bucket: code/ and runs/
INSTANCE_TYPE = "ml.m5.large"  # 2 vCPU, 8 GiB - plenty for ~1M rows; billed per second
MAX_RUNTIME = 3600  # seconds per job: a stuck job is stopped, not billed for hours
PROC = "/opt/ml/processing"

# SageMaker's built-in XGBoost image - the same one the endpoint serves with.
XGBOOST_IMAGE = {
    "us-east-1": "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:3.0-5",
}

# Run-time parameters (name -> default). All strings: hyperparameters and
# container arguments are strings in SageMaker's API anyway. The gate's rules are
# deliberately NOT parameters - changing what "good enough" means needs a PR.
PARAMETERS = {
    "GitCommit": "unknown",  # recorded in the model's lineage
    "DatasetVersion": "v2",  # folder under s3://fs-datasets-<account>/transactions/
    "TrainDays": "21",
    "ValidDays": "4",
    "NEstimators": str(XGB_PARAMS["n_estimators"]),
    "MaxDepth": str(XGB_PARAMS["max_depth"]),
    "LearningRate": str(XGB_PARAMS["learning_rate"]),
}

EVALUATION_REPORT = "EvaluationReport"  # the property file the condition step reads


@dataclass(frozen=True)
class PipelineConfig:
    account_id: str
    image_tag: str  # fs/pipeline:<tag> - the commit the image was built from
    code_uri: str  # s3://.../sourcedir.tar.gz - train_entry.py plus the package
    region: str = "us-east-1"

    @property
    def role_arn(self) -> str:
        return f"arn:aws:iam::{self.account_id}:role/fs-sagemaker-pipeline"

    @property
    def artefacts_bucket(self) -> str:
        return f"fs-artefacts-{self.account_id}"

    @property
    def datasets_bucket(self) -> str:
        return f"fs-datasets-{self.account_id}"

    @property
    def pipeline_image(self) -> str:
        return f"{self.account_id}.dkr.ecr.{self.region}.amazonaws.com/fs/pipeline:{self.image_tag}"

    @property
    def xgboost_image(self) -> str:
        return XGBOOST_IMAGE[self.region]


# --- small helpers for SageMaker's expression language ---------------------------
def get(path: str) -> dict:
    return {"Get": path}


def param(name: str) -> dict:
    if name not in PARAMETERS:
        raise KeyError(f"unknown pipeline parameter {name}")
    return get(f"Parameters.{name}")


def join(*values, on: str = "") -> dict:
    return {"Std:Join": {"On": on, "Values": list(values)}}


EXECUTION_ID = get("Execution.PipelineExecutionId")


def output_uri(step: str, output: str) -> dict:
    return get(f"Steps.{step}.ProcessingOutputConfig.Outputs['{output}'].S3Output.S3Uri")


MODEL_URI = get("Steps.Train.ModelArtifacts.S3ModelArtifacts")  # .../output/model.tar.gz


# --- step builders ----------------------------------------------------------------
def _input(name: str, uri) -> dict:
    return {
        "InputName": name,
        "AppManaged": False,
        "S3Input": {
            "S3Uri": uri,
            "LocalPath": f"{PROC}/input/{name}",
            "S3DataType": "S3Prefix",
            "S3InputMode": "File",
            "S3DataDistributionType": "FullyReplicated",
        },
    }


def _output(name: str, uri) -> dict:
    return {
        "OutputName": name,
        "AppManaged": False,
        "S3Output": {
            "S3Uri": uri,
            "LocalPath": f"{PROC}/output/{name}",
            "S3UploadMode": "EndOfJob",
        },
    }


def _processing(cfg: PipelineConfig, name: str, args: list, inputs=(), outputs=()) -> dict:
    """A step that runs `python -m fraudstream.pipeline <args>` in our image."""
    arguments = {
        "ProcessingResources": {
            "ClusterConfig": {
                "InstanceCount": 1,
                "InstanceType": INSTANCE_TYPE,
                "VolumeSizeInGB": 30,
            }
        },
        # No entrypoint here: the image's own ENTRYPOINT runs, args are appended.
        "AppSpecification": {"ImageUri": cfg.pipeline_image, "ContainerArguments": args},
        "RoleArn": cfg.role_arn,
        "StoppingCondition": {"MaxRuntimeInSeconds": MAX_RUNTIME},
    }
    if inputs:
        arguments["ProcessingInputs"] = list(inputs)
    if outputs:
        arguments["ProcessingOutputConfig"] = {"Outputs": list(outputs)}
    return {"Name": name, "Type": "Processing", "Arguments": arguments}


def run_uri(cfg: PipelineConfig, part: str) -> dict:
    """s3://<artefacts>/models/fraud-pipeline/runs/<execution id>/<part>"""
    return join(f"s3://{cfg.artefacts_bucket}/{PREFIX}/runs/", EXECUTION_ID, f"/{part}")


def prepare_step(cfg: PipelineConfig) -> dict:
    dataset = join(f"s3://{cfg.datasets_bucket}/transactions/", param("DatasetVersion"), "/")
    return _processing(
        cfg,
        "Prepare",
        ["prepare", "--train-days", param("TrainDays"), "--valid-days", param("ValidDays")],
        inputs=[_input("dataset", dataset)],
        outputs=[_output("data", run_uri(cfg, "data"))],
    )


def train_step(cfg: PipelineConfig) -> dict:
    return {
        "Name": "Train",
        "Type": "Training",
        "Arguments": {
            "AlgorithmSpecification": {
                "TrainingImage": cfg.xgboost_image,
                "TrainingInputMode": "File",
            },
            # Script mode: the container downloads sagemaker_submit_directory, then
            # runs sagemaker_program with every other hyperparameter as --name value.
            # The two sagemaker_* values are JSON strings, as the SageMaker SDK sends them.
            "HyperParameters": {
                "sagemaker_program": json.dumps("train_entry.py"),
                "sagemaker_submit_directory": json.dumps(cfg.code_uri),
                "n_estimators": param("NEstimators"),
                "max_depth": param("MaxDepth"),
                "learning_rate": param("LearningRate"),
            },
            "InputDataConfig": [
                {
                    "ChannelName": "data",  # -> SM_CHANNEL_DATA in train_entry.py
                    "DataSource": {
                        "S3DataSource": {
                            "S3DataType": "S3Prefix",
                            "S3Uri": output_uri("Prepare", "data"),
                            "S3DataDistributionType": "FullyReplicated",
                        }
                    },
                    "ContentType": "text/csv",
                }
            ],
            "OutputDataConfig": {"S3OutputPath": run_uri(cfg, "model")},
            "ResourceConfig": {
                "InstanceCount": 1,
                "InstanceType": INSTANCE_TYPE,
                "VolumeSizeInGB": 30,
            },
            "RoleArn": cfg.role_arn,
            "StoppingCondition": {"MaxRuntimeInSeconds": MAX_RUNTIME},
        },
    }


def evaluate_step(cfg: PipelineConfig) -> dict:
    step = _processing(
        cfg,
        "Evaluate",
        [
            "evaluate",
            "--run-id",
            EXECUTION_ID,
            "--git-commit",
            param("GitCommit"),
            "--n-estimators",
            param("NEstimators"),
            "--max-depth",
            param("MaxDepth"),
            "--learning-rate",
            param("LearningRate"),
        ],
        inputs=[_input("model", MODEL_URI), _input("data", output_uri("Prepare", "data"))],
        outputs=[_output("evaluation", run_uri(cfg, "evaluation"))],
    )
    # Lets the condition step read values out of evaluation.json.
    step["PropertyFiles"] = [
        {
            "PropertyFileName": EVALUATION_REPORT,
            "OutputName": "evaluation",
            "FilePath": "evaluation.json",
        }
    ]
    return step


def register_step(cfg: PipelineConfig) -> dict:
    evaluation = output_uri("Evaluate", "evaluation")
    return _processing(
        cfg,
        "Register",
        [
            "register",
            "--metrics-uri",
            join(evaluation, "/metrics.json"),
            "--model-uri",
            MODEL_URI,
        ],
    )


def gate_step(cfg: PipelineConfig) -> dict:
    evaluation = output_uri("Evaluate", "evaluation")
    return {
        "Name": "GatePassed",
        "Type": "Condition",
        "Arguments": {
            "Conditions": [
                {
                    "Type": "Equals",
                    "LeftValue": {
                        "Std:JsonGet": {
                            "PropertyFile": get(
                                f"Steps.Evaluate.PropertyFiles.{EVALUATION_REPORT}"
                            ),
                            "Path": "gate.passed",
                        }
                    },
                    "RightValue": True,
                }
            ],
            "IfSteps": [register_step(cfg)],
            "ElseSteps": [
                {
                    "Name": "GateRejected",
                    "Type": "Fail",
                    "Arguments": {
                        "ErrorMessage": join(
                            "The challenger did not pass the evaluation gate - see ",
                            evaluation,
                            "/evaluation.json",
                        )
                    },
                }
            ],
        },
    }


def build_definition(cfg: PipelineConfig) -> dict:
    return {
        "Version": "2020-12-01",
        "Metadata": {},
        "Parameters": [
            {"Name": name, "Type": "String", "DefaultValue": default}
            for name, default in PARAMETERS.items()
        ],
        "Steps": [prepare_step(cfg), train_step(cfg), evaluate_step(cfg), gate_step(cfg)],
    }
