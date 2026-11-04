"""fraudstream.training.train - fits the baseline XGBoost model.

What: train() fits an XGBoost classifier on the training days, using the
      validation days for early stopping, and returns the model plus a record
      of exactly how it was trained.
Why:  a deliberately simple, fixed recipe. The project is about the lifecycle
      around the model (registry, gates, deployment, drift, retraining), so the
      model only needs to be sensible and reproducible - not tuned to the last
      decimal place.
Concepts:
  - XGBoost (gradient-boosted trees): a panel of small yes/no flowcharts, each
    one trained to fix the mistakes of the ones before it. The answer is all
    their votes added up, turned into a score from 0 to 1.
  - scale_pos_weight: fraud is ~0.5% of rows, so each fraud row is counted as
    if it were ~200 rows (normal rows / fraud rows). Without it, the model can
    do well by mostly ignoring fraud.
  - Early stopping: trees are added one at a time while the validation score
    (PR-AUC) keeps improving; after EARLY_STOPPING_ROUNDS with no improvement,
    training stops and the best number of trees is kept. Like revising until
    practice-paper marks stop going up, rather than memorising the textbook.
Trade-off: the validation days are used twice - for early stopping here and
      for choosing the alert threshold in evaluate.py. That's normal for a
      baseline; what matters is that the TEST days are touched exactly once.
"""

from __future__ import annotations

import numpy as np
import xgboost as xgb

from fraudstream.features import FEATURE_COLUMNS
from fraudstream.training.data import LABEL, TrainingData

EARLY_STOPPING_ROUNDS = 30

# Small, fixed settings - sensible defaults, not tuned.
XGB_PARAMS = {
    "n_estimators": 500,  # upper limit; early stopping usually stops well before
    "learning_rate": 0.1,  # how much each new tree is allowed to change the answer
    "max_depth": 4,  # each flowchart asks at most 4 questions
    "min_child_weight": 1,
    "subsample": 0.8,  # each tree sees a random 80% of rows ...
    "colsample_bytree": 0.8,  # ... and 80% of the clues - makes trees less alike
    "tree_method": "hist",  # fast, and deterministic for a fixed seed
    "eval_metric": "aucpr",  # PR-AUC on validation drives early stopping
    "random_state": 42,
}


def train(data: TrainingData, params: dict | None = None) -> tuple[xgb.XGBClassifier, dict]:
    """Fit on data.train, early-stop on data.valid. Returns (model, training record)."""
    params = {**XGB_PARAMS, **(params or {})}
    y_train = data.train[LABEL]
    n_fraud = int(y_train.sum())
    if n_fraud == 0:
        raise ValueError("training days contain no fraud - nothing to learn from")
    scale_pos_weight = (len(y_train) - n_fraud) / n_fraud

    model = xgb.XGBClassifier(
        **params,
        scale_pos_weight=scale_pos_weight,
        early_stopping_rounds=EARLY_STOPPING_ROUNDS,
    )
    model.fit(
        data.train[FEATURE_COLUMNS],
        y_train,
        eval_set=[(data.valid[FEATURE_COLUMNS], data.valid[LABEL])],
        verbose=False,
    )

    # Which clues the model leaned on most: total improvement ("gain") from
    # every question asked about that clue, as a share of the whole.
    gain = model.get_booster().get_score(importance_type="total_gain")
    total = sum(gain.values()) or 1.0
    importance = {f: round(gain.get(f, 0.0) / total, 4) for f in FEATURE_COLUMNS}

    record = {
        "algorithm": "xgboost",
        "xgboost_version": xgb.__version__,
        "params": params,
        "scale_pos_weight": round(scale_pos_weight, 2),
        "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
        "best_iteration": int(model.best_iteration),
        "trees_used": int(model.best_iteration) + 1,
        "feature_importance": dict(sorted(importance.items(), key=lambda kv: -kv[1])),
    }
    return model, record


def score(model: xgb.XGBClassifier, part) -> np.ndarray:
    """Fraud scores (0-1) for a table of transactions, using the best number of trees."""
    return model.predict_proba(part[FEATURE_COLUMNS])[:, 1]
