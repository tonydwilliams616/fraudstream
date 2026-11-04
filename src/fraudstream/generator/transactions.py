"""fraudstream.generator.transactions - normal (legitimate) card spending.

What: make_legit_transactions() turns the cardholders' habits into weeks of
      everyday purchases: who bought, where, when, how much and whether online.
Why:  fraud is spotted as a break from normal behaviour, so normal behaviour
      has to look real first. It also has to be a little messy - people travel,
      shop at odd hours now and then, and make the occasional big purchase - or
      the model would learn "anything unusual is fraud", which isn't true.
Concept: every transaction carries only RAW facts - what a card network would
      actually see at the moment of purchase. Clues such as "distance from home"
      are worked out later by the feature code, never stored here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fraudstream.generator.entities import CATEGORIES, CITIES

# How often people break their own habits (the "messiness").
_FAVOURITE_SHARE = 0.80  # 80% of purchases are in someone's 3 favourite categories
_FAVOURITE_WEIGHTS = np.array([0.5, 0.3, 0.2])  # most-used favourite first
_OFF_HOURS_SHARE = 0.03  # 3% of purchases outside their usual shopping hours
_AWAY_FROM_HOME_SHARE = 0.05  # 5% of in-person purchases in another city (a day trip)
_TRIP_SHARE = 0.08  # 8% of people take a 2-5 day trip during the period
_AMOUNT_SPREAD = 0.5  # how much one purchase varies around the person's usual spend


def make_legit_transactions(
    cards: pd.DataFrame,
    merchants: pd.DataFrame,
    rng: np.random.Generator,
    start: str = "2026-09-01",
    days: int = 30,
) -> pd.DataFrame:
    """Generate `days` days of normal spending for every cardholder.

    Returns one row per transaction, sorted by time, with columns:
      transaction_id     T000000001, ... in time order
      timestamp          when it happened (UTC)
      card_id            who paid
      merchant_id        where
      merchant_category  kind of shop
      channel            "online" or "in_person"
      merchant_lat/lon   the shop's location (empty for online shops)
      amount             GBP
      is_fraud           0 here - the fraud generator adds the 1s
      fraud_type         empty here - e.g. "card_testing" for fraud rows
    """
    n_cards = len(cards)
    city_names = list(CITIES)

    # --- How many purchases each person makes: their daily rate x days, with
    # Poisson randomness (some weeks busier than others).
    counts = rng.poisson(cards["daily_txns"].to_numpy() * days)
    card_idx = np.repeat(np.arange(n_cards), counts)
    n = len(card_idx)

    # --- When: a random day, then an hour inside their usual shopping hours
    # (or, 3% of the time, any hour at all).
    day = rng.integers(0, days, size=n)
    start_h = cards["active_start"].to_numpy()[card_idx]
    end_h = cards["active_end"].to_numpy()[card_idx]
    hour = rng.integers(start_h, end_h + 1)
    off_hours = rng.random(n) < _OFF_HOURS_SHARE
    hour = np.where(off_hours, rng.integers(0, 24, size=n), hour)
    seconds = rng.integers(0, 3600, size=n)
    timestamp = (
        pd.Timestamp(start, tz="UTC")
        + pd.to_timedelta(day, unit="D")
        + pd.to_timedelta(hour, unit="h")
        + pd.to_timedelta(seconds, unit="s")
    )

    # --- What kind of shop: usually a favourite, sometimes anything.
    favs = np.array(cards["fav_categories"].tolist())[card_idx]  # shape (n, 3)
    fav_pick = rng.choice(3, size=n, p=_FAVOURITE_WEIGHTS)
    category = favs[np.arange(n), fav_pick]
    any_category = rng.choice(list(CATEGORIES), size=n)
    category = np.where(rng.random(n) < _FAVOURITE_SHARE, category, any_category)

    # --- Online or in person, from each person's online habit.
    wants_online = rng.random(n) < cards["online_share"].to_numpy()[card_idx]

    # --- Which city an in-person purchase happens in: usually home, sometimes
    # elsewhere (a day trip), and during a longer trip, the trip city.
    shop_city = cards["home_city"].to_numpy()[card_idx].astype(object)
    away = rng.random(n) < _AWAY_FROM_HOME_SHARE
    shop_city = np.where(away, rng.choice(city_names, size=n), shop_city)

    on_trip = rng.random(n_cards) < _TRIP_SHARE
    trip_city = rng.choice(city_names, size=n_cards)
    trip_start = rng.integers(0, days, size=n_cards)
    trip_len = rng.integers(2, 6, size=n_cards)  # 2-5 days
    in_trip = (
        on_trip[card_idx]
        & (day >= trip_start[card_idx])
        & (day < trip_start[card_idx] + trip_len[card_idx])
    )
    shop_city = np.where(in_trip, trip_city[card_idx], shop_city)

    # --- Pick an actual shop that matches (category, online?, city).
    merchant_idx = _pick_merchants(merchants, category, wants_online, shop_city, rng)
    m = merchants.iloc[merchant_idx].reset_index(drop=True)

    # --- How much: the person's usual spend x what's normal for this kind of
    # shop x some randomness (log-normal, so mostly near usual, now and then big).
    typical = cards["typical_amount"].to_numpy()[card_idx]
    factor = np.array([CATEGORIES[c]["amount_factor"] for c in m["category"]])
    amount = typical * factor * rng.lognormal(0, _AMOUNT_SPREAD, size=n)
    amount = np.round(np.maximum(amount, 0.50), 2)

    tx = pd.DataFrame(
        {
            "timestamp": timestamp,
            "card_id": cards["card_id"].to_numpy()[card_idx],
            "merchant_id": m["merchant_id"].to_numpy(),
            "merchant_category": m["category"].to_numpy(),
            "channel": np.where(m["is_online"].to_numpy(), "online", "in_person"),
            "merchant_lat": m["lat"].to_numpy(),
            "merchant_lon": m["lon"].to_numpy(),
            "amount": amount,
            "is_fraud": np.zeros(n, dtype=np.int8),
            "fraud_type": pd.Series([None] * n, dtype=object),
        }
    )
    # Time order, with ties broken by card so the order is fully reproducible.
    tx = tx.sort_values(["timestamp", "card_id"], kind="stable").reset_index(drop=True)
    tx.insert(0, "transaction_id", [f"T{i:09d}" for i in range(1, len(tx) + 1)])
    return tx


def _pick_merchants(
    merchants: pd.DataFrame,
    category: np.ndarray,
    wants_online: np.ndarray,
    shop_city: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """For each purchase, pick a shop of the right category, online or in the right city.

    Falls back sensibly when no exact match exists: there are no online petrol
    stations (so in person instead), no physical digital-goods shops (so online
    instead), and a small city may have no shop of a rare category (so any city).
    """
    shops = merchants.reset_index(drop=True)
    online_pool = {c: g.index.to_numpy() for c, g in shops[shops["is_online"]].groupby("category")}
    in_person = shops[~shops["is_online"]]
    city_pool = {k: g.index.to_numpy() for k, g in in_person.groupby(["category", "city"])}
    any_city_pool = {c: g.index.to_numpy() for c, g in in_person.groupby("category")}

    out = np.empty(len(category), dtype=np.int64)
    keys = pd.DataFrame({"cat": category, "online": wants_online, "city": shop_city})
    for (cat, online, city), rows in keys.groupby(["cat", "online", "city"]).indices.items():
        if online:
            pool = online_pool.get(cat)
            if pool is None:  # e.g. fuel: nobody sells it online
                pool = city_pool.get((cat, city), any_city_pool.get(cat))
        else:
            pool = city_pool.get((cat, city), any_city_pool.get(cat))
            if pool is None:  # e.g. digital goods: only sold online
                pool = online_pool[cat]
        out[rows] = rng.choice(pool, size=len(rows))
    return out
