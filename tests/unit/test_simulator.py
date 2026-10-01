import random
from datetime import datetime, timezone

import pytest
from ev_fleet_simulator.generator import VehicleGenerator
from ev_fleet_simulator.runner import Config, build_wire_event
from event_schemas import TelemetryEvent
from pydantic import ValidationError


def fixed_time() -> datetime:
    return datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def test_seeded_generator_is_reproducible() -> None:
    first = VehicleGenerator(vehicle_count=10, seed=7, clock=fixed_time)
    second = VehicleGenerator(vehicle_count=10, seed=7, clock=fixed_time)
    assert first.next_event() == second.next_event()


def test_vehicle_count_and_low_battery_demo_vehicle() -> None:
    generator = VehicleGenerator(vehicle_count=100, seed=1, clock=fixed_time)
    assert len(generator.states) == 100
    assert generator.states[0].vehicle_id == "EV-000001"
    assert generator.states[0].soc_pct == 18.0


def test_generator_restores_persisted_state_and_sequence_on_restart() -> None:
    generator = VehicleGenerator(vehicle_count=2, seed=1, clock=fixed_time)
    restored = generator.restore_states({"EV-000001": {
        "vehicle_id": "EV-000001", "sequence": 12, "soc_pct": 44.5,
        "latitude": 12.9, "longitude": 77.6, "charging": True,
    }})
    assert restored == 1
    assert generator.states[0].sequence == 12
    assert generator.states[0].soc_pct == 44.5
    assert generator.states[0].charging is True
    assert generator.states[1].sequence == 0


def test_generated_event_matches_canonical_contract_and_has_stable_id_for_sequence() -> None:
    generator = VehicleGenerator(vehicle_count=3, seed=18, clock=fixed_time)
    event = generator.next_event(vehicle_index=0)
    validated = TelemetryEvent.model_validate(event)
    assert str(validated.event_id) == event["event_id"]
    assert validated.vehicle_id == "EV-000001"
    assert validated.range_km > 0
    assert generator.next_event(vehicle_index=0)["sequence"] == 2


def test_charging_state_transition_emits_stop_event() -> None:
    generator = VehicleGenerator(vehicle_count=1, seed=4, clock=fixed_time)
    state = generator.states[0]
    state.soc_pct = 89.0
    state.charging = True
    state.charging_station_id = "CHG-BLR-001"
    state.charging_power_kw = 60.0
    event = generator.next_event(vehicle_index=0, interval_seconds=60)
    assert event["event_type"] == "CHARGING_STOPPED"
    assert event["charging"] is False
    assert event["charging_power_kw"] == 0


def test_generator_rejects_zero_vehicle_count() -> None:
    with pytest.raises(ValueError):
        VehicleGenerator(vehicle_count=0)


def test_fault_injection_can_create_unknown_and_malformed_events() -> None:
    config = Config(
        mode="DEMO_MODE", seed=1, vehicle_count=1, events_per_second=1,
        duplicate_rate=0, out_of_order_rate=0, malformed_rate=1, unknown_event_rate=1,
        fault_rate=0, burst_multiplier=3, burst_interval=900, burst_duration=300,
    )
    event = VehicleGenerator(vehicle_count=1, seed=4, clock=fixed_time).next_event(vehicle_index=0)
    invalid = build_wire_event(event, random.Random(1), config)
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(invalid)


def test_load_mode_configuration_requires_positive_rates() -> None:
    config = Config(
        mode="LOAD_TEST_MODE", seed=1, vehicle_count=1, events_per_second=0,
        duplicate_rate=0, out_of_order_rate=0, malformed_rate=0, unknown_event_rate=0,
        fault_rate=0, burst_multiplier=3, burst_interval=900, burst_duration=300,
    )
    with pytest.raises(ValueError):
        config.validate()
