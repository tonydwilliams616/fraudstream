"""Tests for fraudstream.monitoring.check - the hourly drift check.

What: the window is the previous clock hour; archive files for that hour are
      read, duplicates dropped, and each model is compared with its OWN
      baseline; inflated amounts publish a significant MaxAlarmPSI; too few
      rows publish no PSI; a missing baseline is reported (exit 1), never
      treated as "no drift"; the command line's --dry-run prints instead of
      publishing.
Why:  this job decides whether the drift alarm rings - and later whether a
      retrain starts. Fakes stand in for S3 and CloudWatch (dependency
      injection), so no AWS is needed.
"""

import gzip
import io
import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.monitoring.__main__ import main
from fraudstream.monitoring.check import baseline_key, previous_hour, read_window, run_check
from fraudstream.monitoring.drift import build_baseline

HOUR = datetime(2026, 11, 28, 14, tzinfo=UTC)
NOW = datetime(2026, 11, 28, 15, 5, tzinfo=UTC)


def features(n, seed, amount_factor=1.0):
    rng = np.random.default_rng(seed)
    amount = rng.lognormal(3.0, 0.8, n) * amount_factor
    hour = rng.integers(0, 24, n)
    online = (rng.random(n) < 0.3).astype(int)
    cat = rng.integers(0, 12, n)
    return pd.DataFrame(
        {
            "amount": amount,
            "log_amount": np.log1p(amount),
            "hour_of_day": hour,
            "is_night": (hour < 6).astype(int),
            "is_online": online,
            "category_code": cat,
            "merchant_risk": cat / 20,
            "distance_from_home_km": np.where(online == 1, np.nan, rng.exponential(8, n)),
            "card_txn_count": rng.integers(0, 400, n),
            "txn_count_10m": rng.poisson(0.2, n),
            "txn_count_1h": rng.poisson(0.8, n),
            "secs_since_last_txn": rng.exponential(30000, n),
            "amount_vs_card_mean": rng.lognormal(0, 0.5, n),
            "is_new_merchant": (rng.random(n) < 0.7).astype(int),
        }
    )[FEATURE_COLUMNS]


def archive_lines(frame, run_id, version, start=0):
    for i, row in enumerate(frame.to_dict("records")):
        feats = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}
        yield json.dumps(
            {
                "transaction_id": f"T{start + i}",
                "model_run_id": run_id,
                "model_version": version,
                "features": feats,
            }
        )


class Missing(Exception):
    def __init__(self):
        super().__init__("NoSuchKey")
        self.response = {"Error": {"Code": "NoSuchKey"}}  # the shape botocore errors have


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put(self, bucket, key, body: bytes):
        self.objects[(bucket, key)] = body

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start : start + 2]  # tiny pages, so paging is exercised
        more = start + 2 < len(keys)
        out = {"Contents": [{"Key": k} for k in page], "IsTruncated": more}
        if more:
            out["NextContinuationToken"] = str(start + 2)
        return out

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise Missing()
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class FakeCloudWatch:
    def __init__(self):
        self.data = []

    def put_metric_data(self, Namespace, MetricData):
        assert Namespace == "fraudstream/drift" and len(MetricData) <= 20
        self.data += MetricData

    def value(self, name, **dims):
        wanted = [{"Name": k, "Value": v} for k, v in dims.items()]
        return [
            m["Value"]
            for m in self.data
            if m["MetricName"] == name and m.get("Dimensions", []) == wanted
        ]


def setup(live, run_id="run-a", version="2", baseline=True, duplicate_file=False):
    s3 = FakeS3()
    if baseline:
        base = build_baseline(features(30_000, seed=1))
        s3.put("art", baseline_key(run_id), json.dumps(base).encode())
    lines = list(archive_lines(live, run_id, version))
    prefix = f"scored/dt={HOUR:%Y-%m-%d}/hour={HOUR:%H}/"
    for n, chunk in enumerate(range(0, len(lines), 1000)):
        body = "\n".join(lines[chunk : chunk + 1000]) + "\n"
        s3.put("scored", f"{prefix}{n:04d}.jsonl.gz", gzip.compress(body.encode()))
    if duplicate_file:  # the same minute archived twice (at-least-once)
        s3.put(
            "scored",
            f"{prefix}dup.jsonl.gz",
            gzip.compress(("\n".join(lines[:1000]) + "\n").encode()),
        )
    s3.put("scored", "scored/dt=2026-11-28/hour=15/late.jsonl.gz", gzip.compress(b""))  # next hour
    return s3


