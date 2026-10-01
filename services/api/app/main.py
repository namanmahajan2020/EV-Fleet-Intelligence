"""Fleet API for health, live state, charging plans, stations, and alerts."""

import logging
from datetime import datetime, timezone
from itertools import islice
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from pymongo import MongoClient
from redis import Redis
from sqlalchemy import bindparam, text

from app.charging import charging_timing, recommend_stations
from app.config import settings
from app.db import SessionLocal, engine
from app.models import User
from app.repositories.redis_state import (
    CHARGING_PLAN_KEY,
    initialize_charging_priority_index,
    initialize_latest_telemetry_index,
    LATEST_TELEMETRY_KEY,
)
from app.repositories.redis_state import client as redis_client
from app.security import issue_token, verify_password, verify_token


class AlertStatusUpdate(BaseModel):
    status: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RangePredictionRequest(BaseModel):
    soc_pct: float = Field(ge=0, le=100)
    soh_pct: float = Field(gt=0, le=100)
    battery_capacity_kwh: float = Field(gt=0, le=300)
    energy_consumption_kwh_per_100km: float = Field(gt=0, le=200)
    battery_temp_c: float = Field(ge=-40, le=100)
    onboard_charging_kw: float = Field(gt=0, le=500)
    target_soc_pct: float = Field(gt=0, le=100)
    requested_trip_km: float = Field(ge=0, le=10000)
    speed_kmh: float = Field(ge=0, le=250)
    fault_codes: list[str] = Field(default_factory=list)

app = FastAPI(
    title="EV-Fleet Intelligence API",
    description="Fleet telemetry and EV charging intelligence API.",
    version="0.1.0",
)
HTTP_REQUESTS = Counter("evfleet_api_requests_total", "API requests", ["method", "path", "status"])
HTTP_LATENCY = Histogram("evfleet_api_request_seconds", "API request latency", ["method", "path"])


@app.on_event("startup")
def warm_fleet_charging_index() -> None:
    indexed = initialize_charging_priority_index()
    logging.getLogger("evfleet.api").info("Fleet charge priority index ready vehicles=%s", indexed)
    recent = initialize_latest_telemetry_index()
    logging.getLogger("evfleet.api").info("Latest telemetry index ready vehicles=%s", recent)


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


@app.get("/api/v1/analytics/timeseries", tags=["analytics"])
def telemetry_timeseries(period_hours: int = Query(24, ge=1, le=720)) -> dict[str, object]:
    """Aggregate real retained samples into hourly fleet trend points."""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=period_hours)
    mongo = MongoClient(
        f"mongodb://{settings.mongo_user}:{settings.mongo_password}@{settings.mongo_host}:{settings.mongo_port}/?authSource=admin",
        serverSelectionTimeoutMS=1500,
    )
    try:
        points = list(mongo["evfleet"]["telemetry"].aggregate([
            {"$match": {"timestamp": {"$gte": cutoff}}},
            {"$group": {
                "_id": {"$dateTrunc": {"date": "$timestamp", "unit": "hour"}},
                "events": {"$sum": 1}, "avg_soc_pct": {"$avg": "$soc_pct"},
                "avg_soh_pct": {"$avg": "$soh_pct"}, "avg_battery_temp_c": {"$avg": "$battery_temp_c"},
                "avg_consumption_kwh_per_100km": {"$avg": "$energy_consumption"},
                "charging_events": {"$sum": {"$cond": ["$charging", 1, 0]}},
            }}, {"$sort": {"_id": 1}}, {"$limit": 721},
        ]))
    finally:
        mongo.close()
    return {
        "period_hours": period_hours,
        "items": [{"timestamp": point["_id"].isoformat(), **{key: round(float(value), 2) if key != "events" and key != "charging_events" else int(value) for key, value in point.items() if key != "_id"}} for point in points],
        "method": "hourly_mongodb_telemetry_aggregation",
        "limitations": "Trends reflect retained simulator telemetry only; sparse early periods may have no samples.",
    }


