"""fraudstream.training.data - prepares a dataset version for training.

What: build_feature_table() runs the FeatureBuilder over every transaction in
      time order and lines the clues up with the answer (is_fraud).
      time_split() cuts that table by day into train / validation / test.
      prepare() does both for a loaded dataset version.
Why:  the model must be judged the way it will be used: predicting the future
      from the past. So the split is by TIME - e.g. days 1-21 train, 22-25
      validation (choosing the alert threshold), 26-30 test (the final, honest
      score) - never a random shuffle, which would let the model study part of
      a fraud episode and be "tested" on the rest of the same episode.
Note: features are computed over all 30 days in ONE pass, before splitting, so
      a card's history carries on across the split - just as it would live.
      That's safe because the leakage test proves a clue never uses later
      transactions.
Columns of each part:
      transaction_id, timestamp, day   - for tracing and analysis, NOT features
      <FEATURE_COLUMNS>                - what the model sees
      is_fraud                         - the answer (label)
      fraud_type                       - for breaking results down by style only.
                                         It's the answer in disguise, so it must
                                         never be given to the model (leakage).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from fraudstream.features import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, FeatureBuilder
from fraudstream.generator.dataset import Dataset

LABEL = "is_fraud"
_ONE_DAY = pd.Timedelta(days=1)


@dataclass(frozen=True)
class TrainingData:
    train: pd.DataFrame
    valid: pd.DataFrame
    test: pd.DataFrame
    info: dict  # where the data came from and where the split falls - goes into the model card


def build_feature_table(dataset: Dataset) -> pd.DataFrame:
    """One row per transaction: ids and time, the clues, and the answer."""
    tx = dataset.transactions.reset_index(drop=True)
    feats = FeatureBuilder(dataset.cards, dataset.merchants).build(tx)
    if not (feats["transaction_id"].to_numpy() == tx["transaction_id"].to_numpy()).all():
        raise RuntimeError("features came back in a different row order")  # build() promises not
    return pd.concat(
        [tx[["transaction_id", "timestamp"]], feats[FEATURE_COLUMNS], tx[[LABEL, "fraud_type"]]],
        axis=1,
    )


def time_split(
    table: pd.DataFrame, start: str, days: int, train_days: int = 21, valid_days: int = 4
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split by simulated day: [0, train_days) train, then valid_days validation, the rest test."""
    if train_days < 1 or valid_days < 1 or train_days + valid_days >= days:
        raise ValueError(f"need train_days + valid_days < days ({train_days}+{valid_days}, {days})")
    day = (table["timestamp"] - pd.Timestamp(start, tz="UTC")) // _ONE_DAY
    if day.min() < 0 or day.max() >= days:
        raise ValueError("transactions fall outside the dataset's simulated days")
    table = table.copy()
    table.insert(2, "day", day.astype(int) + 1)  # day 1 = the first simulated day
    train = table[table["day"] <= train_days]
    valid = table[(table["day"] > train_days) & (table["day"] <= train_days + valid_days)]
    test = table[table["day"] > train_days + valid_days]
    return tuple(part.reset_index(drop=True) for part in (train, valid, test))


def prepare(
    dataset: Dataset, manifest: dict, train_days: int = 21, valid_days: int = 4
) -> TrainingData:
    """Features + label + time split for a loaded dataset version."""
    params = dataset.params
    train, valid, test = time_split(
        build_feature_table(dataset), params.start, params.days, train_days, valid_days
    )
    info = {
        "dataset": manifest["dataset"],
        "dataset_fingerprint": manifest["tables"]["transactions"]["fingerprint"],
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": FEATURE_COLUMNS,
        "label": LABEL,
        "split": {
            name: {
                "days": [int(part["day"].min()), int(part["day"].max())],
                "rows": len(part),
                "fraud_rows": int(part[LABEL].sum()),
            }
            for name, part in (("train", train), ("valid", valid), ("test", test))
        },
    }
    return TrainingData(train, valid, test, info)
