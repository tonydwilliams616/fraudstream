"""scripts/ops/check_scoring.py - proves the consumer lost nothing, and measures latency.

What: reads the consumer's S3 archive files written since --since, and for each
      Kafka partition checks that the scored offsets run unbroken from the
      first to the last. Every transaction gets the next offset (sequence
      number) in its partition, so a missing offset = a lost transaction.
      Also reports duplicates (the same offset scored twice - expected after a
      crash, never after a clean stop), the alert rate and latency percentiles
      (Kafka produce time -> score ready).
Why:  "at-least-once" is a claim; this is the evidence. Run it after killing or
      scaling the consumer mid-stream: gaps must be 0.
Exit code: 1 if any partition has a gap.

Usage (repo root, venv active, `source ./aws-login.sh`):
  python scripts/ops/check_scoring.py --since 2026-11-09T17:40
  python scripts/ops/check_scoring.py --minutes 20
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

DEFAULT_BUCKET = "fs-scored-831547420318"


def analyse(records: list[dict]) -> dict:
    """Gaps and duplicates per partition, alert rate and latency percentiles."""
    offsets: dict[int, Counter] = defaultdict(Counter)
    for r in records:
        offsets[r["kafka_partition"]][r["kafka_offset"]] += 1

    partitions = {}
    for p, counts in sorted(offsets.items()):
        first, last = min(counts), max(counts)
        missing = [o for o in range(first, last + 1) if o not in counts]
        partitions[p] = {
            "first": first,
            "last": last,
            "scored": sum(counts.values()),
            "unique": len(counts),
            "duplicates": sum(c - 1 for c in counts.values()),
            "gaps": len(missing),
            "first_missing": missing[:5],
        }

    latencies = sorted(r["latency_ms"] for r in records if r.get("latency_ms") is not None)

    def pct(q):
        return latencies[min(len(latencies) - 1, int(q * len(latencies)))] if latencies else None

    unique_tx = {r["transaction_id"] for r in records}
    alerts = {r["transaction_id"] for r in records if r["alert"]}
    return {
        "records": len(records),
        "unique_transactions": len(unique_tx),
        "duplicates": sum(v["duplicates"] for v in partitions.values()),
        "gaps": sum(v["gaps"] for v in partitions.values()),
        "alert_rate": round(len(alerts) / len(unique_tx), 5) if unique_tx else None,
        "latency_ms": {
            "p50": pct(0.5),
            "p95": pct(0.95),
            "p99": pct(0.99),
            "max": latencies[-1] if latencies else None,
        },
        "model_versions": sorted({r["model_version"] for r in records}),
        "partitions": partitions,
    }


def load(s3, bucket: str, since: datetime) -> list[dict]:
    """Every record in archive files written at or after `since`."""
    records = []
    day = since.date()
    while day <= datetime.now(UTC).date():
        pages = s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=f"scored/dt={day:%Y-%m-%d}/"
        )
        for page in pages:
            for obj in page.get("Contents", []):
                if obj["LastModified"] >= since:
                    body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                    lines = gzip.decompress(body).decode().splitlines()
                    records.extend(json.loads(line) for line in lines if line)
        day += timedelta(days=1)
    return records


def main(argv: list[str] | None = None, s3=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    when = p.add_mutually_exclusive_group()
    when.add_argument("--since", help="UTC time, e.g. 2026-11-09T17:40")
    when.add_argument("--minutes", type=int, default=30, help="look back this many minutes")
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    args = p.parse_args(argv)

    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    else:
        since = datetime.now(UTC) - timedelta(minutes=args.minutes)
    if s3 is None:
        import boto3

        s3 = boto3.client("s3")

    records = load(s3, args.bucket, since)
    if not records:
        print(f"No archive files since {since:%Y-%m-%d %H:%M} UTC.")
        return 1
    report = analyse(records)
    print(json.dumps(report, indent=2))
    verdict = "PASS: no transaction lost" if report["gaps"] == 0 else "FAIL: offsets missing"
    print(f"{verdict} - {report['unique_transactions']} transactions, "
          f"{report['duplicates']} scored twice")  # fmt: skip
    return 0 if report["gaps"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
