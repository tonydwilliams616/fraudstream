"""scripts/ops/load_test.py - sends extra traffic to the endpoint to prove it scales out.

What: builds REAL feature rows from the dataset's test days (so the requests -
      which data capture records - look like genuine traffic, and don't distort
      drift monitoring), then calls the fs-fraud endpoint at a fixed rate for a
      fixed time from a pool of threads, 50 rows per call like the consumer.
      Every minute it prints calls made, errors and latency (p50 / p99), and
      the endpoint's current instance count, so you can watch it scale.
Why:  auto scaling (terraform/stacks/40-serving/autoscaling.tf) only shows its
      worth under load. Normal traffic is ~6 calls per instance per minute;
      the scaling target is 100. The defaults here send 600 calls a minute
      (~300 per instance with 2), well past the target, for 15 minutes.
Watch: scale-out needs ~3 minutes of high metrics plus ~5-8 minutes for new
      instances to start. Scale-in after the test is deliberately slow (~15+
      minutes of low traffic).
Cost: each extra instance ~$0.115/hour while it runs; calls themselves are free.

Usage (repo root, venv active, `source ./aws-login.sh`, endpoint InService):
  python scripts/ops/load_test.py --per-minute 600 --minutes 15
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

from fraudstream.endpoint import to_csv
from fraudstream.features import FEATURE_COLUMNS
from fraudstream.storage import download_version, load_version
from fraudstream.training.data import prepare

DATASET = "s3://fs-datasets-831547420318/transactions/v2"
ENDPOINT = "fs-fraud"
REGION = "us-east-1"
ROWS_PER_CALL = 50


def test_day_batches(cache: Path) -> list[str]:
    """CSV bodies of 50 real test-day feature rows each."""
    dataset, manifest = load_version(download_version(DATASET, cache))
    rows = prepare(dataset, manifest, 21, 4).test[FEATURE_COLUMNS].to_numpy(float)
    return [
        to_csv(rows[i : i + ROWS_PER_CALL])
        for i in range(0, len(rows) - ROWS_PER_CALL, ROWS_PER_CALL)
    ]


def instance_count(sagemaker) -> str:
    variant = sagemaker.describe_endpoint(EndpointName=ENDPOINT)["ProductionVariants"][0]
    return f"{variant.get('CurrentInstanceCount')}/{variant.get('DesiredInstanceCount')}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--per-minute", type=int, default=600, help="endpoint calls per minute")
    p.add_argument("--minutes", type=int, default=15)
    p.add_argument("--threads", type=int, default=16)
    p.add_argument("--cache", type=Path, default=Path("data"))
    args = p.parse_args(argv)

    bodies = test_day_batches(args.cache)
    runtime = boto3.client("sagemaker-runtime", region_name=REGION)
    sagemaker = boto3.client("sagemaker", region_name=REGION)
    lock = threading.Lock()
    minute = {"calls": 0, "errors": 0, "latency_ms": []}

    def call(body: str) -> None:
        start = time.perf_counter()
        try:
            runtime.invoke_endpoint(
                EndpointName=ENDPOINT, ContentType="text/csv", Accept="text/csv", Body=body
            )["Body"].read()
            ok = True
        except Exception:  # count it and keep going: errors are part of what we measure
            ok = False
        with lock:
            minute["calls"] += 1
            minute["errors"] += 0 if ok else 1
            minute["latency_ms"].append((time.perf_counter() - start) * 1000)

    interval = 60 / args.per_minute
    end = time.time() + args.minutes * 60
    next_report = time.time() + 60
    sent = 0
    print(
        f"{args.per_minute} calls/min for {args.minutes} min; instances (current/desired) "
        f"{instance_count(sagemaker)}"
    )
    with ThreadPoolExecutor(max_workers=args.threads) as pool:
        while time.time() < end:
            pool.submit(call, bodies[sent % len(bodies)])
            sent += 1
            if time.time() >= next_report:
                with lock:
                    lat = sorted(minute["latency_ms"]) or [0.0]
                    p99 = lat[min(len(lat) - 1, int(len(lat) * 0.99))]
                    print(
                        f"{time.strftime('%H:%M:%S')} calls={minute['calls']} "
                        f"errors={minute['errors']} p50={statistics.median(lat):.0f}ms "
                        f"p99={p99:.0f}ms instances={instance_count(sagemaker)}",
                        flush=True,
                    )
                    minute.update(calls=0, errors=0, latency_ms=[])
                next_report += 60
            time.sleep(interval)
    print(f"Done: {sent} calls. Keep watching instances - scale-in takes ~15+ minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
