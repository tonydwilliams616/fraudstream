"""fraudstream.endpoint - talking to the SageMaker endpoint in its own format.

What: to_csv() turns feature rows into the request body the built-in XGBoost
      container reads; parse_scores() turns its reply back into numbers;
      score() sends rows in batches; compare() checks endpoint scores against
      scores computed locally from the same model file.
Why:  the consumer (step 8) and the parity check share one encoder, so the
      format proven correct here is exactly the one used live.
Concepts:
  - Request: CSV, one transaction per line, the features in FEATURE_COLUMNS
    order, no header. An unknown value (e.g. "time since last purchase" for a
    card's first purchase) is an EMPTY field, which XGBoost treats as missing -
    the same as NaN in training.
  - Numbers are written with 17 significant digits, enough to round-trip a
    float exactly, so the endpoint sees the same values the local model does.
  - PARITY: the same model given the same inputs must give the same scores,
    whether it runs on a laptop or behind the endpoint. If it doesn't, the
    results we evaluated (and the alert threshold) don't describe what's
    actually serving.
"""

from __future__ import annotations

import math
import re

import numpy as np

CONTENT_TYPE = "text/csv"
BATCH_ROWS = 500  # well inside the 6 MB request limit


def _cell(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return format(float(value), ".17g")


def to_csv(rows) -> str:
    """Feature rows (sequences of numbers, NaN/None = missing) as a CSV request body."""
    return "\n".join(",".join(_cell(v) for v in row) for row in rows) + "\n"


def parse_scores(body: bytes | str) -> np.ndarray:
    """Scores from the container's CSV reply (separated by commas and/or newlines)."""
    text = body.decode() if isinstance(body, bytes) else body
    return np.array([float(x) for x in re.split(r"[,\s]+", text.strip()) if x], dtype=float)


def score(runtime, endpoint: str, rows, batch_rows: int = BATCH_ROWS) -> np.ndarray:
    """Score rows on the endpoint, batch_rows per request."""
    rows = list(rows)
    out = []
    for start in range(0, len(rows), batch_rows):
        batch = rows[start : start + batch_rows]
        reply = runtime.invoke_endpoint(
            EndpointName=endpoint,
            ContentType=CONTENT_TYPE,
            Accept=CONTENT_TYPE,
            Body=to_csv(batch).encode(),
        )
        scores = parse_scores(reply["Body"].read())
        if len(scores) != len(batch):
            raise ValueError(f"sent {len(batch)} rows, got {len(scores)} scores back")
        out.append(scores)
    return np.concatenate(out) if out else np.array([])


def compare(local, remote, threshold: float, tolerance: float = 1e-6) -> dict:
    """How far endpoint scores are from local ones, and whether any alert decision differs."""
    local, remote = np.asarray(local, dtype=float), np.asarray(remote, dtype=float)
    diff = np.abs(local - remote)
    flips = int(np.sum((local >= threshold) != (remote >= threshold)))
    return {
        "rows": len(local),
        "max_abs_diff": float(diff.max()) if len(diff) else 0.0,
        "rows_over_tolerance": int(np.sum(diff > tolerance)),
        "alert_decisions_changed": flips,
        "alerts_local": int(np.sum(local >= threshold)),
        "match": bool(np.all(diff <= tolerance)) and flips == 0,
    }
