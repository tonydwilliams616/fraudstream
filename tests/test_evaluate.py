"""tests/test_evaluate.py - checks the evaluation maths on small hand-made examples.

What: the alert threshold flags the intended share of transactions; precision,
      recall and the confusion matrix match counts worked out by hand; PR-AUC is
      1.0 for a perfect ranking and about the fraud rate for random scores; the
      confidence range brackets the estimate; recall is broken down by style.
Why:  the evaluation report is what the promotion gate (Phase 3) will trust to
      decide whether a model goes live. A bug here would wave bad models
      through - or block good ones - without anything crashing.
"""

import numpy as np
import pandas as pd
import pytest

from fraudstream.training.data import TrainingData
from fraudstream.training.evaluate import (
    at_threshold,
    bootstrap_pr_auc,
    evaluate,
    pr_auc,
    threshold_for_budget,
)


def test_threshold_flags_the_budgeted_share():
    scores = np.linspace(0, 1, 10_000)
    threshold = threshold_for_budget(scores, 0.01)
    assert (scores >= threshold).sum() == pytest.approx(100, abs=1)


def test_counts_at_threshold_by_hand():
    labels = [1, 1, 1, 0, 0, 0, 0, 0]
    scores = [0.9, 0.8, 0.1, 0.7, 0.2, 0.2, 0.1, 0.0]
    r = at_threshold(labels, scores, 0.5)  # alerts on 0.9, 0.8, 0.7
    assert r["confusion_matrix"] == {"true_pos": 2, "false_pos": 1, "false_neg": 1, "true_neg": 4}
    assert r["alerts"] == 3
    assert r["precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert r["recall"] == pytest.approx(2 / 3, abs=1e-4)


def test_pr_auc_perfect_and_random():
    rng = np.random.default_rng(0)
    labels = (rng.random(50_000) < 0.01).astype(int)
    assert pr_auc(labels, labels.astype(float)) == 1.0
    assert pr_auc(labels, rng.random(len(labels))) == pytest.approx(labels.mean(), abs=0.005)
    assert pr_auc(np.zeros(10), np.zeros(10)) is None  # no fraud: undefined, not a crash


def test_confidence_range_brackets_the_estimate():
    rng = np.random.default_rng(1)
    labels = (rng.random(20_000) < 0.02).astype(int)
    scores = labels * 0.5 + rng.random(len(labels))  # decent but imperfect
    low, high = bootstrap_pr_auc(labels, scores, rounds=200)
    assert low < pr_auc(labels, scores) < high


def _toy_data():
    """Two days of validation and two of test; fraud scores high except one missed ATO."""
    rng = np.random.default_rng(2)

    def part(days):
        n = 2000
        df = pd.DataFrame(
            {"day": np.repeat(days, n // len(days)), "is_fraud": 0, "fraud_type": None}
        )
        df.loc[:9, ["is_fraud", "fraud_type"]] = [1, "card_testing"]
        df.loc[10:11, ["is_fraud", "fraud_type"]] = [1, "account_takeover"]
        scores = rng.random(n) * 0.5
        scores[:11] = 0.99  # every card-testing row and one takeover row score high
        scores[11] = 0.0  # the other takeover row is missed
        return df, scores

    (valid, v_scores), (test, t_scores) = part([8, 9]), part([10, 11])
    data = TrainingData(train=valid.iloc[:0], valid=valid, test=test, info={})
    return data, v_scores, t_scores


def test_report_uses_validation_for_threshold_and_breaks_down_by_style():
    data, v_scores, t_scores = _toy_data()
    report = evaluate(data, v_scores, t_scores, alert_rate=0.01)
    assert report["alert_budget"]["threshold"] == pytest.approx(
        threshold_for_budget(v_scores, 0.01), abs=1e-6
    )
    test = report["test"]
    assert test["alerts_per_day"] == pytest.approx(test["alerts"] / 2)
    assert test["recall_by_fraud_type"] == {
        "account_takeover": {"fraud_rows": 2, "recall": 0.5},
        "card_testing": {"fraud_rows": 10, "recall": 1.0},
    }
    assert test["baseline_pr_auc"] == pytest.approx(12 / 2000, abs=1e-4)
    assert "pr_auc_95ci" in test and "pr_auc_95ci" not in report["valid"]
