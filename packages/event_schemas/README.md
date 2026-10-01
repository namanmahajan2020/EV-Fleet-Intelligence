# Canonical event contract v1

`TelemetryEvent` in `telemetry.py` is the runtime source of truth; `telemetry.schema.json` is the interoperable JSON Schema for non-Python services. Events require a timezone-aware timestamp, bounded geographic/physical measurements, stable vehicle sequence, UUID event ID, and a known event type. Unknown fields are rejected.

- Units: speed km/h, battery temperature °C, distance/odometer km, charging power kW, energy consumption kWh/100 km, and battery percentages 0–100.
- `event_id` is stable for a vehicle sequence; consumers use it for idempotency. An old timestamp/sequence remains a valid event so out-of-order handling can be exercised by the stream processor.
- `charging=true` requires station ID and positive charging power; noncharging events require zero charging power.
- `FAULT` events carry one or more five-character OBD-II diagnostic codes. Missing fields, invalid ranges, unknown event types, and unexpected fields are rejected.
- Breaking changes require a new schema version/topic; additive optional fields may be introduced compatibly.

The current contract tests are `tests/unit/test_telemetry_schema.py`. The ingestion service will route invalid messages to the dead-letter topic with a bounded validation reason.
