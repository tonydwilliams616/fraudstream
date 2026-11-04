"""scripts/data/make_dataset.py - generates a dataset version and (optionally) uploads it.

What: builds the synthetic dataset from a seed, saves it to
      data/transactions/<version>/ (three Parquet files + manifest.json) and,
      with --upload, copies it to s3://fs-datasets-831547420318/transactions/<version>/.
Why:  one command, with every setting written on the command line and in the
      manifest, so any dataset version can be rebuilt and checked later.
Safety: a version is never overwritten, locally or in S3. Uploading also
      requires a clean git working tree (everything committed), so the commit
      recorded in the manifest really is the code that made the data;
      --allow-dirty overrides that for experiments.

Usage (repo root, venv active):
  python scripts/data/make_dataset.py --version v1 --seed 42            # local only
  source ./aws-login.sh
  python scripts/data/make_dataset.py --version v1 --seed 42 --upload   # local + S3
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from fraudstream.generator.dataset import DatasetParams, generate_dataset
from fraudstream.storage import git_state, save_version, upload_version

DEFAULT_BUCKET = "fs-datasets-831547420318"


def main(argv: list[str] | None = None) -> int:
    defaults = DatasetParams(seed=0)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--version", required=True, help="dataset version, e.g. v1")
    p.add_argument("--seed", type=int, required=True, help="random seed - same seed, same data")
    p.add_argument("--n-cards", type=int, default=defaults.n_cards)
    p.add_argument("--n-merchants", type=int, default=defaults.n_merchants)
    p.add_argument("--start", default=defaults.start, help="first simulated day, YYYY-MM-DD")
    p.add_argument("--days", type=int, default=defaults.days)
    p.add_argument("--fraud-rate", type=float, default=defaults.fraud_rate)
    p.add_argument("--out", type=Path, default=Path("data"), help="local root folder")
    p.add_argument("--upload", action="store_true", help="also upload to S3")
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--allow-dirty", action="store_true", help="upload even with uncommitted code")
    args = p.parse_args(argv)

    # Check before spending time generating: is the upload going to be allowed?
    if args.upload and not args.allow_dirty and git_state()["git_dirty"] is not False:
        print("Refusing to upload: commit your changes first (or pass --allow-dirty).")
        return 1

    params = DatasetParams(
        seed=args.seed,
        n_cards=args.n_cards,
        n_merchants=args.n_merchants,
        start=args.start,
        days=args.days,
        fraud_rate=args.fraud_rate,
    )
    started = time.perf_counter()
    print(f"Generating with {params} ...")
    dataset = generate_dataset(params)
    manifest = save_version(dataset, args.out, args.version)
    local_dir = args.out / manifest["dataset"]

    s = manifest["summary"]
    print(f"Saved {local_dir}/ in {time.perf_counter() - started:.0f}s")
    print(
        f"  {s['transactions']:,} transactions, {s['fraud_rows']:,} fraud ({s['fraud_rate']:.2%})"
    )
    print(f"  fraud by type: {s['fraud_by_type']}")
    for info in manifest["tables"].values():
        mb = info["bytes"] / 1e6
        print(
            f"  {info['file']:<22} {info['rows']:>9,} rows {mb:6.1f} MB  {info['fingerprint'][:12]}"
        )

    if args.upload:
        keys = upload_version(local_dir, args.bucket)
        print(f"Uploaded {len(keys)} files to s3://{args.bucket}/{manifest['dataset']}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
