"""tests/test_training_data.py - checks loading a dataset version and splitting it by time.

What: a saved version loads back exactly (and a tampered file is refused);
      download from S3 works and refuses an incomplete version (S3 is faked);
      the feature table lines clues up with the right answers; and the time
      split puts every row in exactly one part, in time order, with no overlap.
Why:  if training silently used the wrong data, the wrong rows, or let
      validation days leak into training, every number the model reports
      would be meaningless - and nothing would crash to tell you.
"""

import json

import pandas as pd
import pytest

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.generator.dataset import DatasetParams, fingerprint, generate_dataset
from fraudstream.storage import MANIFEST, download_version, load_version, save_version
from fraudstream.training.data import LABEL, build_feature_table, prepare, time_split

PARAMS = DatasetParams(seed=3, n_cards=400, n_merchants=150, days=10)


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    root = tmp_path_factory.mktemp("data")
    manifest = save_version(generate_dataset(PARAMS), root, "v1")
    return root / "transactions" / "v1", manifest


@pytest.fixture(scope="module")
def loaded(saved):
    return load_version(saved[0])


def test_load_gives_back_exactly_what_was_saved(saved, loaded):
    dataset, manifest = loaded
    assert manifest == saved[1]
    assert dataset.params == PARAMS
    assert fingerprint(dataset.transactions) == manifest["tables"]["transactions"]["fingerprint"]


def test_tampered_file_is_refused(saved, tmp_path):
    folder = tmp_path / "copy"
    folder.mkdir()
    for f in saved[0].iterdir():
        (folder / f.name).write_bytes(f.read_bytes())
    with open(folder / "merchants.parquet", "ab") as f:
        f.write(b"x")  # one extra byte
    with pytest.raises(ValueError, match="SHA-256"):
        load_version(folder)


class FakeS3:
    """Just enough of the boto3 S3 client for download_version, serving a local folder."""

    class exceptions:  # same shape as boto3's client.exceptions
        class NoSuchKey(Exception):
            pass

    def __init__(self, folder, prefix):
        self.files = {f"{prefix}/{f.name}": f for f in folder.iterdir()}
        self.downloads = []

    def get_object(self, Bucket, Key):
        if Key not in self.files:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": open(self.files[Key], "rb")}

    def download_file(self, bucket, key, filename):
        self.downloads.append(key)
        with open(filename, "wb") as out:
            out.write(self.files[key].read_bytes())


def test_download_then_load(saved, tmp_path):
    s3 = FakeS3(saved[0], "transactions/v1")
    folder = download_version("s3://bucket/transactions/v1/", tmp_path, s3=s3)
    assert folder == tmp_path / "transactions" / "v1"
    assert len(s3.downloads) == 3
    assert load_version(folder)[1] == saved[1]
    # a second call reuses the local copy instead of downloading again
    download_version("s3://bucket/transactions/v1", tmp_path, s3=s3)
    assert len(s3.downloads) == 3


def test_download_refuses_a_version_without_manifest(saved, tmp_path):
    s3 = FakeS3(saved[0], "transactions/v1")
    del s3.files[f"transactions/v1/{MANIFEST}"]
    with pytest.raises(FileNotFoundError, match="incomplete"):
        download_version("s3://bucket/transactions/v1", tmp_path, s3=s3)


def test_feature_table_lines_up_clues_and_answers(loaded):
    dataset, _ = loaded
    table = build_feature_table(dataset)
    assert list(table.columns) == [
        "transaction_id",
        "timestamp",
        *FEATURE_COLUMNS,
        LABEL,
        "fraud_type",
    ]
    assert len(table) == len(dataset.transactions)
    answers = dataset.transactions.set_index("transaction_id")[LABEL]
    assert (table[LABEL].to_numpy() == answers.loc[table["transaction_id"]].to_numpy()).all()


def test_time_split_covers_every_row_once_in_time_order(loaded):
    dataset, manifest = loaded
    data = prepare(dataset, manifest, train_days=7, valid_days=2)
    train, valid, test = data.train, data.valid, data.test
    assert len(train) + len(valid) + len(test) == len(dataset.transactions)
    ids = [set(part["transaction_id"]) for part in (train, valid, test)]
    assert not (ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
    assert train["timestamp"].max() < valid["timestamp"].min()
    assert valid["timestamp"].max() < test["timestamp"].min()
    assert (train["day"].min(), train["day"].max()) == (1, 7)
    assert (valid["day"].min(), valid["day"].max()) == (8, 9)
    assert (test["day"].min(), test["day"].max()) == (10, 10)


def test_split_info_describes_the_parts(loaded):
    dataset, manifest = loaded
    data = prepare(dataset, manifest, train_days=7, valid_days=2)
    assert data.info["dataset"] == "transactions/v1"
    assert data.info["split"]["train"]["days"] == [1, 7]
    assert data.info["split"]["test"]["rows"] == len(data.test)
    assert sum(p["fraud_rows"] for p in data.info["split"].values()) == int(
        dataset.transactions[LABEL].sum()
    )
    json.dumps(data.info)  # must be saveable in the model card


def test_fraud_type_is_never_a_feature():
    assert "fraud_type" not in FEATURE_COLUMNS and LABEL not in FEATURE_COLUMNS


@pytest.mark.parametrize("train_days,valid_days", [(9, 1), (10, 1), (0, 2), (5, 0)])
def test_split_needs_room_for_every_part(loaded, train_days, valid_days):
    table = pd.DataFrame({"transaction_id": [], "timestamp": []})
    with pytest.raises(ValueError):
        time_split(table, PARAMS.start, PARAMS.days, train_days, valid_days)
