"""tests/test_transactions.py - checks the normal (legitimate) spending.

What: proves the transactions are reproducible, in time order, inside the
      chosen 30 days, and that they follow each person's habits most of the time
      (shopping hours, home city, favourite categories) - but not always.
Why:  the model can only learn "fraud looks different" if normal spending is
      realistic. These tests pin down what "realistic" means, so a later change
      can't quietly break it.
"""

import numpy as np
import pandas as pd
import pytest

from fraudstream.generator.entities import make_cardholders, make_merchants
from fraudstream.generator.transactions import make_legit_transactions

START, DAYS = "2026-09-01", 30


def _world(seed=42, n_cards=500, n_shops=400):
    rng = np.random.default_rng(seed)
    cards = make_cardholders(n_cards, rng)
    shops = make_merchants(n_shops, rng)
    tx = make_legit_transactions(cards, shops, rng, start=START, days=DAYS)
    return cards, shops, tx


@pytest.fixture(scope="module")
def world():
    return _world()


def test_same_seed_gives_identical_transactions():
    pd.testing.assert_frame_equal(_world(seed=3)[2], _world(seed=3)[2])


def test_columns_ids_and_time_order(world):
    _, _, tx = world
    assert list(tx.columns) == [
        "transaction_id", "timestamp", "card_id", "merchant_id", "merchant_category",
        "channel", "merchant_lat", "merchant_lon", "amount", "is_fraud", "fraud_type",
    ]  # fmt: skip
    assert tx["transaction_id"].is_unique
    assert tx["timestamp"].is_monotonic_increasing
    assert (tx["is_fraud"] == 0).all() and tx["fraud_type"].isna().all()


def test_all_purchases_fall_inside_the_period(world):
    _, _, tx = world
    start = pd.Timestamp(START, tz="UTC")
    assert tx["timestamp"].min() >= start
    assert tx["timestamp"].max() < start + pd.Timedelta(days=DAYS)


def test_volume_matches_daily_rates(world):
    cards, _, tx = world
    expected = (cards["daily_txns"] * DAYS).sum()
    assert abs(len(tx) - expected) / expected < 0.05


def test_amounts_are_positive_and_vary(world):
    _, _, tx = world
    assert (tx["amount"] >= 0.50).all()
    # Coffee is cheap, travel is expensive
    by_cat = tx.groupby("merchant_category")["amount"].median()
    assert by_cat["coffee"] < by_cat["grocery"] < by_cat["travel"]


def test_most_purchases_follow_habits_but_not_all(world):
    cards, _, tx = world
    t = tx.merge(cards, on="card_id")
    hour = t["timestamp"].dt.hour
    in_hours = (hour >= t["active_start"]) & (hour <= t["active_end"])
    assert 0.95 < in_hours.mean() < 1.0  # mostly in usual hours, a few odd ones

    favourite = [c in f for c, f in zip(t["merchant_category"], t["fav_categories"])]
    assert 0.75 < np.mean(favourite) < 0.95  # mostly favourites, not only


def test_in_person_shopping_is_mostly_near_home(world):
    cards, shops, tx = world
    t = tx[tx["channel"] == "in_person"].merge(cards[["card_id", "home_city"]], on="card_id")
    t = t.merge(shops[["merchant_id", "city"]], on="merchant_id")
    at_home = (t["city"] == t["home_city"]).mean()
    assert 0.80 < at_home < 0.98  # mostly home; trips and day trips elsewhere


def test_online_purchases_have_no_shop_location(world):
    _, _, tx = world
    online = tx[tx["channel"] == "online"]
    assert online["merchant_lat"].isna().all()
    assert tx.loc[tx["channel"] == "in_person", "merchant_lat"].notna().all()
    assert 0.15 < (tx["channel"] == "online").mean() < 0.40
