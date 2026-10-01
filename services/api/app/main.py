"""Fleet API for health, live state, stations, and alert operations."""

from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel
from pymongo import MongoClient
from redis import Redis
from sqlalchemy import text

from app.charging import recommend_stations
from app.config import settings
from app.db import SessionLocal, engine
from app.models import User
from app.repositories.redis_state import client as redis_client
from app.security import issue_token, verify_password, verify_token


class AlertStatusUpdate(BaseModel):
    status: str


class LoginRequest(BaseModel):
    email: str
    password: str

app = FastAPI(
    title="EV-Fleet Intelligence API",
    description="Fleet telemetry and EV charging intelligence API.",
    version="0.1.0",
)
HTTP_REQUESTS = Counter("evfleet_api_requests_total", "API requests", ["method", "path", "status"])
HTTP_LATENCY = Histogram("evfleet_api_request_seconds", "API request latency", ["method", "path"])


@app.middleware("http")
async def observe_request(request, call_next):
    # Browser preflight requests do not carry bearer credentials. Let the outer
    # CORS middleware answer them before rate limiting or authentication runs.
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path.startswith("/api/v1/"):
        bucket = "login" if request.url.path == "/api/v1/auth/token" else "api"
        limit = 10 if bucket == "login" else 1000
        client_ip = request.client.host if request.client else "unknown"
        key = f"rate:{bucket}:{client_ip}"
        try:
            hits = int(redis_client.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                1, key,
            ))
        except Exception:
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Rate-limit service unavailable"}, status_code=503)
        if hits > limit:
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
    if request.url.path.startswith("/api/v1/") and request.url.path != "/api/v1/auth/token" and request.url.path != "/api/v1/system/health":
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        claims = verify_token(token)
        if claims is None:
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Valid bearer token required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        try:
            user_id = UUID(str(claims.get("sub", "")))
            fleet_id = UUID(str(claims.get("fleet_id", "")))
        except (ValueError, TypeError):
            claims = None
        membership = None
        if claims is not None:
            try:
                with SessionLocal() as session:
                    membership = session.execute(text("""
                        SELECT u.id, u.email, r.name AS role, fm.fleet_id
                        FROM users u
                        JOIN fleet_memberships fm ON fm.user_id = u.id
                        JOIN roles r ON r.id = fm.role_id
                        WHERE u.id = :user_id AND u.is_active IS TRUE AND fm.fleet_id = :fleet_id
                    """), {"user_id": user_id, "fleet_id": fleet_id}).mappings().first()
            except Exception:
                from starlette.responses import JSONResponse
                return JSONResponse({"detail": "Authentication service unavailable"}, status_code=503)
        if membership is None:
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Active fleet membership required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        claims["role"] = membership["role"]
        claims["fleet_id"] = str(membership["fleet_id"])
        request.state.user = {"id": str(membership["id"]), "email": membership["email"]}
        if request.method in {"POST", "PATCH", "DELETE"} and claims.get("role") not in {"fleet_manager", "operator"}:
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "This role cannot change fleet state"}, status_code=403)
        request.state.claims = claims
    import time
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


# Register last so CORS wraps auth/rate-limit middleware, including its errors.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/auth/token", tags=["authentication"])
def login(credentials: LoginRequest) -> dict[str, object]:
    from app.db import SessionLocal
    with SessionLocal() as session:
        user = session.query(User).filter(User.email == credentials.email, User.is_active.is_(True)).first()
        if user is not None:
            membership = session.execute(text("""
                SELECT fm.fleet_id, r.name AS role FROM fleet_memberships fm JOIN roles r ON r.id = fm.role_id
                WHERE fm.user_id = :user_id ORDER BY fm.fleet_id LIMIT 1
            """), {"user_id": user.id}).mappings().first()
            if membership and verify_password(credentials.password, user.password_hash):
                token = issue_token(str(user.id), membership["role"], str(membership["fleet_id"]))
                return {
                    "access_token": token,
                    "token_type": "bearer",
                    "expires_in": 3600,
                    "user": {
                        "id": str(user.id),
                        "email": user.email,
                        "role": membership["role"],
                        "fleet_id": str(membership["fleet_id"]),
                    },
                }
    raise HTTPException(status_code=401, detail="Invalid email or password", headers={"WWW-Authenticate": "Bearer"})


@app.get("/healthz", tags=["system"])
def healthcheck() -> dict[str, str]:
    """Container liveness endpoint."""
    return {"status": "ok"}


@app.get("/health", tags=["system"], include_in_schema=False)
def health_alias() -> dict[str, str]:
    """Simple local health URL used by browser and developer checks."""
    return healthcheck()


