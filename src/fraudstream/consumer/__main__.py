"""python -m fraudstream.consumer - the consumer program run by the Fargate task.

What: reads its settings from environment variables, waits until the fraud
      endpoint is InService and serving a model that fits this code, loads
      the dataset version's cardholder and shop records, creates its output
      topics if needed, then scores the `transactions` stream until stopped.
Settings (environment variables):
  BOOTSTRAP_SERVERS      MSK brokers, port 9098 (from 30-streaming). Required.
  INPUT_TOPIC            default "transactions"
  SCORED_TOPIC           default "scored"
  ALERTS_TOPIC           default "fraud.alerts"
  GROUP_ID               default "fs-scorer" (the IAM policy allows only this group)
  ENDPOINT_NAME          default "fs-fraud"
  DATASET_URI            reference tables, default s3://fs-datasets-831547420318/transactions/v2
  ARCHIVE_BUCKET         default fs-scored-831547420318
  BATCH_SIZE             messages per endpoint call, default 50
  BATCH_WAIT_MS          longest wait to fill a batch, default 200
  ARCHIVE_SECONDS        how often to write an archive file and commit, default 60
  MODEL_REFRESH_SECONDS  how often to re-check which model is deployed, default 300
  AWS_REGION             default us-east-1
Start-up order: on a rebuild this task can start before the endpoint exists
  (35-apps is applied before 40-serving). It waits, logging why, instead of
  crashing; transactions wait safely in Kafka meanwhile.
Stopping: on SIGTERM it finishes the batch in hand, writes the archive,
  commits and leaves the group cleanly. After an error it exits WITHOUT
  committing, so the next task re-reads anything not yet safely written.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from fraudstream.consumer.model import EndpointNotReady, check_contract, resolve_model

log = logging.getLogger("fraudstream.consumer")


@dataclass(frozen=True)
class Settings:
    bootstrap_servers: str
    input_topic: str
    scored_topic: str
    alerts_topic: str
    group_id: str
    endpoint: str
    dataset_uri: str
    archive_bucket: str
    batch_size: int
    batch_wait_ms: int
    archive_seconds: float
    model_refresh_seconds: float
    region: str

    @classmethod
    def from_env(cls, env: dict | None = None) -> Settings:
        env = os.environ if env is None else env
        bootstrap = env.get("BOOTSTRAP_SERVERS", "")
        if not bootstrap:
            raise ValueError("BOOTSTRAP_SERVERS is required")
        return cls(
            bootstrap_servers=bootstrap,
            input_topic=env.get("INPUT_TOPIC", "transactions"),
            scored_topic=env.get("SCORED_TOPIC", "scored"),
            alerts_topic=env.get("ALERTS_TOPIC", "fraud.alerts"),
            group_id=env.get("GROUP_ID", "fs-scorer"),
            endpoint=env.get("ENDPOINT_NAME", "fs-fraud"),
            dataset_uri=env.get("DATASET_URI", "s3://fs-datasets-831547420318/transactions/v2"),
            archive_bucket=env.get("ARCHIVE_BUCKET", "fs-scored-831547420318"),
            batch_size=int(env.get("BATCH_SIZE", "50")),
            batch_wait_ms=int(env.get("BATCH_WAIT_MS", "200")),
            archive_seconds=float(env.get("ARCHIVE_SECONDS", "60")),
            model_refresh_seconds=float(env.get("MODEL_REFRESH_SECONDS", "300")),
            region=env.get("AWS_REGION", "us-east-1"),
        )


def wait_for_model(resolve, stop=lambda: False, sleep=time.sleep, max_delay: float = 30):
    """Keep asking until the endpoint is InService; None if told to stop first."""
    delay = 2.0
    while not stop():
        try:
            return resolve()
        except EndpointNotReady as err:
            log.info("waiting for the model: %s (next check in %.0fs)", err, delay)
            sleep(delay)
            delay = min(delay * 2, max_delay)
    return None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    stopping = False

    def on_sigterm(_signum, _frame):
        nonlocal stopping
        log.info("SIGTERM received - finishing up")
        stopping = True

    signal.signal(signal.SIGTERM, on_sigterm)

    import boto3
    from confluent_kafka import Consumer, Producer

    from fraudstream.consumer.archive import ArchiveBuffer
    from fraudstream.consumer.runner import Runner
    from fraudstream.consumer.scoring import Scorer
    from fraudstream.features import FeatureBuilder
    from fraudstream.msk import consumer_config, ensure_topic, producer_config
    from fraudstream.storage import download_version, load_version

    sagemaker = boto3.client("sagemaker", region_name=settings.region)
    runtime = boto3.client("sagemaker-runtime", region_name=settings.region)
    s3 = boto3.client("s3", region_name=settings.region)

    def current_model():
        return resolve_model(sagemaker, settings.endpoint)

    model = wait_for_model(current_model, stop=lambda: stopping)
    if model is None:
        return 0
    check_contract(model)
    log.info(
        "scoring with model v%s (%s), threshold %s", model.version, model.run_id, model.threshold
    )

    folder = download_version(settings.dataset_uri, Path("/tmp/fraudstream-data"))
    dataset, manifest = load_version(folder)
    builder = FeatureBuilder(dataset.cards, dataset.merchants)
    log.info("reference data: %s cards, %s shops from %s", len(dataset.cards),
             len(dataset.merchants), manifest["dataset"])  # fmt: skip

    client_id = f"fs-consumer-{socket.gethostname()}"
    pconf = producer_config(settings.bootstrap_servers, settings.region, client_id)
    for topic in (settings.scored_topic, settings.alerts_topic):
        ensure_topic(pconf, topic)
    producer = Producer(pconf)
    producer.poll(1)  # serves the IAM token callback before the first send
    consumer = Consumer(
        consumer_config(settings.bootstrap_servers, settings.region, client_id, settings.group_id)
    )

    runner = Runner(
        consumer,
        producer,
        Scorer(builder, runtime, settings.endpoint, model),
        ArchiveBuffer(
            s3, settings.archive_bucket, socket.gethostname(), max_age_s=settings.archive_seconds
        ),  # fmt: skip
        settings.scored_topic,
        settings.alerts_topic,
        current_model,
        batch_size=settings.batch_size,
        batch_wait=settings.batch_wait_ms / 1000,
        refresh_s=settings.model_refresh_seconds,
    )
    consumer.subscribe(
        [settings.input_topic], on_assign=runner.on_assign, on_revoke=runner.on_revoke
    )

    last_log = time.monotonic()
    try:
        while not stopping:
            runner.step()
            if time.monotonic() - last_log >= 60:
                log.info("%s, worst latency %s ms", runner.stats, runner.max_latency_ms)
                runner.max_latency_ms = 0
                last_log = time.monotonic()
        runner.checkpoint()  # clean stop: make everything durable and commit
    except Exception:
        runner.healthy = False  # never commit after a failure
        log.exception("stopping after an error - offsets not committed")
        raise
    finally:
        consumer.close()  # leaves the group; on_revoke commits only if healthy
        producer.flush(10)
    log.info("stopped: %s", runner.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
