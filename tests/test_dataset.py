"""tests/test_dataset.py - proves a dataset can be rebuilt exactly from its seed.

What: building a dataset twice with the same parameters gives identical
      fingerprints (SHA-256) for all three tables; a different seed gives
      different data; and the fingerprint notices even a one-penny change.
Why:  the manifest written with each dataset version records its seed and
      fingerprints. These tests are what make that record trustworthy - if the
      generator ever stopped being reproducible (e.g. someone used an unseeded
      random call), CI would fail here rather than models quietly being trained
      on data nobody can recreate.
"""

import pandas as pd
import pytest

from fraudstream.generator.dataset import DatasetParams, fingerprint, generate_dataset

SMALL = DatasetParams(seed=7, n_cards=500, n_merchants=150, days=5)


@pytest.fixture(scope="module")
def small():
    return generate_dataset(SMALL)


def test_same_seed_gives_same_fingerprints(small):
    again = generate_dataset(SMALL)
    for table in ("cards", "merchants", "transactions"):
        assert fingerprint(getattr(small, table)) == fingerprint(getattr(again, table)), table


def test_different_seed_gives_different_data(small):
    other = generate_dataset(DatasetParams(seed=8, n_cards=500, n_merchants=150, days=5))
    assert fingerprint(other.transactions) != fingerprint(small.transactions)


def test_fingerprint_notices_a_one_penny_change(small):
    changed = small.transactions.copy()
    changed.loc[changed.index[100], "amount"] += 0.01
    assert fingerprint(changed) != fingerprint(small.transactions)


def test_fingerprint_ignores_row_labels_but_not_column_order():
    table = pd.DataFrame({"a": [1, 2], "b": [3.5, 4.5]})
    relabelled = table.set_axis([10, 20])  # same data, different internal row labels
    assert fingerprint(relabelled) == fingerprint(table)
    assert fingerprint(table[["b", "a"]]) != fingerprint(table)


def test_fingerprint_is_a_sha256_hex_string(small):
    fp = fingerprint(small.cards)
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)


def test_dataset_keeps_its_parameters(small):
    assert small.params == SMALL
    assert small.transactions["is_fraud"].sum() > 0
