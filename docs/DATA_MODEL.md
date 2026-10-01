# Data Model

## Store ownership

| Store | Data | Consistency intent |
|---|---|---|
| PostgreSQL | Tenant, fleet, users, roles/memberships, vehicle registry, stations/connectors, alerts, charging sessions, audit records | Transactional/strong consistency for ownership and operations data. |
| MongoDB | Canonical per-event telemetry history and bounded historical consumption aggregates | Event writes are append-oriented; event IDs are unique; consumers can replay and reconcile. TTL currently retains raw Mongo telemetry for 90 days. |
| Redis | Latest vehicle JSON state and time-bounded event-seen keys | Fast, rebuildable cache; tolerate eviction/rebuild from durable history. |
| Parquet archive | Planned historical analytical files | Batch-oriented; implementation and retention/cost evidence are pending. |

## PostgreSQL entities

`Tenant 1—N Fleet`; users and fleets are associated through `FleetMembership`, which references a `Role`; each fleet contains vehicles; stations contain connector records; vehicles have alerts and charging sessions; audit rows may reference the acting user. Vehicle registry rows contain only synthetic IDs/VIN-like identifiers. High-volume telemetry is deliberately kept out of the relational core.

The SQLAlchemy models live in `services/api/app/models.py`; initial migration is `services/api/alembic/versions/0001_initial_schema.py`. Alembic runs on API container startup. Seed inserts are idempotent and write vehicles in bounded batches. The local Compose seed requested 100,000 vehicles; confirm the actual row count in the startup logs or with:

```sh
docker compose exec -T postgres psql -U evfleet -d evfleet -c 'SELECT count(*) FROM vehicles;'
```

## Indexes

- Unique indexed tenant slug and fleet code.
- Vehicle primary key and synthetic VIN unique constraint; composite `(fleet_id, status)` supports fleet status views.
- Station code unique; station connector unique by `(station_id, connector_type)`.
- Alert lookup by `(fleet_id, status, created_at)` and vehicle.
- Mongo unique event ID, `(vehicle_id, timestamp DESC)`, and TTL index on `ingested_at` (90 days).
- Mongo timestamp index supports the bounded historical consumption aggregation.
- PostgreSQL trigram GIN index on vehicle ID supports case-insensitive contains search.
- Redis keys: `vehicle:latest:{vehicle_id}` and `event:seen:{event_id}` (seven-day idempotency window).

Migration `0003_vehicle_search` creates the PostgreSQL trigram GIN index. Actual query plans and a single-run before/after search observation are recorded in [`SQL_OPTIMIZATION.md`](SQL_OPTIMIZATION.md). The case-study 1 KB × 100K events/s capacity calculation is a scenario estimate; benchmark capacity is not established. The on-demand Mongo aggregation is not a Parquet or warehouse archive.
