"""fraudstream.generator.dataset - builds a whole dataset from one seed, and fingerprints it.

What: generate_dataset(params) runs every generator step (cardholders, shops,
      normal spending, fraud) in a fixed order from ONE random seed and returns
      the three tables. fingerprint(table) returns a SHA-256 hash of a table's
      contents.
Why:  a model is only reproducible if its training data is. The dataset script
      (step 7) records the parameters and the fingerprints in a manifest, so
      anyone can rebuild the data from the seed and PROVE it's byte-for-byte the
      same data the model was trained on - or spot that something changed.
Concept: a FINGERPRINT (hash) is a short code worked out from every value in
      the table. Change a single penny anywhere and the code is completely
      different; same data always gives the same code. Comparing two 64-character
      codes is much easier than comparing 500,000 rows.
Note: the steps share one random number generator, so their ORDER matters -
      making the shops before the cardholders would give different data from the
      same seed. That's why there is one function that always runs them in the
      same order.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fraudstream.generator.entities import make_cardholders, make_merchants
from fraudstream.generator.fraud import add_fraud
from fraudstream.generator.transactions import make_legit_transactions


@dataclass(frozen=True)
class DatasetParams:
    """Everything needed to rebuild a dataset exactly. Stored in the manifest."""

    seed: int  # no default on purpose: every dataset names its seed
    n_cards: int = 10_000
    n_merchants: int = 2_000
    start: str = "2026-09-01"  # first simulated day (UTC)
    days: int = 30
    fraud_rate: float = 0.005  # target share of all transactions that are fraud


@dataclass(frozen=True)
class Dataset:
    params: DatasetParams
    cards: pd.DataFrame  # the bank's customer records
    merchants: pd.DataFrame  # the bank's shop records
    transactions: pd.DataFrame  # legit + fraud, in time order, with labels


def generate_dataset(params: DatasetParams) -> Dataset:
    """Build cardholders, shops and transactions from params.seed - always in this order."""
    rng = np.random.default_rng(params.seed)
    cards = make_cardholders(params.n_cards, rng)
    merchants = make_merchants(params.n_merchants, rng)
    legit = make_legit_transactions(cards, merchants, rng, start=params.start, days=params.days)
    transactions = add_fraud(
        legit,
        cards,
        merchants,
        rng,
        start=params.start,
        days=params.days,
        fraud_rate=params.fraud_rate,
    )
    return Dataset(params, cards, merchants, transactions)


def fingerprint(table: pd.DataFrame) -> str:
    """SHA-256 of a table's contents: column names and every value, in row order.

    The table is written out as plain text (CSV) and the text is hashed. Plain
    text is used rather than the Parquet file itself because Parquet files also
    contain the library version that wrote them, so the same data written by two
    pandas versions would give different hashes. The row index (pandas' internal
    row labels) is ignored; it isn't data.

    Cells holding a list (e.g. a cardholder's favourite shop types) are written
    as "a|b|c". Otherwise the text would depend on HOW the list is held in
    memory - a Python list before saving, a numpy array after reading Parquet
    back - and the same data would get two different fingerprints.
    """
    canonical = table.copy()
    for col in canonical.columns:
        if canonical[col].dtype == object and canonical[col].map(_is_list_like).any():
            canonical[col] = canonical[col].map(
                lambda v: "|".join(str(x) for x in v) if _is_list_like(v) else v
            )
    text = canonical.to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_list_like(value) -> bool:
    return isinstance(value, (list, tuple, np.ndarray))
