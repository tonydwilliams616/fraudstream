"""fraudstream.training.evaluate - judges a model the way a fraud team would.

What: evaluate() takes the model's scores for the validation and test days and
      returns a report:
        - PR-AUC (the headline) with a 95% confidence range, and ROC-AUC
        - the alert THRESHOLD, chosen on validation so that about ALERT_RATE of
          transactions get flagged (the fraud team's review capacity)
        - at that threshold on the test days: alerts per day, precision,
          recall, the confusion matrix, and recall for each fraud style
Why:  "how good is the model?" only means something as "how much fraud do we
      catch with the alerts we can afford to review?". That's what this
      answers. The test days are used exactly once, here, and never to choose
      anything.
Concepts:
  - Precision: of the alerts raised, the share that were really fraud.
    Recall: of all the fraud, the share we raised an alert for.
  - PR-AUC: precision averaged over every possible threshold - one number for
    the whole trade-off. Random guessing scores about the fraud rate (~0.005),
    so it's honest for rare events. ROC-AUC is reported for comparison; it
    looks flattering on imbalanced data because it rewards correctly ignoring
    the huge pile of easy normal transactions.
  - Bootstrap confidence range: the test days have only ~300 fraud rows, so one
    PR-AUC could be lucky. We re-draw the test set (with replacement) 1,000
    times, score each, and report the middle 95% of results.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from fraudstream.training.data import LABEL, TrainingData

ALERT_RATE = 0.005  # the fraud team can review 0.5% of transactions (~84 a day on v1)
BOOTSTRAP_ROUNDS = 1000


def threshold_for_budget(scores, alert_rate: float = ALERT_RATE) -> float:
    """The score above which roughly `alert_rate` of these transactions fall."""
    if not 0 < alert_rate < 1:
        raise ValueError("alert_rate must be between 0 and 1")
    return float(np.quantile(np.asarray(scores), 1 - alert_rate))


def at_threshold(labels, scores, threshold: float) -> dict:
    """Confusion matrix, precision and recall when alerting on score >= threshold."""
    y = np.asarray(labels).astype(bool)
    alert = np.asarray(scores) >= threshold
    tp = int((alert & y).sum())  # fraud we alerted on
    fp = int((alert & ~y).sum())  # false alarms
    fn = int((~alert & y).sum())  # fraud we missed
    tn = int((~alert & ~y).sum())  # normal, left alone
    return {
        "alerts": tp + fp,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "confusion_matrix": {"true_pos": tp, "false_pos": fp, "false_neg": fn, "true_neg": tn},
    }


def pr_auc(labels, scores) -> float | None:
    y = np.asarray(labels)
    return round(float(average_precision_score(y, scores)), 4) if y.any() else None


def roc_auc(labels, scores) -> float | None:
    y = np.asarray(labels)
    return round(float(roc_auc_score(y, scores)), 4) if 0 < y.sum() < len(y) else None


def bootstrap_pr_auc(labels, scores, rounds: int = BOOTSTRAP_ROUNDS, seed: int = 0) -> list:
    """Middle 95% of PR-AUC over `rounds` resamples of the same rows (with replacement)."""
    y, s = np.asarray(labels), np.asarray(scores)
    rng = np.random.default_rng(seed)
    results = []
    for _ in range(rounds):
        idx = rng.integers(0, len(y), len(y))
        if y[idx].any():
            results.append(average_precision_score(y[idx], s[idx]))
    low, high = np.percentile(results, [2.5, 97.5])
    return [round(float(low), 4), round(float(high), 4)]


def evaluate(data: TrainingData, valid_scores, test_scores, alert_rate: float = ALERT_RATE) -> dict:
    """The full report: threshold from validation, honest numbers from test."""
    threshold = threshold_for_budget(valid_scores, alert_rate)
    report = {"alert_budget": {"alert_rate": alert_rate, "threshold": round(threshold, 6)}}
    for name, part, scores in (
        ("valid", data.valid, valid_scores),
        ("test", data.test, test_scores),
    ):
        y = part[LABEL].to_numpy()
        days = part["day"].nunique()
        result = {
            "rows": len(part),
            "fraud_rows": int(y.sum()),
            "baseline_pr_auc": round(float(y.mean()), 4),  # what random guessing scores
            "pr_auc": pr_auc(y, scores),
            "roc_auc": roc_auc(y, scores),
            **at_threshold(y, scores, threshold),
        }
        result["alerts_per_day"] = round(result["alerts"] / days, 1)
        if name == "test":
            result["pr_auc_95ci"] = bootstrap_pr_auc(y, scores)
            result["recall_by_fraud_type"] = recall_by_fraud_type(part, scores, threshold)
        report[name] = result
    return report


def recall_by_fraud_type(part: pd.DataFrame, scores, threshold: float) -> dict:
    """Share of each fraud style that scored above the threshold (and how many there were)."""
    alerted = np.asarray(scores) >= threshold
    out = {}
    for style, rows in part[part[LABEL] == 1].groupby("fraud_type").groups.items():
        hits = alerted[part.index.get_indexer(rows)]
        out[style] = {"fraud_rows": len(hits), "recall": round(float(hits.mean()), 4)}
    return out