@app.get("/api/v1/system/health", tags=["system"])
def system_health() -> dict[str, str]:
    """Readiness probes for the relational, telemetry, and cache stores."""
    checks: dict[str, str] = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "unavailable"

    mongo = MongoClient(
        f"mongodb://{settings.mongo_user}:{settings.mongo_password}"
        f"@{settings.mongo_host}:{settings.mongo_port}/?authSource=admin",
        serverSelectionTimeoutMS=1500,
    )
    try:
        mongo.admin.command("ping")
        checks["mongodb"] = "ok"
    except Exception:
        checks["mongodb"] = "unavailable"
    finally:
        mongo.close()

    try:
        Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=1).ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    return {"status": "ok" if all(value == "ok" for value in checks.values()) else "degraded", **checks}


@app.get("/api/v1/fleet/summary", tags=["fleet"])
def fleet_summary() -> dict[str, object]:
    with engine.connect() as connection:
        counts = connection.execute(text("""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'active') AS active
            FROM vehicles
        """)).mappings().one()
        alert_counts = connection.execute(text("""
            SELECT count(*) FILTER (WHERE status = 'open') AS open_alerts,
                   count(*) FILTER (WHERE status = 'open' AND severity = 'critical') AS critical_alerts
            FROM alerts
        """)).mappings().one()
    return {**dict(counts), **dict(alert_counts)}


@app.get("/api/v1/analytics/consumption", tags=["analytics"])
def consumption_analytics(period_hours: int = Query(24, ge=1, le=720), limit: int = Query(20, ge=1, le=100)) -> dict[str, object]:
    """On-demand historical aggregation over retained telemetry, bounded to 30 days."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=period_hours)
    mongo = MongoClient(
        f"mongodb://{settings.mongo_user}:{settings.mongo_password}@{settings.mongo_host}:{settings.mongo_port}/?authSource=admin",
        serverSelectionTimeoutMS=1500,
    )
    try:
        collection = mongo["evfleet"]["telemetry"]
        summary = list(collection.aggregate([
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {"$group": {
                "_id": "$vehicle_id", "events": {"$sum": 1},
                "avg_consumption": {"$avg": "$energy_consumption"},
            }},
            {"$group": {
                "_id": None, "events": {"$sum": "$events"},
                "vehicle_count": {"$sum": 1},
                "weighted_consumption": {"$sum": {"$multiply": ["$avg_consumption", "$events"]}},
            }},
            {"$project": {
                "_id": 0, "events": 1, "vehicle_count": 1,
                "avg_consumption_kwh_per_100km": {"$round": [{"$divide": ["$weighted_consumption", "$events"]}, 2]},
            }},
        ]))
        top = list(collection.aggregate([
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {"$group": {
                "_id": "$vehicle_id", "events": {"$sum": 1},
                "avg_consumption_kwh_per_100km": {"$avg": "$energy_consumption"},
            }},
            {"$sort": {"events": -1}},
            {"$limit": limit},
            {"$project": {
                "_id": 0, "vehicle_id": "$_id", "events": 1,
                "avg_consumption_kwh_per_100km": {"$round": ["$avg_consumption_kwh_per_100km", 2]},
            }},
        ]))
    finally:
        mongo.close()
    return {"period_hours": period_hours, "since": cutoff.isoformat(), "summary": summary[0] if summary else {"events": 0, "vehicle_count": 0, "avg_consumption_kwh_per_100km": None}, "highest_reporting_vehicles": top, "method": "on_demand_mongodb_aggregation", "limitations": "Bounded query over retained telemetry; not a distributed historical warehouse or long-range trend model."}


@app.get("/api/v1/vehicles", tags=["vehicles"])
def list_vehicles(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), search: str | None = None) -> dict[str, object]:
    where = "WHERE vehicle_id ILIKE :search" if search else ""
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if search:
        params["search"] = f"%{search}%"
    with engine.connect() as connection:
        rows = connection.execute(text(f"""
            SELECT vehicle_id, make, model, model_year, status, battery_capacity_kwh, connector_type
            FROM vehicles {where} ORDER BY vehicle_id LIMIT :limit OFFSET :offset
        """), params).mappings().all()
        total = connection.execute(text(f"SELECT count(*) FROM vehicles {where}"), params).scalar_one()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/v1/vehicles/{vehicle_id}", tags=["vehicles"])
def vehicle_detail(vehicle_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT vehicle_id, fleet_id, make, model, model_year, status, battery_capacity_kwh,
                   connector_type, onboard_charging_kw
            FROM vehicles WHERE vehicle_id = :vehicle_id
        """), {"vehicle_id": vehicle_id}).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    state = redis_client.hget(f"vehicle:latest:{vehicle_id}", "payload")
    import json
    return {"vehicle": dict(row), "latest": json.loads(state) if state else None}


