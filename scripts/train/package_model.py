"""scripts/train/package_model.py - packages a training run and (optionally) publishes it.

What: for models/<run-id>/ it writes model_card.md and model.tar.gz, checks the
      packed model scores exactly like the trained one, and with --upload
      copies the run to s3://fs-artefacts-831547420318/models/fraud-baseline/<run-id>/.
Why:  only a described, checked, permanently stored model can be registered
      (Phase 3) or deployed (Phase 2).
Safety: uploading refuses a model trained from uncommitted code (metrics.json
      records this), because its recorded commit wouldn't be the code that made
      it - retrain from clean main instead. Published runs are never overwritten.

Usage (repo root, venv active; --upload needs `source ./aws-login.sh` first):
  python scripts/train/package_model.py models/<run-id>
  python scripts/train/package_model.py models/<run-id> --upload
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fraudstream.training.package import (
    METRICS,
    build_model_tarball,
    check_packaged_model,
    upload_model,
    write_model_card,
)

DEFAULT_BUCKET = "fs-artefacts-831547420318"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("run_dir", type=Path, help="a run folder, e.g. models/baseline-2026...")
    p.add_argument("--upload", action="store_true", help="publish to the artefacts bucket")
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    args = p.parse_args(argv)

    metrics = json.loads((args.run_dir / METRICS).read_text())
    if args.upload and metrics["code"]["git_dirty"] is not False:
        print("Refusing to upload: this model was trained from uncommitted code.")
        print("Commit and merge, then retrain from clean main.")
        return 1

    card = write_model_card(args.run_dir)
    tarball = build_model_tarball(args.run_dir)
    check_packaged_model(args.run_dir)
    print(f"Wrote {card} and {tarball} ({tarball.stat().st_size / 1e3:.0f} kB)")
    print("Checked: the packaged model scores exactly like the trained model")

    if args.upload:
        keys = upload_model(args.run_dir, args.bucket)
        prefix = keys[0].rsplit("/", 1)[0]
        print(f"Uploaded {len(keys)} files to s3://{args.bucket}/{prefix}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
