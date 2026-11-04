"""fraudstream.generator.fraud - the fraudulent transactions, and mixing them in.

What: add_fraud() picks some cards to be "compromised", creates a short burst of
      fraud on each one in one of three styles, labels those rows is_fraud = 1,
      and mixes them into the normal spending in time order.
Why:  the model learns what fraud looks like from labelled examples. Because we
      create the fraud ourselves, every label is certain - something real data
      never gives you (real labels arrive late, and some fraud is never reported).
Concept: fraud is RARE (about 0.5% of transactions here, similar to real card
      fraud), and it comes in EPISODES: a stolen card is used several times in a
      short window, not once. The three styles:
        card_testing      a burst of tiny online purchases to check the stolen
                          card works, before it's sold or used for something big
        account_takeover  a few large purchases, often far from home, often at
                          odd hours, in easy-to-resell categories
        online_fraud      several card-not-present purchases at high-risk online
                          shops over a day or two
      About 15% of episodes are "stealthy": amounts, hours and shops close to the
      cardholder's normal habits. Those are hard to catch on purpose - real
      fraudsters try to blend in, and a model that only catches the obvious
      cases would look better here than it ever could in reality.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fraudstream.generator.entities import EVERYDAY_CATEGORIES

# Share of fraud episodes of each style.
STYLES = {"card_testing": 0.40, "account_takeover": 0.30, "online_fraud": 0.30}
_STEALTH_SHARE = 0.15
# Average fraud rows per episode, by style (used to hit the target fraud rate).
_ROWS_PER_EPISODE = {"card_testing": 6.8, "account_takeover": 2.0, "online_fraud": 3.5}

_RISKY_ONLINE = ["digital_goods", "gift_cards", "electronics", "entertainment"]
_RESELLABLE = ["electronics", "gift_cards", "travel"]


def add_fraud(
    legit: pd.DataFrame,
    cards: pd.DataFrame,
    merchants: pd.DataFrame,
    rng: np.random.Generator,
    start: str = "2026-09-01",
    days: int = 30,
    fraud_rate: float = 0.005,
) -> pd.DataFrame:
    """Return legit + fraud transactions, sorted by time, with fresh transaction IDs.

    fraud_rate is the target share of ALL transactions that are fraud.
    """
    # How many episodes give roughly fraud_rate of all rows.
    target_rows = fraud_rate * len(legit) / (1 - fraud_rate)
    mean_rows = sum(STYLES[s] * _ROWS_PER_EPISODE[s] for s in STYLES)
    n_episodes = max(1, round(target_rows / mean_rows))

    # Each compromised card has exactly one episode.
    victims = rng.choice(len(cards), size=n_episodes, replace=False)
    styles = rng.choice(list(STYLES), size=n_episodes, p=list(STYLES.values()))
    stealthy = rng.random(n_episodes) < _STEALTH_SHARE

    shops = merchants.reset_index(drop=True)
    period_start = pd.Timestamp(start, tz="UTC")
    period_end = period_start + pd.Timedelta(days=days)

    rows: list[dict] = []
    for victim, style, stealth in zip(victims, styles, stealthy):
        card = cards.iloc[victim]
        make = {
            "card_testing": _card_testing,
            "account_takeover": _account_takeover,
            "online_fraud": _online_fraud,
        }[style]
        episode = make(card, shops, rng, bool(stealth))
        # Episode start: any day of the period, so fraud is spread evenly to the
        # very end. An episode still running when the period ends is cut off
        # there (its later rows are dropped) - just as a real data extract cuts
        # through whatever fraud happens to be in progress.
        t0 = period_start + pd.Timedelta(days=int(rng.integers(0, days)))
        t0 = _set_hour(t0, card, rng, stealth)
        for offset, shop_idx, amount in episode:
            if t0 + offset >= period_end:
                continue
            shop = shops.iloc[shop_idx]
            rows.append(
                {
                    "timestamp": t0 + offset,
                    "card_id": card["card_id"],
                    "merchant_id": shop["merchant_id"],
                    "merchant_category": shop["category"],
                    "channel": "online" if shop["is_online"] else "in_person",
                    "merchant_lat": shop["lat"],
                    "merchant_lon": shop["lon"],
                    "amount": round(max(float(amount), 0.50), 2),
                    "is_fraud": 1,
                    "fraud_type": str(style),
                }
            )

    fraud = pd.DataFrame(rows).astype({"is_fraud": np.int8})
    out = pd.concat([legit.drop(columns="transaction_id"), fraud], ignore_index=True)
    out = out.sort_values(
        ["timestamp", "card_id", "merchant_id", "amount"], kind="stable"
    ).reset_index(drop=True)
    out.insert(0, "transaction_id", [f"T{i:09d}" for i in range(1, len(out) + 1)])
    return out


# -----------------------------------------------------------------------------
# The three styles. Each returns a list of (time offset, shop index, amount)
# relative to the episode's start time.
# -----------------------------------------------------------------------------
def _card_testing(card, shops, rng, stealth):
    """3-10 tiny online purchases a minute or two apart; sometimes a big one after."""
    n = int(rng.integers(3, 11))
    pool = _pool(shops, _RISKY_ONLINE, online=True)
    gaps = np.cumsum(rng.integers(20, 240, size=n))  # 20 s to 4 min apart
    if stealth:  # spread out and a bit larger - looks more like normal browsing
        gaps = np.cumsum(rng.integers(600, 3600, size=n))
    amounts = rng.uniform(0.50, 5.00, size=n)
    episode = [(pd.Timedelta(seconds=int(g)), int(rng.choice(pool)), a)
               for g, a in zip(gaps, amounts)]  # fmt: skip
    if rng.random() < 0.3:  # the card "works" - now cash in
        big = card["typical_amount"] * rng.uniform(5, 15)
        pool_big = _pool(shops, _RESELLABLE, online=True)
        episode.append((pd.Timedelta(seconds=int(gaps[-1] + 600)), int(rng.choice(pool_big)), big))
    return episode


def _account_takeover(card, shops, rng, stealth):
    """1-3 large purchases within a few hours, in person far from home or online."""
    n = int(rng.integers(1, 4))
    if stealth:  # near-normal amounts, everyday shops, home city
        pool = _pool(shops, EVERYDAY_CATEGORIES, online=False, city=card["home_city"])
        amounts = card["typical_amount"] * rng.uniform(1.0, 2.5, size=n)
    else:
        if rng.random() < 0.6:  # in person, in a different city
            pool = _pool(shops, _RESELLABLE, online=False, not_city=card["home_city"])
        else:
            pool = _pool(shops, _RESELLABLE, online=True)
        amounts = card["typical_amount"] * rng.uniform(5, 20, size=n)
    offsets = np.sort(rng.integers(0, 4 * 3600, size=n))
    return [(pd.Timedelta(seconds=int(o)), int(rng.choice(pool)), a)
            for o, a in zip(offsets, amounts)]  # fmt: skip


def _online_fraud(card, shops, rng, stealth):
    """2-5 card-not-present purchases at risky online shops over one or two days."""
    n = int(rng.integers(2, 6))
    if stealth:
        pool = _pool(shops, EVERYDAY_CATEGORIES, online=True)
        amounts = card["typical_amount"] * rng.uniform(0.8, 2.0, size=n)
    else:
        pool = _pool(shops, _RISKY_ONLINE, online=True)
        amounts = card["typical_amount"] * rng.uniform(2, 6, size=n)
    offsets = np.sort(rng.integers(0, 48 * 3600, size=n))
    return [(pd.Timedelta(seconds=int(o)), int(rng.choice(pool)), a)
            for o, a in zip(offsets, amounts)]  # fmt: skip


def _pool(shops, categories, online, city=None, not_city=None):
    """Indexes of shops matching the filters, loosening them if nothing matches."""
    mask = shops["category"].isin(categories) & (shops["is_online"] == online)
    if city is not None:
        mask &= shops["city"] == city
    if not_city is not None:
        mask &= shops["city"] != not_city
    idx = shops.index[mask].to_numpy()
    if len(idx) == 0:  # e.g. no matching shop in a small city
        idx = shops.index[shops["category"].isin(categories)].to_numpy()
    return idx


def _set_hour(t0, card, rng, stealth):
    """Fraud often happens while the cardholder is asleep; stealthy fraud doesn't."""
    if stealth:
        hour = int(rng.integers(card["active_start"], card["active_end"] + 1))
    elif rng.random() < 0.5:
        hour = int(rng.integers(0, 6))  # 00:00-05:59
    else:
        hour = int(rng.integers(0, 24))
    return t0.normalize() + pd.Timedelta(hours=hour, seconds=int(rng.integers(0, 3600)))
