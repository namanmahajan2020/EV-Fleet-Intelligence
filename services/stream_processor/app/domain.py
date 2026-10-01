"""Pure derived-state and alert rules for canonical EV telemetry."""

from dataclasses import dataclass
from datetime import datetime

from event_schemas import TelemetryEvent


@dataclass(frozen=True)
class AlertCandidate:
    alert_type: str
    severity: str
    message: str


def estimate_range_km(soc_pct: float, usable_capacity_kwh: float, consumption_kwh_per_100km: float) -> float:
    """Physics-based range baseline; returns zero for invalid inputs."""
    if not (0 <= soc_pct <= 100) or usable_capacity_kwh < 0 or consumption_kwh_per_100km <= 0:
        raise ValueError("range inputs are outside their valid domain")
    return round((soc_pct / 100.0) * usable_capacity_kwh * 100.0 / consumption_kwh_per_100km, 1)


def assess_battery_health(soh_pct: float, battery_temp_c: float, fault_codes: list[str]) -> dict[str, object]:
    """Explainable rules-only health assessment, not a trained degradation model."""
    faulted = any(code in {"P0A80", "P1A10"} for code in fault_codes)
    if soh_pct < 80 or faulted or battery_temp_c >= 60:
        category, risk = "critical", "high"
    elif soh_pct < 90 or battery_temp_c >= 50:
        category, risk = "watch", "moderate"
    else:
        category, risk = "healthy", "baseline"
    reasons = []
    if soh_pct < 80:
        reasons.append("state_of_health_below_80_pct")
    if faulted:
        reasons.append("battery_diagnostic_trouble_code")
    if battery_temp_c >= 50:
        reasons.append("battery_temperature_at_or_above_50_c")
    return {"category": category, "risk": risk, "reasons": reasons, "method": "transparent_threshold_rules"}


def derive_state(event: TelemetryEvent) -> dict[str, object]:
    """Compute user-facing state from event fields and simple, explainable thresholds."""
    health = assess_battery_health(event.soh_pct, event.battery_temp_c, event.fault_codes)
    return {
        **event.model_dump(mode="json"),
        "battery_health_category": health["category"],
        "degradation_risk": health["risk"],
        "battery_health_reasons": health["reasons"],
        "charging_needed": event.soc_pct <= 20 or event.range_km <= 35,
        "processed_at": datetime.now(event.timestamp.tzinfo).isoformat(),
    }


def evaluate_alerts(event: TelemetryEvent) -> list[AlertCandidate]:
    alerts: list[AlertCandidate] = []
    if event.soc_pct <= 20:
        severity = "critical" if event.soc_pct <= 10 else "warning"
        alerts.append(AlertCandidate("LOW_BATTERY", severity, f"Battery charge is {event.soc_pct:.1f}%"))
    if event.range_km <= 35:
        alerts.append(AlertCandidate("LOW_RANGE", "warning", f"Estimated remaining range is {event.range_km:.1f} km"))
    if event.soh_pct < 80 or any(code in {"P0A80", "P1A10"} for code in event.fault_codes):
        alerts.append(AlertCandidate("BATTERY_DEGRADATION", "high", f"Battery SoH is {event.soh_pct:.1f}% or a battery DTC was reported"))
    if event.battery_temp_c >= 50:
        severity = "critical" if event.battery_temp_c >= 60 else "warning"
        alerts.append(AlertCandidate("HIGH_BATTERY_TEMPERATURE", severity, f"Battery temperature is {event.battery_temp_c:.1f}°C"))
    return alerts
