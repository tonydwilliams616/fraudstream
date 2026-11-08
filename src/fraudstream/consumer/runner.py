"""fraudstream.consumer.runner - the conveyor belt: read, score, write, then tick off.

What: Runner.step() takes one micro-batch from Kafka (up to batch_size
      messages, or whatever arrives within batch_wait seconds), scores it,
      sends every scored record to the `scored` topic and alerts to
      `fraud.alerts`, and buffers the records for the S3 archive. About once a
      minute checkpoint() makes everything durable and only THEN commits the
      Kafka offsets.
Why this order (at-least-once delivery):
  1. wait until Kafka has confirmed every scored record and alert (flush),
  2. write the archive file to S3,
  3. commit offsets - "everything before here is done".
  A crash anywhere before step 3 means the same transactions are read again
  and re-scored: nothing is lost, some may be written twice. Records carry
  transaction_id so readers can drop duplicates. (Exactly-once would need
  Kafka transactions AND an S3 write in the same transaction, which S3 can't
  join - at-least-once plus de-duplication is the standard answer.)
Partition hand-overs: before partitions are taken away (another consumer
  joined, or this one is stopping), checkpoint() runs, so the new owner starts
  exactly where this one finished. Cards on partitions this consumer RECEIVES
  start with empty history (see FeatureBuilder's trade-off).
Model changes: every refresh_s the deployed model is looked up again. A
  promotion is picked up with its own threshold; a model that breaks the
  feature contract stops scoring (it raises) rather than scoring wrongly.
"""

from __future__ import annotations

import json
import logging
import time

from fraudstream.consumer.model import EndpointNotReady, check_contract
from fraudstream.consumer.scoring import Incoming, to_alert

log = logging.getLogger(__name__)

_TIMESTAMP_NOT_AVAILABLE = 0  # confluent_kafka.TIMESTAMP_NOT_AVAILABLE


class DeliveryError(Exception):
    """Kafka didn't confirm every scored record - don't commit."""


class Runner:
    def __init__(
        self,
        consumer,
        producer,
        scorer,
        archive,
        scored_topic: str,
        alerts_topic: str,
        resolve_model,
        batch_size: int = 50,
        batch_wait: float = 0.2,
        refresh_s: float = 300,
        clock=time.monotonic,
    ):
        self.consumer = consumer
        self.producer = producer
        self.scorer = scorer
        self.archive = archive
        self.scored_topic = scored_topic
        self.alerts_topic = alerts_topic
        self.resolve_model = resolve_model
        self.batch_size = batch_size
        self.batch_wait = batch_wait
        self.refresh_s = refresh_s
        self.clock = clock
        self._last_refresh = clock()
        self.stats = {"consumed": 0, "scored": 0, "alerts": 0, "bad_messages": 0, "files": 0}
        self.max_latency_ms = 0  # worst produce-to-scored time since the last stats log
        self.failed_deliveries = 0
        self.healthy = True  # set False after an error: then NEVER commit (see on_revoke)

    # --- Kafka callbacks ----------------------------------------------------------
    def on_assign(self, _consumer, partitions):
        log.info("assigned partitions %s", sorted(p.partition for p in partitions))

    def on_revoke(self, _consumer, partitions):
        parts = sorted(p.partition for p in partitions)
        if not self.healthy:
            # After a failure some consumed messages were never scored; committing now
            # would skip them for good. Leave the offsets where they were.
            log.warning("revoking partitions %s after a failure - not committing", parts)
            return
        log.info("revoking partitions %s - checkpointing first", parts)
        self.checkpoint()

    def _on_delivery(self, err, _msg):
        if err is not None:
            self.failed_deliveries += 1
            log.error("delivery failed: %s", err)

    # --- the loop -----------------------------------------------------------------
    def step(self) -> int:
        """One micro-batch. Returns how many records were scored."""
        self._maybe_refresh_model()
        messages = self.consumer.consume(num_messages=self.batch_size, timeout=self.batch_wait)
        batch = []
        for msg in messages:
            if msg.error():
                log.warning("kafka: %s", msg.error())  # e.g. topic not created yet
                continue
            self.stats["consumed"] += 1
            try:
                value = json.loads(msg.value())
            except (TypeError, ValueError):
                self.stats["bad_messages"] += 1
                log.warning("skipping unreadable message at %s/%s", msg.partition(), msg.offset())
                continue
            ts_type, ts = msg.timestamp()
            produced = None if ts_type == _TIMESTAMP_NOT_AVAILABLE else ts
            batch.append(Incoming(value, msg.partition(), msg.offset(), produced))

        records = self.scorer.score_batch(batch)
        for record in records:
            key = str(record["card_id"]).encode()
            self.producer.produce(
                self.scored_topic, key=key, value=json.dumps(record), on_delivery=self._on_delivery
            )
            if record["alert"]:
                self.producer.produce(
                    self.alerts_topic,
                    key=key,
                    value=json.dumps(to_alert(record)),
                    on_delivery=self._on_delivery,
                )
                self.stats["alerts"] += 1
        self.producer.poll(0)
        self.archive.add(records)
        self.stats["scored"] += len(records)
        latencies = [r["latency_ms"] for r in records if r["latency_ms"] is not None]
        if latencies:
            self.max_latency_ms = max(self.max_latency_ms, *latencies)
        if self.archive.due():
            self.checkpoint()
        return len(records)

    def checkpoint(self) -> None:
        """Make everything so far durable, then commit offsets. Order matters - see module notes."""
        pending = self.producer.flush(30)
        if pending or self.failed_deliveries:
            raise DeliveryError(
                f"{pending} records unconfirmed, {self.failed_deliveries} failed - not committing"
            )
        if self.archive.flush():
            self.stats["files"] += 1
        try:
            self.consumer.commit(asynchronous=False)
        except Exception as err:
            if "NO_OFFSET" not in str(err):  # nothing new to commit is fine
                raise

    def _maybe_refresh_model(self) -> None:
        if self.clock() - self._last_refresh < self.refresh_s:
            return
        self._last_refresh = self.clock()
        try:
            model = self.resolve_model()
        except EndpointNotReady as err:
            log.info("model check skipped (%s) - keeping v%s", err, self.scorer.model.version)
            return
        if model != self.scorer.model:
            check_contract(model)  # raises: never score with a model that doesn't fit
            log.info(
                "now scoring with model v%s (was v%s), threshold %s",
                model.version, self.scorer.model.version, model.threshold,
            )  # fmt: skip
            self.scorer.model = model
