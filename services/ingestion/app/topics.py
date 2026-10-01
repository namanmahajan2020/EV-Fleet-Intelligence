"""Provision versioned Kafka streams before the MQTT subscription becomes active."""

import logging
import os
import time

from confluent_kafka.admin import AdminClient, NewTopic

LOG = logging.getLogger(__name__)
TOPICS = (
    (os.getenv("KAFKA_TELEMETRY_TOPIC", "evfleet.telemetry.v1"), 6, {"cleanup.policy": "delete", "retention.ms": "604800000"}),
    (os.getenv("KAFKA_DEAD_LETTER_TOPIC", "evfleet.telemetry.dlq.v1"), 3, {"cleanup.policy": "delete", "retention.ms": "1209600000"}),
)


def ensure_topics(bootstrap_servers: str) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers, "client.id": "evfleet-topic-admin"})
    last_error: Exception | None = None
    for attempt in range(20):
        try:
            futures = admin.create_topics(
                [NewTopic(name, num_partitions=partitions, replication_factor=1, config=config) for name, partitions, config in TOPICS],
                operation_timeout=10,
                request_timeout=15,
            )
            for name, future in futures.items():
                try:
                    future.result()
                    LOG.info("Created Kafka topic %s", name)
                except Exception as exc:
                    # TopicExists is expected on a service restart; any other admin error fails startup.
                    if "TOPIC_ALREADY_EXISTS" not in str(exc):
                        raise
                    LOG.info("Kafka topic %s already exists", name)
            return
        except Exception as exc:
            last_error = exc
            LOG.warning("Kafka unavailable while ensuring topics (attempt %s/20): %s", attempt + 1, exc)
            time.sleep(min(1 + attempt, 5))
    raise RuntimeError("could not provision Kafka topics") from last_error
