"""fraudstream.generator.entities - the imaginary cardholders and shops.

What: make_cardholders() and make_merchants() each return a table (a pandas
      DataFrame) with one row per person or shop and the "habits" that later
      decide what their transactions look like.
Why:  realistic fraud data starts with realistic people. Fraud is mostly
      spotted as a break from someone's normal habits (an unusual amount, place,
      time or kind of shop), so every cardholder needs habits to break.
Concept: randomness with a seed. Every random choice comes from a numpy
      Generator created from one number (the seed). Same seed -> identical
      people and shops, every time, on any machine.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# -----------------------------------------------------------------------------
# Shop categories.
#   amount_factor: how a purchase here compares with the cardholder's usual
#                  spend (coffee is small, travel is large)
#   online_share:  fraction of shops in this category that are online-only
#   risk:          how often this kind of shop is targeted by fraud
#                  (0 = rarely, 1 = often) - e.g. gift cards and electronics
#                  are easy to resell, so fraudsters favour them
# -----------------------------------------------------------------------------
CATEGORIES: dict[str, dict[str, float]] = {
    "grocery":       {"amount_factor": 1.0, "online_share": 0.10, "risk": 0.1},
    "fuel":          {"amount_factor": 1.5, "online_share": 0.00, "risk": 0.2},
    "restaurant":    {"amount_factor": 1.2, "online_share": 0.05, "risk": 0.1},
    "coffee":        {"amount_factor": 0.2, "online_share": 0.00, "risk": 0.1},
    "pharmacy":      {"amount_factor": 0.8, "online_share": 0.20, "risk": 0.1},
    "clothing":      {"amount_factor": 2.0, "online_share": 0.50, "risk": 0.4},
    "entertainment": {"amount_factor": 1.0, "online_share": 0.50, "risk": 0.3},
    "travel":        {"amount_factor": 8.0, "online_share": 0.80, "risk": 0.5},
    "electronics":   {"amount_factor": 5.0, "online_share": 0.60, "risk": 0.8},
    "digital_goods": {"amount_factor": 0.6, "online_share": 1.00, "risk": 0.8},
    "gift_cards":    {"amount_factor": 2.5, "online_share": 0.70, "risk": 0.9},
}  # fmt: skip

# Categories most people buy from regularly; each cardholder's favourites are
# drawn from these. The riskier categories are still visited, just less often.
EVERYDAY_CATEGORIES = ["grocery", "fuel", "restaurant", "coffee", "pharmacy", "clothing",
                       "entertainment"]  # fmt: skip

# -----------------------------------------------------------------------------
# UK cities: (latitude, longitude, share of the population living there).
# People and in-person shops are placed near these, so "distance from home"
# means something: shopping in your own city is normal, 500 km away is not.
# -----------------------------------------------------------------------------
CITIES: dict[str, tuple[float, float, float]] = {
    "London":      (51.507, -0.128, 0.25),
    "Manchester":  (53.480, -2.242, 0.09),
    "Birmingham":  (52.486, -1.890, 0.09),
    "Leeds":       (53.801, -1.549, 0.07),
    "Glasgow":     (55.864, -4.252, 0.07),
    "Liverpool":   (53.408, -2.992, 0.06),
    "Bristol":     (51.454, -2.588, 0.06),
    "Edinburgh":   (55.953, -3.188, 0.06),
    "Nottingham":  (52.954, -1.155, 0.05),
    "Cardiff":     (51.481, -3.179, 0.05),
    "Newcastle":   (54.978, -1.618, 0.05),
    "Southampton": (50.910, -1.404, 0.05),
    "Belfast":     (54.597, -5.930, 0.05),
}  # fmt: skip

# How far (in degrees) people and shops are scattered around a city centre:
# about 10 km, so neighbours are close but not on top of each other.
_SCATTER_DEG = 0.08


def _city_arrays() -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    names = list(CITIES)
    lat = np.array([CITIES[c][0] for c in names])
    lon = np.array([CITIES[c][1] for c in names])
    share = np.array([CITIES[c][2] for c in names])
    return names, lat, lon, share / share.sum()


def _place_near_cities(n: int, rng: np.random.Generator):
    """Pick a city for each of n things (weighted by population) and scatter them."""
    names, lat, lon, share = _city_arrays()
    idx = rng.choice(len(names), size=n, p=share)
    # Longitude degrees are shorter than latitude degrees in the UK, so scatter
    # a little more east-west to keep the spread roughly circular.
    lats = lat[idx] + rng.normal(0, _SCATTER_DEG, n)
    lons = lon[idx] + rng.normal(0, _SCATTER_DEG * 1.6, n)
    return np.array(names)[idx], lats, lons


def make_cardholders(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Create n imaginary cardholders, each with their own spending habits.

    Columns:
      card_id          C000001, C000002, ...
      home_city        city they live in (lat/lon below are near it)
      home_lat/lon     where they live
      typical_amount   their usual purchase size in GBP. Log-normal: most people
                       spend modestly, a few spend a lot - like real incomes.
      daily_txns       how many purchases they make on an average day
      active_start/end the hours they usually shop between (e.g. 7 to 22)
      online_share     fraction of their purchases made online
      fav_categories   their three everyday shop types, most-used first
    """
    city, lat, lon = _place_near_cities(n, rng)

    # Median usual purchase about GBP 25; sigma 0.6 gives a realistic spread
    # (roughly GBP 8 to GBP 80 for most people, a few beyond).
    typical_amount = np.round(rng.lognormal(mean=np.log(25), sigma=0.6, size=n), 2)

    # Median about 1.5 purchases a day; some people far more, some far fewer.
    daily_txns = np.round(rng.lognormal(mean=np.log(1.5), sigma=0.5, size=n), 2)

    # Most people shop from early morning to late evening; 5% are night owls
    # who shop much later. Hour 0-23.
    active_start = rng.integers(6, 10, size=n)  # 6, 7, 8 or 9
    active_end = rng.integers(20, 24, size=n)  # 20, 21, 22 or 23
    night_owl = rng.random(n) < 0.05
    active_end = np.where(night_owl, 23, active_end)
    active_start = np.where(night_owl, rng.integers(10, 14, size=n), active_start)

    # Beta(2, 6) averages 25% online, with some people much more and some less.
    online_share = np.round(rng.beta(2, 6, size=n), 3)

    fav_categories = [
        list(rng.choice(EVERYDAY_CATEGORIES, size=3, replace=False)) for _ in range(n)
    ]

    return pd.DataFrame(
        {
            "card_id": [f"C{i:06d}" for i in range(1, n + 1)],
            "home_city": city,
            "home_lat": np.round(lat, 4),
            "home_lon": np.round(lon, 4),
            "typical_amount": typical_amount,
            "daily_txns": daily_txns,
            "active_start": active_start,
            "active_end": active_end,
            "online_share": online_share,
            "fav_categories": fav_categories,
        }
    )


