"""Tests for fraudstream.monitoring - the drift baseline and PSI.

What: identical data scores ~0; inflated amounts score "significant" on the
      amount features and nothing else; a share moving into "missing" or into
      a value training never saw is caught; an empty band can't produce an
      infinite PSI; history features never raise the alarm; a baseline survives
      a JSON round trip unchanged; the command line writes a baseline file.
Why:  this number decides whether a retrain starts. A detector that never fires
      - or fires on every restart - would make the whole loop pointless.
"""

import json

import numpy as np
import pandas as pd
import pytest

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.monitoring.__main__ import main
from fraudstream.monitoring.drift import (
    ALARM_FEATURES,
    INFO_FEATURES,
    build_baseline,
    drift_report,
    psi,
    verdict,
)


def rows(n, seed, amount_factor=1.0, online_share=0.3, new_category=False):
    """Feature rows shaped roughly like fraudstream's, with knobs for drift."""
    rng = np.random.default_rng(seed)
    amount = rng.lognormal(3.0, 0.8, n) * amount_factor
    online = (rng.random(n) < online_share).astype(int)
    hour = rng.integers(0, 24, n)
    category = rng.integers(0, 12, n)
    if new_category:
        category[: n // 3] = 99  # a shop type training never saw
    return pd.DataFrame(
        {
            "amount": amount,
            "log_amount": np.log1p(amount),
            "hour_of_day": hour,
            "is_night": (hour < 6).astype(int),
            "is_online": online,
            "category_code": category,
            "merchant_risk": category / 20,
            "distance_from_home_km": np.where(online == 1, np.nan, rng.exponential(8, n)),
            "card_txn_count": rng.integers(0, 400, n),
            "txn_count_10m": rng.poisson(0.2, n),
            "txn_count_1h": rng.poisson(0.8, n),
            "secs_since_last_txn": rng.exponential(30000, n),
            "amount_vs_card_mean": rng.lognormal(0, 0.5, n),
            "is_new_merchant": (rng.random(n) < 0.7).astype(int),
        }
    )[FEATURE_COLUMNS]


@pytest.fixture(scope="module")
def baseline():
    return build_baseline(rows(50_000, seed=1))


def test_the_same_world_scores_stable(baseline):
    report = drift_report(baseline, rows(20_000, seed=2))
    assert all(v < 0.02 for v in report["psi"].values())
    assert report["verdict"] == "stable"


def test_inflated_amounts_are_significant_on_amount_features_only(baseline):
    report = drift_report(baseline, rows(20_000, seed=2, amount_factor=2.0))
    assert report["psi"]["amount"] > 0.25 and report["psi"]["log_amount"] > 0.25
    assert report["worst_alarm_feature"] in ("amount", "log_amount")
    assert report["verdict"] == "significant"
    assert report["psi"]["hour_of_day"] < 0.02 and report["psi"]["is_online"] < 0.02


def test_a_shift_online_moves_missing_distances_too(baseline):
    report = drift_report(baseline, rows(20_000, seed=2, online_share=0.6))
    assert report["psi"]["is_online"] > 0.25
    assert report["psi"]["distance_from_home_km"] > 0.25  # more "missing" (online has no distance)


def test_a_value_training_never_saw_counts_as_drift(baseline):
    report = drift_report(baseline, rows(20_000, seed=2, new_category=True))
    assert report["psi"]["category_code"] > 0.25


def test_history_features_are_reported_but_never_raise_the_alarm(baseline):
    live = rows(20_000, seed=2)
    live["card_txn_count"] = 0  # a fresh consumer: every card's history is empty
    report = drift_report(baseline, live)
    assert report["psi"]["card_txn_count"] > 1
    assert report["verdict"] == "stable"
    assert set(ALARM_FEATURES).isdisjoint(INFO_FEATURES)
    assert set(ALARM_FEATURES) | set(INFO_FEATURES) == set(FEATURE_COLUMNS)


def test_psi_basics():
    assert psi([0.5, 0.5], [0.5, 0.5]) == 0
    assert psi([0.5, 0.3, 0.2], [0.2, 0.3, 0.5]) > 0.25  # the bakery example
    assert np.isfinite(psi([1.0, 0.0], [0.0, 1.0]))  # empty bands get a floor, never ln(0)
    assert [verdict(v) for v in (0.05, 0.2, 0.3)] == ["stable", "moderate", "significant"]


def test_baseline_survives_json_and_rejects_other_feature_schemas(baseline):
    live = rows(5_000, seed=3, amount_factor=2.0)
    reloaded = json.loads(json.dumps(baseline))
    assert drift_report(reloaded, live) == drift_report(baseline, live)
    with pytest.raises(ValueError, match="feature schema"):
        drift_report({**baseline, "feature_schema_version": "0"}, live)
    with pytest.raises(ValueError):
        build_baseline(rows(10, seed=1).iloc[0:0])


def test_bands_come_from_training_and_hour_gets_one_band_per_value(baseline):
    hour = baseline["features"]["hour_of_day"]
    assert hour["kind"] == "values" and len(hour["values"]) == 24
    amount = baseline["features"]["amount"]
    assert amount["kind"] == "bands" and len(amount["edges"]) == 9  # deciles: 10 bands
    assert sum(amount["shares"]) == pytest.approx(1, abs=1e-4)


def test_command_line_writes_a_baseline(tmp_path):
    train = rows(2_000, seed=4)
    train.insert(0, "transaction_id", [f"T{i}" for i in range(len(train))])
    train["is_fraud"] = 0
    train.to_csv(tmp_path / "train.csv", index=False)
    out = tmp_path / "run" / "drift-baseline.json"
    assert main(["baseline", "--train-csv", str(tmp_path / "train.csv"), "--out", str(out)]) == 0
    saved = json.loads(out.read_text())
    assert saved["rows"] == 2_000 and set(saved["features"]) == set(FEATURE_COLUMNS)
