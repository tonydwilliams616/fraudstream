"""fraudstream.consumer.metrics - the consumer's vital signs, for Prometheus.

What: one object, ConsumerMetrics, that holds every number the consumer
      publishes on http://<task>:8000/metrics. A collector next to the consumer
      (Phase 5, 35-apps) reads that page every 15 s and sends the numbers to
      Amazon Managed Service for Prometheus; Grafana draws them.
The numbers:
  fraudstream_transactions_consumed_total      messages read from Kafka
  fraudstream_bad_messages_total               unreadable messages skipped
  fraudstream_transactions_scored_total        scored, by model_version
  fraudstream_alerts_total                     alerts raised, by model_version
  fraudstream_end_to_end_latency_seconds       simulator produced -> score ready
  fraudstream_endpoint_call_seconds            one SageMaker call (each attempt)
  fraudstream_endpoint_errors_total            failed SageMaker calls
  fraudstream_batch_size                       transactions per endpoint call
  fraudstream_score                            the model's score distribution
  fraudstream_model_info                       1, labelled with the live model
  fraudstream_last_checkpoint_timestamp_seconds  when offsets were last committed
Three kinds of metric:
  Counter   only goes up (restarts reset it to 0; Prometheus' rate() copes)
  Gauge     goes up and down - a reading at a moment in time
  Histogram counts observations into buckets ("under 0.1 s", "under 0.25 s"...)
            so percentiles (p50, p99) can be worked out later, across tasks
Why a registry is passed in: the program uses prometheus_client's global
  registry (which also adds Python process metrics such as memory and CPU).
  Tests give each ConsumerMetrics its own empty registry, because registering
  the same metric name twice in one registry is an error.
Why so few labels: every label value creates a separate time series that is
  stored and billed. model_version has a handful of values; card_id would have
  10,000 - so per-card detail stays in the scored archive, not in metrics.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Bucket edges, in the metric's unit. Chosen around what we expect to see:
# end-to-end is normally ~0.2-1 s but reaches minutes when scoring is held up.
END_TO_END_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600)
ENDPOINT_BUCKETS = (0.025, 0.05, 0.075, 0.1, 0.15, 0.25, 0.5, 1, 2.5, 5)
BATCH_BUCKETS = (1, 2, 5, 10, 20, 30, 40, 50)
SCORE_BUCKETS = (0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0)


class ConsumerMetrics:
    def __init__(self, registry: CollectorRegistry | None = None):
        registry = CollectorRegistry() if registry is None else registry
        self.registry = registry

        # Counters: the "_total" suffix is added by the library.
        self.consumed = Counter(
            "fraudstream_transactions_consumed", "Messages read from Kafka.", registry=registry
        )
        self.bad_messages = Counter(
            "fraudstream_bad_messages", "Unreadable messages skipped.", registry=registry
        )
        self.scored = Counter(
            "fraudstream_transactions_scored",
            "Transactions scored, by the model version that scored them.",
            ["model_version"],
            registry=registry,
        )
        self.alerts = Counter(
            "fraudstream_alerts",
            "Fraud alerts raised, by model version.",
            ["model_version"],
            registry=registry,
        )
        self.endpoint_errors = Counter(
            "fraudstream_endpoint_errors", "Failed SageMaker endpoint calls.", registry=registry
        )

        # Histograms.
        self.end_to_end = Histogram(
            "fraudstream_end_to_end_latency_seconds",
            "From the simulator producing a transaction to its score being ready.",
            buckets=END_TO_END_BUCKETS,
            registry=registry,
        )
        self.endpoint_call = Histogram(
            "fraudstream_endpoint_call_seconds",
            "Duration of one SageMaker endpoint call (each attempt, including failures).",
            buckets=ENDPOINT_BUCKETS,
            registry=registry,
        )
        self.batch_size = Histogram(
            "fraudstream_batch_size",
            "Transactions sent in one endpoint call.",
            buckets=BATCH_BUCKETS,
            registry=registry,
        )
        self.scores = Histogram(
            "fraudstream_score",
            "The model's fraud scores (watch for the distribution shifting).",
            buckets=SCORE_BUCKETS,
            registry=registry,
        )

        # Gauges.
        self.model_info = Gauge(
            "fraudstream_model_info",
            "Always 1; the labels say which model and threshold are scoring.",
            ["model_version", "threshold"],
            registry=registry,
        )
        self.last_checkpoint = Gauge(
            "fraudstream_last_checkpoint_timestamp_seconds",
            "Unix time of the last successful offset commit.",
            registry=registry,
        )

    def record_batch(self, records: list[dict]) -> None:
        """Count one scored micro-batch (the records Scorer.score_batch returned)."""
        if not records:
            return
        self.batch_size.observe(len(records))
        for record in records:
            version = str(record["model_version"])
            self.scored.labels(model_version=version).inc()
            if record["alert"]:
                self.alerts.labels(model_version=version).inc()
            self.scores.observe(record["score"])
            if record["latency_ms"] is not None:
                self.end_to_end.observe(record["latency_ms"] / 1000)

    def set_model(self, version: int, threshold: float) -> None:
        """Point model_info at the model now scoring (and drop the old one's series)."""
        self.model_info.clear()
        self.model_info.labels(model_version=str(version), threshold=str(threshold)).set(1)
