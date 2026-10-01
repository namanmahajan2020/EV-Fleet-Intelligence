"""MQTT publisher with rate control and configurable data fault injection."""

import json
import logging
import os
import random
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt
from event_schemas import TelemetryEvent
from pydantic import ValidationError
from redis import Redis

from ev_fleet_simulator.generator import VehicleGenerator

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
STOP = threading.Event()
REQUIRED_FIELDS = (
    "vehicle_id", "timestamp", "latitude", "longitude", "speed_kmh", "soc_pct", "soh_pct",
    "battery_temp_c", "range_km", "energy_consumption", "odometer_km", "charging",
    "charging_power_kw", "sequence", "event_type", "event_id",
)


def env_float(name: str, default: float, low: float = 0.0, high: float = 1.0) -> float:
    value = float(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


@dataclass(frozen=True)
class Config:
    mode: str
    seed: int
    vehicle_count: int
    events_per_second: float
    duplicate_rate: float
    out_of_order_rate: float
    malformed_rate: float
    unknown_event_rate: float
    fault_rate: float
    burst_multiplier: float
    burst_interval: float
    burst_duration: float

    @classmethod
    def from_env(cls) -> "Config":
        mode = os.getenv("SIMULATOR_MODE", "DEMO_MODE").upper()
        if mode not in {"DEMO_MODE", "LOAD_TEST_MODE"}:
            raise ValueError("SIMULATOR_MODE must be DEMO_MODE or LOAD_TEST_MODE")
        return cls(
            mode=mode,
            seed=int(os.getenv("SIMULATOR_SEED", "20260930")),
            vehicle_count=int(os.getenv("SIMULATOR_VEHICLE_COUNT", "100000")),
            events_per_second=float(os.getenv("SIMULATOR_EVENTS_PER_SECOND", "20")),
            duplicate_rate=env_float("SIMULATOR_DUPLICATE_RATE", 0.01),
            out_of_order_rate=env_float("SIMULATOR_OUT_OF_ORDER_RATE", 0.02),
            malformed_rate=env_float("SIMULATOR_MALFORMED_RATE", 0.0),
            unknown_event_rate=env_float("SIMULATOR_UNKNOWN_EVENT_RATE", 0.0),
            fault_rate=env_float("SIMULATOR_FAULT_RATE", 0.001),
            burst_multiplier=float(os.getenv("SIMULATOR_BURST_MULTIPLIER", "3")),
            burst_interval=float(os.getenv("SIMULATOR_BURST_INTERVAL_SECONDS", "900")),
            burst_duration=float(os.getenv("SIMULATOR_BURST_DURATION_SECONDS", "300")),
        )

    def validate(self) -> None:
        if self.vehicle_count < 1 or self.events_per_second <= 0:
            raise ValueError("vehicle count and event rate must be positive")
        if self.burst_multiplier < 1 or self.burst_interval <= 0 or self.burst_duration <= 0:
            raise ValueError("burst multiplier/interval/duration must be positive (multiplier >= 1)")


def build_wire_event(event: dict[str, Any], rng: random.Random, config: Config) -> dict[str, Any]:
    """Inject explicitly configured invalid payloads at the MQTT edge."""
    wire = dict(event)
    if rng.random() < config.unknown_event_rate:
        wire["event_type"] = "UNKNOWN_EVENT"
    if rng.random() < config.malformed_rate:
        wire.pop(rng.choice(REQUIRED_FIELDS), None)
    return wire


def main() -> None:
    config = Config.from_env()
    config.validate()
    rng = random.Random(config.seed + 1)
    generator = VehicleGenerator(config.vehicle_count, config.seed, config.fault_rate)
    redis_client = Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
        socket_connect_timeout=3,
    )
    while not STOP.is_set():
        try:
            pipeline = redis_client.pipeline()
            for state in generator.states:
                pipeline.hget(f"vehicle:latest:{state.vehicle_id}", "payload")
            payloads = pipeline.execute()
            latest = [json.loads(payload) for payload in payloads if payload]
            restored = generator.restore_states({payload["vehicle_id"]: payload for payload in latest})
            logging.info("Restored persisted latest telemetry for %s/%s vehicles", restored, config.vehicle_count)
            break
        except Exception as error:
            logging.warning("Waiting for Redis state before simulator start: %s", error)
            STOP.wait(2)
    if STOP.is_set():
        redis_client.close()
        return
    host = os.getenv("MQTT_HOST", "localhost")
    port = int(os.getenv("MQTT_PORT", "1883"))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ev-fleet-sim-{config.seed}")
    client.max_queued_messages_set(max(10_000, int(config.events_per_second * 10)))
    client.reconnect_delay_set(min_delay=1, max_delay=15)
    client.connect_async(host, port, keepalive=60)
    client.loop_start()
    while not client.is_connected() and not STOP.wait(0.1):
        pass
    if not client.is_connected():
        client.loop_stop()
        raise RuntimeError("simulator stopped before MQTT connection was established")
    recent: deque[dict[str, Any]] = deque(maxlen=128)
    started = time.monotonic()
    last_report = started
    next_publish = started
    vehicle_cursor = 0
    published = duplicates = malformed = 0
    burst_logged = False

    def stop(_signal: int, _frame: object) -> None:
        STOP.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logging.info("Simulator starting mode=%s seed=%s vehicles=%s target_rate=%.1f/s", config.mode, config.seed, config.vehicle_count, config.events_per_second)
    try:
        while not STOP.is_set():
            elapsed = time.monotonic() - started
            burst_on = config.mode == "LOAD_TEST_MODE" and elapsed % config.burst_interval < config.burst_duration
            current_rate = config.events_per_second * (config.burst_multiplier if burst_on else 1)
            if burst_on and not burst_logged:
                logging.info("Burst window started multiplier=%.1fx duration=%.0fs", config.burst_multiplier, config.burst_duration)
                burst_logged = True
            elif not burst_on:
                burst_logged = False

            event = generator.next_event(vehicle_index=vehicle_cursor, interval_seconds=config.vehicle_count / current_rate)
            vehicle_cursor = (vehicle_cursor + 1) % config.vehicle_count
            if rng.random() < config.out_of_order_rate and recent:
                previous = recent.popleft()
                recent.append(event)
                payloads = (event, previous)
            else:
                recent.append(event)
                payloads = (event,)

            for item in payloads:
                wire = build_wire_event(item, rng, config)
                try:
                    TelemetryEvent.model_validate(wire)
                    invalid = False
                except ValidationError:
                    invalid = True
                malformed += int(invalid)
                info = client.publish(
                    f"evfleet/telemetry/v1/{item['vehicle_id']}",
                    json.dumps(wire, separators=(",", ":")), qos=1,
                )
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    published += 1
                else:
                    logging.warning("publish queue/back-pressure rc=%s published=%s", info.rc, published)
                if rng.random() < config.duplicate_rate:
                    duplicate_result = client.publish(
                        f"evfleet/telemetry/v1/{item['vehicle_id']}",
                        json.dumps(item, separators=(",", ":")), qos=1,
                    )
                    if duplicate_result.rc == mqtt.MQTT_ERR_SUCCESS:
                        duplicates += 1
                        published += 1

            next_publish += 1 / current_rate
            delay = next_publish - time.monotonic()
            if delay > 0:
                STOP.wait(delay)
            elif delay < -1:
                next_publish = time.monotonic()  # Do not accumulate catch-up work after a pause.

            now = time.monotonic()
            if now - last_report >= 10:
                actual_rate = published / max(0.001, now - started)
                logging.info("mqtt_queue_accepts=%s mqtt_queue_accept_rate=%.1f/s target_base_rate=%.1f/s duplicate_attempts=%s invalid_attempts=%s", published, actual_rate, current_rate, duplicates, malformed)
                last_report = now
    finally:
        client.loop_stop()
        client.disconnect()
        redis_client.close()
        logging.info("Simulator stopped. publish_attempts=%s duplicates=%s invalid_attempts=%s", published, duplicates, malformed)
