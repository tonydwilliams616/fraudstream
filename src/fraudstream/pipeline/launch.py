"""python -m fraudstream.pipeline.launch - publish the pipeline, start a run, wait for it.

What: 1. packs the training code: train_entry.py at the top plus the fraudstream
         package (what SageMaker's XGBoost container needs in script mode), and
         uploads it to models/fraud-pipeline/code/<commit>/sourcedir.tar.gz
      2. upserts the pipeline definition (create the first time, update after)
      3. starts a run with any parameters given on the command line
      4. with --wait, follows the run, prints each step's status and decides the
         exit code from --expect:
           registered  the run must succeed (the challenger passed the gate)
           rejected    the gate must reject it - how the gate itself is tested
Why: the GitHub workflow runs exactly this, and so can you from a laptop.
Lineage: the code bundle and the image are both named by the same commit, and
      that commit is passed to the run as GitCommit, so every registered model
      points back to the code that made it.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import fraudstream
from fraudstream.pipeline.definition import (
    PARAMETERS,
    PIPELINE_NAME,
    PREFIX,
    PipelineConfig,
    build_definition,
)

ENTRY = "train_entry.py"
DONE = {"Succeeded", "Failed", "Stopped"}
GATE_FAIL_STEP = "GateRejected"


# --- 1. the code bundle -------------------------------------------------------------
def source_bundle() -> bytes:
    """sourcedir.tar.gz: train_entry.py at the top, the fraudstream package beside it.

    Built the same way every time (sorted names, fixed timestamps - including
    gzip's own header timestamp, which tarfile would set to "now"), so the same
    code always gives the same bytes.
    """
    package = Path(fraudstream.__file__).parent
    files = sorted(
        p
        for p in package.rglob("*.py")
        if "__pycache__" not in p.parts and not p.name.startswith(".")
    )
    entries = [(ENTRY, package / "pipeline" / ENTRY)]
    entries += [(f"fraudstream/{p.relative_to(package).as_posix()}", p) for p in files]

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, path in entries:
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    return gzip.compress(buf.getvalue(), mtime=0)


def upload_bundle(s3, cfg_bucket: str, git_commit: str) -> str:
    key = f"{PREFIX}/code/{git_commit}/sourcedir.tar.gz"
    s3.put_object(Bucket=cfg_bucket, Key=key, Body=source_bundle())
    return f"s3://{cfg_bucket}/{key}"


# --- 2. and 3. upsert and start ------------------------------------------------------
def upsert(sagemaker, definition: dict, role_arn: str) -> str:
    body = json.dumps(definition)
    try:
        sagemaker.describe_pipeline(PipelineName=PIPELINE_NAME)
    except sagemaker.exceptions.ResourceNotFound:
        sagemaker.create_pipeline(
            PipelineName=PIPELINE_NAME,
            PipelineDescription="Train, evaluate against the champion, register if it passes",
            PipelineDefinition=body,
            RoleArn=role_arn,
            Tags=[{"Key": "Project", "Value": "fraudstream"}],
        )
        return "created"
    sagemaker.update_pipeline(PipelineName=PIPELINE_NAME, PipelineDefinition=body, RoleArn=role_arn)
    return "updated"


def start(sagemaker, git_commit: str, params: dict) -> str:
    values = {"GitCommit": git_commit, **params}
    unknown = set(values) - set(PARAMETERS)
    if unknown:
        raise ValueError(f"unknown pipeline parameters: {sorted(unknown)}")
    response = sagemaker.start_pipeline_execution(
        PipelineName=PIPELINE_NAME,
        PipelineExecutionDisplayName=f"{git_commit[:12]}-{int(time.time())}",
        PipelineParameters=[{"Name": k, "Value": str(v)} for k, v in values.items()],
    )
    return response["PipelineExecutionArn"]


# --- 4. wait and judge -----------------------------------------------------------------
def outcome(status: str, steps: list[dict]) -> str:
    """'registered', 'rejected' (by the gate) or 'error' (anything else went wrong)."""
    if status == "Succeeded":
        return "registered"
    if status == "Failed" and any(s["StepName"] == GATE_FAIL_STEP for s in steps):
        return "rejected"
    return "error"


def wait(sagemaker, arn: str, poll: int = 30) -> tuple[str, list[dict]]:
    seen = {}
    while True:
        status = sagemaker.describe_pipeline_execution(PipelineExecutionArn=arn)[
            "PipelineExecutionStatus"
        ]
        steps = sagemaker.list_pipeline_execution_steps(
            PipelineExecutionArn=arn, SortOrder="Ascending"
        )["PipelineExecutionSteps"]
        for s in steps:
            if seen.get(s["StepName"]) != s["StepStatus"]:
                seen[s["StepName"]] = s["StepStatus"]
                print(f"  {s['StepName']:<14} {s['StepStatus']}", flush=True)
        if status in DONE:
            return status, steps
        time.sleep(poll)


def summary(arn: str, status: str, steps: list[dict], result: str) -> str:
    lines = [
        "## Training pipeline run",
        "",
        f"Execution: `{arn}`",
        f"Status: **{status}** - result: **{result}**",
        "",
        "| Step | Status | Reason |",
        "|---|---|---|",
    ]
    for s in steps:
        reason = (s.get("FailureReason") or "").replace("|", "/").replace("\n", " ")[:200]
        lines.append(f"| {s['StepName']} | {s['StepStatus']} | {reason} |")
    return "\n".join(lines) + "\n"


# --- command line --------------------------------------------------------------------
def parse(argv):
    p = argparse.ArgumentParser(prog="python -m fraudstream.pipeline.launch")
    p.add_argument("--git-commit", required=True, help="full commit SHA of the code")
    p.add_argument("--image-tag", help="fs/pipeline image tag (default: --git-commit)")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--dataset-version")
    p.add_argument("--n-estimators")
    p.add_argument("--max-depth")
    p.add_argument("--learning-rate")
    p.add_argument("--wait", action="store_true", help="follow the run to the end")
    p.add_argument("--expect", choices=["registered", "rejected"], default="registered")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    import boto3

    account = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]
    s3 = boto3.client("s3", region_name=args.region)
    sagemaker = boto3.client("sagemaker", region_name=args.region)

    bucket = f"fs-artefacts-{account}"
    code_uri = upload_bundle(s3, bucket, args.git_commit)
    cfg = PipelineConfig(
        account_id=account,
        image_tag=args.image_tag or args.git_commit,
        code_uri=code_uri,
        region=args.region,
    )
    print(f"Code:     {code_uri}")
    print(f"Image:    {cfg.pipeline_image}")
    print(f"Pipeline: {upsert(sagemaker, build_definition(cfg), cfg.role_arn)} {PIPELINE_NAME}")

    params = {
        name: value
        for name, value in {
            "DatasetVersion": args.dataset_version,
            "NEstimators": args.n_estimators,
            "MaxDepth": args.max_depth,
            "LearningRate": args.learning_rate,
        }.items()
        if value
    }
    arn = start(sagemaker, args.git_commit, params)
    print(f"Started:  {arn}  parameters={json.dumps(params)}")
    if not args.wait:
        return 0

    status, steps = wait(sagemaker, arn)
    result = outcome(status, steps)
    text = summary(arn, status, steps, result)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)
    if result != args.expect:
        print(f"Expected the run to end '{args.expect}', but it ended '{result}'")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
