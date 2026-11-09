"""tests/test_check_scoring.py - checks the "nothing lost" audit of the scored archive.

What: unbroken offsets pass; a missing offset is reported as a gap and fails;
      the same offset scored twice counts as a duplicate, not a loss; latency
      percentiles and the alert rate come out right; the script reads gzipped
      archive files from (faked) S3.
Why:  this script is the evidence behind "the consumer loses nothing" - it has
      to be right itself.
"""

import gzip
import json
import runpy
from datetime import UTC, datetime
from pathlib import Path


def rec(partition, offset, latency=100, alert=False):
    return {
        "transaction_id": f"t{partition}-{offset}",
        "kafka_partition": partition,
        "kafka_offset": offset,
        "latency_ms": latency,
        "alert": alert,
        "model_version": 1,
    }


def script():
    path = Path(__file__).parents[1] / "scripts" / "ops" / "check_scoring.py"
    return runpy.run_path(str(path))


def test_unbroken_offsets_have_no_gaps():
    report = script()["analyse"]([rec(0, o) for o in range(10)] + [rec(1, o) for o in range(5, 8)])
    assert report["gaps"] == 0 and report["duplicates"] == 0
    assert report["partitions"][1]["first"] == 5 and report["partitions"][1]["last"] == 7


def test_missing_offset_is_a_gap():
    report = script()["analyse"]([rec(0, o) for o in (0, 1, 3, 4)])
    assert report["gaps"] == 1 and report["partitions"][0]["first_missing"] == [2]


def test_rescored_offset_is_a_duplicate_not_a_loss():
    report = script()["analyse"]([rec(0, o) for o in (0, 1, 1, 2)])
    assert report["gaps"] == 0 and report["duplicates"] == 1
    assert report["unique_transactions"] == 3


def test_latency_percentiles_and_alert_rate():
    records = [rec(0, o, latency=o * 10, alert=(o == 99)) for o in range(100)]
    report = script()["analyse"](records)
    assert report["latency_ms"] == {"p50": 500, "p95": 950, "p99": 990, "max": 990}
    assert report["alert_rate"] == 0.01


class FakeS3:
    def __init__(self, files):
        self.files = files  # key -> (last_modified, records)

    def get_paginator(self, name):
        files = self.files

        class Pager:
            def paginate(self, Bucket, Prefix):
                yield {
                    "Contents": [
                        {"Key": k, "LastModified": t}
                        for k, (t, _) in files.items()
                        if k.startswith(Prefix)
                    ]
                }

        return Pager()

    def get_object(self, Bucket, Key):
        lines = "".join(json.dumps(r) + "\n" for r in self.files[Key][1])

        class Body:
            def read(self_inner):
                return gzip.compress(lines.encode())

        return {"Body": Body()}


def test_script_reads_archive_files_after_since(capsys):
    today = datetime.now(UTC)
    old = today.replace(hour=0, minute=0, second=0, microsecond=0)
    key = f"scored/dt={today:%Y-%m-%d}/hour=00/"
    s3 = FakeS3({
        key + "a.jsonl.gz": (old, [rec(0, 0)]),  # before --since: ignored
        key + "b.jsonl.gz": (today, [rec(0, o) for o in range(1, 4)]),
    })  # fmt: skip
    since = old.replace(minute=1).strftime("%Y-%m-%dT%H:%M")
    assert script()["main"](["--since", since], s3=s3) == 0
    out = capsys.readouterr().out
    assert "PASS: no transaction lost - 3 transactions, 0 scored twice" in out
