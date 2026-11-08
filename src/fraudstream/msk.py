"""fraudstream.msk - connecting to Amazon MSK: IAM sign-in, client settings, topic set-up.

What: producer_config() and consumer_config() are the settings every app uses
      to talk to the cluster; ensure_topic() creates a topic if it's missing.
      Shared by the simulator (producer) and the consumer (both).
Why these settings:
  - SASL_SSL + OAUTHBEARER: the client proves who it is with a short-lived
    token signed by its IAM role (aws-msk-iam-sasl-signer). No passwords
    exist anywhere; MSK checks each request against the role's policy
    (19-ml), which allows each app only its own topics and group.
  - Producer, acks=all + idempotence: a send succeeds only once the in-sync
    copies (at least 2 of 3) have it, and a retry after a network blip can
    never create a duplicate. Together: no loss, no duplicates per partition.
  - Consumer, no auto-commit: the app says when it has finished with
    messages (after its results are safely written), never a timer.
  - Consumer, cooperative-sticky assignment: when consumers join or leave,
    partitions move one at a time and only as needed, instead of every
    consumer dropping everything. Each moved partition means its cards'
    history starts cold on the new owner, so moving fewer matters.
The Kafka libraries are imported inside the functions, so the rest of the
package (and its tests) doesn't need them installed.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

TOPIC_PARTITIONS = 6
TOPIC_REPLICATION = 3


def _auth(bootstrap_servers: str, region: str, client_id: str) -> dict:
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
    }


def producer_config(bootstrap_servers: str, region: str, client_id: str) -> dict:
    """confluent-kafka producer settings for MSK with IAM authentication."""
    return {
        **_auth(bootstrap_servers, region, client_id),
        "acks": "all",
        "enable.idempotence": True,
        "compression.type": "lz4",
        "linger.ms": 20,  # wait up to 20 ms to batch messages together
    }


def consumer_config(bootstrap_servers: str, region: str, client_id: str, group_id: str) -> dict:
    """confluent-kafka consumer settings: manual commits, start from the beginning if new."""
    return {
        **_auth(bootstrap_servers, region, client_id),
        "group.id": group_id,
        "enable.auto.commit": False,  # commit only after results are safely written
        "auto.offset.reset": "earliest",  # a brand-new group starts at the oldest message
        "partition.assignment.strategy": "cooperative-sticky",
        "session.timeout.ms": 45000,
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
