"""fraudstream.consumer - scores the transaction stream with the live model.

What: model.py finds out which model the endpoint is serving and checks it
      expects exactly the features this code builds; scoring.py turns a
      micro-batch of transactions into scored records and alerts; archive.py
      collects scored records into roughly one-minute files in S3. The Kafka
      loop and the program the Fargate task runs come in __main__.py.
Why:  this is where the two halves meet - the stream (simulator -> MSK) and
      the model (registry -> endpoint) - using the SAME FeatureBuilder as
      training and the SAME request encoder the parity check proved.
"""
