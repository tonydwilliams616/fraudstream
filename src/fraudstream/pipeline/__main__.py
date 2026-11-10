"""python -m fraudstream.pipeline <step> - runs one pipeline step in our pipeline image.

What: the command line for the steps that run in our own image (the train step
      runs in SageMaker's XGBoost container, see train_entry.py). Defaults are the
      folders SageMaker Processing mounts, so the pipeline passes only what differs.
  prepare   --dataset DIR --out DIR --train-days N --valid-days N
  evaluate  --model DIR --data DIR --out DIR --run-id ID --git-commit SHA
            [--no-champion] [--n-estimators/--max-depth/--learning-rate as trained]
            [--min-pr-auc --pr-auc-tolerance --recall-tolerance]
  register  --metrics-uri S3 --model-uri S3
Exit code is 0 even when the gate fails: a failed gate is a valid result, which
the pipeline's condition step reads from evaluation.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fraudstream.pipeline import gate
from fraudstream.pipeline.steps import (
    evaluate_model,
    load_champion,
    prepare_data,
    register_challenger,
)

PROC = Path("/opt/ml/processing")
GROUP = "fs-fraud-detection"
REGION = "us-east-1"


def parse(argv):
    p = argparse.ArgumentParser(prog="python -m fraudstream.pipeline")
    sub = p.add_subparsers(dest="step", required=True)

    pr = sub.add_parser("prepare")
    pr.add_argument("--dataset", type=Path, default=PROC / "input" / "dataset")
    pr.add_argument("--out", type=Path, default=PROC / "output" / "data")
    pr.add_argument("--train-days", type=int, default=21)
    pr.add_argument("--valid-days", type=int, default=4)

    ev = sub.add_parser("evaluate")
    ev.add_argument("--model", type=Path, default=PROC / "input" / "model")
    ev.add_argument("--data", type=Path, default=PROC / "input" / "data")
    ev.add_argument("--out", type=Path, default=PROC / "output" / "evaluation")
    ev.add_argument("--run-id", required=True)
    ev.add_argument("--git-commit", default="unknown")
    ev.add_argument("--no-champion", action="store_true", help="skip the registry lookup")
    ev.add_argument("--n-estimators", type=int)
    ev.add_argument("--max-depth", type=int)
    ev.add_argument("--learning-rate", type=float)
    ev.add_argument("--min-pr-auc", type=float, default=gate.MIN_PR_AUC)
    ev.add_argument("--pr-auc-tolerance", type=float, default=gate.PR_AUC_TOLERANCE)
    ev.add_argument("--recall-tolerance", type=float, default=gate.RECALL_TOLERANCE)

    rg = sub.add_parser("register")
    rg.add_argument("--metrics-uri", required=True)
    rg.add_argument("--model-uri", required=True)

    for parser in (ev, rg):
        parser.add_argument("--group", default=GROUP)
        parser.add_argument("--region", default=REGION)
    return p.parse_args(argv)


def main(argv: list[str] | None = None, clients=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.step == "prepare":
        info = prepare_data(args.dataset, args.out, args.train_days, args.valid_days)
        print(json.dumps(info["split"]))
        return 0

    if clients is None and not (args.step == "evaluate" and args.no_champion):
        import boto3

        clients = {
            "sagemaker": boto3.client("sagemaker", region_name=args.region),
            "s3": boto3.client("s3", region_name=args.region),
        }

    if args.step == "evaluate":
        (model_tar,) = Path(args.model).glob("*.tar.gz")  # the training job's model.tar.gz
        champion = (
            None
            if args.no_champion
            else load_champion(clients["sagemaker"], clients["s3"], args.group)
        )
        params = {
            k: v
            for k, v in {
                "n_estimators": args.n_estimators,
                "max_depth": args.max_depth,
                "learning_rate": args.learning_rate,
            }.items()
            if v is not None
        }
        summary = evaluate_model(
            model_tar,
            args.data,
            args.out,
            run_id=args.run_id,
            git_commit=args.git_commit,
            params=params,
            champion=champion,
            gate_rules={
                "min_pr_auc": args.min_pr_auc,
                "pr_auc_tolerance": args.pr_auc_tolerance,
                "recall_tolerance": args.recall_tolerance,
            },
        )
        print(json.dumps(summary, indent=2))
        return 0

    arn, created = register_challenger(
        clients["sagemaker"],
        clients["s3"],
        args.metrics_uri,
        args.model_uri,
        args.group,
        args.region,
    )
    print(("Registered " if created else "Already registered: ") + arn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