@app.get("/api/v1/live-vehicles", tags=["vehicles"])
def live_vehicle_states(limit: int = Query(500, ge=1, le=2000)) -> dict[str, object]:
    """Read the most recent cached state for seeded vehicles with telemetry."""
    keys = list(redis_client.scan_iter(match="vehicle:latest:*", count=500))
    states = []
    import json
    for key in keys[:limit]:
        payload = redis_client.hget(key, "payload")
        if payload:
            states.append(json.loads(payload))
    states.sort(key=lambda state: state.get("timestamp", ""), reverse=True)
    return {"items": states, "count": len(states)}


@app.get("/api/v1/alerts", tags=["alerts"])
def list_alerts(limit: int = Query(100, ge=1, le=500), status: str | None = None) -> dict[str, object]:
    where = "WHERE status = :status" if status else ""
    params: dict[str, object] = {"limit": limit}
    if status:
        if status not in {"open", "acknowledged", "resolved"}:
            raise HTTPException(status_code=422, detail="Unsupported alert status")
        params["status"] = status
    with engine.connect() as connection:
        rows = connection.execute(text(f"""
            SELECT id, fleet_id, vehicle_id, alert_type, severity, message, status, created_at
            FROM alerts {where} ORDER BY created_at DESC LIMIT :limit
        """), params).mappings().all()
    return {"items": [dict(row) for row in rows]}


@app.patch("/api/v1/alerts/{alert_id}", tags=["alerts"])
def update_alert_status(alert_id: str, update: AlertStatusUpdate) -> dict[str, object]:
    if update.status not in {"acknowledged", "resolved"}:
        raise HTTPException(status_code=422, detail="Status must be acknowledged or resolved")
    column = "acknowledged_at" if update.status == "acknowledged" else "resolved_at"
    with engine.begin() as connection:
        row = connection.execute(text(f"""
            UPDATE alerts SET status = :status, {column} = now()
            WHERE id = :alert_id AND status = :expected
            RETURNING id, fleet_id, vehicle_id, alert_type, severity, message, status, created_at,
                      acknowledged_at, resolved_at
        """), {"status": update.status, "alert_id": alert_id, "expected": "open" if update.status == "acknowledged" else "acknowledged"}).mappings().first()
        if row is None:
            exists = connection.execute(text("SELECT status FROM alerts WHERE id = :id"), {"id": alert_id}).scalar_one_or_none()
            if exists is None:
                raise HTTPException(status_code=404, detail="Alert not found")
            raise HTTPException(status_code=409, detail=f"Cannot transition alert from {exists} to {update.status}")
        connection.execute(text("""
            INSERT INTO audit_logs(action, resource_type, resource_id, event_data)
            VALUES (:action, 'alert', :resource_id, CAST(:event_data AS jsonb))
        """), {"action": f"alert.{update.status}", "resource_id": str(row["id"]), "event_data": '{"source":"api"}'})
    return dict(row)


@app.get("/api/v1/charging-stations", tags=["charging"])
def charging_stations(limit: int = Query(100, ge=1, le=500)) -> dict[str, object]:
    """Return active charging station connectors from the relational registry."""
    with engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT s.station_code, s.name, s.latitude, s.longitude, c.connector_type,
                   c.power_kw, c.available_ports, c.price_per_kwh_inr
            FROM charging_stations s JOIN station_connectors c ON c.station_id = s.id
            WHERE s.is_active ORDER BY s.station_code, c.connector_type LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return {"items": [dict(row) for row in rows]}


@app.get("/api/v1/vehicles/{vehicle_id}/charging-recommendations", tags=["charging"])
def charging_recommendations(vehicle_id: str, target_soc_pct: float = Query(80, gt=0, le=100)) -> dict[str, object]:
    import json
    with engine.connect() as connection:
        vehicle = connection.execute(text("""
            SELECT vehicle_id, connector_type, onboard_charging_kw, battery_capacity_kwh
            FROM vehicles WHERE vehicle_id = :vehicle_id
        """), {"vehicle_id": vehicle_id}).mappings().first()
        stations = connection.execute(text("""
            SELECT s.station_code, s.name, s.latitude, s.longitude, c.connector_type,
                   c.power_kw, c.available_ports, c.price_per_kwh_inr
            FROM charging_stations s JOIN station_connectors c ON c.station_id = s.id
            WHERE s.is_active AND c.available_ports > 0
        """)).mappings().all()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    payload = redis_client.hget(f"vehicle:latest:{vehicle_id}", "payload")
    if not payload:
        raise HTTPException(status_code=409, detail="No live telemetry available for vehicle")
    state = json.loads(payload)
    battery = state.get("battery_health_category")
    soc = float(state["soc_pct"])
    candidates = recommend_stations(latitude=float(state["latitude"]), longitude=float(state["longitude"]), range_km=float(state["range_km"]), connector_type=vehicle["connector_type"], onboard_kw=float(vehicle["onboard_charging_kw"]), capacity_kwh=float(vehicle["battery_capacity_kwh"]), soc_pct=soc, target_soc_pct=target_soc_pct, stations=[dict(station) for station in stations])
    return {"vehicle_id": vehicle_id, "current_soc_pct": soc, "target_soc_pct": target_soc_pct, "battery_health_category": battery, "method": "nearest_reachable_compatible_available_station", "score_weights": {"distance": 0.7, "price": 0.3}, "items": candidates[:10]}