def make_merchants(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Create n imaginary shops.

    Columns:
      merchant_id   M00001, M00002, ...
      category      kind of shop (see CATEGORIES)
      is_online     online-only shop (no physical location)
      city/lat/lon  where an in-person shop is; empty for online shops
      risk          how often this kind of shop is targeted by fraud (0-1),
                    with a little per-shop variation around its category
    """
    names = list(CATEGORIES)
    # Everyday categories are more common than, say, gift-card sellers.
    weights = np.array([3.0 if c in EVERYDAY_CATEGORIES else 1.0 for c in names])
    category = rng.choice(names, size=n, p=weights / weights.sum())

    online_share = np.array([CATEGORIES[c]["online_share"] for c in category])
    is_online = rng.random(n) < online_share

    city, lat, lon = _place_near_cities(n, rng)
    # Online shops have no physical location - "distance from home" won't
    # apply to them, which the features will handle.
    city = np.where(is_online, None, city)
    lat = np.where(is_online, np.nan, np.round(lat, 4))
    lon = np.where(is_online, np.nan, np.round(lon, 4))

    base_risk = np.array([CATEGORIES[c]["risk"] for c in category])
    risk = np.clip(np.round(base_risk + rng.normal(0, 0.05, n), 3), 0.0, 1.0)

    return pd.DataFrame(
        {
            "merchant_id": [f"M{i:05d}" for i in range(1, n + 1)],
            "category": category,
            "is_online": is_online,
            "city": city,
            "lat": lat,
            "lon": lon,
            "risk": risk,
        }
    )
