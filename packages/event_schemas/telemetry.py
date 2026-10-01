"""Canonical version 1 event contract for synthetic EV telemetry."""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EventType(StrEnum):
    TELEMETRY = "TELEMETRY"
    HEARTBEAT = "HEARTBEAT"
    CHARGING_STARTED = "CHARGING_STARTED"
    CHARGING_UPDATE = "CHARGING_UPDATE"
    CHARGING_STOPPED = "CHARGING_STOPPED"
    FAULT = "FAULT"


class TelemetryEvent(BaseModel):
    """Validated shared event; energy consumption is kWh per 100 km."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    vehicle_id: str = Field(min_length=3, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    timestamp: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    speed_kmh: float = Field(ge=0, le=300)
    soc_pct: float = Field(ge=0, le=100)
    soh_pct: float = Field(gt=0, le=100)
    battery_temp_c: float = Field(ge=-40, le=120)
    range_km: float = Field(ge=0, le=2000)
    energy_consumption: float = Field(gt=0, le=200)
    odometer_km: float = Field(ge=0)
    charging: bool
    charging_station_id: str | None = Field(default=None, max_length=48)
    charging_power_kw: float = Field(ge=0, le=1000)
    fault_codes: list[str] = Field(default_factory=list, max_length=8)
    sequence: int = Field(ge=0)
    event_type: EventType
    event_id: UUID

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @field_validator("fault_codes")
    @classmethod
    def validate_fault_codes(cls, values: list[str]) -> list[str]:
        if any(len(code) != 5 or code[0] not in "PCBU" or any(char not in "0123456789ABCDEF" for char in code[1:]) for code in values):
            raise ValueError("fault codes must use the five-character OBD-II DTC form")
        return values

    @model_validator(mode="after")
    def validate_charging_fields(self) -> Self:
        if self.charging and (not self.charging_station_id or self.charging_power_kw <= 0):
            raise ValueError("charging events require a station id and positive charging power")
        if not self.charging and self.charging_power_kw != 0:
            raise ValueError("charging_power_kw must be zero when the vehicle is not charging")
        if self.event_type in {EventType.CHARGING_STARTED, EventType.CHARGING_UPDATE} and not self.charging:
            raise ValueError("charging event types require charging=true")
        if self.event_type == EventType.CHARGING_STOPPED and self.charging:
            raise ValueError("CHARGING_STOPPED requires charging=false")
        if self.event_type == EventType.FAULT and not self.fault_codes:
            raise ValueError("FAULT events require at least one fault code")
        return self
