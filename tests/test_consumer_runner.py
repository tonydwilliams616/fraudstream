"""tests/test_consumer_runner.py - checks the consumer's loop: read, score, write, then commit.

What: a micro-batch produces one scored record per transaction and an alert
      only above the threshold, keyed by card; a checkpoint waits for Kafka to
      confirm, then writes the S3 file, and only then commits offsets; if
      anything wasn't confirmed it refuses to commit; a partition hand-over
      checkpoints first, except after a failure; unreadable messages are
      skipped; a promoted model is picked up and a model that breaks the
      feature contract stops scoring; the program waits for the endpoint at
      start-up; MSK consumer settings commit manually. Kafka and AWS are faked.
Why:  the order "confirm, archive, THEN commit" is what makes the consumer lose
      nothing when it crashes. These tests pin that order down.
"""

import io
import json
from dataclasses import replace

import pandas as pd
import pytest

from fraudstream.consumer.__main__ import Settings, wait_for_model
from fraudstream.consumer.archive import ArchiveBuffer
from fraudstream.consumer.model import EndpointNotReady, ModelContractError, ModelInfo
from fraudstream.consumer.runner import DeliveryError, Runner
from fraudstream.consumer.scoring import Scorer
from fraudstream.features import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, FeatureBuilder
from fraudstream.msk import consumer_config

MODEL = ModelInfo(
    package_arn="arn:aws:sagemaker:us-east-1:1:model-package/fs-fraud-detection/1",
    version=1,
    run_id="baseline-x",
    approval="Approved",
    threshold=0.9,
    feature_schema_version=FEATURE_SCHEMA_VERSION,
    features=tuple(FEATURE_COLUMNS),
)
CARDS = pd.DataFrame({"card_id": ["c1", "c2"], "home_lat": [51.5, 53.4], "home_lon": [-0.1, -2.2]})
SHOPS = pd.DataFrame({"merchant_id": ["m1"], "risk": [0.1]})


def tx(i, card="c1", amount=10.0):
    return {
        "schema_version": "1",
        "transaction_id": f"t{i}",
        "timestamp": f"2026-11-08T10:{i:02d}:00+00:00",
        "card_id": card,
        "merchant_id": "m1",
        "merchant_category": "grocery",
        "channel": "in_person",
        "merchant_lat": 51.5,
        "merchant_lon": -0.1,
        "amount": amount,
    }


class Msg:
    def __init__(self, value, partition=0, offset=0, ts=(1, 1_000_000), error=None):
        self._value, self._p, self._o, self._ts, self._err = value, partition, offset, ts, error

    def error(self):
        return self._err

    def value(self):
        return self._value if isinstance(self._value, bytes) else json.dumps(self._value).encode()

    def partition(self):
        return self._p

    def offset(self):
        return self._o

    def timestamp(self):
        return self._ts


class FakeConsumer:
    def __init__(self, batches, events):
        self.batches, self.events = list(batches), events

    def consume(self, num_messages, timeout):
        return self.batches.pop(0) if self.batches else []

    def commit(self, asynchronous):
        self.events.append("commit")


class FakeProducer:
    def __init__(self, events, unconfirmed=0):
        self.sent, self.events, self.unconfirmed = [], events, unconfirmed

    def produce(self, topic, key, value, on_delivery):
        self.sent.append((topic, key, json.loads(value)))

    def poll(self, _timeout):
        return 0

    def flush(self, _timeout):
        self.events.append("flush")
        return self.unconfirmed


class FakeS3:
    def __init__(self, events):
        self.events, self.keys = events, []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.events.append("s3")
        self.keys.append(Key)


class FakeRuntime:
    def invoke_endpoint(self, EndpointName, ContentType, Accept, Body):
        rows = Body.decode().strip().split("\n")
        return {
            "Body": io.BytesIO("\n".join(str(float(r.split(",")[0]) / 1000) for r in rows).encode())
        }


def runner(batches, unconfirmed=0, resolve=lambda: MODEL, clock=None):
    events = []
    clock = clock or [0.0]
    archive = ArchiveBuffer(FakeS3(events), "b", "c", max_age_s=60, clock=lambda: clock[0])
    scorer = Scorer(FeatureBuilder(CARDS, SHOPS), FakeRuntime(), "fs-fraud", MODEL)
    r = Runner(
        FakeConsumer(batches, events), FakeProducer(events, unconfirmed), scorer, archive,
        "scored", "fraud.alerts", resolve, refresh_s=300, clock=lambda: clock[0],
    )  # fmt: skip
    return r, events, clock


