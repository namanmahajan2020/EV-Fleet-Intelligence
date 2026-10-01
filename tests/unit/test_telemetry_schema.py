from datetime import datetime, timezone
from uuid import uuid4

import pytest
from event_schemas import EventType, TelemetryEvent
from pydantic import ValidationError


def valid_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "vehicle_id": "EV-000001",
        "timestamp": datetime.now(timezone.utc),
        "latitude": 12.97,
        "longitude": 77.59,
        "speed_kmh": 35.0,
        "soc_pct": 54.0,
        "soh_pct": 96.0,
        "battery_temp_c": 27.0,
        "range_km": 182.0,
        "energy_consumption": 16.5,
        "odometer_km": 5214.0,
        "charging": False,
        "charging_station_id": None,
        "charging_power_kw": 0.0,
        "fault_codes": [],
        "sequence": 1,
        "event_type": EventType.TELEMETRY,
        "event_id": uuid4(),
    }
    event.update(overrides)
    return event


def test_accepts_complete_canonical_event() -> None:
    model = TelemetryEvent.model_validate(valid_event())
    assert model.vehicle_id == "EV-000001"
    assert model.schema_version == 1


@pytest.mark.parametrize("field", ["vehicle_id", "timestamp", "latitude", "longitude", "speed_kmh", "soc_pct", "soh_pct", "battery_temp_c", "range_km", "energy_consumption", "odometer_km", "charging", "charging_power_kw", "sequence", "event_type", "event_id"])
def test_rejects_missing_required_field(field: str) -> None:
    event = valid_event()
    event.pop(field)
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(event)


@pytest.mark.parametrize("field,value", [("latitude", 91), ("longitude", -181), ("soc_pct", 101), ("soh_pct", 0), ("speed_kmh", -1), ("battery_temp_c", 121), ("sequence", -1)])
def test_rejects_out_of_range_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(valid_event(**{field: value}))


def test_rejects_unknown_event_type_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(valid_event(event_type="UNRECOGNISED"))
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(valid_event(driver_name="not-allowed"))


def test_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(valid_event(timestamp=datetime(2026, 9, 30)))


def test_charging_event_requires_station_and_power() -> None:
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(valid_event(event_type="CHARGING_STARTED", charging=True))


def test_fault_event_requires_valid_diagnostic_codes() -> None:
    event = valid_event(event_type="FAULT", fault_codes=["P0A80"])
    assert TelemetryEvent.model_validate(event).fault_codes == ["P0A80"]
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(valid_event(event_type="FAULT"))
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(valid_event(event_type="FAULT", fault_codes=["invalid"]))
