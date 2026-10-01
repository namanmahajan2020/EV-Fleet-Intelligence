from charging import charging_timing, haversine_km, recommend_stations
from ev_fleet_simulator.generator import VehicleGenerator
from event_schemas import TelemetryEvent
from evfleet_processor.domain import (
    assess_battery_health,
    derive_state,
    estimate_range_km,
    evaluate_alerts,
)
from security import hash_password, issue_token, verify_password, verify_token


def sample_event() -> TelemetryEvent:
    return TelemetryEvent.model_validate(VehicleGenerator(vehicle_count=1, seed=22).next_event(vehicle_index=0))


def test_derives_battery_and_charge_state() -> None:
    event = sample_event().model_copy(update={"soc_pct": 19.5, "range_km": 34.0, "soh_pct": 85.0})
    state = derive_state(event)
    assert state["battery_health_category"] == "watch"
    assert state["degradation_risk"] == "moderate"
    assert state["charging_needed"] is True


def test_alert_rules_return_expected_types_and_severities() -> None:
    event = sample_event().model_copy(update={
        "soc_pct": 8.0,
        "range_km": 21.0,
        "soh_pct": 75.0,
        "battery_temp_c": 61.0,
        "fault_codes": ["P0A80"],
    })
    alerts = evaluate_alerts(event)
    assert {alert.alert_type for alert in alerts} == {
        "LOW_BATTERY", "LOW_RANGE", "BATTERY_DEGRADATION", "HIGH_BATTERY_TEMPERATURE",
    }
    assert next(alert for alert in alerts if alert.alert_type == "LOW_BATTERY").severity == "critical"
    assert next(alert for alert in alerts if alert.alert_type == "HIGH_BATTERY_TEMPERATURE").severity == "critical"


def test_healthy_vehicle_has_no_threshold_alerts() -> None:
    event = sample_event().model_copy(update={"soc_pct": 65.0, "range_km": 220.0, "soh_pct": 94.0, "battery_temp_c": 30.0})
    assert evaluate_alerts(event) == []
    assert derive_state(event)["battery_health_category"] == "healthy"


def test_battery_assessment_explains_risk_factors() -> None:
    result = assess_battery_health(76, 51, ["P0A80"])
    assert result["category"] == "critical"
    assert result["method"] == "transparent_threshold_rules"
    assert "battery_diagnostic_trouble_code" in result["reasons"]


def test_range_baseline_uses_energy_and_rejects_invalid_consumption() -> None:
    assert estimate_range_km(50, 80, 20) == 200.0
    try:
        estimate_range_km(50, 80, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero consumption must not produce an unbounded estimate")


def test_password_hash_and_bearer_token_round_trip() -> None:
    hashed = hash_password("correct horse battery")
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password", hashed)
    token = issue_token("user-id", "fleet_manager", "fleet-id")
    claims = verify_token(token)
    assert claims and claims["fleet_id"] == "fleet-id"
    assert verify_token(token + "x") is None
    assert verify_token(issue_token("user-id", "fleet_manager", "fleet-id", ttl_seconds=-1)) is None
    assert verify_token(issue_token("user-id", "superuser", "fleet-id")) is None


def test_bearer_token_rejects_unexpected_jwt_algorithm() -> None:
    import base64
    import hashlib
    import hmac

    from security import SECRET

    token = issue_token("user-id", "fleet_manager", "fleet-id")
    _header, payload, _signature = token.split(".")
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = f"{header}.{payload}"
    signature = base64.urlsafe_b64encode(hmac.new(SECRET, body.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert verify_token(f"{body}.{signature}") is None


def test_charger_recommendation_filters_and_minimizes_estimated_bill() -> None:
    stations = [
        {"station_code":"near", "name":"Near", "latitude":12.98, "longitude":77.60, "connector_type":"CCS2", "power_kw":60, "available_ports":2, "price_per_kwh_inr":20},
        {"station_code":"cheap", "name":"Cheap", "latitude":12.99, "longitude":77.60, "connector_type":"CCS2", "power_kw":40, "available_ports":1, "price_per_kwh_inr":10},
        {"station_code":"wrong", "name":"Wrong", "latitude":12.98, "longitude":77.60, "connector_type":"TYPE2", "power_kw":22, "available_ports":4, "price_per_kwh_inr":5},
        {"station_code":"full", "name":"Full", "latitude":12.98, "longitude":77.60, "connector_type":"CCS2", "power_kw":60, "available_ports":0, "price_per_kwh_inr":2},
    ]
    options = recommend_stations(latitude=12.9716, longitude=77.5946, range_km=10, connector_type="CCS2", onboard_kw=11, capacity_kwh=60, soc_pct=20, target_soc_pct=80, stations=stations)
    assert [option["station_code"] for option in options][0] == "cheap"
    assert all(option["station_code"] in {"near", "cheap"} for option in options)
    assert options[0]["estimated_charge_minutes"] > 0
    assert options[0]["estimated_cost_inr"] < options[1]["estimated_cost_inr"]
    assert options[0]["estimated_grid_energy_kwh"] > options[0]["energy_to_target_kwh"]
    assert options[0]["savings_vs_best_inr"] == 0
    assert options[1]["savings_vs_best_inr"] > 0
    assert haversine_km(12.9716, 77.5946, 12.9716, 77.5946) == 0


def test_charger_recommendation_preserves_range_reserve() -> None:
    station = {"station_code": "near-limit", "name": "Near limit", "latitude": 12.98,
               "longitude": 77.60, "connector_type": "CCS2", "power_kw": 60,
               "available_ports": 1, "price_per_kwh_inr": 10}
    options = recommend_stations(latitude=12.9716, longitude=77.5946, range_km=2.0,
                                 connector_type="CCS2", onboard_kw=11, capacity_kwh=60,
                                 soc_pct=20, target_soc_pct=80, stations=[station])
    assert options == []


def test_charging_timing_prioritizes_range_state_and_safety() -> None:
    assert charging_timing(soc_pct=50, range_km=30, battery_temp_c=30, fault_codes=[])["status"] == "charge_now"
    assert charging_timing(soc_pct=35, range_km=100, battery_temp_c=30, fault_codes=[])["status"] == "plan_soon"
    assert charging_timing(soc_pct=70, range_km=180, battery_temp_c=30, fault_codes=[])["status"] == "monitor"
    assert charging_timing(soc_pct=10, range_km=15, battery_temp_c=35, fault_codes=[], is_charging=True)["status"] == "charging"
    hold = charging_timing(soc_pct=10, range_km=15, battery_temp_c=35, fault_codes=["P0A80"])
    assert hold["status"] == "service_review"
    assert hold["safety_hold"] is True
