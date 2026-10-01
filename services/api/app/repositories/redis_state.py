"""Redis key conventions for current vehicle state and idempotency windows."""

import json
from typing import Any

from redis import Redis

try:
    from app.config import settings
except ModuleNotFoundError:
    from api_storage.config import settings

client = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
DEDUP_TTL_SECONDS = 7 * 24 * 60 * 60
_MONOTONIC_STATE_SCRIPT = """
local previous = redis.call('HGET', KEYS[1], 'sequence')
if previous and tonumber(previous) > tonumber(ARGV[1]) then return -1 end
if previous and tonumber(previous) == tonumber(ARGV[1]) then return 0 end
redis.call('HSET', KEYS[1], 'sequence', ARGV[1], 'timestamp', ARGV[2], 'payload', ARGV[3])
return 1
"""


def latest_state_key(vehicle_id: str) -> str:
    return f"vehicle:latest:{vehicle_id}"


def mark_event_seen(event_id: str, ttl_seconds: int = DEDUP_TTL_SECONDS) -> bool:
    """Atomically record an event id; False means a live key already existed."""
    return bool(client.set(f"event:seen:{event_id}", "1", nx=True, ex=ttl_seconds))


def set_latest_state(vehicle_id: str, state: dict[str, Any]) -> bool:
    """Atomically replace cached state only when the incoming vehicle sequence advances."""
    return apply_latest_state(vehicle_id, state) == 1


def apply_latest_state(vehicle_id: str, state: dict[str, Any]) -> int:
    """Return 1 when advanced, 0 for idempotent same sequence, and -1 when stale."""
    return int(client.eval(
        _MONOTONIC_STATE_SCRIPT,
        1,
        latest_state_key(vehicle_id),
        int(state["sequence"]),
        str(state["timestamp"]),
        json.dumps(state, separators=(",", ":")),
    ))


def get_latest_state(vehicle_id: str) -> dict[str, Any] | None:
    value = client.hget(latest_state_key(vehicle_id), "payload")
    return json.loads(value) if value else None
