"""fraudstream.training - turns a dataset version into a trained, evaluated model.

What: data.py     prepares the training data (features, label, time-based split)
      train.py    fits the baseline XGBoost model
      evaluate.py judges it: PR-AUC, alert threshold, precision/recall by style
Why:  keeping training code in the package (not in a notebook) means the same
      code runs on your Mac today and inside a SageMaker training job later,
      and it's tested in CI like everything else.
"""
