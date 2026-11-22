"""fraudstream.simulator.chargebacks - the bank's chargeback feed: fraud labels that arrive late.

What: ChargebackFeed.observe() sees every transaction the simulator sends,
      together with the truth the generator knows. For a fraud, it writes a
      chargeback and dates it for delivery 1-3 simulated days after the
      purchase (about 1 in 10 is never reported). post_due() writes every
      chargeback whose delivery date has passed on the SIMULATED clock to S3:
        s3://<scored bucket>/labels/chargebacks/dt=<date known>/<time>-<run>-<n>.jsonl
Why:  retraining on what really streamed through the system needs labels, and
      in real life labels come late: the cardholder spots a payment they didn't
      make and disputes it, days or weeks later. The messages on Kafka never
      carry labels; this feed is the only way the truth comes back.
Concepts:
  - DELAYED LABELS. Each chargeback carries event_time (when the purchase
    happened) and known_at (when the bank found out). A training set built "as
    of" some moment may only use chargebacks known by then - otherwise it
    learns from the future (leakage). A file is only written once its known_at
    has passed, so nothing can be read early.
  - LABEL MATURITY. Legitimate purchases get no message - banks don't send
    "this was fine" notices. Silence means "legit" only once a purchase is
    older than the longest usual delay; before that, its chargeback may simply
    not have arrived yet.
  - LABEL NOISE. Some fraud is never reported (report_rate 0.9), so training
    labels are imperfect - as they are in every real fraud system.
  - A HEAP (heapq) keeps the waiting chargebacks in delivery-date order, so the
    next one due is always at the front - a post office sorting letters by
    delivery date.
Known limits: chargebacks still waiting when the simulator stops are lost
  (they become "never reported"). A same-day restart replays transactions and
  so their chargebacks too - readers de-duplicate on transaction_id.
"""

from __future__ import annotations

import heapq
import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import numpy as np

log = logging.getLogger("fraudstream.simulator.chargebacks")

CHARGEBACK_SCHEMA_VERSION = "1"


def event_time(message: dict) -> datetime:
    """The purchase time of a message (its ISO 8601 `timestamp`)."""
    return datetime.fromisoformat(message["timestamp"])


class ChargebackFeed:
    def __init__(
        self,
        write: Callable[[list[dict]], None],
        rng: np.random.Generator,
        min_days: float = 1.0,
        max_days: float = 3.0,
        report_rate: float = 0.9,
        write_every_seconds: float = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not 0 <= min_days <= max_days:
            raise ValueError("need 0 <= min_days <= max_days")
        if not 0 <= report_rate <= 1:
            raise ValueError("report_rate must be between 0 and 1")
        self._write = write
        self._rng = rng
        self.min_days = min_days
        self.max_days = max_days
        self.report_rate = report_rate
        self.write_every_seconds = write_every_seconds
        self._clock = clock
        self._last_write: float | None = None
        # (known_at, sequence, record): the sequence breaks ties, so two
        # chargebacks due at the same moment never compare their dicts.
        self._pending: list[tuple[datetime, int, dict]] = []
        self._sequence = 0
        self.frauds = 0
        self.unreported = 0
        self.posted = 0

    @property
    def waiting(self) -> int:
        return len(self._pending)

    def observe(self, message: dict, truth: dict) -> None:
        """Note a sent transaction; a fraud gets a chargeback dated for later."""
        if not truth["is_fraud"]:
            return
        self.frauds += 1
        if self._rng.random() >= self.report_rate:
            self.unreported += 1
            return
        happened = event_time(message)
        known_at = happened + timedelta(days=float(self._rng.uniform(self.min_days, self.max_days)))
        record = {
            "schema_version": CHARGEBACK_SCHEMA_VERSION,
            "transaction_id": message["transaction_id"],
            "card_id": message["card_id"],
            "event_time": happened.isoformat(),
            "known_at": known_at.isoformat(),
            "reason": "fraud",
        }
        heapq.heappush(self._pending, (known_at, self._sequence, record))
        self._sequence += 1

    def post_due(self, now: datetime, force: bool = False) -> int:
        """Write every chargeback known by `now` (the simulated clock).

        At most one write per write_every_seconds of real time (one small file
        a minute, not one per transaction), unless force. Returns how many were
        written.
        """
        too_soon = (
            self._last_write is not None
            and self._clock() - self._last_write < self.write_every_seconds
        )
        if too_soon and not force:
            return 0
        self._last_write = self._clock()
        due = []
        while self._pending and self._pending[0][0] <= now:
            due.append(heapq.heappop(self._pending)[2])
        if due:
            self._write(due)
            self.posted += len(due)
        return len(due)


class S3ChargebackWriter:
    """Writes one batch of chargebacks as one JSON-lines file in S3."""

    def __init__(self, s3, bucket: str, prefix: str):
        self.s3 = s3
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.run = uuid.uuid4().hex[:8]  # this simulator run: keys never collide
        self.files = 0

    def key_for(self, records: list[dict]) -> str:
        first = datetime.fromisoformat(records[0]["known_at"])
        self.files += 1
        return f"{self.prefix}/dt={first:%Y-%m-%d}/{first:%H%M%S}-{self.run}-{self.files:05d}.jsonl"

    def __call__(self, records: list[dict]) -> None:
        body = "".join(json.dumps(r) + "\n" for r in records)
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self.key_for(records),
            Body=body.encode(),
            ContentType="application/x-ndjson",
        )


def log_writer(records: list[dict]) -> None:
    """DRY_RUN: log chargebacks instead of writing them."""
    for r in records:
        log.info(
            "chargeback %s (purchase %s, known %s)",
            r["transaction_id"],
            r["event_time"],
            r["known_at"],
        )
