"""Idempotent persistence adapters for one telemetry event."""

import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from api_storage import redis_state
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError
from redis import Redis

MONGO_USER = os.environ["MONGO_INITDB_ROOT_USERNAME"]
MONGO_PASSWORD = os.environ["MONGO_INITDB_ROOT_PASSWORD"]
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
DATABASE_URL = os.environ["DATABASE_URL"]
PG_CONNINFO = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
MONGO_URI = f"mongodb://{MONGO_USER}:{MONGO_PASSWORD}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin"

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
telemetry = mongo_client["evfleet"]["telemetry"]
redis_client = Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
pg_pool = ConnectionPool(PG_CONNINFO, min_size=1, max_size=10, open=True)


def initialize_storage() -> None:
    mongo_client.admin.command("ping")
    telemetry.create_index([("event_id", ASCENDING)], unique=True, name="uq_telemetry_event_id")
    telemetry.create_index([("vehicle_id", ASCENDING), ("timestamp", DESCENDING)], name="ix_vehicle_timestamp")
    telemetry.create_index([("timestamp", ASCENDING)], name="ix_telemetry_timestamp")
    telemetry.create_index([("ingested_at", ASCENDING)], expireAfterSeconds=7_776_000, name="ttl_telemetry_90d")
    redis_client.ping()
    with pg_pool.connection() as conn:
        conn.execute("SELECT 1")


def write_history(event: dict[str, Any]) -> bool:
    document = {**event, "ingested_at": datetime.now(timezone.utc)}
    timestamp = document.get("timestamp")
    if isinstance(timestamp, str):
        document["timestamp"] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    try:
        telemetry.insert_one(document)
        return True
    except DuplicateKeyError:
        return False


def update_latest_state(vehicle_id: str, state: dict[str, Any]) -> int:
    return redis_state.apply_latest_state(vehicle_id, state)


def save_alert(vehicle_id: str, alert: Any, occurred_at: datetime) -> dict[str, Any] | None:
    bucket = int(occurred_at.timestamp() // 300)
    dedupe_key = f"{vehicle_id}:{alert.alert_type}:{bucket}"
    alert_id = uuid5(NAMESPACE_URL, f"ev-fleet-alert/{dedupe_key}")
    query = """
      INSERT INTO alerts (id, fleet_id, vehicle_id, alert_type, severity, message, status, created_at, dedupe_key)
      SELECT %s, v.fleet_id, v.vehicle_id, %s, %s, %s, 'open', %s, %s
      FROM vehicles AS v
      WHERE v.vehicle_id = %s
      ON CONFLICT (dedupe_key) DO NOTHING
      RETURNING id, fleet_id, vehicle_id, alert_type, severity, message, status, created_at
    """
    with pg_pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, (alert_id, alert.alert_type, alert.severity, alert.message, occurred_at, dedupe_key, vehicle_id))
        row = cursor.fetchone()
        return row


def publish_latest_update(state: dict[str, Any]) -> int:
    return int(redis_client.publish("fleet:telemetry:updates", json.dumps(state, separators=(",", ":"))))


def close_storage() -> None:
    pg_pool.close()
    mongo_client.close()
