"""fraudstream.storage - saves a dataset as a numbered version, locally and in S3.

What: save_version() writes the three tables (transactions, cards, merchants)
      as Parquet files plus a manifest.json into <root>/<name>/<version>/,
      e.g. data/transactions/v1/. upload_version() copies that folder to the
      datasets bucket under the same path.
Why:  every model must point at exactly the data it learned from. A numbered
      version that never changes, with a manifest describing it, is what makes
      "model 7 was trained on transactions/v1" a checkable statement.
Concepts:
  - IMMUTABLE VERSIONS. A version is written once and never changed - like a
    published book edition. Need different data? Make v2. Both the local save
    and the upload refuse to overwrite, and each S3 upload is a conditional
    write ("only if this key doesn't already exist"), so S3 itself enforces it.
  - The MANIFEST is the dataset's label: the generator parameters and seed
    (enough to rebuild it), the code commit that made it, library versions,
    row counts, a fingerprint of each table's contents and a SHA-256 of each
    file. It's uploaded LAST, so a version with a manifest is a complete
    version; a half-finished upload has no manifest and is ignored.
  - Parquet: a compact, typed file format for tables. It keeps column types
    (timestamps stay timestamps), is about 10x smaller than CSV, and is what
    SageMaker training jobs and pandas read most efficiently.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import pandas as pd

import fraudstream
from fraudstream.generator.dataset import Dataset, fingerprint

TABLES = ("transactions", "cards", "merchants")
MANIFEST = "manifest.json"
MANIFEST_FORMAT = 1  # bump if the manifest's layout changes
_VERSION_PATTERN = re.compile(r"^v[1-9][0-9]*$")  # v1, v2, ... (never "latest")


def save_version(dataset: Dataset, root: Path, version: str, name: str = "transactions") -> dict:
    """Write the dataset to <root>/<name>/<version>/ and return its manifest.

    Refuses if that version already exists. Each table is read back after
    writing and its fingerprint compared, so a file that doesn't hold exactly
    the generated data is never published.
    """
    if not _VERSION_PATTERN.match(version):
        raise ValueError(f"version must look like v1, v2, ... - got {version!r}")
    out = Path(root) / name / version
    if out.exists():
        raise FileExistsError(f"{out} already exists - versions are never overwritten")
    out.mkdir(parents=True)

    tables = {}
    for table in TABLES:
        df = getattr(dataset, table)
        path = out / f"{table}.parquet"
        df.to_parquet(path, index=False)
        fp = fingerprint(df)
        if fingerprint(pd.read_parquet(path)) != fp:
            raise RuntimeError(f"{path}: data read back from Parquet differs from what was written")
        tables[table] = {
            "file": path.name,
            "rows": len(df),
            "columns": list(df.columns),
            "fingerprint": fp,  # of the contents - see generator.dataset.fingerprint
            "sha256": file_sha256(path),  # of the file's bytes - for integrity checks
            "bytes": path.stat().st_size,
        }

    manifest = {
        "manifest_format": MANIFEST_FORMAT,
        "dataset": f"{name}/{version}",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "params": asdict(dataset.params),
        "code": {"package_version": fraudstream.__version__, **git_state()},
        "environment": {
            "python": platform.python_version(),
            **{lib: _library_version(lib) for lib in ("numpy", "pandas", "pyarrow")},
        },
        "tables": tables,
        "summary": summarise(dataset.transactions),
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def upload_version(local_dir: Path, bucket: str, s3=None) -> list[str]:
    """Upload a saved version folder to s3://<bucket>/<name>/<version>/; return the keys.

    Refuses if anything already exists under that prefix. Data files go first
    and the manifest last. s3 is a boto3 S3 client (made here if not given;
    tests pass a fake one).
    """
    local_dir = Path(local_dir)
    manifest = json.loads((local_dir / MANIFEST).read_text())
    prefix = manifest["dataset"] + "/"  # e.g. "transactions/v1/"
    if s3 is None:
        import boto3  # only needed for uploads, so imported only here

        s3 = boto3.client("s3")

    existing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    if existing.get("KeyCount", 0) > 0:
        raise FileExistsError(
            f"s3://{bucket}/{prefix} already exists - versions are never overwritten"
        )

    files = [t["file"] for t in manifest["tables"].values()] + [MANIFEST]  # manifest LAST
    keys = []
    for file in files:
        key = prefix + file
        with open(local_dir / file, "rb") as body:
            # IfNoneMatch="*": S3 rejects the write if the key already exists,
            # even if someone else uploaded it a moment after the check above.
            s3.put_object(Bucket=bucket, Key=key, Body=body, IfNoneMatch="*")
        keys.append(key)
    return keys


def summarise(transactions: pd.DataFrame) -> dict:
    """Headline numbers for the manifest, so a version can be sanity-checked at a glance."""
    fraud = transactions[transactions["is_fraud"] == 1]
    return {
        "transactions": len(transactions),
        "fraud_rows": len(fraud),
        "fraud_rate": round(len(fraud) / len(transactions), 5),
        "fraud_by_type": {k: int(v) for k, v in fraud["fraud_type"].value_counts().items()},
        "cards_with_fraud": int(fraud["card_id"].nunique()),
        "first_timestamp": transactions["timestamp"].min().isoformat(),
        "last_timestamp": transactions["timestamp"].max().isoformat(),
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_state() -> dict:
    """The commit the code was run from, and whether there were uncommitted changes."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_dirty": None}
    return {"git_commit": commit, "git_dirty": bool(status.strip())}


def _library_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None
