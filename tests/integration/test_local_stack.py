"""Integration checks and disposable lifecycle fixture for the Compose stack."""
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import psycopg
from confluent_kafka.admin import AdminClient
from pymongo import MongoClient
from redis import Redis

DATABASE_URL = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
MONGO_URI = os.environ["MONGO_URI"]
API = os.getenv("API_BASE_URL", "http://api:8000")


def retry(read, description: str):
    last = None
    for _ in range(30):
        try:
            value = read()
            if value:
                return value
        except Exception as error:  # dependency startup may race the test container
            last = error
        time.sleep(1)
    raise AssertionError(f"{description} did not become ready: {last}")


def test_postgres_seed_and_migration() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        total = conn.execute("SELECT count(*) FROM vehicles").fetchone()[0]
        migration = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert total == 100_000
    assert migration == "0003_vehicle_search"


def test_mongodb_history_and_redis_latest_state() -> None:
    def read_mongo():
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=1000)
        try:
            client.admin.command("ping")
            return client["evfleet"]["telemetry"].find_one({"vehicle_id": "EV-000001"})
        finally:
            client.close()

    event = retry(read_mongo, "MongoDB telemetry event")
    assert event and event["event_id"]

    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    state = retry(lambda: redis.hget("vehicle:latest:EV-000001", "payload"), "Redis latest vehicle state")
    assert json.loads(state)["vehicle_id"] == "EV-000001"
    assert redis.zscore("fleet:charging:priority", "EV-000001") is not None


def test_kafka_telemetry_topic_exists() -> None:
    admin = AdminClient({"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"]})
    topics = retry(lambda: admin.list_topics(timeout=2).topics, "Kafka metadata")
    assert "evfleet.telemetry.v1" in topics
    assert topics["evfleet.telemetry.v1"].partitions


