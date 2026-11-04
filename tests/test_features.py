"""tests/test_features.py - checks the clues (features) worked out for each transaction.

What: checks each feature against values worked out by hand (e.g. London to
      Manchester is about 262 km), that unknown cards/shops don't crash, and
      that the feature contract (names, order, category codes) stays fixed.
      History clues (counts, averages) are tested in test_feature_history.py.
Why:  a wrong feature doesn't crash anything - the model just quietly makes
      worse decisions. Tests are the only way to catch it.
"""

import math

import numpy as np
import pandas as pd
import pytest

from fraudstream.features import FEATURE_COLUMNS, FeatureBuilder
from fraudstream.features.builder import CATEGORY_CODES, haversine_km
from fraudstream.generator.entities import CATEGORIES

LONDON = (51.507, -0.128)
MANCHESTER = (53.480, -2.242)


@pytest.fixture(scope="module")
def builder():
    cards = pd.DataFrame({"card_id": ["C1"], "home_lat": [LONDON[0]], "home_lon": [LONDON[1]]})
    shops = pd.DataFrame({"merchant_id": ["M1", "M2"], "risk": [0.1, 0.9]})
    return FeatureBuilder(cards, shops)


def _tx(**overrides):
    tx = {
        "transaction_id": "T1",
        "card_id": "C1",
        "merchant_id": "M1",
        "timestamp": pd.Timestamp("2026-09-01 14:30", tz="UTC"),
        "amount": 25.0,
        "channel": "in_person",
        "merchant_category": "grocery",
        "merchant_lat": LONDON[0],
        "merchant_lon": LONDON[1],
    }
    tx.update(overrides)
    return tx


def test_haversine_matches_known_distance():
    assert haversine_km(*LONDON, *MANCHESTER) == pytest.approx(262, abs=3)
    assert haversine_km(*LONDON, *LONDON) == 0


def test_features_come_out_in_contract_order(builder):
    assert list(builder.features_for(_tx())) == FEATURE_COLUMNS


def test_simple_features(builder):
    f = builder.features_for(_tx())
    assert f["amount"] == 25.0
    assert f["log_amount"] == pytest.approx(math.log(26))
    assert f["hour_of_day"] == 14 and f["is_night"] == 0
    assert f["is_online"] == 0
    assert f["category_code"] == CATEGORY_CODES["grocery"]
    assert f["merchant_risk"] == 0.1
    assert f["distance_from_home_km"] == 0


def test_far_away_purchase_at_night(builder):
    f = builder.features_for(
        _tx(
            timestamp=pd.Timestamp("2026-09-01 03:10", tz="UTC"),
            merchant_lat=MANCHESTER[0],
            merchant_lon=MANCHESTER[1],
            merchant_id="M2",
        )
    )
    assert f["is_night"] == 1
    assert f["distance_from_home_km"] == pytest.approx(262, abs=3)
    assert f["merchant_risk"] == 0.9


def test_online_purchases_have_no_distance(builder):
    f = builder.features_for(_tx(channel="online", merchant_lat=np.nan, merchant_lon=np.nan))
    assert f["is_online"] == 1
    assert math.isnan(f["distance_from_home_km"])


def test_unknown_card_shop_or_category_does_not_crash(builder):
    f = builder.features_for(_tx(card_id="NEW", merchant_id="NEW", merchant_category="pets"))
    assert math.isnan(f["distance_from_home_km"])
    assert math.isnan(f["merchant_risk"])
    assert f["category_code"] == -1


def test_category_codes_are_fixed():
    # These numbers are part of every trained model. Changing them silently
    # would make old models misread new data - so this test pins them.
    assert CATEGORY_CODES["grocery"] == 0
    assert CATEGORY_CODES["gift_cards"] == 10
    assert set(CATEGORY_CODES) == set(CATEGORIES)
