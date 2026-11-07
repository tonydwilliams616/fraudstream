"""fraudstream.simulator.kafka - the Kafka side: IAM sign-in, topic set-up, sending.

What: producer_config() is the producer's settings; ensure_topic() creates the
      topic if it's missing; KafkaSink sends messages keyed by card_id.
Why these settings:
  - SASL_SSL + OAUTHBEARER: the client proves who it is with a short-lived
    token signed by its IAM role (aws-msk-iam-sasl-signer). No passwords
    exist anywhere; MSK checks each request against the role's policy
    (19-ml), which allows this app only its own topic.
  - acks=all + idempotence: a send succeeds only once the in-sync copies
    (at least 2 of 3) have it, and a retry after a network blip can never
    create a duplicate. Together: no loss, no duplicates per partition.
  - key = card_id: every purchase on a card lands on the same partition, so
    the consumer sees each card's purchases in order (see the Q&A log).
The Kafka libraries are imported inside the functions, so the rest of the
package (and its tests) doesn't need them installed.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

TOPIC_PARTITIONS = 6
TOPIC_REPLICATION = 3


def producer_config(bootstrap_servers: str, region: str, client_id: str) -> dict:
    """confluent-kafka producer settings for MSK with IAM authentication."""

    def iam_token(_config_str):
        from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

        token, expiry_ms = MSKAuthTokenProvider.generate_auth_token(region)
        return token, expiry_ms / 1000  # the client wants seconds

    return {
        "bootstrap.servers": bootstrap_servers,
        "client.id": client_id,
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "OAUTHBEARER",
        "oauth_cb": iam_token,
        "acks": "all",
        "enable.idempotence": True,
        "compression.type": "lz4",
        "linger.ms": 20,  # wait up to 20 ms to batch messages together
    }


def ensure_topic(config: dict, topic: str) -> None:
    """Create the topic if it doesn't exist (auto topic creation is off on the cluster)."""
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({k: v for k, v in config.items() if k in _ADMIN_KEYS})
    admin.poll(1)  # serves the IAM token callback before the first request
    future = admin.create_topics(
        [NewTopic(topic, num_partitions=TOPIC_PARTITIONS, replication_factor=TOPIC_REPLICATION)]
    )[topic]
    try:
        future.result(timeout=30)
        log.info("created topic %s", topic)
    except KafkaException as err:
        if err.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise
        log.info("topic %s already exists", topic)


_ADMIN_KEYS = {"bootstrap.servers", "client.id", "security.protocol", "sasl.mechanisms", "oauth_cb"}


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
