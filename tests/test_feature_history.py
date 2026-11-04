"""tests/test_feature_history.py - checks the history clues, leakage and skew.

What: three kinds of check on the clues worked out from a card's earlier
      purchases:
        1. correctness - a small hand-made sequence of purchases where every
           expected value is worked out by hand
        2. no leakage  - adding LATER transactions never changes the clues of
           earlier ones (a clue that peeked at the future would change)
        3. no skew     - transactions arriving one at a time as JSON, the way
           the live consumer will read them from Kafka, get exactly the same
           clues as the training table
Why:  leakage and training-serving skew are the two classic ways a model looks
      great in testing and fails live. Neither causes an error - so these tests
      are the only thing standing between them and the model.
"""

import json
import math

import numpy as np
import pandas as pd
import pytest

from fraudstream.features import FEATURE_COLUMNS, FeatureBuilder
from fraudstream.generator.entities import make_cardholders, make_merchants
from fraudstream.generator.fraud import add_fraud
from fraudstream.generator.transactions import make_legit_transactions

T0 = pd.Timestamp("2026-09-01 12:00", tz="UTC")


def _purchase(n, card, secs, amount, merchant):
    return {
        "transaction_id": f"T{n}",
        "card_id": card,
        "merchant_id": merchant,
        "timestamp": T0 + pd.Timedelta(seconds=secs),
        "amount": amount,
        "channel": "online",
        "merchant_category": "grocery",
        "merchant_lat": np.nan,
        "merchant_lon": np.nan,
    }


@pytest.fixture(scope="module")
def dataset():
    rng = np.random.default_rng(11)
    cards = make_cardholders(400, rng)
    shops = make_merchants(300, rng)
    tx = add_fraud(make_legit_transactions(cards, shops, rng, days=10), cards, shops, rng, days=10)
    return cards, shops, tx


def test_history_clues_by_hand():
    builder = FeatureBuilder(
        pd.DataFrame({"card_id": [], "home_lat": [], "home_lon": []}),
        pd.DataFrame({"merchant_id": [], "risk": []}),
    )
    sequence = [  # card, seconds after T0, amount, shop
        ("C1", 0, 10.0, "M1"),
        ("C1", 60, 20.0, "M1"),
        ("C2", 90, 99.0, "M1"),  # a different card - must not affect C1
        ("C1", 300, 30.0, "M2"),
        ("C1", 1000, 5.0, "M2"),
        ("C1", 4000, 40.0, "M1"),
    ]
    f = [builder.features_for(_purchase(i, *p)) for i, p in enumerate(sequence)]

    def check(row, count, n10m, n1h, since, ratio, new):
        assert row["card_txn_count"] == count
        assert row["txn_count_10m"] == n10m
        assert row["txn_count_1h"] == n1h
        assert (math.isnan(row["secs_since_last_txn"]) if since is None
                else row["secs_since_last_txn"] == since)  # fmt: skip
        assert (math.isnan(row["amount_vs_card_mean"]) if ratio is None
                else row["amount_vs_card_mean"] == pytest.approx(ratio, abs=1e-4))  # fmt: skip
        assert row["is_new_merchant"] == new

    check(f[0], 0, 0, 0, None, None, 1)  # first ever purchase: no history
    check(f[1], 1, 1, 1, 60, 20 / 10, 0)  # 1 min later, same shop, twice the average
    check(f[2], 0, 0, 0, None, None, 1)  # C2's first purchase
    check(f[3], 2, 2, 2, 240, 30 / 15, 1)  # new shop M2
    check(f[4], 3, 0, 3, 700, 5 / 20, 0)  # 16 min after start: nothing in last 10 min
    check(f[5], 4, 0, 1, 3000, 40 / 16.25, 0)  # only the 1000 s purchase is within the hour


def test_later_transactions_never_change_earlier_clues(dataset):
    """The leakage test: a clue that peeked at the future would change here."""
    cards, shops, tx = dataset
    first_half = tx.iloc[: len(tx) // 2]
    from_full = FeatureBuilder(cards, shops).build(tx).iloc[: len(first_half)]
    from_half = FeatureBuilder(cards, shops).build(first_half)
    pd.testing.assert_frame_equal(from_full.reset_index(drop=True), from_half)


def test_streamed_json_gives_same_clues_as_training_table(dataset):
    """The skew test: training reads a pandas table; live reads JSON from Kafka."""
    cards, shops, tx = dataset
    training = FeatureBuilder(cards, shops).build(tx)

    # What the consumer will receive: one JSON message per transaction, with
    # timestamps as ISO strings and missing values as null.
    messages = [json.dumps(m) for m in json.loads(tx.to_json(orient="records", date_format="iso"))]
    live = FeatureBuilder(cards, shops)
    streamed = pd.DataFrame([live.features_for(json.loads(m)) for m in messages])

    pd.testing.assert_frame_equal(
        training[FEATURE_COLUMNS].astype(float), streamed[FEATURE_COLUMNS].astype(float)
    )


def test_history_clues_separate_card_testing_from_normal(dataset):
    cards, shops, tx = dataset
    feats = (
        FeatureBuilder(cards, shops)
        .build(tx)
        .merge(tx[["transaction_id", "fraud_type"]], on="transaction_id")
    )
    testing = feats[feats["fraud_type"] == "card_testing"]
    normal = feats[feats["fraud_type"].isna()]
    assert testing["txn_count_10m"].median() >= 2  # bursts
    assert normal["txn_count_10m"].median() == 0
    assert testing["is_new_merchant"].mean() > normal["is_new_merchant"].mean()
