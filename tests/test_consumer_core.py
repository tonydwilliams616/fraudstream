"""tests/test_consumer_core.py - checks the consumer's model lookup, scoring and archive.

What: the deployed model is found by following endpoint -> config -> model ->
      package, and refused if it isn't Approved or expects other features; a
      micro-batch is scored with one endpoint call, in order, with the right
      alerts, model version and latency; a failing endpoint is retried WITHOUT
      rebuilding features (which would double-count card history); messages in
      an unknown format are skipped; scored records are archived as gzipped
      JSON lines in dt=/hour= folders when big or old enough. AWS is faked.
Why:  these are the decisions that make a score trustworthy - right model,
      right features, right threshold, nothing lost, nothing double-counted.
"""

import gzip
import io
import json
from datetime import UTC, datetime

import pandas as pd
import pytest

from fraudstream.consumer.archive import ArchiveBuffer
from fraudstream.consumer.model import (
    EndpointNotReady,
    ModelContractError,
    check_contract,
    resolve_model,
)
from fraudstream.consumer.scoring import Incoming, Scorer, to_alert
from fraudstream.features import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, FeatureBuilder

PACKAGE = "arn:aws:sagemaker:us-east-1:1:model-package/fs-fraud-detection/3"


class FakeSageMaker:
    def __init__(self, status="InService", approval="Approved", features=None, schema=None):
        self.status = status
        self.props = {
            "run_id": "baseline-x",
            "alert_threshold": "0.9",
            "feature_schema_version": schema or FEATURE_SCHEMA_VERSION,
            "features": " ".join(features or FEATURE_COLUMNS),
        }
        self.approval = approval

    def describe_endpoint(self, EndpointName):
        if self.status is None:
            raise RuntimeError(f"Could not find endpoint {EndpointName}")
        return {"EndpointStatus": self.status, "EndpointConfigName": "cfg-1"}

    def describe_endpoint_config(self, EndpointConfigName):
        return {"ProductionVariants": [{"ModelName": "fs-fraud-v3"}]}

    def describe_model(self, ModelName):
        return {"PrimaryContainer": {"ModelPackageName": PACKAGE}}

    def describe_model_package(self, ModelPackageName):
        return {
            "ModelPackageVersion": 3,
            "ModelApprovalStatus": self.approval,
            "CustomerMetadataProperties": self.props,
        }


def test_model_is_resolved_from_what_the_endpoint_serves():
    model = resolve_model(FakeSageMaker(), "fs-fraud")
    assert (model.package_arn, model.version, model.threshold) == (PACKAGE, 3, 0.9)
    assert list(model.features) == FEATURE_COLUMNS
    check_contract(model)  # fits this code


@pytest.mark.parametrize("status", [None, "Creating", "Updating"])
def test_missing_or_busy_endpoint_means_wait(status):
    with pytest.raises(EndpointNotReady):
        resolve_model(FakeSageMaker(status=status), "fs-fraud")


@pytest.mark.parametrize(
    "fake",
    [
        FakeSageMaker(approval="PendingManualApproval"),
        FakeSageMaker(schema="999"),
        FakeSageMaker(features=list(reversed(FEATURE_COLUMNS))),
    ],
)
def test_model_that_does_not_fit_is_refused(fake):
    with pytest.raises(ModelContractError):
        check_contract(resolve_model(fake, "fs-fraud"))


# --- scoring --------------------------------------------------------------------
CARDS = pd.DataFrame({"card_id": ["c1", "c2"], "home_lat": [51.5, 53.4], "home_lon": [-0.1, -2.2]})
SHOPS = pd.DataFrame({"merchant_id": ["m1", "m2"], "risk": [0.1, 0.8]})