def test_the_window_is_the_previous_clock_hour():
    assert previous_hour(datetime(2026, 11, 28, 15, 5, 30, tzinfo=UTC)) == HOUR
    assert previous_hour(datetime(2026, 11, 28, 0, 1, tzinfo=UTC)) == datetime(
        2026, 11, 27, 23, tzinfo=UTC
    )


def test_reads_every_file_of_the_hour_and_drops_duplicates():
    s3 = setup(features(5_000, seed=2), duplicate_file=True)
    window = read_window(s3, "scored", HOUR)
    assert len(window) == 5_000 and window["transaction_id"].is_unique
    assert set(window["model_version"]) == {"2"}


def test_unchanged_world_publishes_a_low_max_alarm_psi():
    cw = FakeCloudWatch()
    summary = run_check(setup(features(25_000, seed=2)), cw, "scored", "art", HOUR, now=NOW)
    assert summary["rows"] == 25_000 and summary["max_alarm_psi"] < 0.05
    assert cw.value("MaxAlarmPSI") == [summary["max_alarm_psi"]]
    assert cw.value("RowsChecked") == [25_000]
    assert len(cw.value("PSI", Feature="amount", ModelVersion="2")) == 1


def test_doubled_prices_publish_a_significant_max_alarm_psi():
    cw = FakeCloudWatch()
    summary = run_check(
        setup(features(25_000, seed=2, amount_factor=2.0)), cw, "scored", "art", HOUR, now=NOW
    )
    assert summary["max_alarm_psi"] > 0.25
    assert summary["models"]["2"]["worst_alarm_feature"] in ("amount", "log_amount")


def test_too_few_rows_publish_no_psi_at_all():
    cw = FakeCloudWatch()
    summary = run_check(
        setup(features(3_000, seed=2, amount_factor=2.0)), cw, "scored", "art", HOUR, now=NOW
    )
    assert summary["max_alarm_psi"] is None and summary["models"] == {}
    assert cw.value("MaxAlarmPSI") == [] and cw.value("RowsChecked") == [3_000]


def test_a_missing_baseline_is_reported_never_treated_as_no_drift():
    cw = FakeCloudWatch()
    summary = run_check(
        setup(features(25_000, seed=2), baseline=False), cw, "scored", "art", HOUR, now=NOW
    )
    assert summary["missing_baselines"] == ["run-a"]
    assert cw.value("MaxAlarmPSI") == []


def test_each_model_is_compared_with_its_own_baseline():
    s3 = setup(features(25_000, seed=2))
    # a second model, trained on the doubled-price world, scoring doubled prices
    s3.put(
        "art", baseline_key("run-b"), json.dumps(build_baseline(features(30_000, 7, 2.0))).encode()
    )
    lines = list(
        archive_lines(features(25_000, seed=3, amount_factor=2.0), "run-b", "6", start=10**6)
    )
    body = "\n".join(lines) + "\n"
    s3.put(
        "scored",
        f"scored/dt={HOUR:%Y-%m-%d}/hour={HOUR:%H}/zz.jsonl.gz",
        gzip.compress(body.encode()),
    )
    summary = run_check(s3, FakeCloudWatch(), "scored", "art", HOUR, now=NOW)
    assert set(summary["models"]) == {"2", "6"}
    assert summary["models"]["6"]["verdict"] == "stable"  # new normal vs its own training data


def test_command_line_dry_run_and_exit_codes(capsys):
    s3 = setup(features(25_000, seed=2))
    args = [
        "check",
        "--hour",
        "2026-11-28T14",
        "--scored-bucket",
        "scored",
        "--artefacts-bucket",
        "art",
    ]
    assert main([*args, "--dry-run"], s3=s3) == 0
    assert "fraudstream/drift MaxAlarmPSI{}" in capsys.readouterr().out
    missing = setup(features(25_000, seed=2), baseline=False)
    assert main([*args, "--dry-run"], s3=missing) == 1