def test_batch_goes_to_scored_and_only_alerts_to_alerts():
    r, events, _ = runner([[Msg(tx(0, amount=5)), Msg(tx(1, card="c2", amount=950), offset=1)]])
    assert r.step() == 2
    topics = [(t, k) for t, k, _ in r.producer.sent]
    assert topics == [("scored", b"c1"), ("scored", b"c2"), ("fraud.alerts", b"c2")]
    assert r.stats["alerts"] == 1 and len(r.archive.records) == 2
    assert "commit" not in events  # not due yet: nothing committed


def test_checkpoint_confirms_then_archives_then_commits():
    r, events, clock = runner([[Msg(tx(0))], [Msg(tx(1), offset=1)]])
    r.step()
    clock[0] = 61  # archive is now a minute old
    r.step()
    assert events == ["flush", "s3", "commit"]
    assert r.stats["files"] == 1


def test_unconfirmed_records_mean_no_archive_and_no_commit():
    r, events, clock = runner([[Msg(tx(0))]], unconfirmed=3)
    r.step()
    with pytest.raises(DeliveryError):
        r.checkpoint()
    assert events == ["flush"]


def test_hand_over_checkpoints_first_unless_unhealthy():
    r, events, _ = runner([[Msg(tx(0))]])
    r.step()
    r.on_revoke(None, [])
    assert events == ["flush", "s3", "commit"]
    r2, events2, _ = runner([[Msg(tx(0))]])
    r2.step()
    r2.healthy = False
    r2.on_revoke(None, [])
    assert events2 == []  # after a failure, never commit


def test_unreadable_messages_and_kafka_errors_are_skipped():
    r, _, _ = runner([[Msg(b"not json"), Msg(None, error="UNKNOWN_TOPIC"), Msg(tx(0), offset=2)]])
    assert r.step() == 1
    assert r.stats["bad_messages"] == 1 and r.stats["consumed"] == 2


def test_latency_uses_kafka_timestamp_when_present():
    r, _, _ = runner([[Msg(tx(0), ts=(0, 0))]])  # TIMESTAMP_NOT_AVAILABLE
    r.step()
    assert r.archive.records[0]["latency_ms"] is None


def test_promoted_model_is_picked_up_with_its_threshold():
    v2 = replace(MODEL, version=2, threshold=0.5)
    r, _, clock = runner([[], [Msg(tx(0, amount=600))]], resolve=lambda: v2)
    clock[0] = 301
    r.step()
    assert r.scorer.model.version == 2
    r.step()
    assert r.archive.records[0]["alert"] and r.archive.records[0]["model_version"] == 2


def test_model_that_breaks_the_contract_stops_scoring():
    bad = replace(MODEL, version=2, feature_schema_version="999")
    r, _, clock = runner([[]], resolve=lambda: bad)
    clock[0] = 301
    with pytest.raises(ModelContractError):
        r.step()


def test_endpoint_updating_during_refresh_keeps_current_model():
    def updating():
        raise EndpointNotReady("Updating")

    r, _, clock = runner([[]], resolve=updating)
    clock[0] = 301
    r.step()
    assert r.scorer.model.version == 1


def test_startup_waits_for_the_endpoint():
    answers = iter([EndpointNotReady("Creating"), EndpointNotReady("Creating"), MODEL])
    sleeps = []

    def resolve():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert wait_for_model(resolve, sleep=sleeps.append) == MODEL
    assert sleeps == [2.0, 4.0]  # backs off
    assert wait_for_model(resolve, stop=lambda: True) is None


def test_settings_defaults_and_required_brokers():
    s = Settings.from_env({"BOOTSTRAP_SERVERS": "b-1:9098"})
    assert (s.input_topic, s.scored_topic, s.alerts_topic) == (
        "transactions",
        "scored",
        "fraud.alerts",
    )
    assert (s.group_id, s.endpoint, s.batch_size) == ("fs-scorer", "fs-fraud", 50)
    with pytest.raises(ValueError):
        Settings.from_env({})


def test_consumer_commits_manually_and_moves_partitions_gently():
    config = consumer_config("b-1:9098", "us-east-1", "fs-consumer", "fs-scorer")
    assert config["enable.auto.commit"] is False
    assert config["partition.assignment.strategy"] == "cooperative-sticky"
    assert config["group.id"] == "fs-scorer" and config["sasl.mechanisms"] == "OAUTHBEARER"
