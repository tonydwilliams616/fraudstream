"""fraudstream.monitoring.drift - is live data still shaped like the training data?

What: build_baseline() records, for every feature, how the TRAINING rows were
      spread out: one band per value for features with only a few values
      (hour of day, shop type), ten equal-sized bands (deciles) for the rest
      (amounts, distances), plus a band for "missing". drift_report() puts a
      window of LIVE rows into exactly the same bands and measures, per
      feature, how far the shares have moved - the Population Stability Index.
Why:  data drift (the inputs changing) can be seen without labels, so it can be
      caught within the hour - long before chargebacks could show the model is
      getting worse.
Concepts:
  - PSI = sum over bands of (live share - training share) x ln(live / training).
    0 means identical. Rule of thumb used in banking and credit risk:
      < 0.1 stable   0.1 - 0.25 moderate shift   > 0.25 significant shift.
    Analogy: a bakery's usual mix is 50% small loaves, 30% medium, 20% large.
    PSI is one number for "how different is this week's mix from usual?".
  - The bands come from the TRAINING data and never move, so the same value
    always lands in the same band; live values outside the training range fall
    into the end bands, and a value training never saw (a new shop type) goes
    to an "other" band - itself a sign of drift.
  - An empty band gets a tiny floor share (0.0001), so ln() never sees zero.
  - ALARM features vs INFORMATION-only features. History features (how many
    purchases this card has made, is this a new shop for the card) depend on
    how long the stream has been running: the consumer's card history starts
    empty after every rebuild, so for hours they look "drifted" although the
    world hasn't changed - SKEW from warm-up, not drift. They're measured and
    reported, but only features that depend on the purchase alone can raise
    the alarm.
  - The baseline belongs to ONE model: it's built from that model's own
    training rows. After a retrain, live data is compared with the new
    model's training data - otherwise the alarm could never clear.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fraudstream.features import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION

BASELINE_SCHEMA_VERSION = "1"
BASELINE_FILE = "drift-baseline.json"  # next to train.csv in each pipeline run's data/

# Only clues worked out from the purchase itself (and fixed reference data).
ALARM_FEATURES = [
    "amount",
    "log_amount",
    "hour_of_day",
    "is_night",
    "is_online",
    "category_code",
    "merchant_risk",
    "distance_from_home_km",
]
# Depend on the card's history in the consumer, which restarts empty: reported only.
INFO_FEATURES = [f for f in FEATURE_COLUMNS if f not in ALARM_FEATURES]

MAX_VALUES = 25  # at most this many distinct values -> one band per value (24 hours fit)
DECILES = 10
FLOOR = 1e-4  # share given to an empty band
STABLE, SIGNIFICANT = 0.1, 0.25  # PSI rule-of-thumb lines


def _band_shares(spec: dict, values: pd.Series) -> np.ndarray:
    """Share of `values` in each of the spec's bands; the last band is "missing"."""
    values = pd.to_numeric(values, errors="coerce")
    n = len(values)
    present = values.dropna().to_numpy(dtype=float)
    if spec["kind"] == "values":
        known = np.asarray(spec["values"], dtype=float)
        counts = [int(np.sum(present == v)) for v in known]
        counts.append(int(np.sum(~np.isin(present, known))))  # "other": never seen in training
    else:
        edges = np.asarray(spec["edges"], dtype=float)
        # Band i holds (edges[i-1], edges[i]]; band 0 everything up to edges[0],
        # the last band everything above edges[-1].
        index = np.searchsorted(edges, present, side="left")
        counts = np.bincount(index, minlength=len(edges) + 1).tolist()
    counts.append(n - len(present))  # missing
    return np.asarray(counts, dtype=float) / n


def _spec_for(values: pd.Series) -> dict:
    present = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    distinct = np.unique(present)
    if len(distinct) <= MAX_VALUES:
        spec = {"kind": "values", "values": distinct.tolist()}
    else:
        inner = np.linspace(0, 1, DECILES + 1)[1:-1]
        spec = {"kind": "bands", "edges": np.unique(np.quantile(present, inner)).tolist()}
    spec["shares"] = [round(s, 6) for s in _band_shares(spec, values).tolist()]
    return spec


def build_baseline(train: pd.DataFrame, source: dict | None = None) -> dict:
    """How each feature was spread out in a model's training rows (JSON-ready)."""
    if len(train) == 0:
        raise ValueError("can't build a baseline from no rows")
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "rows": len(train),
        "source": source or {},
        "alarm_features": ALARM_FEATURES,
        "features": {name: _spec_for(train[name]) for name in FEATURE_COLUMNS},
    }


def psi(expected, actual) -> float:
    """Population Stability Index between two lists of band shares."""
    e = np.maximum(np.asarray(expected, dtype=float), FLOOR)
    a = np.maximum(np.asarray(actual, dtype=float), FLOOR)
    return float(np.sum((a - e) * np.log(a / e)))


def verdict(value: float) -> str:
    if value < STABLE:
        return "stable"
    return "moderate" if value <= SIGNIFICANT else "significant"


def drift_report(baseline: dict, live: pd.DataFrame) -> dict:
    """PSI per feature for a window of live rows, and the worst alarm feature."""
    if baseline.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError(
            f"baseline is for feature schema {baseline.get('feature_schema_version')}, "
            f"this code computes schema {FEATURE_SCHEMA_VERSION}"
        )
    if len(live) == 0:
        raise ValueError("no live rows to compare")
    scores = {
        name: round(psi(spec["shares"], _band_shares(spec, live[name])), 4)
        for name, spec in baseline["features"].items()
    }
    alarm = {k: v for k, v in scores.items() if k in baseline["alarm_features"]}
    worst = max(alarm, key=alarm.get)
    return {
        "rows": len(live),
        "psi": scores,
        "worst_alarm_feature": worst,
        "max_alarm_psi": alarm[worst],
        "verdict": verdict(alarm[worst]),
    }
