"""tests/test_endpoint.py - checks the endpoint request/reply format and the parity comparison.

What: missing values become empty CSV fields, numbers survive the round trip
      exactly, both reply layouts the container may use are parsed, rows are
      sent in batches with a row-count check, and compare() fails on any score
      drift or changed alert decision. The endpoint is faked.
Why:  the consumer will use this exact encoder live. A silent format slip (a
      NaN written as "nan", rounded numbers, a dropped row) would change scores
      without any error - the parity check exists to catch that, so its own
      parts have to be right.
"""

import io
import math

import numpy as np
import pytest

from fraudstream.endpoint import compare, parse_scores, score, to_csv


def test_missing_values_are_empty_fields():
    assert to_csv([[1.0, float("nan"), None, 0]]) == "1,,,0\n"


def test_numbers_round_trip_exactly():
    values = [0.1, 1 / 3, 123456.789012345, 1e-12, math.pi]
    parsed = [float(x) for x in to_csv([values]).strip().split(",")]
    assert parsed == values


def test_replies_in_either_layout_are_parsed():
    assert parse_scores(b"0.1,0.2,0.3").tolist() == [0.1, 0.2, 0.3]
    assert parse_scores("0.1\n0.2\n0.3\n").tolist() == [0.1, 0.2, 0.3]


class FakeRuntime:
    """Scores each row as its first value; records the batch sizes it was sent."""

    def __init__(self, drop_one=False):
        self.batches = []
        self.drop_one = drop_one

    def invoke_endpoint(self, EndpointName, ContentType, Accept, Body):
        assert ContentType == Accept == "text/csv"
        lines = Body.decode().strip().split("\n")
        self.batches.append(len(lines))
        scores = [line.split(",")[0] for line in lines]
        if self.drop_one:
            scores = scores[:-1]
        return {"Body": io.BytesIO(",".join(scores).encode())}


def test_rows_are_sent_in_batches_and_come_back_in_order():
    runtime = FakeRuntime()
    rows = [[i / 1000, 1.0] for i in range(1200)]
    scores = score(runtime, "fs-fraud", rows, batch_rows=500)
    assert runtime.batches == [500, 500, 200]
    assert scores.tolist() == [r[0] for r in rows]


def test_a_short_reply_is_an_error_not_a_misaligned_result():
    with pytest.raises(ValueError, match="got 4"):
        score(FakeRuntime(drop_one=True), "fs-fraud", [[0.1]] * 5)


def test_compare_passes_identical_scores():
    local = np.array([0.1, 0.95, 0.5])
    assert compare(local, local.copy(), threshold=0.9)["match"]


def test_compare_fails_on_drift_and_on_a_changed_decision():
    local = np.array([0.1, 0.9000001, 0.5])
    drifted = compare(local, local + 1e-4, threshold=0.99)
    assert not drifted["match"] and drifted["rows_over_tolerance"] == 3
    flipped = compare(local, np.array([0.1, 0.8999999, 0.5]), threshold=0.9, tolerance=1e-3)
    assert flipped["alert_decisions_changed"] == 1 and not flipped["match"]
