"""fraudstream.monitoring - is the live world still the world the model learned?

What: drift.py records how each feature was spread out in a model's training
      data (the BASELINE) and measures how far a window of live, scored
      transactions has moved away from it (PSI, per feature).
      __main__.py is the command line: `python -m fraudstream.monitoring`.
Why:  a model is a snapshot of the world it was trained on. When the world
      changes (prices rise, people shop online more), its scores quietly get
      worse long before anyone can measure that with labels. Comparing the
      INPUTS with the training data needs no labels, so it can raise the alarm
      within the hour.
Note: SageMaker Model Monitor would do the data-quality half of this, but it is
      closed to new AWS customers (AWS recommends open-source drift checks plus
      CloudWatch instead), so the check is our own code - small, tested, and
      explainable line by line.
"""
