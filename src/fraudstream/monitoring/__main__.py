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
  check     [--hour YYYY-MM-DDTHH] [--min-rows N] [--dry-run]
            the hourly drift check (check.py): one clock hour of the scored
            archive (default: the previous hour) against each model's baseline,
            PSI per feature published to CloudWatch. Buckets come from
            SCORED_BUCKET and ARTEFACTS_BUCKET (set by 35-apps). --dry-run
            prints the results instead of publishing them. Exit code 1 if a
            model had no baseline.
URIs may be s3://bucket/key or local paths. S3 needs AWS credentials (boto3).
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from fraudstream.monitoring.check import MIN_ROWS, previous_hour, run_check
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


class PrintMetrics:
    """--dry-run: stands in for the CloudWatch client and prints what it would send."""

    def put_metric_data(self, Namespace, MetricData):  # boto3's argument names
        for m in MetricData:
            dims = ", ".join(f"{d['Name']}={d['Value']}" for d in m.get("Dimensions", []))
            print(f"{Namespace} {m['MetricName']}{{{dims}}} = {m['Value']:.4f}")


def baseline_command(train_csv: str, out: str, s3=None) -> dict:
    train = pd.read_csv(io.BytesIO(read_bytes(train_csv, s3)))
    baseline = build_baseline(train, source={"train_csv": train_csv})
    write_bytes(out, (json.dumps(baseline, indent=2) + "\n").encode(), "application/json", s3)
    return baseline


def main(argv: list[str] | None = None, s3=None, cloudwatch=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fraudstream.monitoring")
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("baseline", help="build a model's drift baseline from train.csv")
    b.add_argument("--train-csv", required=True)
    b.add_argument("--out", required=True)
    c = commands.add_parser("check", help="hourly drift check: PSI per feature to CloudWatch")
    c.add_argument("--hour", help="UTC hour, e.g. 2026-11-28T14 (default: the previous hour)")
    c.add_argument("--min-rows", type=int, default=int(os.environ.get("MIN_ROWS", MIN_ROWS)))
    c.add_argument("--scored-bucket", default=os.environ.get("SCORED_BUCKET"))
    c.add_argument("--artefacts-bucket", default=os.environ.get("ARTEFACTS_BUCKET"))
    c.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "baseline":
        baseline = baseline_command(args.train_csv, args.out, s3)
        print(f"baseline from {baseline['rows']} training rows written to {args.out}")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not (args.scored_bucket and args.artefacts_bucket):
        parser.error(
            "set --scored-bucket and --artefacts-bucket (or SCORED_BUCKET, ARTEFACTS_BUCKET)"
        )
    hour = (
        datetime.strptime(args.hour, "%Y-%m-%dT%H").replace(tzinfo=UTC)
        if args.hour
        else previous_hour(datetime.now(UTC))
    )
    if cloudwatch is None:
        if args.dry_run:
            cloudwatch = PrintMetrics()
        else:
            import boto3

            cloudwatch = boto3.client("cloudwatch")
    summary = run_check(
        s3 or _s3(), cloudwatch, args.scored_bucket, args.artefacts_bucket, hour, args.min_rows
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "models"}, default=str))
    return 1 if summary["missing_baselines"] else 0


if __name__ == "__main__":
    sys.exit(main())
