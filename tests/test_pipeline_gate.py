"""tests/test_pipeline_gate.py - checks the promotion rule (champion vs challenger).

What: the first model needs only to clear the floor; a challenger within the
      tolerances of the champion passes; one meaningfully worse at ranking fraud
      (PR-AUC) or at catching it (recall) fails; every check is reported with its
      value and requirement, so a failed run explains itself.
Why:  the gate decides which models a person is even asked to review. A wrong
      rule either lets worse models through or rejects good ones at random.
"""

from fraudstream.pipeline.gate import decide

CHAMPION = {"pr_auc": 0.795, "recall": 0.72}


def test_first_model_needs_only_the_floor():
    assert decide({"pr_auc": 0.65, "recall": 0.1}, None)["passed"]
    result = decide({"pr_auc": 0.55, "recall": 0.9}, None)
    assert not result["passed"] and [c["check"] for c in result["checks"]] == ["pr_auc above floor"]


def test_challenger_as_good_within_the_noise_passes():
    assert decide({"pr_auc": 0.78, "recall": 0.68}, CHAMPION)["passed"]  # -0.015, -0.04


def test_meaningfully_worse_ranking_fails():
    result = decide({"pr_auc": 0.76, "recall": 0.75}, CHAMPION)
    failed = [c for c in result["checks"] if not c["passed"]]
    assert not result["passed"]
    assert failed == [
        {"check": "pr_auc vs champion", "value": 0.76, "required": 0.775, "passed": False}
    ]


def test_catching_less_fraud_fails_even_with_good_ranking():
    result = decide({"pr_auc": 0.81, "recall": 0.6}, CHAMPION)
    assert not result["passed"]
    assert [c["check"] for c in result["checks"] if not c["passed"]] == ["recall vs champion"]


def test_tolerances_are_adjustable():
    strict = decide({"pr_auc": 0.79, "recall": 0.72}, CHAMPION, pr_auc_tolerance=0.0)
    assert not strict["passed"]
