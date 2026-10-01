"""At-least-once Kafka consumer that stores history, advances latest state, and opens alerts."""

import logging
import os
import signal
import time
from datetime import timezone
from typing import Any

from confluent_kafka import Consumer, KafkaError, TopicPartition
from event_schemas import TelemetryEvent
from evfleet_processor.domain import derive_state, evaluate_alerts
from evfleet_processor.storage import (
    close_storage,
    initialize_storage,
    publish_latest_update,
    save_alert,
    update_latest_state,
    write_history,
)
from pydantic import ValidationError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("evfleet.processor")
STOP = False


def process_event(event: TelemetryEvent) -> dict[str, Any]:
    wire_event = event.model_dump(mode="json")
    new_history_record = write_history(wire_event)
    state = derive_state(event)
    state_result = update_latest_state(event.vehicle_id, state)
    if state_result < 0:
        return {"history_inserted": new_history_record, "state": "stale", "alerts_created": 0}

    alerts_created = 0
    for candidate in evaluate_alerts(event):
        stored_alert = save_alert(event.vehicle_id, candidate, event.timestamp.astimezone(timezone.utc))
        alerts_created += int(stored_alert is not None)
    # Same-sequence replays may re-publish; clients can discard by event_id. The state is rebuildable from Redis.
    subscribers = publish_latest_update({**state, "alerts": [alert.alert_type for alert in evaluate_alerts(event)]})
    return {
        "history_inserted": new_history_record,
        "state": "advanced" if state_result == 1 else "replay",
        "alerts_created": alerts_created,
        "live_subscribers": subscribers,
    }


def stop(_signal: int, _frame: object) -> None:
    global STOP
    STOP = True


def main() -> None:
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    topic = os.getenv("KAFKA_TELEMETRY_TOPIC", "evfleet.telemetry.v1")
    group = os.getenv("KAFKA_CONSUMER_GROUP", "evfleet-stream-processor-v1")
    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": group,
        "client.id": "evfleet-stream-processor",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "auto.offset.reset": "earliest",
        "allow.auto.create.topics": False,
        "max.poll.interval.ms": 300_000,
        "session.timeout.ms": 45_000,
    })
    initialize_storage()
    consumer.subscribe([topic])
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    received = processed = duplicates = stale = failures = 0
    LOG.info("Stream processor online topic=%s group=%s", topic, group)
    try:
        while not STOP:
            message = consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                LOG.error("Kafka consumer error: %s", message.error())
                continue
            received += 1
            try:
                event = TelemetryEvent.model_validate_json(message.value())
                result = process_event(event)
                consumer.commit(message=message, asynchronous=False)
                processed += 1
                duplicates += int(result["state"] == "replay")
                stale += int(result["state"] == "stale")
                if processed % 1000 == 0:
                    LOG.info("received=%s processed=%s duplicate_replays=%s stale_events=%s failures=%s", received, processed, duplicates, stale, failures)
            except ValidationError:
                # This topic is populated only after ingestion validation; treat contract drift as a poison record.
                failures += 1
                LOG.exception("Kafka record failed canonical schema validation; offset remains uncommitted")
                consumer.seek(TopicPartition(message.topic(), message.partition(), message.offset()))
                time.sleep(1)
            except Exception:
                failures += 1
                LOG.exception("Event processing failed; retrying the same Kafka offset")
                consumer.seek(TopicPartition(message.topic(), message.partition(), message.offset()))
                time.sleep(1)
    finally:
        consumer.close()
        close_storage()
        LOG.info("Stream processor stopped received=%s processed=%s duplicates=%s stale=%s failures=%s", received, processed, duplicates, stale, failures)


if __name__ == "__main__":
    main()
