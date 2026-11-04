"""tests/test_storage.py - checks saving and uploading dataset versions.

What: a saved version has the three Parquet files and a manifest whose
      fingerprints, file hashes and row counts are right; data read back from
      Parquet has the same fingerprint as the data generated; versions are never
      overwritten; and the S3 upload sends the manifest last, with every write
      conditional. S3 is replaced by a small fake, so no AWS access is needed.
Why:  the manifest is what later stages trust (training records which dataset it
      used; DR restores from it). If it could be wrong, or a version could be
      silently replaced, that trust would be misplaced.
"""

import json
import runpy
from pathlib import Path

import pandas as pd
import pytest

from fraudstream.generator.dataset import DatasetParams, fingerprint, generate_dataset
from fraudstream.storage import MANIFEST, TABLES, file_sha256, save_version, upload_version

PARAMS = DatasetParams(seed=3, n_cards=400, n_merchants=150, days=5)


@pytest.fixture(scope="module")
def dataset():
    return generate_dataset(PARAMS)


@pytest.fixture
def saved(dataset, tmp_path):
    manifest = save_version(dataset, tmp_path, "v1")
    return tmp_path / "transactions" / "v1", manifest


def test_version_folder_has_tables_and_manifest(saved):
    folder, _ = saved
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [f"{t}.parquet" for t in TABLES] + [MANIFEST]
    )


def test_manifest_describes_the_files(saved, dataset):
    folder, manifest = saved
    assert manifest == json.loads((folder / MANIFEST).read_text())
    assert manifest["dataset"] == "transactions/v1"
    assert manifest["params"]["seed"] == PARAMS.seed
    for table, info in manifest["tables"].items():
        assert info["rows"] == len(getattr(dataset, table))
        assert info["sha256"] == file_sha256(folder / info["file"])
        assert info["fingerprint"] == fingerprint(getattr(dataset, table))


def test_parquet_round_trip_keeps_the_data(saved, dataset):
    folder, manifest = saved
    for table in TABLES:
        back = pd.read_parquet(folder / f"{table}.parquet")
        assert fingerprint(back) == manifest["tables"][table]["fingerprint"], table


def test_summary_counts_fraud(saved, dataset):
    _, manifest = saved
    tx = dataset.transactions
    assert manifest["summary"]["fraud_rows"] == int(tx["is_fraud"].sum())
    assert sum(manifest["summary"]["fraud_by_type"].values()) == int(tx["is_fraud"].sum())


def test_existing_version_is_never_overwritten(saved, dataset, tmp_path):
    with pytest.raises(FileExistsError):
        save_version(dataset, tmp_path, "v1")


@pytest.mark.parametrize("bad", ["latest", "1", "v0", "v1.2", "V1"])
def test_version_names_are_numbered(dataset, tmp_path, bad):
    with pytest.raises(ValueError):
        save_version(dataset, tmp_path, bad)


class FakeS3:
    """Just enough of the boto3 S3 client for upload_version."""

    def __init__(self, existing=()):
        self.objects = {key: b"" for key in existing}
        self.puts = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {"KeyCount": min(MaxKeys, sum(k.startswith(Prefix) for k in self.objects))}

    def put_object(self, Bucket, Key, Body, IfNoneMatch):
        assert IfNoneMatch == "*" and Key not in self.objects
        self.objects[Key] = Body.read()
        self.puts.append(Key)


def test_upload_sends_every_file_with_manifest_last(saved):
    folder, _ = saved
    s3 = FakeS3()
    keys = upload_version(folder, "bucket", s3=s3)
    assert keys == s3.puts
    assert keys[-1] == "transactions/v1/manifest.json"
    assert set(keys) == {f"transactions/v1/{t}.parquet" for t in TABLES} | {keys[-1]}
    assert s3.objects[keys[-1]] == (folder / MANIFEST).read_bytes()


def test_upload_refuses_an_existing_version(saved):
    folder, _ = saved
    s3 = FakeS3(existing=["transactions/v1/manifest.json"])
    with pytest.raises(FileExistsError):
        upload_version(folder, "bucket", s3=s3)
    assert s3.puts == []


def test_make_dataset_script_saves_a_local_version(tmp_path, capsys):
    script = Path(__file__).parents[1] / "scripts" / "data" / "make_dataset.py"
    main = runpy.run_path(str(script))["main"]
    args = ["--version", "v1", "--seed", "3", "--n-cards", "400", "--n-merchants", "150"]
    assert main([*args, "--days", "5", "--out", str(tmp_path)]) == 0
    manifest = json.loads((tmp_path / "transactions" / "v1" / MANIFEST).read_text())
    assert manifest["tables"]["transactions"]["fingerprint"] == fingerprint(
        generate_dataset(PARAMS).transactions
    )
    assert "fraud by type" in capsys.readouterr().out
