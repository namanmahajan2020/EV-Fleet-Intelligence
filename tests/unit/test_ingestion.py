import json
from types import SimpleNamespace

from ev_fleet_simulator.generator import VehicleGenerator
from evfleet_ingestion.main import KafkaForwarder


class FakeProducer:
    def __init__(self, delivery_error: Exception | None = None, fail_first: bool = False) -> None:
        self.callback = None
        self.record = None
        self.delivery_error = delivery_error
        self.fail_first = fail_first
        self.failed_once = False

    def produce(self, topic, key, value, headers, on_delivery):
        if self.fail_first and not self.failed_once:
            self.failed_once = True
            raise BufferError("queue full")
        self.record = {"topic": topic, "key": key, "value": value, "headers": headers}
        self.callback = on_delivery

    def poll(self, _timeout):
        if self.callback:
            callback, self.callback = self.callback, None
            callback(self.delivery_error, SimpleNamespace(topic=lambda: self.record["topic"]))


class FakeMqttClient:
    def __init__(self) -> None:
        self.acks: list[tuple[int, int]] = []

    def ack(self, mid: int, qos: int) -> int:
        self.acks.append((mid, qos))
        return 0


def mqtt_message(payload: bytes) -> SimpleNamespace:
    return SimpleNamespace(payload=payload, topic="evfleet/telemetry/v1/EV-000001", mid=42, qos=1)


def test_valid_event_is_acked_only_after_kafka_delivery() -> None:
    event = VehicleGenerator(vehicle_count=1, seed=8).next_event(vehicle_index=0)
    producer, mqtt_client = FakeProducer(), FakeMqttClient()
    forwarder = KafkaForwarder(producer, mqtt_client, "evfleet.telemetry.v1", "evfleet.telemetry.dlq.v1")

    assert forwarder.forward(mqtt_message(json.dumps(event).encode())) is True
    assert producer.record["topic"] == "evfleet.telemetry.v1"
    assert producer.record["key"] == b"EV-000001"
    assert json.loads(producer.record["value"])["event_id"] == event["event_id"]
    assert mqtt_client.acks == [(42, 1)]
    assert forwarder.accepted == 1


def test_invalid_event_is_sent_to_dead_letter_topic() -> None:
    producer, mqtt_client = FakeProducer(), FakeMqttClient()
    forwarder = KafkaForwarder(producer, mqtt_client, "evfleet.telemetry.v1", "evfleet.telemetry.dlq.v1")

    assert forwarder.forward(mqtt_message(b"not-json")) is True
    dead_letter = json.loads(producer.record["value"])
    assert producer.record["topic"] == "evfleet.telemetry.dlq.v1"
    assert dead_letter["raw_payload"] == "not-json"
    assert dead_letter["source_topic"].endswith("EV-000001")
    assert mqtt_client.acks == [(42, 1)]
    assert forwarder.rejected == 1


def test_failed_kafka_delivery_does_not_ack_mqtt_message() -> None:
    producer, mqtt_client = FakeProducer(delivery_error=RuntimeError("broker unavailable")), FakeMqttClient()
    forwarder = KafkaForwarder(producer, mqtt_client, "evfleet.telemetry.v1", "evfleet.telemetry.dlq.v1")
    event = VehicleGenerator(vehicle_count=1, seed=9).next_event(vehicle_index=0)

    assert forwarder.forward(mqtt_message(json.dumps(event).encode())) is False
    assert mqtt_client.acks == []


def test_full_producer_queue_is_drained_before_retry() -> None:
    producer, mqtt_client = FakeProducer(fail_first=True), FakeMqttClient()
    forwarder = KafkaForwarder(producer, mqtt_client, "evfleet.telemetry.v1", "evfleet.telemetry.dlq.v1")
    event = VehicleGenerator(vehicle_count=1, seed=10).next_event(vehicle_index=0)

    assert forwarder.forward(mqtt_message(json.dumps(event).encode())) is True
    assert producer.failed_once
    assert mqtt_client.acks == [(42, 1)]