@app.get("/api/v1/vehicles/{vehicle_id}/range-estimate", tags=["vehicles"])
def range_estimate(vehicle_id: str) -> dict[str, object]:
    import json
    with engine.connect() as connection:
        vehicle = connection.execute(text("""
            SELECT vehicle_id, battery_capacity_kwh FROM vehicles WHERE vehicle_id = :vehicle_id
        """), {"vehicle_id": vehicle_id}).mappings().first()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    payload = redis_client.hget(f"vehicle:latest:{vehicle_id}", "payload")
    if not payload:
        raise HTTPException(status_code=409, detail="No live telemetry available for vehicle")
    state = json.loads(payload)
    soc = float(state["soc_pct"])
    soh = float(state["soh_pct"])
    consumption = float(state["energy_consumption"])
    usable_kwh = float(vehicle["battery_capacity_kwh"]) * soh / 100.0
    estimate_km = round((soc / 100.0) * usable_kwh * 100.0 / consumption, 1)
    return {"vehicle_id": vehicle_id, "estimated_range_km": estimate_km, "simulator_reported_range_km": state["range_km"], "inputs": {"soc_pct": soc, "soh_pct": soh, "usable_capacity_kwh": round(usable_kwh, 2), "energy_consumption_kwh_per_100km": consumption}, "method": "energy_balance_baseline_v1", "limitations": "Does not model route, speed, weather, grade, HVAC, or charge/discharge efficiency."}


@app.get("/api/v1/vehicles/{vehicle_id}/battery-health", tags=["vehicles"])
def battery_health(vehicle_id: str) -> dict[str, object]:
    import json
    from datetime import datetime

    payload = redis_client.hget(f"vehicle:latest:{vehicle_id}", "payload")
    if not payload:
        raise HTTPException(status_code=404, detail="No live battery telemetry found")
    state = json.loads(payload)
    mongo = MongoClient(
        f"mongodb://{settings.mongo_user}:{settings.mongo_password}@{settings.mongo_host}:{settings.mongo_port}/?authSource=admin",
        serverSelectionTimeoutMS=1500,
    )
    try:
        samples = list(mongo["evfleet"]["telemetry"].find(
            {"vehicle_id": vehicle_id}, {"timestamp": 1, "soh_pct": 1, "charging": 1}
        ).sort("timestamp", -1).limit(500))
    finally:
        mongo.close()
    samples.reverse()
    degradation_rate = None
    rate_method = "insufficient_history"
    if len(samples) >= 2:
        first_ts, last_ts = samples[0]["timestamp"], samples[-1]["timestamp"]
        if isinstance(first_ts, datetime) and isinstance(last_ts, datetime):
            elapsed_days = (last_ts - first_ts).total_seconds() / 86400
            if elapsed_days >= 1 / 24 and elapsed_days > 0:
                degradation_rate = round((float(samples[-1]["soh_pct"]) - float(samples[0]["soh_pct"])) / elapsed_days, 4)
                rate_method = "observed_soh_change_pct_points_per_day"
    transitions = sum(samples[i]["charging"] != samples[i - 1]["charging"] for i in range(1, len(samples)))
    faulted = any(code in {"P0A80", "P1A10"} for code in state.get("fault_codes", []))
    reasons = []
    if state["soh_pct"] < 80:
        reasons.append("state_of_health_below_80_pct")
    if state["battery_temp_c"] >= 50:
        reasons.append("battery_temperature_at_or_above_50_c")
    if faulted:
        reasons.append("battery_diagnostic_trouble_code")
    return {
        "vehicle_id": vehicle_id, "soh_pct": state["soh_pct"], "battery_temp_c": state["battery_temp_c"],
        "category": state["battery_health_category"], "degradation_risk": state["degradation_risk"],
        "reasons": reasons, "observed_degradation_pct_points_per_day": degradation_rate,
        "degradation_method": rate_method, "charging_state_transitions_in_sample": transitions,
        "sample_count": len(samples), "method": "rules_and_recent_telemetry_observations",
        "limitations": "Recent charging state transitions are not equivalent to full charge cycles; degradation rate needs at least one hour of history and is not a future-life prediction.",
    }
