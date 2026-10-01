"""Seeded synthetic EV state and canonical event generation."""

import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from event_schemas import EventType, TelemetryEvent

LATITUDE_BOUNDS = (12.77, 13.17)
LONGITUDE_BOUNDS = (77.36, 77.84)
STATIONS = (
    ("CHG-BLR-001", 12.9784, 77.6408, "CCS2", 60.0),
    ("CHG-BLR-002", 12.9756, 77.6068, "CCS2", 120.0),
    ("CHG-BLR-003", 12.9352, 77.6245, "TYPE2", 22.0),
    ("CHG-BLR-004", 12.9698, 77.7500, "CCS2", 90.0),
    ("CHG-BLR-005", 12.9250, 77.5838, "CHADEMO", 50.0),
    ("CHG-BLR-006", 13.0290, 77.5500, "CCS2", 50.0),
    ("CHG-BLR-007", 12.8450, 77.6600, "CCS2", 60.0),
    ("CHG-BLR-008", 13.0350, 77.5970, "CCS2", 75.0),
    ("CHG-BLR-009", 12.9160, 77.4800, "TYPE2", 22.0),
    ("CHG-BLR-010", 12.9590, 77.6970, "CCS2", 75.0),
    ("CHG-BLR-011", 12.9250, 77.5700, "CHADEMO", 50.0),
    ("CHG-BLR-012", 13.0430, 77.6200, "CCS2", 60.0),
    ("CHG-BLR-013", 12.9700, 77.5350, "TYPE2", 22.0),
    ("CHG-BLR-014", 12.9270, 77.6770, "CCS2", 90.0),
    ("CHG-BLR-015", 12.9110, 77.6380, "CHADEMO", 50.0),
)
MODELS = (("Voltara", "City", 48.0), ("Northstar", "Cargo", 76.0), ("Luma", "Touring", 82.0))
FAULT_CODES = ("P0A80", "P0A0D", "P1A10", "U0100")


def _reflect_into_bounds(value: float, delta: float, bounds: tuple[float, float]) -> float:
    """Move by delta and reflect at the geofence instead of pinning at its edge."""
    lower, upper = bounds
    width = upper - lower
    offset = (value - lower + delta) % (2 * width)
    if offset > width:
        offset = 2 * width - offset
    return lower + offset


@dataclass
class VehicleState:
    vehicle_id: str
    latitude: float
    longitude: float
    speed_kmh: float
    soc_pct: float
    soh_pct: float
    battery_capacity_kwh: float
    connector_type: str
    onboard_charging_kw: float
    energy_consumption: float
    odometer_km: float
    battery_temp_c: float
    charging: bool = False
    charging_station_id: str | None = None
    charging_power_kw: float = 0.0
    sequence: int = 0


