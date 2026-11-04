"""fraudstream - real-time card-fraud scoring.

What: the top of the Python package. Everything the project's code needs lives
      in sub-packages here:
        generator/  - creates synthetic cardholders, shops and transactions
        features/   - turns raw transactions into clues (features) for the model
      (more arrive in later sessions: training, simulator, consumer).
Why:  one installable package means training on your Mac, CI, and the live
      consumer all import the SAME code - which is what stops features being
      calculated differently in training and in live scoring.
"""

__version__ = "0.1.0"
