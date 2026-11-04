"""fraudstream.training - turns a dataset version into a trained, evaluated model.

What: data.py prepares the training data (features, label, time-based split).
      Training and evaluation arrive in the next steps.
Why:  keeping training code in the package (not in a notebook) means the same
      code runs on your Mac today and inside a SageMaker training job later,
      and it's tested in CI like everything else.
"""