class VehicleGenerator:
    """Generate one stateful telemetry stream per configured synthetic vehicle."""

    def __init__(
        self,
        vehicle_count: int = 1000,
        seed: int = 20260930,
        fault_rate: float = 0.001,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if vehicle_count < 1:
            raise ValueError("vehicle_count must be at least one")
        if not 0 <= fault_rate <= 1:
            raise ValueError("fault_rate must be between zero and one")
        self.rng = random.Random(seed)
        self.fault_rate = fault_rate
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.seed = seed
        self.states = [self._new_vehicle(number) for number in range(1, vehicle_count + 1)]

    def restore_states(self, latest_by_vehicle: dict[str, dict[str, object]]) -> int:
        """Resume sequence and physical state from persisted latest telemetry."""
        restored = 0
        fields = (
            "latitude", "longitude", "speed_kmh", "soc_pct", "soh_pct",
            "energy_consumption", "odometer_km", "battery_temp_c", "charging",
            "charging_station_id", "charging_power_kw", "sequence",
        )
        by_id = {state.vehicle_id: state for state in self.states}
        for vehicle_id, payload in latest_by_vehicle.items():
            state = by_id.get(vehicle_id)
            if state is None:
                continue
            for field in fields:
                if field in payload:
                    setattr(state, field, payload[field])
            # Older simulator versions clamped vehicles onto the exact geofence
            # edges. Reinitialize only those synthetic edge artifacts with the
            # same fleet-wide distribution used for new vehicles.
            if state.latitude in LATITUDE_BOUNDS:
                state.latitude = self.rng.uniform(*LATITUDE_BOUNDS)
            if state.longitude in LONGITUDE_BOUNDS:
                state.longitude = self.rng.uniform(*LONGITUDE_BOUNDS)
            restored += 1
        return restored

    def _new_vehicle(self, number: int) -> VehicleState:
        rng = self.rng
        _make, _model, capacity = rng.choice(MODELS)
        connector = rng.choices(["CCS2", "TYPE2", "CHADEMO"], weights=[65, 22, 13])[0]
        initial_soc = 18.0 if number == 1 else min(98.0, max(12.0, rng.gauss(61.0, 19.0)))
        return VehicleState(
            vehicle_id=f"EV-{number:06d}",
            latitude=rng.uniform(*LATITUDE_BOUNDS),
            longitude=rng.uniform(*LONGITUDE_BOUNDS),
            speed_kmh=0.0,
            soc_pct=round(initial_soc, 2),
            soh_pct=round(rng.uniform(86.0, 100.0), 2),
            battery_capacity_kwh=capacity,
            connector_type=connector,
            onboard_charging_kw=rng.choice((7.2, 11.0, 22.0)),
            energy_consumption=round(rng.uniform(14.0, 23.0), 2),
            odometer_km=round(rng.uniform(120.0, 160_000.0), 1),
            battery_temp_c=round(rng.uniform(20.0, 36.0), 1),
        )

    def next_event(self, vehicle_index: int | None = None, interval_seconds: float = 1.0) -> dict[str, object]:
        state = self.states[vehicle_index if vehicle_index is not None else self.rng.randrange(len(self.states))]
        rng = self.rng
        state.sequence += 1
        state.speed_kmh = round(max(0.0, min(115.0, rng.gauss(34.0, 22.0))), 1)
        elapsed_hours = max(0.001, interval_seconds) / 3600
        distance_km = state.speed_kmh * elapsed_hours
        bearing = rng.uniform(-math.pi, math.pi)
        latitude_delta = distance_km * math.cos(bearing) / 111.0
        longitude_delta = distance_km * math.sin(bearing) / max(1.0, 111.0 * math.cos(math.radians(state.latitude)))
        state.latitude = _reflect_into_bounds(state.latitude, latitude_delta, LATITUDE_BOUNDS)
        state.longitude = _reflect_into_bounds(state.longitude, longitude_delta, LONGITUDE_BOUNDS)
        state.odometer_km += distance_km
        if state.charging:
            state.soc_pct = min(95.0, state.soc_pct + state.charging_power_kw * elapsed_hours / state.battery_capacity_kwh * 100)
            if state.soc_pct >= 90 or rng.random() < 0.015:
                state.charging = False
                state.charging_station_id = None
                state.charging_power_kw = 0.0
                event_type = EventType.CHARGING_STOPPED
            else:
                event_type = EventType.CHARGING_UPDATE
        else:
            energy_used_kwh = state.energy_consumption * distance_km / 100
            state.soc_pct = max(3.0, state.soc_pct - energy_used_kwh / state.battery_capacity_kwh * 100)
            # At fleet scale each vehicle may only emit once per long simulator
            # cycle, so a low battery needs to trigger a charging visit promptly.
            if state.soc_pct <= 12 or (state.soc_pct < 24 and rng.random() < 0.55):
                compatible = [station for station in STATIONS if station[3] == state.connector_type]
                station = rng.choice(compatible or list(STATIONS))
                state.charging = True
                state.charging_station_id = station[0]
                state.charging_power_kw = min(state.onboard_charging_kw, station[4])
                event_type = EventType.CHARGING_STARTED
            else:
                event_type = EventType.TELEMETRY

        state.soh_pct = max(70.0, state.soh_pct - distance_km * 0.00002)
        now = self.clock()
        ambient = 29.0 + 5.0 * self._daily_temperature(now)
        thermal_target = ambient + (7.0 if state.charging else min(6.0, state.speed_kmh / 30))
        state.battery_temp_c += (thermal_target - state.battery_temp_c) * 0.08 + rng.uniform(-0.4, 0.4)
        state.battery_temp_c = min(65.0, max(-10.0, state.battery_temp_c))
        fault_codes: list[str] = []
        if rng.random() < self.fault_rate:
            fault_codes = [rng.choice(FAULT_CODES)]
            event_type = EventType.FAULT

        usable_energy = state.battery_capacity_kwh * state.soh_pct / 100 * state.soc_pct / 100
        range_km = usable_energy / state.energy_consumption * 100
        event = {
            "schema_version": 1,
            "vehicle_id": state.vehicle_id,
            "timestamp": now.astimezone(timezone.utc).isoformat(),
            "latitude": round(state.latitude, 6),
            "longitude": round(state.longitude, 6),
            "speed_kmh": state.speed_kmh,
            "soc_pct": round(state.soc_pct, 2),
            "soh_pct": round(state.soh_pct, 2),
            "battery_temp_c": round(state.battery_temp_c, 2),
            "range_km": round(range_km, 2),
            "energy_consumption": state.energy_consumption,
            "odometer_km": round(state.odometer_km, 3),
            "charging": state.charging,
            "charging_station_id": state.charging_station_id,
            "charging_power_kw": round(state.charging_power_kw, 2),
            "fault_codes": fault_codes,
            "sequence": state.sequence,
            "event_type": event_type.value,
            "event_id": str(uuid5(NAMESPACE_URL, f"ev-fleet-intelligence/{state.vehicle_id}/{state.sequence}")),
        }
        return TelemetryEvent.model_validate(event).model_dump(mode="json")

    @staticmethod
    def _daily_temperature(value: datetime) -> float:
        hour = value.hour + value.minute / 60
        return max(-1.0, min(1.0, -abs(hour - 15) / 18 + 0.55))
