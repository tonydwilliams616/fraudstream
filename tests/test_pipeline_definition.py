"""tests/test_pipeline_definition.py - checks the pipeline definition and the code bundle.

What: the definition has the four steps in order with the gate choosing between
      Register and GateRejected; every reference points at a parameter or step
      that exists (a typo would otherwise only show up as a failed run in AWS);
      hyperparameters are strings; every output lands under the runs/ prefix the
      pipeline role may write to; parameter defaults match the code's own
      defaults; the code bundle has the entry point on top and is reproducible;
      and the run outcome is classified correctly.
Why:  SageMaker checks a definition only when it runs it. These tests catch the
      mistakes that would otherwise cost a 15-minute run to discover.
"""

import io
import json
import tarfile

from fraudstream.pipeline import __main__ as cli
from fraudstream.pipeline.definition import (
    EVALUATION_REPORT,
    PARAMETERS,
    PipelineConfig,
    build_definition,
)
from fraudstream.pipeline.launch import outcome, source_bundle
from fraudstream.training.train import XGB_PARAMS

CFG = PipelineConfig(
    account_id="123456789012",
    image_tag="abc123",
    code_uri="s3://fs-artefacts-123456789012/models/fraud-pipeline/code/abc123/sourcedir.tar.gz",
)
DEF = build_definition(CFG)


def all_steps(steps):
    for step in steps:
        yield step
        if step["Type"] == "Condition":
            yield from all_steps(step["Arguments"]["IfSteps"])
            yield from all_steps(step["Arguments"]["ElseSteps"])


def walk(value):
    """Every dict nested anywhere inside value."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def test_steps_and_the_gate_choose_the_ending():
    assert [s["Name"] for s in DEF["Steps"]] == ["Prepare", "Train", "Evaluate", "GatePassed"]
    gate = DEF["Steps"][3]["Arguments"]
    assert [s["Name"] for s in gate["IfSteps"]] == ["Register"]
    assert [(s["Name"], s["Type"]) for s in gate["ElseSteps"]] == [("GateRejected", "Fail")]
    condition = gate["Conditions"][0]
    assert condition["LeftValue"]["Std:JsonGet"]["Path"] == "gate.passed"
    assert condition["RightValue"] is True


def test_every_reference_points_at_something_that_exists():
    names = {s["Name"] for s in all_steps(DEF["Steps"])}
    declared = {p["Name"] for p in DEF["Parameters"]}
    files = {
        f["PropertyFileName"] for s in all_steps(DEF["Steps"]) for f in s.get("PropertyFiles", [])
    }
    for node in walk(DEF):
        path = node.get("Get")
        if not isinstance(path, str):
            continue
        kind, _, rest = path.partition(".")
        if kind == "Parameters":
            assert rest in declared, path
        elif kind == "Steps":
            step, _, attribute = rest.partition(".")
            assert step in names, path
            if attribute.startswith("PropertyFiles."):
                assert attribute.split(".")[1] in files, path
        else:
            assert path == "Execution.PipelineExecutionId", path
    assert files == {EVALUATION_REPORT}


def test_definition_is_json_and_hyperparameters_are_strings():
    json.dumps(DEF)
    train = DEF["Steps"][1]["Arguments"]
    for value in train["HyperParameters"].values():
        assert isinstance(value, str) or set(value) == {"Get"}
    assert json.loads(train["HyperParameters"]["sagemaker_program"]) == "train_entry.py"
    assert all(p["Type"] == "String" for p in DEF["Parameters"])


def test_outputs_only_under_the_runs_prefix():
    runs = "s3://fs-artefacts-123456789012/models/fraud-pipeline/runs/"
    targets = [
        o["S3Output"]["S3Uri"]
        for s in all_steps(DEF["Steps"])
        for o in s["Arguments"].get("ProcessingOutputConfig", {}).get("Outputs", [])
    ]
    targets.append(DEF["Steps"][1]["Arguments"]["OutputDataConfig"]["S3OutputPath"])
    assert len(targets) == 3  # data, model, evaluation - and nothing else
    for uri in targets:
        assert uri["Std:Join"]["Values"][0] == runs


def test_images_and_role():
    processing = [s for s in all_steps(DEF["Steps"]) if s["Type"] == "Processing"]
    assert [s["Name"] for s in processing] == ["Prepare", "Evaluate", "Register"]
    for s in processing:
        assert s["Arguments"]["AppSpecification"]["ImageUri"].endswith("/fs/pipeline:abc123")
        assert s["Arguments"]["RoleArn"] == "arn:aws:iam::123456789012:role/fs-sagemaker-pipeline"
    train = DEF["Steps"][1]["Arguments"]
    assert train["AlgorithmSpecification"]["TrainingImage"].endswith("sagemaker-xgboost:3.0-5")


def test_parameter_defaults_match_the_code():
    assert PARAMETERS["NEstimators"] == str(XGB_PARAMS["n_estimators"])
    assert PARAMETERS["MaxDepth"] == str(XGB_PARAMS["max_depth"])
    assert PARAMETERS["LearningRate"] == str(XGB_PARAMS["learning_rate"])
    prepare = cli.parse(["prepare"])
    assert PARAMETERS["TrainDays"] == str(prepare.train_days)
    assert PARAMETERS["ValidDays"] == str(prepare.valid_days)


def test_code_bundle_has_the_entry_point_and_is_reproducible():
    first = source_bundle()
    assert first == source_bundle()  # same code -> same bytes
    with tarfile.open(fileobj=io.BytesIO(first)) as tar:
        names = tar.getnames()
    assert names[0] == "train_entry.py"
    assert "fraudstream/__init__.py" in names
    assert "fraudstream/training/train.py" in names
    assert "fraudstream/pipeline/train_entry.py" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]


def test_outcome_tells_a_rejection_from_a_breakage():
    gate_failed = [{"StepName": "GatePassed"}, {"StepName": "GateRejected"}]
    broken = [{"StepName": "Prepare"}]
    assert outcome("Succeeded", []) == "registered"
    assert outcome("Failed", gate_failed) == "rejected"
    assert outcome("Failed", broken) == "error"
    assert outcome("Stopped", gate_failed) == "error"
