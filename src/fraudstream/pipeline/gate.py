"""fraudstream.pipeline.gate - the promotion rule: is the challenger good enough to register?

What: decide() compares the challenger's test results with the champion's (the
      newest Approved model, scored on the SAME days) and returns pass/fail with
      every check spelled out, so a failed run says exactly why.
The rule (defaults):
  1. challenger PR-AUC >= 0.6                      - an absolute floor: never register junk
  2. challenger PR-AUC >= champion PR-AUC - 0.02   - not meaningfully worse at ranking fraud
  3. challenger recall >= champion recall - 0.05   - not missing more fraud at the alert budget
  With no champion yet (first model), only the floor applies.
Why the tolerances: the test days hold only a few hundred fraud rows, so PR-AUC
  moves by a few points from chance alone (the baseline's 95% range is about
  0.76-0.83). "Strictly better" would reject good models at random; a small
  tolerance accepts a challenger that is as good within the noise.
Passing means "registered for review" (PendingManualApproval) - not deployed.
"""

from __future__ import annotations

MIN_PR_AUC = 0.6
PR_AUC_TOLERANCE = 0.02
RECALL_TOLERANCE = 0.05


def decide(
    challenger: dict,
    champion: dict | None,
    min_pr_auc: float = MIN_PR_AUC,
    pr_auc_tolerance: float = PR_AUC_TOLERANCE,
    recall_tolerance: float = RECALL_TOLERANCE,
) -> dict:
    """challenger/champion: test-day results with pr_auc and recall. Returns {passed, checks}."""
    checks = [_check("pr_auc above floor", challenger["pr_auc"], min_pr_auc)]
    if champion is not None:
        checks.append(
            _check(
                "pr_auc vs champion",
                challenger["pr_auc"],
                round(champion["pr_auc"] - pr_auc_tolerance, 4),
            )
        )
        checks.append(
            _check(
                "recall vs champion",
                challenger["recall"],
                round(champion["recall"] - recall_tolerance, 4),
            )
        )
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def _check(name: str, value: float, required: float) -> dict:
    return {"check": name, "value": value, "required": required, "passed": value >= required}
