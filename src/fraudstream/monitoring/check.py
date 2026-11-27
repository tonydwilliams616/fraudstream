"""fraudstream.monitoring.check - the hourly drift check (the inspector).

What: run_check() reads one clock hour of the scored archive
      (s3://<scored bucket>/scored/dt=<date>/hour=<hh>/), groups the records by
      the model that scored them (model_run_id), loads THAT model's drift
      baseline from its pipeline run, measures PSI per feature
      (drift.drift_report) and publishes the results to CloudWatch:
        namespace fraudstream/drift
          PSI          {Feature, ModelVersion}  one per feature and model
          MaxAlarmPSI  (no dimensions)          worst alarm feature, any model
          RowsChecked  (no dimensions)          how many transactions were compared
      A CloudWatch alarm on MaxAlarmPSI > 0.25 (35-apps) is the drift bell.
Why:  data drift needs no labels, so it can be caught within the hour. The job
      only READS (archive, baselines) and publishes numbers; it never changes
      the model - deciding what to do is the next step's job (6f, retrain).
Rules:
  - The window is the PREVIOUS full clock hour: the job runs at hh:05, when
    every archive file for that hour has been written (one a minute).
  - Fewer rows than min_rows (default 20,000, about one simulated day at 10 a
    second): no PSI is published, only RowsChecked. A short window doesn't
    cover every simulated hour of the day, so hour_of_day would look drifted -
    5,000 rows of unchanged data scored 0.13.
  - Duplicates (the archive is at-least-once) are dropped by transaction_id.
  - A model with no baseline is never treated as "no drift": it's logged as an
    error and the job exits 1, so the failed task shows up in ECS.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import UTC, datetime, timedelta

import pandas as pd

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.monitoring.drift import BASELINE_FILE, drift_report

log = logging.getLogger("fraudstream.monitoring.check")

NAMESPACE = "fraudstream/drift"
MIN_ROWS = 20_000
PIPELINE_RUNS = "models/fraud-pipeline/runs"  # in the artefacts bucket


def previous_hour(now: datetime) -> datetime:
    """Start of the last complete clock hour before `now` (UTC)."""
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)


def baseline_key(run_id: str) -> str:
    return f"{PIPELINE_RUNS}/{run_id}/data/{BASELINE_FILE}"


def _keys(s3, bucket: str, prefix: str):
    """Every object key under a prefix, page by page."""
    token = None
    while True:
        request = {"Bucket": bucket, "Prefix": prefix}
        if token:
            request["ContinuationToken"] = token
        page = s3.list_objects_v2(**request)
        for item in page.get("Contents", []):
            yield item["Key"]
        if not page.get("IsTruncated"):
            return
        token = page["NextContinuationToken"]


def read_window(s3, bucket: str, hour: datetime, prefix: str = "scored") -> pd.DataFrame:
    """One clock hour of archived records: the features plus who scored them."""
    rows = []
    for key in _keys(s3, bucket, f"{prefix}/dt={hour:%Y-%m-%d}/hour={hour:%H}/"):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        for line in gzip.decompress(body).decode().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            rows.append(
                {
                    "transaction_id": record["transaction_id"],
                    "model_run_id": record["model_run_id"],
                    "model_version": str(record["model_version"]),
                    **{name: record["features"].get(name) for name in FEATURE_COLUMNS},
                }
            )
    columns = ["transaction_id", "model_run_id", "model_version", *FEATURE_COLUMNS]
    frame = pd.DataFrame(rows, columns=columns)
    return frame.drop_duplicates("transaction_id").reset_index(drop=True)


def load_baseline(s3, bucket: str, run_id: str) -> dict | None:
    try:
        body = s3.get_object(Bucket=bucket, Key=baseline_key(run_id))["Body"].read()
    except Exception as err:  # only "not there" is expected; anything else is re-raised
        code = getattr(err, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "AccessDenied"):
            return None
        raise
    return json.loads(body)


def _metric(name: str, value: float, when: datetime, **dimensions) -> dict:
    datum = {"MetricName": name, "Value": float(value), "Timestamp": when, "Unit": "None"}
    if dimensions:
        datum["Dimensions"] = [{"Name": k, "Value": str(v)} for k, v in dimensions.items()]
    return datum


def run_check(
    s3,
    cloudwatch,
    scored_bucket: str,
    artefacts_bucket: str,
    hour: datetime,
    min_rows: int = MIN_ROWS,
    namespace: str = NAMESPACE,
    now: datetime | None = None,
) -> dict:
    """Compare one hour of live data with each model's baseline; publish the results."""
    now = now or datetime.now(UTC)
    window = read_window(s3, scored_bucket, hour)
    summary = {
        "hour": hour.isoformat(),
        "rows": len(window),
        "models": {},
        "missing_baselines": [],
        "max_alarm_psi": None,
    }
    metrics = [_metric("RowsChecked", len(window), now)]

    for (run_id, version), rows in window.groupby(["model_run_id", "model_version"]):
        if len(rows) < min_rows:
            log.info(
                "model v%s: %s rows, fewer than %s - not compared", version, len(rows), min_rows
            )
            continue
        baseline = load_baseline(s3, artefacts_bucket, run_id)
        if baseline is None:
            log.error("model v%s: no drift baseline at %s", version, baseline_key(run_id))
            summary["missing_baselines"].append(run_id)
            continue
        report = drift_report(baseline, rows)
        summary["models"][version] = report
        metrics += [
            _metric("PSI", value, now, Feature=feature, ModelVersion=version)
            for feature, value in report["psi"].items()
        ]
        log.info(
            "model v%s: %s rows, worst alarm feature %s PSI %.3f (%s)",
            version, report["rows"], report["worst_alarm_feature"],
            report["max_alarm_psi"], report["verdict"],
        )  # fmt: skip

    if summary["models"]:
        summary["max_alarm_psi"] = max(r["max_alarm_psi"] for r in summary["models"].values())
        metrics.append(_metric("MaxAlarmPSI", summary["max_alarm_psi"], now))
    for start in range(0, len(metrics), 20):  # small batches, well inside the API's limits
        cloudwatch.put_metric_data(Namespace=namespace, MetricData=metrics[start : start + 20])
    return summary