@app.get("/api/v1/battery-health/summary", tags=["battery"])
def fleet_battery_summary() -> dict[str, object]:
    """Summarize real current Redis states, cached briefly to avoid repeated full scans."""
    import json

    cache_key = "fleet:battery-health:summary:v1"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    total = count = healthy = watch = critical = hot = charging = low = 0
    soh_sum = 0.0
    keys = redis_client.scan_iter(match="vehicle:latest:*", count=500)
    while batch := list(islice(keys, 500)):
        pipe = redis_client.pipeline(transaction=False)
        for key in batch:
            pipe.hget(key, "payload")
        for payload in pipe.execute():
            if not payload:
                continue
            try:
                state = json.loads(payload)
                count += 1
                soh = float(state["soh_pct"])
                soh_sum += soh
                category = state.get("battery_health_category")
                healthy += category == "healthy"
                watch += category == "watch"
                critical += category == "critical"
                hot += float(state["battery_temp_c"]) >= 50
                charging += bool(state.get("charging"))
                low += float(state["soc_pct"]) <= 20 or float(state["range_km"]) <= 35
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
    with engine.connect() as connection:
        total = int(connection.execute(text("SELECT count(*) FROM vehicles WHERE status = 'active'")).scalar_one())
    result = {
        "total_fleet": total, "reporting": count, "offline": max(0, total - count),
        "average_soh_pct": round(soh_sum / count, 2) if count else None,
        "healthy": healthy, "watch": watch, "critical": critical, "temperature_risk": hot,
        "charging": charging, "low_battery": low,
        "method": "current_redis_latest_state_aggregation",
        "limitations": "Only reporting vehicles contribute battery metrics; offline means no retained latest telemetry. No full charge-cycle counter is available.",
    }
    redis_client.setex(cache_key, 15, json.dumps(result))
    return result


