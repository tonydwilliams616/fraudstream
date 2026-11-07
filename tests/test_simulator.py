"""tests/test_simulator.py - checks the simulator's messages, pacing and settings.

What: messages carry exactly the agreed fields and never the fraud label; the
      stream is in time order, reproducible and has unique IDs across blocks;
      clues worked out from the messages - after a JSON round trip, the way
      the consumer will receive them - equal clues from the same rows as a
      table; the pacer keeps a steady rate; settings come from the
      environment; a dry run prints the expected number of messages. No
      Kafka or AWS needed.
Why:  the message format is a contract between two separately deployed
      programs (simulator and consumer). These tests are the contract: if
      either side changes it, CI fails before anything is deployed.
"""

import io
import json
from itertools import islice

import pandas as pd
import pytest

from fraudstream.features import FEATURE_COLUMNS, FeatureBuilder
from fraudstream.generator.dataset import DatasetParams, generate_dataset
from fraudstream.simulator.__main__ import PrintSink, Settings, run
from fraudstream.simulator.events import MESSAGE_FIELDS, Pacer, stream_transactions, to_message
from fraudstream.simulator.kafka import producer_config
from fraudstream.storage import save_version


@pytest.fixture(scope="module")
def dataset():
    return generate_dataset(DatasetParams(seed=3, n_cards=400, n_merchants=150, days=5))


def _take(dataset, n, start="2026-11-07", seed=1, block_days=1):
    stream = stream_transactions(
        dataset.cards, dataset.merchants, start=start, seed=seed, block_days=block_days
    )
    return list(islice(stream, n))


def test_message_has_the_agreed_fields_and_no_labels(dataset):
    msg = _take(dataset, 1)[0]
    assert list(msg) == ["schema_version", *MESSAGE_FIELDS]
    assert "is_fraud" not in msg and "fraud_type" not in msg
    assert isinstance(msg["timestamp"], str) and msg["timestamp"].endswith("+00:00")
    json.dumps(msg)  # must be plain JSON


def test_missing_values_become_null():
    row = {f: "x" for f in MESSAGE_FIELDS} | {
        "timestamp": pd.Timestamp("2026-11-07 10:00", tz="UTC"),
        "merchant_lat": float("nan"),
        "merchant_lon": float("nan"),
        "amount": 12.5,
    }
    msg = to_message(row)
    assert msg["merchant_lat"] is None and msg["merchant_lon"] is None
    assert msg["amount"] == 12.5


def test_stream_is_time_ordered_across_blocks_with_unique_ids(dataset):
    msgs = _take(dataset, 1500)  # 400 cards x ~1.7 purchases/day: crosses into day 2+
    times = pd.to_datetime([m["timestamp"] for m in msgs])
    assert times.is_monotonic_increasing
    assert times.normalize().nunique() >= 2  # really did cross a block boundary
    assert len({m["transaction_id"] for m in msgs}) == len(msgs)
    assert times[0].date() == pd.Timestamp("2026-11-07").date()  # real calendar day, not shifted


def test_stream_is_reproducible_from_start_and_seed(dataset):
    assert _take(dataset, 200, seed=7) == _take(dataset, 200, seed=7)
    assert _take(dataset, 200, seed=7) != _take(dataset, 200, seed=8)


def test_clues_from_streamed_messages_match_clues_from_a_table(dataset):
    # The consumer receives JSON one message at a time; training-style code
    # sees a table. Both must produce identical clues (no training-serving skew).
    msgs = _take(dataset, 1000)
    live = FeatureBuilder(dataset.cards, dataset.merchants)
    streamed = pd.DataFrame([live.features_for(json.loads(json.dumps(m))) for m in msgs])
    table = pd.DataFrame(msgs).assign(timestamp=lambda d: pd.to_datetime(d["timestamp"]))
    batch = FeatureBuilder(dataset.cards, dataset.merchants).build(table)
    pd.testing.assert_frame_equal(
        streamed[FEATURE_COLUMNS].astype(float), batch[FEATURE_COLUMNS].astype(float)
    )


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 6))
        self.now += seconds


def test_pacer_keeps_a_steady_rate():
    clock = FakeClock()
    pacer = Pacer(10, clock=clock, sleep=clock.sleep)
    for _ in range(5):
        pacer.wait()
    assert clock.slept == [0.1, 0.1, 0.1, 0.1]  # first message goes straight away
    assert clock.now == pytest.approx(100.4)


def test_pacer_starts_afresh_after_falling_behind():
    clock = FakeClock()
    pacer = Pacer(10, clock=clock, sleep=clock.sleep)
    pacer.wait()
    clock.now += 5  # Kafka was slow for 5 seconds
    pacer.wait()
    pacer.wait()
    assert clock.slept == [0.1]  # no burst of 50 catch-up messages


def test_settings_from_environment():
    s = Settings.from_env({"BOOTSTRAP_SERVERS": "b-1:9098", "START_DATE": "2026-11-07"})
    assert s.topic == "transactions" and s.rate == 10 and s.max_messages == 0
    assert s.seed == pd.Timestamp("2026-11-07").date().toordinal()  # differs every day
    assert Settings.from_env({"BOOTSTRAP_SERVERS": "b", "SEED": "5"}).seed == 5
    with pytest.raises(ValueError, match="BOOTSTRAP_SERVERS"):
        Settings.from_env({})
    assert Settings.from_env({"DRY_RUN": "1"}).dry_run


def test_dry_run_prints_the_requested_number_of_messages(dataset, tmp_path):
    save_version(dataset, tmp_path, "v1")
    settings = Settings.from_env(
        {
            "DRY_RUN": "1",
            "DATASET_URI": str(tmp_path / "transactions" / "v1"),
            "START_DATE": "2026-11-07",
            "MAX_MESSAGES": "50",
            "RATE": "1000",
        }
    )
    out = io.StringIO()
    sent = run(settings, PrintSink(out), Pacer(1000, sleep=lambda s: None))
    lines = out.getvalue().splitlines()
    assert sent == len(lines) == 50
    assert json.loads(lines[0])["schema_version"] == "1"


def test_run_stops_when_asked(dataset, tmp_path):
    save_version(dataset, tmp_path, "v1")
    settings = Settings.from_env(
        {"DRY_RUN": "1", "DATASET_URI": str(tmp_path / "transactions" / "v1")}
    )
    calls = iter([False] * 10 + [True])
    sent = run(
        settings,
        PrintSink(io.StringIO()),
        Pacer(1000, sleep=lambda s: None),
        stop=lambda: next(calls),
    )
    assert sent == 10


def test_producer_settings_guarantee_no_loss_and_no_duplicates():
    config = producer_config("b-1:9098", "us-east-1", "fs-simulator")
    assert config["acks"] == "all" and config["enable.idempotence"] is True
    assert config["security.protocol"] == "SASL_SSL" and config["sasl.mechanisms"] == "OAUTHBEARER"
    assert callable(config["oauth_cb"])
