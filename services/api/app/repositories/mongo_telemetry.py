"""MongoDB document store for validated telemetry history."""

from datetime import datetime, timezone
from typing import Any

from app.config import settings
from pymongo import MongoClient

_client = MongoClient(
    f"mongodb://{settings.mongo_user}:{settings.mongo_password}"
    f"@{settings.mongo_host}:{settings.mongo_port}/?authSource=admin",
    serverSelectionTimeoutMS=3000,
)
_collection = _client["evfleet"]["telemetry"]


def append_event(event: dict[str, Any]) -> bool:
    """Insert a validated event once; return False when its event_id already exists."""
    document = {**event, "ingested_at": datetime.now(timezone.utc)}
    timestamp = document.get("timestamp")
    if isinstance(timestamp, str):
        document["timestamp"] = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    try:
        _collection.insert_one(document)
        return True
    except Exception as exc:
        # Avoid coupling the domain to MongoDB exception classes or leaking driver types.
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            return False
        raise


def recent_events(vehicle_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Fetch a bounded newest-first telemetry page for a vehicle."""
    return list(
        _collection.find({"vehicle_id": vehicle_id}, {"_id": False})
        .sort("timestamp", -1)
        .limit(max(1, min(limit, 500)))
    )
