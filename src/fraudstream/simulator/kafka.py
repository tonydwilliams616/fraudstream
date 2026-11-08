"""fraudstream.simulator.kafka - sends simulated transactions to Kafka.

What: KafkaSink sends messages to one topic, keyed by card_id, and counts
      what was delivered. The connection settings and topic set-up live in
      fraudstream.msk (shared with the consumer) and are re-exported here.
Why key = card_id: every purchase on a card lands on the same partition, so
      the consumer sees each card's purchases in order (see the Q&A log).
"""

from __future__ import annotations

import json
import logging

from fraudstream.msk import ensure_topic, producer_config

__all__ = ["KafkaSink", "ensure_topic", "producer_config"]

log = logging.getLogger(__name__)


class KafkaSink:
    """Sends messages to one topic, keyed by card_id; counts what was delivered."""

    def __init__(self, config: dict, topic: str):
        from confluent_kafka import Producer

        self._producer = Producer(config)
        self._producer.poll(1)  # serves the IAM token callback before the first send
        self._topic = topic
        self.delivered = 0
        self.failed = 0

    def _on_delivery(self, err, _msg):
        if err is None:
            self.delivered += 1
        else:
            self.failed += 1
            log.error("delivery failed: %s", err)

    def send(self, message: dict) -> None:
        self._producer.produce(
            self._topic,
            key=message["card_id"].encode(),
            value=json.dumps(message).encode(),
            on_delivery=self._on_delivery,
        )
        self._producer.poll(0)  # serve delivery callbacks without blocking

    def flush(self, timeout: float = 30) -> None:
        self._producer.flush(timeout)
