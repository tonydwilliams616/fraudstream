"""fraudstream.simulator - streams synthetic card transactions into Kafka.

What: events.py turns the Phase 1 generator's output into a stream of
      messages (the transaction only - never the fraud label), paced at a
      steady rate. kafka.py sends them to MSK with IAM authentication.
      __main__.py is the program the Fargate task runs:
      `python -m fraudstream.simulator`.
Why:  the live system needs a realistic, endless supply of transactions from
      the SAME cardholders and shops the model was trained on, so the
      consumer's features and the model's scores mean the same thing live as
      they did in testing.
"""
