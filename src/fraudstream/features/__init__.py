"""fraudstream.features - turns raw transactions into clues (features) for the model.

What: FeatureBuilder takes raw transactions (who, where, when, how much) and
      works out the clues the model learns from, e.g. distance from home.
Why:  the SAME code runs in training (replaying history in time order) and in
      live scoring (one transaction at a time in the consumer). Writing it once
      is the defence against training-serving skew: clues calculated one way
      in training and another way live, which silently ruins a model.
"""

from fraudstream.features.builder import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION, FeatureBuilder

__all__ = ["FEATURE_COLUMNS", "FEATURE_SCHEMA_VERSION", "FeatureBuilder"]
