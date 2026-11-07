"""python -m fraudstream.simulator - the simulator program run by the Fargate task.

What: reads its settings from environment variables (how ECS passes config),
      loads the dataset version's cardholders and shops (checked against its
      manifest), creates the topic if needed, then sends transactions at a
      steady rate until stopped (or until MAX_MESSAGES, for tests).
Settings (environment variables):
  BOOTSTRAP_SERVERS  MSK brokers, port 9098 (from 30-streaming). Required unless DRY_RUN.
  TOPIC              default "transactions"
  DATASET_URI        default s3://fs-datasets-831547420318/transactions/v2
  RATE               messages per second, default 10
  START_DATE         first simulated day (YYYY-MM-DD), default today (UTC)
  SEED               default: derived from START_DATE, so each day's stream differs
  FRAUD_RATE         default 0.005
  MAX_MESSAGES       stop after this many (0 = run forever), default 0
  DRY_RUN            "1" = print messages instead of sending (no Kafka, no AWS needed
                     if DATASET_URI is a local folder)
  AWS_REGION         default us-east-1
Stopping: ECS sends SIGTERM before stopping a task; the simulator finishes the
message in hand, flushes what's queued, logs its totals and exits cleanly.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from fraudstream.simulator.events import Pacer, stream_transactions
from fraudstream.storage import download_version, load_version

log = logging.getLogger("fraudstream.simulator")


@dataclass(frozen=True)
class Settings:
    bootstrap_servers: str
    topic: str
    dataset_uri: str
    rate: float
    start_date: str
    seed: int
    fraud_rate: float
    max_messages: int
    dry_run: bool
    region: str

    @classmethod
    def from_env(cls, env: dict | None = None) -> Settings:
        env = os.environ if env is None else env
        start = env.get("START_DATE") or datetime.now(UTC).date().isoformat()
        dry_run = env.get("DRY_RUN", "0") == "1"
        bootstrap = env.get("BOOTSTRAP_SERVERS", "")
        if not bootstrap and not dry_run:
            raise ValueError("BOOTSTRAP_SERVERS is required (or set DRY_RUN=1)")
        return cls(
            bootstrap_servers=bootstrap,
            topic=env.get("TOPIC", "transactions"),
            dataset_uri=env.get("DATASET_URI", "s3://fs-datasets-831547420318/transactions/v2"),
            rate=float(env.get("RATE", "10")),
            start_date=start,
            seed=int(env.get("SEED") or date.fromisoformat(start).toordinal()),
            fraud_rate=float(env.get("FRAUD_RATE", "0.005")),
            max_messages=int(env.get("MAX_MESSAGES", "0")),
            dry_run=dry_run,
            region=env.get("AWS_REGION", "us-east-1"),
        )


class PrintSink:
    """DRY_RUN: print each message as one line of JSON instead of sending it."""

    def __init__(self, out=sys.stdout):
        self.out = out
        self.delivered = 0
        self.failed = 0

    def send(self, message: dict) -> None:
        self.out.write(json.dumps(message) + "\n")
        self.delivered += 1

    def flush(self, timeout: float = 30) -> None:
        self.out.flush()


def run(settings: Settings, sink, pacer: Pacer, stop=lambda: False) -> int:
    """Send messages until stop() is true or max_messages is reached. Returns how many were sent."""
    folder = (
        download_version(settings.dataset_uri, Path("/tmp/fraudstream-data"))
        if settings.dataset_uri.startswith("s3://")
        else Path(settings.dataset_uri)
    )
    dataset, manifest = load_version(folder)
    log.info(
        "streaming %s cards / %s shops from %s, start %s, seed %s, %s/s",
        len(dataset.cards), len(dataset.merchants), manifest["dataset"],
        settings.start_date, settings.seed, settings.rate,
    )  # fmt: skip
    stream = stream_transactions(
        dataset.cards,
        dataset.merchants,
        start=settings.start_date,
        seed=settings.seed,
        fraud_rate=settings.fraud_rate,
    )
    sent = 0
    last_log = time.monotonic()
    for message in stream:
        if stop() or (settings.max_messages and sent >= settings.max_messages):
            break
        pacer.wait()
        sink.send(message)
        sent += 1
        if time.monotonic() - last_log >= 60:
            log.info("sent %s, delivered %s, failed %s", sent, sink.delivered, sink.failed)
            last_log = time.monotonic()
    sink.flush()
    log.info("stopped: sent %s, delivered %s, failed %s", sent, sink.delivered, sink.failed)
    return sent


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()

    stopping = False

    def on_sigterm(_signum, _frame):
        nonlocal stopping
        log.info("SIGTERM received - finishing up")
        stopping = True

    signal.signal(signal.SIGTERM, on_sigterm)

    if settings.dry_run:
        sink = PrintSink()
    else:
        from fraudstream.simulator.kafka import KafkaSink, ensure_topic, producer_config

        config = producer_config(settings.bootstrap_servers, settings.region, "fs-simulator")
        ensure_topic(config, settings.topic)
        sink = KafkaSink(config, settings.topic)

    run(settings, sink, Pacer(settings.rate), stop=lambda: stopping)
    return 1 if sink.failed else 0


if __name__ == "__main__":
    sys.exit(main())
