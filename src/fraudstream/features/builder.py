"""fraudstream.features.builder - the one place features are calculated.

What: FeatureBuilder.features_for(tx) returns the clues for one transaction and
      then remembers it in that card's history; FeatureBuilder.build(df) does
      the same for a whole table, row by row in time order, by calling
      features_for - never a separate shortcut.
Why:  training and live scoring must calculate every clue identically. Having a
      single function that both use makes that true by construction.
Concept: the FEATURE CONTRACT. FEATURE_COLUMNS (names and order) and
      FEATURE_SCHEMA_VERSION are stored with every trained model. The live
      consumer refuses to score with a model whose schema version differs from
      its own, so a model can never be fed clues it wasn't trained on.

Two kinds of clue:
  - simple: read straight off this transaction plus reference data the bank
    has (each card's home address, each shop's risk rating)
  - history: worked out from the card's EARLIER transactions only - e.g. how
    many purchases in the last 10 minutes. A transaction is added to the
    history only AFTER its own clues are calculated, so a clue can never
    include the transaction itself or anything after it (no leakage).
It deliberately does NOT use the generator's private settings (e.g. a person's
"true" typical spend) - a real bank never sees those and has to learn them
from the card's history.

Trade-off: history lives in memory. If the live consumer restarts, it starts
with empty history, so history clues are "cold" for a while (amount_vs_card_mean
missing, counts too low). card_txn_count tells the model how much history it
had. A production system would rebuild history from the event log or keep it in
an external store (e.g. DynamoDB or a feature store).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Bump this whenever a feature is added, removed or calculated differently.
FEATURE_SCHEMA_VERSION = "1"

# Fixed category codes. The model needs numbers, not words, so each shop type
# gets a number. The list is written here, not learned from the data, so the
# codes can never shift between training and live scoring. New categories go
# at the END (and need a schema version bump) - never reorder this list.
CATEGORY_CODES: dict[str, int] = {
    name: code
    for code, name in enumerate(
        [
            "grocery",
            "fuel",
            "restaurant",
            "coffee",
            "pharmacy",
            "clothing",
            "entertainment",
            "travel",
            "electronics",
            "digital_goods",
            "gift_cards",
        ]
    )
}
UNKNOWN_CATEGORY = -1

# The clues the model sees, in this exact order.
FEATURE_COLUMNS = [
    # --- simple clues (this transaction + reference data)
    "amount",  # GBP, as paid
    "log_amount",  # log(1 + amount): squeezes GBP 1 vs GBP 1,000 onto a gentler scale
    "hour_of_day",  # 0-23, UTC
    "is_night",  # 1 if 00:00-05:59, when most people are asleep
    "is_online",  # 1 if card-not-present
    "category_code",  # shop type as a number (see CATEGORY_CODES)
    "merchant_risk",  # how often this kind of shop is targeted (0-1)
    "distance_from_home_km",  # home to shop; empty for online purchases
    # --- history clues (this card's EARLIER transactions only)
    "card_txn_count",  # how many earlier purchases we've seen on this card
    "txn_count_10m",  # earlier purchases on this card in the last 10 minutes
    "txn_count_1h",  # ... in the last hour
    "secs_since_last_txn",  # seconds since this card's previous purchase; empty if none
    "amount_vs_card_mean",  # this amount / the card's average so far; empty if none
    "is_new_merchant",  # 1 if this card has never paid this shop before
]

_EARTH_RADIUS_KM = 6371.0
_TEN_MINUTES = 600
_ONE_HOUR = 3600


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two points on Earth (the 'as the crow flies' distance)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class _CardHistory:
    """What we remember about one card's earlier transactions."""

    count: int = 0
    amount_sum: float = 0.0
    last_time: float | None = None  # seconds since 1970
    recent_times: deque = field(default_factory=deque)  # purchase times in the last hour
    merchants: set = field(default_factory=set)


class FeatureBuilder:
    """Calculates features for transactions, one at a time or for a whole table.

    cards:     table with card_id, home_lat, home_lon (the bank's customer records)
    merchants: table with merchant_id, risk (the bank's merchant records)

    A builder remembers every transaction it has seen, so transactions must be
    given to it in time order - just as they arrive from the stream.
    """

    def __init__(self, cards: pd.DataFrame, merchants: pd.DataFrame):
        self._home = {
            cid: (lat, lon)
            for cid, lat, lon in zip(cards["card_id"], cards["home_lat"], cards["home_lon"])
        }
        self._risk = dict(zip(merchants["merchant_id"], merchants["risk"]))
        self._history: dict[str, _CardHistory] = {}

    def features_for(self, tx: Mapping) -> dict:
        """Clues for ONE transaction, then add it to its card's history.

        tx needs the raw fields: card_id, merchant_id, timestamp, amount, channel,
        merchant_category, merchant_lat, merchant_lon. They may arrive as Python
        or pandas values, or as JSON-style values (ISO timestamp strings, None for
        missing) - the clues come out the same either way.

        Unknown cards or shops (possible live) give empty values, not an error.
        """
        card_id = tx["card_id"]
        amount = float(tx["amount"])
        ts = pd.Timestamp(tx["timestamp"])
        now = ts.timestamp()
        online = tx["channel"] == "online"

        distance = math.nan
        home = self._home.get(card_id)
        lat, lon = tx["merchant_lat"], tx["merchant_lon"]
        if not online and home is not None and not _missing(lat) and not _missing(lon):
            distance = round(haversine_km(home[0], home[1], float(lat), float(lon)), 3)

        # --- history clues, from EARLIER transactions only
        h = self._history.setdefault(card_id, _CardHistory())
        while h.recent_times and h.recent_times[0] <= now - _ONE_HOUR:
            h.recent_times.popleft()  # forget purchases more than an hour old
        last_10m = sum(1 for t in h.recent_times if t > now - _TEN_MINUTES)

        features = {
            "amount": amount,
            "log_amount": math.log1p(amount),
            "hour_of_day": ts.hour,
            "is_night": int(ts.hour < 6),
            "is_online": int(online),
            "category_code": CATEGORY_CODES.get(tx["merchant_category"], UNKNOWN_CATEGORY),
            "merchant_risk": float(self._risk.get(tx["merchant_id"], math.nan)),
            "distance_from_home_km": distance,
            "card_txn_count": h.count,
            "txn_count_10m": last_10m,
            "txn_count_1h": len(h.recent_times),
            "secs_since_last_txn": math.nan if h.last_time is None else now - h.last_time,
            "amount_vs_card_mean": (
                math.nan if h.count == 0 else round(amount / (h.amount_sum / h.count), 4)
            ),
            "is_new_merchant": int(tx["merchant_id"] not in h.merchants),
        }

        # --- only now remember this transaction
        h.count += 1
        h.amount_sum += amount
        h.last_time = now
        h.recent_times.append(now)
        h.merchants.add(tx["merchant_id"])
        return features

    def build(self, transactions: pd.DataFrame) -> pd.DataFrame:
        """Clues for a whole table, in the same row order, keyed by transaction_id.

        Works by calling features_for() on every row in time order - exactly
        what the live consumer will do - so the two can never disagree.
        """
        ordered = transactions.sort_values("timestamp", kind="stable")
        rows: Iterable[Mapping] = ordered.to_dict("records")
        feats = pd.DataFrame([self.features_for(r) for r in rows], columns=FEATURE_COLUMNS)
        feats.insert(0, "transaction_id", ordered["transaction_id"].to_numpy())
        # Back to the caller's row order.
        feats = feats.set_index("transaction_id").loc[transactions["transaction_id"]]
        return feats.reset_index()


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and np.isnan(value))
