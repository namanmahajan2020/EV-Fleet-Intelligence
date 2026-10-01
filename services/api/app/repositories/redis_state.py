"""Redis key conventions for current vehicle state and idempotency windows."""

import itertools
import json
from typing import Any
from uuid import uuid4

from redis import Redis

try:
    from app.config import settings
except ModuleNotFoundError:
    from api_storage.config import settings

client = Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
DEDUP_TTL_SECONDS = 7 * 24 * 60 * 60
CHARGING_PLAN_KEY = "fleet:charging:priority"
CHARGING_PLAN_READY_KEY = "fleet:charging:priority:ready:v2"
# The score groups match charging_timing: now=0, service review=0.5, soon=1,
# currently charging=1.25, monitor=2.
# A small range component sorts vehicles by lower remaining range within each group.
_MONOTONIC_STATE_SCRIPT = """
local previous = redis.call('HGET', KEYS[1], 'sequence')
if previous and tonumber(previous) > tonumber(ARGV[1]) then return -1 end
if previous and tonumber(previous) == tonumber(ARGV[1]) then return 0 end
redis.call('HSET', KEYS[1], 'sequence', ARGV[1], 'timestamp', ARGV[2], 'payload', ARGV[3])
local soc = tonumber(ARGV[4])
local range = tonumber(ARGV[5])
local temperature = tonumber(ARGV[6])
local priority = 2
if temperature >= 60 or ARGV[7] == '1' then
    priority = 0.5
elseif ARGV[8] == '1' then
    priority = 1.25
elseif soc <= 20 or range <= 35 then
    priority = 0
elseif soc <= 40 or range <= 80 then
    priority = 1
end
local score = priority + math.min(range, 9999) / 100000
redis.call('ZADD', KEYS[2], score, ARGV[9])
return 1
"""


def latest_state_key(vehicle_id: str) -> str:
    return f"vehicle:latest:{vehicle_id}"


def _priority_score(state: dict[str, Any]) -> float:
    soc = float(state["soc_pct"])
    range_km = float(state["range_km"])
    safety_hold = float(state["battery_temp_c"]) >= 60 or any(
        code in {"P0A80", "P1A10"} for code in state.get("fault_codes", [])
    )
    if safety_hold:
        priority = 0.5
    elif bool(state.get("charging", False)):
        priority = 1.25
    elif soc <= 20 or range_km <= 35:
        priority = 0
    elif soc <= 40 or range_km <= 80:
        priority = 1
    else:
        priority = 2
    return priority + min(range_km, 9999) / 100000


def initialize_charging_priority_index(batch_size: int = 500) -> int:
    """Backfill the plan index once before the stream processor starts consuming."""
    if client.exists(CHARGING_PLAN_READY_KEY):
        return int(client.zcard(CHARGING_PLAN_KEY))
    lock_key = f"{CHARGING_PLAN_READY_KEY}:lock"
    lock_token = str(uuid4())
    if not client.set(lock_key, lock_token, nx=True, ex=900):
        return int(client.zcard(CHARGING_PLAN_KEY))
    indexed = 0
    try:
        if client.exists(CHARGING_PLAN_READY_KEY):
            return int(client.zcard(CHARGING_PLAN_KEY))
        keys = client.scan_iter(match="vehicle:latest:*", count=batch_size)
        while batch := list(itertools.islice(keys, batch_size)):
            reads = client.pipeline(transaction=False)
            for key in batch:
                reads.hget(key, "payload")
            payloads = reads.execute()
            scores: dict[str, float] = {}
            for key, payload in zip(batch, payloads, strict=True):
                if not payload:
                    continue
                try:
                    state = json.loads(payload)
                    scores[str(state["vehicle_id"])] = _priority_score(state)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
            if scores:
                client.zadd(CHARGING_PLAN_KEY, scores)
                indexed += len(scores)
        client.set(CHARGING_PLAN_READY_KEY, "1")
        return indexed
    finally:
        client.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end",
            1, lock_key, lock_token,
        )


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
        2,
        latest_state_key(vehicle_id),
        CHARGING_PLAN_KEY,
        int(state["sequence"]),
        str(state["timestamp"]),
        json.dumps(state, separators=(",", ":")),
        float(state["soc_pct"]),
        float(state["range_km"]),
        float(state["battery_temp_c"]),
        int(any(code in {"P0A80", "P1A10"} for code in state.get("fault_codes", []))),
        int(bool(state.get("charging", False))),
        vehicle_id,
    ))


def get_latest_state(vehicle_id: str) -> dict[str, Any] | None:
    value = client.hget(latest_state_key(vehicle_id), "payload")
    return json.loads(value) if value else None
