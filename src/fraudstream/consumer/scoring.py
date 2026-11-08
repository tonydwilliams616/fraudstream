"""fraudstream.consumer.scoring - one micro-batch of transactions in, scored records out.

What: Scorer.score_batch() takes the transactions from one poll of Kafka,
      builds each one's features (FeatureBuilder - the same code as training),
      scores them all with ONE endpoint call, and returns a scored record per
      transaction: the raw fields, the features, the score, whether it's an
      alert, which model scored it, and how long it took. to_alert() is the
      smaller message the fraud team's alert topic gets.
Why these choices:
  - Micro-batching: one endpoint call per batch (e.g. 50 transactions) instead
    of one per transaction cuts per-call overhead; the cost is waiting up to
    the batch window (e.g. 200 ms) before scoring.
  - Features are built ONCE per transaction, before the endpoint call, and the
    call is retried on failure. Building them again on retry would add each
    transaction to its card's history twice and skew every later clue.
  - If the endpoint keeps failing, score_batch raises: the loop stops without
    committing, the task restarts, and Kafka hands the same transactions back.
    Nothing is lost; history starts cold (see FeatureBuilder's trade-off).
  - Latency is measured from when the simulator PRODUCED the message (Kafka's
    timestamp) to when its score is ready - real elapsed time. The event
    timestamp in the message is simulated time and runs faster than real life.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from fraudstream.consumer.model import ModelInfo
from fraudstream.endpoint import score
from fraudstream.features import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION

log = logging.getLogger(__name__)

MESSAGE_SCHEMA_VERSION = "1"  # the simulator's message format this code understands
RAW_FIELDS = (
    "transaction_id",
    "timestamp",
    "card_id",
    "merchant_id",
    "merchant_category",
    "channel",
    "merchant_lat",
    "merchant_lon",
    "amount",
)


@dataclass(frozen=True)
class Incoming:
    """One transaction as it came off Kafka."""

    value: dict  # the decoded message
    partition: int
    offset: int
    produced_ms: int | None  # Kafka timestamp: when the simulator produced it


def _json_safe(value):
    return None if isinstance(value, float) and math.isnan(value) else value


class Scorer:
    def __init__(
        self,
        builder,
        runtime,
        endpoint: str,
        model: ModelInfo,
        retries: int = 4,
        sleep=time.sleep,
        now_ms=lambda: int(time.time() * 1000),
    ):
        self.builder = builder
        self.runtime = runtime
        self.endpoint = endpoint
        self.model = model
        self.retries = retries
        self.sleep = sleep
        self.now_ms = now_ms
        self.skipped = 0  # messages in a format this code doesn't understand

    def score_batch(self, batch: list[Incoming]) -> list[dict]:
        """Scored records for a batch, in the same order. Raises if the endpoint keeps failing."""
        usable = []
        for item in batch:
            if item.value.get("schema_version") != MESSAGE_SCHEMA_VERSION:
                self.skipped += 1
                log.warning("skipping %s: unknown message schema", item.value.get("transaction_id"))
                continue
            usable.append(item)
        if not usable:
            return []

        features = [self.builder.features_for(item.value) for item in usable]  # once only
        rows = [[f[name] for name in FEATURE_COLUMNS] for f in features]
        scores = self._score_with_retry(rows)
        scored_ms = self.now_ms()
        scored_at = datetime.fromtimestamp(scored_ms / 1000, UTC).isoformat()

        records = []
        for item, feats, s in zip(usable, features, scores, strict=True):
            records.append(
                {
                    **{k: item.value.get(k) for k in RAW_FIELDS},
                    "features": {k: _json_safe(v) for k, v in feats.items()},
                    "feature_schema_version": FEATURE_SCHEMA_VERSION,
                    "score": float(s),
                    "alert": bool(s >= self.model.threshold),
                    "threshold": self.model.threshold,
                    "model_version": self.model.version,
                    "model_run_id": self.model.run_id,
                    "scored_at": scored_at,
                    "latency_ms": None
                    if item.produced_ms is None
                    else scored_ms - item.produced_ms,
                    "kafka_partition": item.partition,
                    "kafka_offset": item.offset,
                }
            )
        return records

    def _score_with_retry(self, rows):
        delay = 0.5
        for attempt in range(1, self.retries + 2):
            try:
                return score(self.runtime, self.endpoint, rows)
            except ValueError:
                raise  # a malformed reply won't fix itself
            except Exception as err:
                if attempt > self.retries:
                    raise
                log.warning("endpoint call failed (attempt %s): %s - retrying", attempt, err)
                self.sleep(delay)
                delay = min(delay * 2, 8)


def to_alert(record: dict) -> dict:
    """The alert for the fraud team: what, where, how suspicious, which model said so."""
    keys = (
        "transaction_id",
        "timestamp",
        "card_id",
        "merchant_id",
        "merchant_category",
        "channel",
        "amount",
        "score",
        "threshold",
        "model_version",
        "scored_at",
    )
    return {k: record[k] for k in keys}
