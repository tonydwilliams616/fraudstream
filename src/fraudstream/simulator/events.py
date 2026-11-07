"""fraudstream.simulator.events - turns generated transactions into a stream of messages.

What: stream_transactions() generates transactions block by block (7
      simulated days at a time, then the next 7, forever) for the dataset's
      own cardholders and shops, and yields them as messages in time order.
      to_message() is the exact shape put on the `transactions` topic.
      Pacer sends them at a steady rate (e.g. 10 a second).
Why:  same people, same shops, same habits as the training data - only new
      days and a new random seed, i.e. the "future" the model has never seen.
Concepts:
  - EVENT TIME vs PROCESSING TIME. Each message carries the moment the
    purchase happened (event time). The simulator sends them faster than real
    life - a simulated day in about half an hour at 10 a second - so the
    moment a message ARRIVES (processing time) is not the moment it HAPPENED.
    The consumer works out every clue from event time, so "purchases on this
    card in the last 10 minutes" means 10 simulated minutes, exactly as in
    training, however fast the stream runs.
  - NO LABELS ON THE WIRE. A bank doesn't know a purchase is fraud when it
    happens - it finds out days later (chargebacks). So messages never carry
    is_fraud or fraud_type; sending them would let the live system "cheat".
    The answers for these transactions arrive later as a separate feed
    (Phase 4, delayed labels).
  - Hour of day matters to the model, so simulated days start at midnight
    UTC on real calendar dates and are never shifted to "now".
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator

import numpy as np
import pandas as pd

from fraudstream.generator.fraud import add_fraud
from fraudstream.generator.transactions import make_legit_transactions

MESSAGE_SCHEMA_VERSION = "1"  # bump if fields are added, removed or change meaning

# Exactly what goes on the wire - the raw fields FeatureBuilder needs.
MESSAGE_FIELDS = [
    "transaction_id",
    "timestamp",  # event time, ISO 8601 UTC
    "card_id",
    "merchant_id",
    "merchant_category",
    "channel",
    "merchant_lat",
    "merchant_lon",
    "amount",
]


def to_message(row) -> dict:
    """One transaction as a JSON-ready message: no labels, timestamps as ISO text, NaN as null."""
    msg = {"schema_version": MESSAGE_SCHEMA_VERSION}
    for field in MESSAGE_FIELDS:
        value = row[field]
        if field == "timestamp":
            value = pd.Timestamp(value).isoformat()
        elif isinstance(value, float | np.floating):
            value = None if math.isnan(value) else float(value)
        msg[field] = value
    return msg


def stream_transactions(
    cards: pd.DataFrame,
    merchants: pd.DataFrame,
    start: str,
    seed: int,
    block_days: int = 7,
    fraud_rate: float = 0.005,
) -> Iterator[dict]:
    """Messages in event-time order, block after block, never ending.

    Each block is generated in one go, so multi-day habits (trips away, fraud
    episodes spanning days) behave as in training. Block k uses seed + k, so
    the whole stream is reproducible from (start, seed).
    """
    block_start = pd.Timestamp(start, tz="UTC").normalize()
    block = 0
    while True:
        rng = np.random.default_rng(seed + block)
        day = block_start.strftime("%Y-%m-%d")
        legit = make_legit_transactions(cards, merchants, rng, start=day, days=block_days)
        tx = add_fraud(
            legit, cards, merchants, rng, start=day, days=block_days, fraud_rate=fraud_rate
        )
        # The generator numbers rows from T000000001 in every block; prefix the
        # block's start date so IDs stay unique across the whole stream.
        tx["transaction_id"] = f"S{block_start:%Y%m%d}-" + tx["transaction_id"]
        for row in tx.to_dict("records"):
            yield to_message(row)
        block_start += pd.Timedelta(days=block_days)
        block += 1


class Pacer:
    """Lets one message through every 1/rate seconds.

    It keeps a schedule rather than sleeping a fixed amount after each send, so
    time spent sending doesn't slow the stream down. If it falls more than a
    second behind (e.g. Kafka was briefly slow) it starts a fresh schedule
    instead of bursting to catch up.
    """

    def __init__(
        self,
        rate: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next is None or now - self._next > 1.0:
            self._next = now
        if self._next > now:
            self._sleep(self._next - now)
        self._next += self.interval
