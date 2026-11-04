"""tests/test_fraud.py - checks the fraud and how it's mixed into normal spending.

What: proves fraud is rare (about 0.5%), comes in episodes on compromised
      cards, has the shape of each style (tiny bursts, big out-of-town purchases,
      risky online shops), and that mixing it in keeps the data reproducible,
      in time order and with unique IDs.
Why:  these labels are what the model learns from. If the fraud didn't look
      like the styles we intend, the model would learn the wrong lessons.
"""

import numpy as np
import pandas as pd
import pytest

from fraudstream.generator.entities import make_cardholders, make_merchants
from fraudstream.generator.fraud import STYLES, add_fraud
from fraudstream.generator.transactions import make_legit_transactions

START, DAYS = "2026-09-01", 30


def _dataset(seed=42, n_cards=3000, n_shops=800):
    rng = np.random.default_rng(seed)
    cards = make_cardholders(n_cards, rng)
    shops = make_merchants(n_shops, rng)
    legit = make_legit_transactions(cards, shops, rng, start=START, days=DAYS)
    tx = add_fraud(legit, cards, shops, rng, start=START, days=DAYS)
    return cards, legit, tx


@pytest.fixture(scope="module")
def data():
    return _dataset()


def test_same_seed_gives_identical_dataset():
    pd.testing.assert_frame_equal(
        _dataset(seed=5, n_cards=800)[2], _dataset(seed=5, n_cards=800)[2]
    )


def test_fraud_is_rare_but_present(data):
    _, legit, tx = data
    assert len(tx) > len(legit)
    rate = tx["is_fraud"].mean()
    assert 0.0035 < rate < 0.0065  # about 0.5%


def test_all_three_styles_appear_and_only_on_fraud_rows(data):
    _, _, tx = data
    fraud = tx[tx["is_fraud"] == 1]
    assert set(fraud["fraud_type"]) == set(STYLES)
    assert fraud["fraud_type"].notna().all()
    assert tx.loc[tx["is_fraud"] == 0, "fraud_type"].isna().all()


def test_mixed_dataset_is_ordered_unique_and_inside_the_period(data):
    _, _, tx = data
    assert tx["transaction_id"].is_unique
    assert tx["timestamp"].is_monotonic_increasing
    start = pd.Timestamp(START, tz="UTC")
    assert tx["timestamp"].min() >= start
    assert tx["timestamp"].max() < start + pd.Timedelta(days=DAYS)


def test_each_compromised_card_has_one_style(data):
    _, _, tx = data
    styles_per_card = tx[tx["is_fraud"] == 1].groupby("card_id")["fraud_type"].nunique()
    assert (styles_per_card == 1).all()


def test_card_testing_is_bursts_of_tiny_online_purchases(data):
    _, _, tx = data
    ct = tx[tx["fraud_type"] == "card_testing"]
    tiny = ct[ct["amount"] <= 5.00]
    assert len(tiny) / len(ct) > 0.85  # mostly tiny; a few "cash in" purchases
    assert (tiny["channel"] == "online").all()
    assert ct.groupby("card_id").size().median() >= 3  # bursts, not one-offs


def test_account_takeover_is_mostly_large_and_unusual(data):
    cards, _, tx = data
    ato = tx[tx["fraud_type"] == "account_takeover"].merge(cards, on="card_id")
    ratio = ato["amount"] / ato["typical_amount"]
    assert ratio.median() > 4  # several times the cardholder's normal spend
    night = ato["timestamp"].dt.hour < 6
    assert night.mean() > 0.25  # far more night-time than normal spending


def test_online_fraud_is_card_not_present(data):
    _, _, tx = data
    of = tx[tx["fraud_type"] == "online_fraud"]
    assert (of["channel"] == "online").all()


def test_some_fraud_is_stealthy(data):
    cards, _, tx = data
    fraud = tx[tx["is_fraud"] == 1].merge(cards, on="card_id")
    near_normal = (fraud["amount"] < fraud["typical_amount"] * 2.5) & (fraud["amount"] > 5)
    assert near_normal.mean() > 0.05  # not all fraud is obvious