def test_authenticated_api_and_unauthenticated_rejection() -> None:
    credentials = {"email": os.environ["DEMO_OPERATOR_EMAIL"], "password": os.environ["DEMO_OPERATOR_PASSWORD"]}
    request = Request(f"{API}/api/v1/auth/token", data=json.dumps(credentials).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        login = json.load(response)
        token = login["access_token"]
        assert login["token_type"] == "bearer"
        assert login["expires_in"] == 3600
        assert login["user"]["email"] == credentials["email"]
        assert login["user"]["role"] in {"fleet_manager", "operator", "analyst"}
    request = Request(f"{API}/api/v1/fleet/summary", headers={"Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=5) as response:
        assert json.load(response)["total"] == 100_000
    request = Request(f"{API}/api/v1/analytics/consumption?period_hours=24", headers={"Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=30) as response:
        analytics = json.load(response)
        assert analytics["summary"]["events"] >= 1
    request = Request(f"{API}/api/v1/charging-stations?limit=50", headers={"Authorization": f"Bearer {token}"})
    with urlopen(request, timeout=5) as response:
        stations = json.load(response)["items"]
        assert len(stations) >= 15
        assert {"station_code", "connector_type", "available_ports"}.issubset(stations[0])
    try:
        urlopen(f"{API}/api/v1/fleet/summary", timeout=5)
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("protected API accepted a request without a bearer token")
    invalid = Request(f"{API}/api/v1/fleet/summary", headers={"Authorization": "Bearer expired-or-invalid"})
    try:
        urlopen(invalid, timeout=5)
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("protected API accepted an invalid bearer token")
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("UPDATE users SET is_active = false WHERE email = %s", (credentials["email"],))
    try:
        try:
            urlopen(Request(f"{API}/api/v1/fleet/summary", headers={"Authorization": f"Bearer {token}"}), timeout=5)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("protected API accepted a token for an inactive user")
    finally:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("UPDATE users SET is_active = true WHERE email = %s", (credentials["email"],))


def test_fleet_charge_plan_and_vehicle_insights() -> None:
    credentials = {"email": os.environ["DEMO_OPERATOR_EMAIL"], "password": os.environ["DEMO_OPERATOR_PASSWORD"]}
    login = Request(f"{API}/api/v1/auth/token", data=json.dumps(credentials).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(login, timeout=5) as response:
        token = json.load(response)["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    with urlopen(Request(f"{API}/api/v1/fleet/charging-plan?limit=25&target_soc_pct=80", headers=headers), timeout=15) as response:
        plan = json.load(response)
    assert 0 < plan["count"] <= 25
    assert plan["method"] == "state_thresholds_then_lowest_estimated_energy_bill"
    assert {item["charge_timing"] for item in plan["items"]}.issubset({"charge_now", "plan_soon", "service_review", "charging", "monitor"})
    for item in plan["items"]:
        assert "battery_health_category" in item and "range_km" in item
        if item["safety_hold"]:
            assert item["best_station"] is None
        elif item["best_station"]:
            options = [item["best_station"], *item["alternatives"]]
            assert options[0]["estimated_cost_inr"] <= min(option["estimated_cost_inr"] for option in options)

    for endpoint in ("range-estimate", "battery-health", "charging-recommendations"):
        with urlopen(Request(f"{API}/api/v1/vehicles/EV-000001/{endpoint}", headers=headers), timeout=10) as response:
            insight = json.load(response)
            assert insight["vehicle_id"] == "EV-000001"
            if endpoint == "charging-recommendations":
                assert "charge_timing" in insight
                assert insight["items"] == sorted(insight["items"], key=lambda option: (option["estimated_cost_inr"], option["distance_km"], option["estimated_charge_minutes"]))

    with urlopen(Request(f"{API}/api/v1/vehicles?limit=5&offset=0", headers=headers), timeout=10) as response:
        registry = json.load(response)
    assert registry["total"] == 100_000 and len(registry["items"]) == 5
    assert "latest" in registry["items"][0]
    with urlopen(Request(f"{API}/api/v1/vehicles?limit=5&state=low_battery&sort_by=soc", headers=headers), timeout=30) as response:
        low_battery = json.load(response)
    assert all(item["latest"]["soc_pct"] <= 20 or item["latest"]["range_km"] <= 35 for item in low_battery["items"])
    with urlopen(Request(f"{API}/api/v1/vehicles?limit=5&state=offline&battery_health=healthy", headers=headers), timeout=30) as response:
        offline_with_health_filter = json.load(response)
    assert offline_with_health_filter["total"] == 0
    with urlopen(Request(f"{API}/api/v1/vehicles/EV-000001/telemetry?period_hours=24&limit=20", headers=headers), timeout=10) as response:
        history = json.load(response)
    assert history["vehicle_id"] == "EV-000001" and history["count"] > 0
    with urlopen(Request(f"{API}/api/v1/battery-health/summary", headers=headers), timeout=30) as response:
        health = json.load(response)
    assert health["reporting"] > 0 and health["total_fleet"] == 100_000
    with urlopen(Request(f"{API}/api/v1/analytics/timeseries?period_hours=24", headers=headers), timeout=20) as response:
        trend = json.load(response)
    assert isinstance(trend["items"], list)
    with urlopen(Request(
        f"{API}/api/v1/predictions/range",
        data=json.dumps({
            "soc_pct": 60, "soh_pct": 95, "battery_temp_c": 28,
            "energy_consumption_kwh_per_100km": 17, "speed_kmh": 35,
            "requested_trip_km": 80, "battery_capacity_kwh": 76,
            "onboard_charging_kw": 11, "target_soc_pct": 80,
        }).encode(), headers={**headers, "Content-Type": "application/json"}, method="POST",
    ), timeout=10) as response:
        prediction = json.load(response)
    assert prediction["method"] == "energy_balance_baseline_v1"
    assert prediction["prediction"]["estimated_remaining_range_km"] > 0


def test_browser_cors_preflight_and_auth_error_headers() -> None:
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        request = Request(
            f"{API}/api/v1/fleet/summary",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
            method="OPTIONS",
        )
        with urlopen(request, timeout=5) as response:
            assert response.status in (200, 204)
            assert response.headers["Access-Control-Allow-Origin"] == origin
            assert response.headers.get("Access-Control-Allow-Credentials") == "true"
            assert "GET" in response.headers.get("Access-Control-Allow-Methods", "")
            assert "authorization" in response.headers.get("Access-Control-Allow-Headers", "").lower()

    request = Request(f"{API}/api/v1/fleet/summary", headers={"Origin": "http://localhost:5173"})
    try:
        urlopen(request, timeout=5)
    except HTTPError as error:
        assert error.code == 401
        assert error.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    else:
        raise AssertionError("protected API accepted a request without a bearer token")


def test_alert_lifecycle_and_audit_are_transactional() -> None:
    credentials = {"email": os.environ["DEMO_OPERATOR_EMAIL"], "password": os.environ["DEMO_OPERATOR_PASSWORD"]}
    login = Request(f"{API}/api/v1/auth/token", data=json.dumps(credentials).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(login, timeout=5) as response:
        token = json.load(response)["access_token"]
    alert_id = uuid4()
    with psycopg.connect(DATABASE_URL) as conn:
        fleet_id = conn.execute("SELECT fleet_id FROM vehicles WHERE vehicle_id = 'EV-000001'").fetchone()[0]
        conn.execute(
            "INSERT INTO alerts (id, fleet_id, vehicle_id, alert_type, severity, message, status, created_at, dedupe_key) "
            "VALUES (%s,%s,'EV-000001','INTEGRATION_TEST','info','temporary integration fixture','open',now(),%s)",
            (alert_id, fleet_id, f"integration-test:{alert_id}"),
        )

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def patch(status: str):
        request = Request(f"{API}/api/v1/alerts/{alert_id}", data=json.dumps({"status": status}).encode(), headers=headers, method="PATCH")
        return urlopen(request, timeout=5)

    try:
        with patch("acknowledged") as response:
            assert json.load(response)["status"] == "acknowledged"
        try:
            patch("acknowledged")
        except HTTPError as error:
            assert error.code == 409
        else:
            raise AssertionError("alert accepted an invalid repeated acknowledgement")
        with patch("resolved") as response:
            assert json.load(response)["status"] == "resolved"
        with psycopg.connect(DATABASE_URL) as conn:
            entries = conn.execute(
                "SELECT action FROM audit_logs WHERE resource_id = %s ORDER BY action",
                (str(alert_id),),
            ).fetchall()
        assert {entry[0] for entry in entries} == {"alert.acknowledged", "alert.resolved"}
    finally:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("DELETE FROM audit_logs WHERE resource_id = %s", (str(alert_id),))
            conn.execute("DELETE FROM alerts WHERE id = %s", (alert_id,))
