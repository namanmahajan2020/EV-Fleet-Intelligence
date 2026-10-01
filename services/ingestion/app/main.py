"""Validated MQTT intake with bounded buffering and Kafka delivery acknowledgement."""

import json
import logging
import os
import queue
import signal
import threading
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt
from confluent_kafka import KafkaError, Producer
from event_schemas import TelemetryEvent
from evfleet_ingestion.topics import ensure_topics
from pydantic import ValidationError

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
LOG = logging.getLogger("evfleet.ingestion")
STOP = threading.Event()


class KafkaForwarder:
    """Bridge MQTT messages to a durable Kafka record before manually acknowledging MQTT."""

    def __init__(self, producer: Producer, mqtt_client: mqtt.Client, telemetry_topic: str, dlq_topic: str) -> None:
        self.producer = producer
        self.mqtt_client = mqtt_client
        self.telemetry_topic = telemetry_topic
        self.dlq_topic = dlq_topic
        self.accepted = 0
        self.rejected = 0
        self.duplicates_forwarded = 0

    def forward(self, message: mqtt.MQTTMessage) -> bool:
        raw = message.payload
        try:
            decoded = json.loads(raw)
            event = TelemetryEvent.model_validate(decoded)
            record = json.dumps(event.model_dump(mode="json"), separators=(",", ":")).encode()
            topic, key = self.telemetry_topic, event.vehicle_id.encode()
            headers = [("schema_version", str(event.schema_version).encode()), ("event_id", str(event.event_id).encode())]
            invalid = False
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError) as exc:
            topic, key, invalid = self.dlq_topic, message.topic.encode(), True
            record = json.dumps(
                {
                    "source_topic": message.topic,
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "validation_error": str(exc)[:2000],
                    "raw_payload": raw.decode("utf-8", errors="replace")[:32_000],
                },
                separators=(",", ":"),
            ).encode()
            headers = [("record_type", b"dead-letter")]

        delivered = threading.Event()
        result: dict[str, Any] = {}

        def on_delivery(error: KafkaError | None, _record: Any) -> None:
            result["error"] = error
            delivered.set()

        try:
            while True:
                try:
                    self.producer.produce(topic, key=key, value=record, headers=headers, on_delivery=on_delivery)
                    break
                except BufferError:
                    # Polling drains delivery reports and applies bounded back-pressure.
                    self.producer.poll(0.1)
            while not delivered.wait(0.05):
                self.producer.poll(0.05)
        except Exception:
            LOG.exception("Kafka produce failed; MQTT packet remains unacknowledged")
            return False

        if result.get("error") is not None:
            LOG.error("Kafka delivery failed; MQTT packet remains unacknowledged: %s", result["error"])
            return False

        # MQTT QoS 1 plus Kafka acks=all gives at-least-once delivery across restarts.
        # Replayed duplicates are removed downstream using the stable event_id.
        self.mqtt_client.ack(message.mid, message.qos)
        if invalid:
            self.rejected += 1
            LOG.warning("Invalid telemetry moved to %s (source=%s)", topic, message.topic)
        else:
            self.accepted += 1
            if self.accepted % 1000 == 0:
                LOG.info("Kafka-acknowledged valid_events=%s invalid_events=%s", self.accepted, self.rejected)
        return True


def main() -> None:
    mqtt_host = os.getenv("MQTT_HOST", "localhost")
    mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    telemetry_topic = os.getenv("KAFKA_TELEMETRY_TOPIC", "evfleet.telemetry.v1")
    dlq_topic = os.getenv("KAFKA_DEAD_LETTER_TOPIC", "evfleet.telemetry.dlq.v1")
    topic_filter = os.getenv("MQTT_TOPIC_FILTER", "evfleet/telemetry/v1/#")
    worker_count = max(1, int(os.getenv("INGESTION_WORKERS", "4")))
    capacity = max(worker_count, int(os.getenv("INGESTION_QUEUE_CAPACITY", "10000")))
    ensure_topics(bootstrap)

    producer = Producer({
        "bootstrap.servers": bootstrap,
        "client.id": "evfleet-mqtt-ingestion",
        "acks": "all",
        "enable.idempotence": True,
        "retries": 10,
        "max.in.flight.requests.per.connection": 5,
        "compression.type": "zstd",
        "linger.ms": 5,
        "batch.size": 65536,
    })
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="evfleet-ingestion", manual_ack=True)
    client.max_inflight_messages_set(min(1000, capacity))
    client.reconnect_delay_set(min_delay=1, max_delay=20)
    messages: queue.Queue[mqtt.MQTTMessage | None] = queue.Queue(maxsize=capacity)

    def on_connect(_client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if reason_code.is_failure:
            LOG.error("MQTT connection rejected: %s", reason_code)
            return
        LOG.info("Connected to MQTT %s:%s; subscribing to %s", mqtt_host, mqtt_port, topic_filter)
        _client.subscribe(topic_filter, qos=1)

    def on_message(_client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            messages.put_nowait(message)
        except queue.Full:
            # Leave the packet unacknowledged; MQTT will redeliver it after this process reconnects.
            LOG.error("Ingestion queue full; withholding MQTT acknowledgement")

    client.on_connect = on_connect
    client.on_message = on_message
    forwarder = KafkaForwarder(producer, client, telemetry_topic, dlq_topic)

    def worker() -> None:
        while not STOP.is_set():
            try:
                message = messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if message is None:
                messages.task_done()
                break
            try:
                forwarder.forward(message)
            finally:
                messages.task_done()

    workers = [threading.Thread(target=worker, name=f"ingest-{i}", daemon=True) for i in range(worker_count)]
    for thread in workers:
        thread.start()

    def stop(_signal: int, _frame: object) -> None:
        STOP.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client.connect_async(mqtt_host, mqtt_port, keepalive=60)
    client.loop_start()
    LOG.info("Ingestion online; telemetry_topic=%s dlq_topic=%s workers=%s capacity=%s", telemetry_topic, dlq_topic, worker_count, capacity)
    try:
        STOP.wait()
    finally:
        client.loop_stop()
        for _ in workers:
            try:
                messages.put_nowait(None)
            except queue.Full:
                break
        for thread in workers:
            thread.join(timeout=5)
        producer.flush(10)
        client.disconnect()
        LOG.info("Ingestion stopped; queue_remaining=%s", messages.qsize())


if __name__ == "__main__":
    main()
