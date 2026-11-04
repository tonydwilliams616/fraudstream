"""tests/test_entities.py - checks the imaginary cardholders and shops.

What: proves the same seed gives identical people and shops, and that their
      habits fall in sensible ranges (amounts, hours, online share, locations).
Why:  everything later - transactions, fraud, features, the model - is built on
      these tables. If they're wrong or not reproducible, everything after is too.
"""

import numpy as np
import pandas as pd

from fraudstream.generator.entities import (
    CATEGORIES,
    EVERYDAY_CATEGORIES,
    make_cardholders,
    make_merchants,
)


def _cards(seed=42, n=2000):
    return make_cardholders(n, np.random.default_rng(seed))


def _shops(seed=42, n=1000):
    return make_merchants(n, np.random.default_rng(seed))


def test_same_seed_gives_identical_tables():
    pd.testing.assert_frame_equal(_cards(seed=7), _cards(seed=7))
    pd.testing.assert_frame_equal(_shops(seed=7), _shops(seed=7))


def test_different_seed_gives_different_people():
    assert not _cards(seed=1)["typical_amount"].equals(_cards(seed=2)["typical_amount"])


def test_cardholder_ids_are_unique_and_count_is_right():
    cards = _cards(n=2000)
    assert len(cards) == 2000
    assert cards["card_id"].is_unique


def test_cardholder_habits_are_sensible():
    cards = _cards()
    # Usual spend: positive, median near GBP 25
    assert (cards["typical_amount"] > 0).all()
    assert 20 < cards["typical_amount"].median() < 30
    # Shopping hours: start before end, all valid hours
    assert (cards["active_start"] < cards["active_end"]).all()
    assert cards["active_end"].between(0, 23).all()
    # Online share: a fraction, averaging about a quarter
    assert cards["online_share"].between(0, 1).all()
    assert 0.20 < cards["online_share"].mean() < 0.30
    # Three different everyday favourites each
    for favs in cards["fav_categories"]:
        assert len(set(favs)) == 3
        assert set(favs) <= set(EVERYDAY_CATEGORIES)


def test_cardholders_live_in_the_uk():
    cards = _cards()
    assert cards["home_lat"].between(49.5, 59).all()
    assert cards["home_lon"].between(-8, 2).all()


def test_shop_categories_and_online_flags():
    shops = _shops()
    assert shops["merchant_id"].is_unique
    assert set(shops["category"]) <= set(CATEGORIES)
    # Online shops have no location; in-person shops always do
    online = shops[shops["is_online"]]
    in_person = shops[~shops["is_online"]]
    assert online["lat"].isna().all() and online["city"].isna().all()
    assert in_person["lat"].notna().all() and in_person["city"].notna().all()
    # Digital goods are always online; fuel never is
    assert shops.loc[shops["category"] == "digital_goods", "is_online"].all()
    assert not shops.loc[shops["category"] == "fuel", "is_online"].any()


def test_shop_risk_is_a_fraction_and_follows_category():
    shops = _shops()
    assert shops["risk"].between(0, 1).all()
    mean_risk = shops.groupby("category")["risk"].mean()
    assert mean_risk["gift_cards"] > mean_risk["grocery"]