def message(i, card="c1", amount=10.0, schema="1"):
    return {
        "schema_version": schema,
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


class FakeRuntime:
    """Scores each row by its amount / 1000; can fail the first N calls."""

    def __init__(self, fail_first=0):
        self.calls = 0
        self.fail_first = fail_first

    def invoke_endpoint(self, EndpointName, ContentType, Accept, Body):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ConnectionError("endpoint unavailable")
        rows = Body.decode().strip().split("\n")
        return {
            "Body": io.BytesIO("\n".join(str(float(r.split(",")[0]) / 1000) for r in rows).encode())
        }


def scorer(runtime, retries=4):
    model = resolve_model(FakeSageMaker(), "fs-fraud")
    return Scorer(
        FeatureBuilder(CARDS, SHOPS), runtime, "fs-fraud", model,
        retries=retries, sleep=lambda s: None, now_ms=lambda: 1_000_500,
    )  # fmt: skip


def test_batch_is_scored_in_one_call_with_alerts_and_lineage():
    runtime = FakeRuntime()
    batch = [Incoming(message(i, amount=a), 0, 100 + i, 1_000_000) for i, a in enumerate([5, 950])]
    records = scorer(runtime).score_batch(batch)
    assert runtime.calls == 1
    assert [r["transaction_id"] for r in records] == ["t0", "t1"]
    assert [r["alert"] for r in records] == [False, True]  # 0.005 and 0.95 vs threshold 0.9
    first = records[0]
    assert first["model_version"] == 3 and first["threshold"] == 0.9
    assert first["latency_ms"] == 500 and first["kafka_offset"] == 100
    assert set(first["features"]) == set(FEATURE_COLUMNS)
    assert first["features"]["secs_since_last_txn"] is None  # first purchase: no NaN in JSON
    json.dumps(records)  # every record is JSON-ready


def test_endpoint_failures_are_retried_without_double_counting_history():
    runtime = FakeRuntime(fail_first=2)
    s = scorer(runtime)
    s.score_batch([Incoming(message(0), 0, 0, None)])
    nxt = s.score_batch([Incoming(message(1), 0, 1, None)])
    assert runtime.calls == 4  # 2 failures + 1 success, then 1
    assert nxt[0]["features"]["card_txn_count"] == 1  # the first purchase counted once


def test_endpoint_that_keeps_failing_raises():
    with pytest.raises(ConnectionError):
        scorer(FakeRuntime(fail_first=99), retries=2).score_batch(
            [Incoming(message(0), 0, 0, None)]
        )


def test_unknown_message_format_is_skipped_not_scored():
    s = scorer(FakeRuntime())
    records = s.score_batch([Incoming(message(0, schema="2"), 0, 0, None)])
    assert records == [] and s.skipped == 1


def test_alert_message_is_the_short_form():
    record = scorer(FakeRuntime()).score_batch([Incoming(message(0, amount=990), 0, 0, None)])[0]
    alert = to_alert(record)
    assert alert["score"] == record["score"] and "features" not in alert


# --- archive --------------------------------------------------------------------
class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body


def test_archive_writes_when_old_or_big_enough():
    clock = [0.0]
    buf = ArchiveBuffer(FakeS3(), "b", "c1", max_records=3, max_age_s=60, clock=lambda: clock[0])
    assert not buf.due() and buf.flush() is None  # nothing to write
    buf.add([{"transaction_id": "t1"}])
    assert not buf.due()
    clock[0] = 61
    assert buf.due()  # old enough
    buf.flush()
    buf.add([{"transaction_id": f"t{i}"} for i in range(3)])
    assert buf.due()  # big enough


def test_archive_file_is_gzipped_json_lines_in_hour_folders():
    s3 = FakeS3()
    when = datetime(2026, 11, 8, 14, 5, 6, tzinfo=UTC)
    buf = ArchiveBuffer(s3, "b", "task-abc", now=lambda: when)
    buf.add([{"transaction_id": "t1", "score": 0.5}, {"transaction_id": "t2", "score": 0.9}])
    key = buf.flush()
    assert key.startswith("scored/dt=2026-11-08/hour=14/20261108T140506") and key.endswith(
        "-task-abc.jsonl.gz"
    )
    lines = gzip.decompress(s3.objects[key]).decode().splitlines()
    assert [json.loads(line)["transaction_id"] for line in lines] == ["t1", "t2"]
    assert buf.records == [] and not buf.due()
