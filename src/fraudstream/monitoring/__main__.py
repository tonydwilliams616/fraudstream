"""python -m fraudstream.monitoring <command> - the drift check's command line.

What:
  baseline  --train-csv URI --out URI
            builds a model's drift baseline from its training rows (the
            train.csv of its pipeline run) and writes it as JSON. The pipeline's
            Prepare step does this for every new run; this command is for
            models trained before that existed, e.g. v2:
              python -m fraudstream.monitoring baseline \\
                --train-csv s3://fs-artefacts-<acct>/models/fraud-pipeline/runs/<run-id>/data/train.csv \\
                --out       s3://fs-artefacts-<acct>/models/fraud-pipeline/runs/<run-id>/data/drift-baseline.json
URIs may be s3://bucket/key or local paths. S3 needs AWS credentials (boto3).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import pandas as pd

from fraudstream.monitoring.drift import build_baseline


def read_bytes(uri: str, s3=None) -> bytes:
    if uri.startswith("s3://"):
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        s3 = s3 or _s3()
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return Path(uri).read_bytes()


def write_bytes(uri: str, data: bytes, content_type: str, s3=None) -> None:
    if uri.startswith("s3://"):
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        s3 = s3 or _s3()
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    else:
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
        Path(uri).write_bytes(data)


def _s3():
    import boto3

    return boto3.client("s3")


def baseline_command(train_csv: str, out: str, s3=None) -> dict:
    train = pd.read_csv(io.BytesIO(read_bytes(train_csv, s3)))
    baseline = build_baseline(train, source={"train_csv": train_csv})
    write_bytes(out, (json.dumps(baseline, indent=2) + "\n").encode(), "application/json", s3)
    return baseline


def main(argv: list[str] | None = None, s3=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fraudstream.monitoring")
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("baseline", help="build a model's drift baseline from train.csv")
    b.add_argument("--train-csv", required=True)
    b.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if args.command == "baseline":
        baseline = baseline_command(args.train_csv, args.out, s3)
        print(f"baseline from {baseline['rows']} training rows written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
