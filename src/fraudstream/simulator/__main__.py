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
  DRIFT_PARAMETER    SSM parameter holding the drift scenario, re-read every minute
                     (see fraudstream.simulator.drift). Empty = use DRIFT_SCENARIO.
  DRIFT_SCENARIO     fixed scenario when there's no parameter, default "none"
  LABELS_URI         where chargebacks are written, e.g. s3://<scored bucket>/labels/chargebacks
                     (see fraudstream.simulator.chargebacks). Empty = no chargeback feed.
  CHARGEBACK_MIN_DAYS / CHARGEBACK_MAX_DAYS
                     simulated days from purchase to chargeback, default 1 and 3
  CHARGEBACK_REPORT_RATE  share of fraud that is ever reported, default 0.9
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

import numpy as np

from fraudstream.simulator.chargebacks import (
    ChargebackFeed,
    S3ChargebackWriter,
    event_time,
    log_writer,
)
from fraudstream.simulator.drift import SCENARIOS, DriftSwitch, fixed, ssm_parameter
from fraudstream.simulator.events import Pacer, stream_labelled
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
    drift_parameter: str = ""
    drift_scenario: str = "none"
    labels_uri: str = ""
    chargeback_min_days: float = 1.0
    chargeback_max_days: float = 3.0
    chargeback_report_rate: float = 0.9

    @classmethod
    def from_env(cls, env: dict | None = None) -> Settings:
        env = os.environ if env is None else env
        start = env.get("START_DATE") or datetime.now(UTC).date().isoformat()
        dry_run = env.get("DRY_RUN", "0") == "1"
        bootstrap = env.get("BOOTSTRAP_SERVERS", "")
        if not bootstrap and not dry_run:
            raise ValueError("BOOTSTRAP_SERVERS is required (or set DRY_RUN=1)")
        drift_scenario = env.get("DRIFT_SCENARIO") or "none"
        if drift_scenario not in SCENARIOS:
            raise ValueError(f"DRIFT_SCENARIO must be one of {sorted(SCENARIOS)}")
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
            drift_parameter=env.get("DRIFT_PARAMETER", ""),
            drift_scenario=drift_scenario,
            labels_uri=env.get("LABELS_URI", ""),
            chargeback_min_days=float(env.get("CHARGEBACK_MIN_DAYS", "1")),
            chargeback_max_days=float(env.get("CHARGEBACK_MAX_DAYS", "3")),
            chargeback_report_rate=float(env.get("CHARGEBACK_REPORT_RATE", "0.9")),
        )

    def drift_switch(self) -> DriftSwitch:
        """The live switch (SSM parameter) if one is named, else a fixed scenario."""
        if self.drift_parameter:
            return DriftSwitch(ssm_parameter(self.drift_parameter, self.region))
        return DriftSwitch(fixed(self.drift_scenario))

    def chargeback_feed(self) -> ChargebackFeed | None:
        """The chargeback feed: to S3 if LABELS_URI is set, logged in DRY_RUN, else none."""
        if self.labels_uri:
            if not self.labels_uri.startswith("s3://"):
                raise ValueError("LABELS_URI must be s3://<bucket>/<prefix>")
            import boto3

            bucket, _, prefix = self.labels_uri.removeprefix("s3://").partition("/")
            write = S3ChargebackWriter(boto3.client("s3", region_name=self.region), bucket, prefix)
        elif self.dry_run:
            write = log_writer
        else:
            return None
        return ChargebackFeed(
            write,
            # Its own random stream, seeded from the run's seed: the same run
            # gives the same delays, without disturbing the transactions'.
            np.random.default_rng([self.seed, 1]),
            min_days=self.chargeback_min_days,
            max_days=self.chargeback_max_days,
            report_rate=self.chargeback_report_rate,
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


def run(
    settings: Settings,
    sink,
    pacer: Pacer,
    stop=lambda: False,
    drift: DriftSwitch | None = None,
    chargebacks: ChargebackFeed | None = None,
) -> int:
    """Send messages until stop() is true or max_messages is reached. Returns how many were sent."""
    drift = drift or settings.drift_switch()
    chargebacks = chargebacks if chargebacks is not None else settings.chargeback_feed()
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
    stream = stream_labelled(
        dataset.cards,
        dataset.merchants,
        start=settings.start_date,
        seed=settings.seed,
        fraud_rate=settings.fraud_rate,
    )
    sent = 0
    now = None  # the simulated clock: event time of the latest message sent
    last_log = time.monotonic()
    for message, truth in stream:
        if stop() or (settings.max_messages and sent >= settings.max_messages):
            break
        pacer.wait()
        sink.send(drift.apply(message))
        sent += 1
        now = event_time(message)
        if chargebacks:
            chargebacks.observe(message, truth)
            chargebacks.post_due(now)
        if time.monotonic() - last_log >= 60:
            log.info(
                "sent %s, delivered %s, failed %s, drift %s, chargebacks posted %s waiting %s",
                sent, sink.delivered, sink.failed, drift.scenario,
                chargebacks.posted if chargebacks else 0, chargebacks.waiting if chargebacks else 0,
            )  # fmt: skip
            last_log = time.monotonic()
    sink.flush()
    if chargebacks and now is not None:
        chargebacks.post_due(now, force=True)  # what's due by now; later ones are lost
        log.info(
            "chargebacks: %s frauds, %s posted, %s never reported, %s lost on stop",
            chargebacks.frauds, chargebacks.posted, chargebacks.unreported, chargebacks.waiting,
        )  # fmt: skip
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
