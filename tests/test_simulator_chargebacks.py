"""Tests for fraudstream.simulator.chargebacks - the delayed fraud labels.

What: only fraud gets a chargeback; each is dated 1-3 simulated days after the
      purchase and written only once that date has passed; some fraud is never
      reported; writes are throttled to one file per interval; records carry
      both times and no features; the S3 key layout; run() feeds every sent
      transaction's truth to the feed, and the wire still carries no labels.
Why:  6e builds training sets from these files. A chargeback written early
      would leak the future into training; a missing one would silently turn
      fraud into "legit".
"""

import io
import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from fraudstream.generator.dataset import DatasetParams, generate_dataset
from fraudstream.simulator.__main__ import PrintSink, Settings, run
from fraudstream.simulator.chargebacks import ChargebackFeed, S3ChargebackWriter, event_time
from fraudstream.simulator.drift import DriftSwitch, fixed
from fraudstream.simulator.events import Pacer, stream_labelled
from fraudstream.storage import save_version

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
FRAUD = {"is_fraud": True, "fraud_type": "card_testing"}
LEGIT = {"is_fraud": False, "fraud_type": None}


def msg(n, when=T0):
    return {
        "transaction_id": f"T{n}",
        "card_id": "C1",
        "timestamp": when.isoformat(),
        "amount": 9.5,
    }


class Collect:
    """A writer that keeps every batch it's given."""

    def __init__(self):
        self.batches = []

    def __call__(self, records):
        self.batches.append(records)

    @property
    def records(self):
        return [r for batch in self.batches for r in batch]


def feed(write, **kw):
    kw.setdefault("write_every_seconds", 0)
    return ChargebackFeed(write, np.random.default_rng(0), **kw)


def test_legit_purchases_never_get_a_chargeback():
    out = Collect()
    f = feed(out, report_rate=1)
    f.observe(msg(1), LEGIT)
    f.post_due(T0 + timedelta(days=30))
    assert out.records == [] and f.frauds == 0


def test_a_chargeback_is_written_only_once_its_known_at_has_passed():
    out = Collect()
    f = feed(out, report_rate=1, min_days=1, max_days=3)
    f.observe(msg(1), FRAUD)
    assert f.post_due(T0 + timedelta(hours=23)) == 0  # too early: nobody knows yet
    assert f.post_due(T0 + timedelta(days=3)) == 1
    (record,) = out.records
    known = datetime.fromisoformat(record["known_at"])
    assert T0 + timedelta(days=1) <= known <= T0 + timedelta(days=3)
    assert record["event_time"] == T0.isoformat() and record["transaction_id"] == "T1"
    assert "amount" not in record and "is_fraud" not in record  # a label, not a copy of the data


def test_chargebacks_come_out_in_delivery_order():
    out = Collect()
    f = feed(out, report_rate=1, min_days=0, max_days=5)
    for n in range(50):
        f.observe(msg(n, T0 + timedelta(minutes=n)), FRAUD)
    f.post_due(T0 + timedelta(days=10))
    known = [r["known_at"] for r in out.records]
    assert len(known) == 50 and known == sorted(known)


@pytest.mark.parametrize("rate, expected_posted", [(0.0, 0), (1.0, 20)])
def test_report_rate_controls_how_much_fraud_is_ever_reported(rate, expected_posted):
    out = Collect()
    f = feed(out, report_rate=rate, min_days=0, max_days=0)
    for n in range(20):
        f.observe(msg(n), FRAUD)
    f.post_due(T0)
    assert f.posted == expected_posted and f.unreported == 20 - expected_posted


def test_writes_are_throttled_to_one_per_interval_unless_forced():
    out, clock = Collect(), iter([0, 10, 20, 70]).__next__
    f = ChargebackFeed(out, np.random.default_rng(0), 0, 0, 1, write_every_seconds=60, clock=clock)
    f.observe(msg(1), FRAUD)
    assert f.post_due(T0) == 1  # clock 0: first write
    f.observe(msg(2), FRAUD)
    assert f.post_due(T0) == 0  # clock 10: too soon
    assert f.post_due(T0, force=True) == 1  # clock 20: forced (stopping)
    assert len(out.batches) == 2


def test_settings_are_validated():
    with pytest.raises(ValueError):
        ChargebackFeed(Collect(), np.random.default_rng(0), min_days=3, max_days=1)
    with pytest.raises(ValueError):
        ChargebackFeed(Collect(), np.random.default_rng(0), report_rate=1.5)
    with pytest.raises(ValueError, match="LABELS_URI"):
        Settings.from_env({"DRY_RUN": "1", "LABELS_URI": "/tmp/labels"}).chargeback_feed()
    assert Settings.from_env({"BOOTSTRAP_SERVERS": "b"}).chargeback_feed() is None


def test_s3_writer_layout():
    class FakeS3:
        def __init__(self):
            self.calls = []

        def put_object(self, **kw):
            self.calls.append(kw)

    s3 = FakeS3()
    write = S3ChargebackWriter(s3, "fs-scored-1", "/labels/chargebacks/")
    write([{"known_at": "2026-11-09T03:04:05+00:00", "transaction_id": "T1"}])
    (call,) = s3.calls
    assert call["Bucket"] == "fs-scored-1"
    assert call["Key"].startswith("labels/chargebacks/dt=2026-11-09/030405-")
    assert json.loads(call["Body"].decode().strip())["transaction_id"] == "T1"


def test_stream_truth_matches_the_generator_and_never_reaches_the_wire():
    dataset = generate_dataset(DatasetParams(seed=3, n_cards=200, n_merchants=80, days=2))
    pairs = []
    for pair in stream_labelled(
        dataset.cards, dataset.merchants, start="2026-11-07", seed=1, block_days=1, fraud_rate=0.05
    ):
        pairs.append(pair)
        if len(pairs) == 3000:
            break
    assert any(t["is_fraud"] for _, t in pairs) and not all(t["is_fraud"] for _, t in pairs)
    assert all(t["fraud_type"] for _, t in pairs if t["is_fraud"])
    assert all(t["fraud_type"] is None for _, t in pairs if not t["is_fraud"])
    assert not any("is_fraud" in m or "fraud_type" in m for m, _ in pairs)


def test_run_gives_the_feed_every_sent_fraud(tmp_path):
    dataset = generate_dataset(DatasetParams(seed=3, n_cards=200, n_merchants=80, days=2))
    save_version(dataset, tmp_path, "v1")
    env = {
        "DRY_RUN": "1",
        "DATASET_URI": str(tmp_path / "transactions" / "v1"),
        "START_DATE": "2026-11-07",
        "MAX_MESSAGES": "3000",
        "FRAUD_RATE": "0.05",
    }
    out, wire = Collect(), io.StringIO()
    f = feed(out, report_rate=1, min_days=0, max_days=0)  # known at once
    run(
        Settings.from_env(env),
        PrintSink(wire),
        Pacer(10**6, sleep=lambda s: None),
        drift=DriftSwitch(fixed("none")),
        chargebacks=f,
    )
    sent = [json.loads(line) for line in wire.getvalue().splitlines()]
    sent_ids = {m["transaction_id"] for m in sent}
    charged = {r["transaction_id"] for r in out.records}
    assert f.frauds > 0 and len(charged) == f.frauds == f.posted
    assert charged <= sent_ids  # only for transactions that were actually sent
    assert max(event_time(m) for m in sent) >= max(
        datetime.fromisoformat(r["known_at"]) for r in out.records
    )