@app.get("/api/v1/vehicles", tags=["vehicles"])
def list_vehicles(
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), search: str | None = None,
    state: str = Query("all", pattern="^(all|reporting|offline|charging|low_battery|critical)$"),
    battery_health: str = Query("all", pattern="^(all|healthy|watch|critical)$"),
    sort_by: str = Query("vehicle_id", pattern="^(vehicle_id|soc|range)$"),
) -> dict[str, object]:
    """Page the fleet registry; telemetry filters scan Redis server-side in bounded batches."""
    import json

    filtered = state != "all" or battery_health != "all"
    if not filtered:
        where = "WHERE vehicle_id ILIKE :search" if search else ""
        params: dict[str, object] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = f"%{search}%"
        with engine.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT vehicle_id, make, model, model_year, status, battery_capacity_kwh,
                       connector_type, onboard_charging_kw
                FROM vehicles {where} ORDER BY vehicle_id LIMIT :limit OFFSET :offset
            """), params).mappings().all()
            total = int(connection.execute(text(f"SELECT count(*) FROM vehicles {where}"), params).scalar_one())
        items = [dict(row) for row in rows]
        pipe = redis_client.pipeline(transaction=False)
        for row in items:
            pipe.hget(f"vehicle:latest:{row['vehicle_id']}", "payload")
        for row, payload in zip(items, pipe.execute(), strict=True):
            row["latest"] = json.loads(payload) if payload else None
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                "filters": {"state": state, "battery_health": battery_health, "sort_by": sort_by}}

    matches: list[tuple[str, dict[str, object]]] = []
    reporting_ids: set[str] = set()
    keys = redis_client.scan_iter(match="vehicle:latest:*", count=500)
    while batch := list(islice(keys, 500)):
        pipe = redis_client.pipeline(transaction=False)
        for key in batch:
            pipe.hget(key, "payload")
        for key, payload in zip(batch, pipe.execute(), strict=True):
            if not payload:
                continue
            vehicle_id = key.removeprefix("vehicle:latest:")
            reporting_ids.add(vehicle_id)
            if search and search.casefold() not in vehicle_id.casefold():
                continue
            try:
                live = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
            is_low = float(live["soc_pct"]) <= 20 or float(live["range_km"]) <= 35
            if state == "offline":
                continue
            if state == "charging" and not live.get("charging"):
                continue
            if state == "low_battery" and not is_low:
                continue
            if state == "critical" and live.get("battery_health_category") != "critical":
                continue
            if battery_health != "all" and live.get("battery_health_category") != battery_health:
                continue
            matches.append((vehicle_id, live))
    if state == "offline":
        if battery_health != "all":
            matches = []
        else:
            where = "WHERE status = 'active'"
            params: dict[str, object] = {}
            if search:
                where += " AND vehicle_id ILIKE :search"
                params["search"] = f"%{search}%"
            with engine.connect() as connection:
                registry_ids = connection.execute(text(f"SELECT vehicle_id FROM vehicles {where} ORDER BY vehicle_id"), params).scalars().all()
            matches = [(str(vehicle_id), {}) for vehicle_id in registry_ids if str(vehicle_id) not in reporting_ids]
    if sort_by == "soc":
        matches.sort(key=lambda entry: float(entry[1].get("soc_pct", 101)))
    elif sort_by == "range":
        matches.sort(key=lambda entry: float(entry[1].get("range_km", float("inf"))))
    else:
        matches.sort(key=lambda entry: entry[0])
    total = len(matches)
    page = matches[offset:offset + limit]
    page_ids = [vehicle_id for vehicle_id, _ in page]
    if not page_ids:
        rows = []
    else:
        query = text("""
            SELECT vehicle_id, make, model, model_year, status, battery_capacity_kwh,
                   connector_type, onboard_charging_kw
            FROM vehicles WHERE vehicle_id IN :vehicle_ids
        """).bindparams(bindparam("vehicle_ids", expanding=True))
        with engine.connect() as connection:
            rows = connection.execute(query, {"vehicle_ids": page_ids}).mappings().all()
    registry = {str(row["vehicle_id"]): dict(row) for row in rows}
    live_by_id = {vehicle_id: live for vehicle_id, live in page}
    items = []
    for vehicle_id in page_ids:
        if vehicle_id in registry:
            items.append({**registry[vehicle_id], "latest": live_by_id.get(vehicle_id) or None})
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "filters": {"state": state, "battery_health": battery_health, "sort_by": sort_by},
            "method": "server_side_live_state_filter_and_pagination"}


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
    """Return the most recently updated bounded sample of real cached vehicle states."""
    vehicle_ids = redis_client.zrevrange(LATEST_TELEMETRY_KEY, 0, limit - 1)
    keys = [f"vehicle:latest:{vehicle_id}" for vehicle_id in vehicle_ids]
    import json
    if keys:
        pipeline = redis_client.pipeline(transaction=False)
        for key in keys:
            pipeline.hget(key, "payload")
        payloads = pipeline.execute()
    else:
        payloads = []
    states = [json.loads(payload) for payload in payloads if payload]
    states.sort(key=lambda state: state.get("timestamp", ""), reverse=True)
    reporting_count = int(redis_client.zcard(CHARGING_PLAN_KEY))
    return {
        "items": states,
        "count": len(states),
        "reporting_vehicle_count": reporting_count,
        "sampled_limit": limit,
        "truncated": reporting_count > len(states),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "method": "bounded_most_recent_redis_latest_state_sample",
    }


@app.get("/api/v1/alerts", tags=["alerts"])
def list_alerts(limit: int = Query(100, ge=1, le=500), status: str | None = None, vehicle_id: str | None = None) -> dict[str, object]:
    conditions = []
    params: dict[str, object] = {"limit": limit}
    if status:
        if status not in {"open", "acknowledged", "resolved"}:
            raise HTTPException(status_code=422, detail="Unsupported alert status")
        params["status"] = status
        conditions.append("status = :status")
    if vehicle_id:
        params["vehicle_id"] = vehicle_id
        conditions.append("vehicle_id = :vehicle_id")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
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


@app.get("/api/v1/fleet/charging-plan", tags=["charging"])
def fleet_charging_plan(
    target_soc_pct: float = Query(80, gt=0, le=100),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, object]:
    """Prioritize reporting vehicles and show their cheapest feasible charge stop."""
    import json

    ranked_ids = redis_client.zrange(CHARGING_PLAN_KEY, 0, limit - 1)
    if ranked_ids:
        pipeline = redis_client.pipeline(transaction=False)
        for vehicle_id in ranked_ids:
            pipeline.hget(f"vehicle:latest:{vehicle_id}", "payload")
        raw_payloads = pipeline.execute()
    else:
        # Supports existing local Redis volumes until each vehicle emits a new event
        # and receives an entry in the priority index.
        keys = list(islice(redis_client.scan_iter(match="vehicle:latest:*", count=500), limit))
        pipeline = redis_client.pipeline(transaction=False)
        for key in keys:
            pipeline.hget(key, "payload")
        raw_payloads = pipeline.execute()
        ranked_ids = [key.removeprefix("vehicle:latest:") for key in keys]
    payloads: list[dict[str, object]] = []
    for payload in raw_payloads:
        if payload:
            try:
                payloads.append(json.loads(payload))
            except (TypeError, json.JSONDecodeError):
                continue
    if not payloads:
        return {
            "items": [], "count": 0, "reporting_vehicle_count": int(redis_client.zcard(CHARGING_PLAN_KEY)),
            "truncated": False, "target_soc_pct": target_soc_pct,
            "status_counts": {"charge_now": 0, "plan_soon": 0, "service_review": 0, "charging": 0, "monitor": 0},
            "limitations": "No vehicles are currently reporting live telemetry.",
        }

    vehicle_ids = [str(state["vehicle_id"]) for state in payloads]
    vehicle_query = text("""
        SELECT vehicle_id, connector_type, onboard_charging_kw, battery_capacity_kwh
        FROM vehicles WHERE vehicle_id IN :vehicle_ids
    """).bindparams(bindparam("vehicle_ids", expanding=True))
    with engine.connect() as connection:
        vehicles = connection.execute(vehicle_query, {"vehicle_ids": vehicle_ids}).mappings().all()
        station_rows = connection.execute(text("""
            SELECT s.station_code, s.name, s.latitude, s.longitude, c.connector_type,
                   c.power_kw, c.available_ports, c.price_per_kwh_inr
            FROM charging_stations s JOIN station_connectors c ON c.station_id = s.id
            WHERE s.is_active AND c.available_ports > 0
        """)).mappings().all()
    vehicle_by_id = {str(vehicle["vehicle_id"]): dict(vehicle) for vehicle in vehicles}
    stations = [dict(row) for row in station_rows]

    items = []
    for state in payloads:
        vehicle_id = str(state["vehicle_id"])
        vehicle = vehicle_by_id.get(vehicle_id)
        if not vehicle:
            continue
        action = charging_timing(
            soc_pct=float(state["soc_pct"]), range_km=float(state["range_km"]),
            battery_temp_c=float(state["battery_temp_c"]), fault_codes=list(state.get("fault_codes", [])),
            is_charging=bool(state.get("charging", False)),
        )
        options = []
        if not action["safety_hold"] and action["status"] != "charging":
            options = recommend_stations(
                latitude=float(state["latitude"]), longitude=float(state["longitude"]),
                range_km=float(state["range_km"]), connector_type=str(vehicle["connector_type"]),
                onboard_kw=float(vehicle["onboard_charging_kw"]),
                capacity_kwh=float(vehicle["battery_capacity_kwh"]), soc_pct=float(state["soc_pct"]),
                target_soc_pct=target_soc_pct, stations=stations,
                consumption_kwh_per_100km=float(state["energy_consumption"]),
            )
        best = options[0] if options else None
        items.append({
            "vehicle_id": vehicle_id,
            "latitude": state["latitude"], "longitude": state["longitude"],
            "speed_kmh": state["speed_kmh"], "timestamp": state["timestamp"],
            "soc_pct": state["soc_pct"], "soh_pct": state["soh_pct"],
            "range_km": state["range_km"], "battery_temp_c": state["battery_temp_c"],
            "battery_health_category": state["battery_health_category"],
            "battery_health_reasons": state.get("battery_health_reasons", []),
            "fault_codes": state.get("fault_codes", []),
            "charge_timing": action["status"], "charge_timing_label": action["label"],
            "safety_hold": action["safety_hold"],
            "best_station": best,
            "alternatives": options[1:4],
        })
    priority = {"charge_now": 0, "service_review": 1, "plan_soon": 2, "charging": 3, "monitor": 4}
    items.sort(key=lambda item: (
        priority[item["charge_timing"]],
        item["best_station"]["estimated_cost_inr"] if item["best_station"] else float("inf"),
        float(item["range_km"]),
    ))
    indexed_count = int(redis_client.zcard(CHARGING_PLAN_KEY))
    status_counts = {
        "charge_now": int(redis_client.zcount(CHARGING_PLAN_KEY, 0, "(0.1")),
        "service_review": int(redis_client.zcount(CHARGING_PLAN_KEY, 0.5, "(0.6")),
        "plan_soon": int(redis_client.zcount(CHARGING_PLAN_KEY, 1, "(1.1")),
        "charging": int(redis_client.zcount(CHARGING_PLAN_KEY, 1.25, "(1.35")),
        "monitor": int(redis_client.zcount(CHARGING_PLAN_KEY, 2, "(2.1")),
    }
    if indexed_count == 0:
        for item in items:
            status_counts[item["charge_timing"]] += 1
    return {
        "items": items, "count": len(items), "target_soc_pct": target_soc_pct,
        "reporting_vehicle_count": indexed_count or len(items), "truncated": (indexed_count or len(items)) > limit,
        "status_counts": status_counts,
        "method": "state_thresholds_then_lowest_estimated_energy_bill",
        "ordering": "charge urgency, then estimated bill, then remaining range",
        "limitations": "Live flat station prices only; no time-of-use tariffs, route/traffic, reservation, or charging execution. Only vehicles reporting telemetry are included.",
    }


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
    timing = charging_timing(
        soc_pct=soc, range_km=float(state["range_km"]),
        battery_temp_c=float(state["battery_temp_c"]), fault_codes=list(state.get("fault_codes", [])),
        is_charging=bool(state.get("charging", False)),
    )
    candidates = [] if timing["safety_hold"] or timing["status"] == "charging" else recommend_stations(
        latitude=float(state["latitude"]), longitude=float(state["longitude"]),
        range_km=float(state["range_km"]), connector_type=vehicle["connector_type"],
        onboard_kw=float(vehicle["onboard_charging_kw"]), capacity_kwh=float(vehicle["battery_capacity_kwh"]),
        soc_pct=soc, target_soc_pct=target_soc_pct,
        stations=[dict(station) for station in stations],
        consumption_kwh_per_100km=float(state["energy_consumption"]),
    )
    return {
        "vehicle_id": vehicle_id, "current_soc_pct": soc, "current_range_km": state["range_km"],
        "target_soc_pct": target_soc_pct, "battery_health_category": battery,
        "charge_timing": timing, "method": "lowest_estimated_energy_bill_with_reachability_and_safety_checks",
        "items": candidates[:10],
        "limitations": "Uses current flat station prices and straight-line distance; excludes time-of-use tariffs, traffic, station fees, and route energy uncertainty.",
    }


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


@app.get("/api/v1/vehicles/{vehicle_id}/telemetry", tags=["vehicles"])
def vehicle_telemetry(vehicle_id: str, period_hours: int = Query(24, ge=1, le=2160), limit: int = Query(500, ge=1, le=2000)) -> dict[str, object]:
    """Return indexed, retained historical telemetry for one vehicle."""
    from datetime import datetime, timedelta, timezone

    with engine.connect() as connection:
        exists = connection.execute(text("SELECT 1 FROM vehicles WHERE vehicle_id = :id"), {"id": vehicle_id}).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=period_hours)
    mongo = MongoClient(
        f"mongodb://{settings.mongo_user}:{settings.mongo_password}@{settings.mongo_host}:{settings.mongo_port}/?authSource=admin",
        serverSelectionTimeoutMS=1500,
    )
    try:
        samples = list(mongo["evfleet"]["telemetry"].find(
            {"vehicle_id": vehicle_id, "timestamp": {"$gte": cutoff}},
            {"_id": 0, "timestamp": 1, "soc_pct": 1, "soh_pct": 1, "battery_temp_c": 1,
             "range_km": 1, "energy_consumption": 1, "speed_kmh": 1, "charging": 1,
             "charging_power_kw": 1, "event_type": 1},
        ).sort("timestamp", -1).limit(limit))
    finally:
        mongo.close()
    samples.reverse()
    for sample in samples:
        sample["timestamp"] = sample["timestamp"].isoformat()
    return {"vehicle_id": vehicle_id, "period_hours": period_hours, "items": samples,
            "count": len(samples), "method": "indexed_mongodb_telemetry_history"}


@app.post("/api/v1/predictions/range", tags=["predictions"])
def predict_range(request: RangePredictionRequest) -> dict[str, object]:
    """Run a transparent deterministic energy-balance estimate from supplied inputs."""
    from app.charging import charging_timing

    usable_capacity = request.battery_capacity_kwh * request.soh_pct / 100
    estimated_range = request.soc_pct / 100 * usable_capacity * 100 / request.energy_consumption_kwh_per_100km
    full_range = usable_capacity * 100 / request.energy_consumption_kwh_per_100km
    timing = charging_timing(
        soc_pct=request.soc_pct, range_km=estimated_range, battery_temp_c=request.battery_temp_c,
        fault_codes=request.fault_codes,
    )
    target = max(request.soc_pct, request.target_soc_pct)
    energy_needed = max(0.0, (target - request.soc_pct) / 100 * usable_capacity)
    charge_minutes = energy_needed / (request.onboard_charging_kw * 0.9) * 60
    trip_feasible = request.requested_trip_km <= estimated_range * 0.9
    return {
        "inputs": request.model_dump(),
        "prediction": {
            "estimated_remaining_range_km": round(estimated_range, 1),
            "estimated_full_charge_range_km": round(full_range, 1),
            "trip_with_10pct_reserve_feasible": trip_feasible,
            "charging_required_for_trip": not trip_feasible,
            "charge_timing": timing["status"], "charge_timing_label": timing["label"],
            "safety_hold": timing["safety_hold"],
            "estimated_charge_minutes_to_target": round(charge_minutes),
            "target_soc_pct": target,
            "battery_risk": "high" if request.soh_pct < 80 or request.battery_temp_c >= 60 else "moderate" if request.soh_pct < 90 or request.battery_temp_c >= 50 else "baseline",
        },
        "method": "energy_balance_baseline_v1",
        "explanation": "Range = SoC × SoH-adjusted battery capacity ÷ consumption. Trip feasibility reserves 10% of estimated range. Charging time assumes constant onboard power and 90% efficiency.",
        "limitations": "Deterministic estimate, not trained ML inference. Does not model route, traffic, weather, grade, HVAC, battery curve, charger availability, or charging fees.",
    }


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
