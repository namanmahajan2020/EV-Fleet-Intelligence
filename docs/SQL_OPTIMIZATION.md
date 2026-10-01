# PostgreSQL query plans (local run)

Captured from the Compose PostgreSQL 16 container after seeding 100,000 vehicle rows. `EXPLAIN (ANALYZE, BUFFERS)` was run once before and once after migration `0003_vehicle_search`. Execution times are host- and cache-dependent single-run observations, not a benchmark.

| Query | Before | After | Change |
|---|---:|---:|---|
| Contains search: `vehicle_id ILIKE '%EV-000001%'` | 43.465 ms, Seq Scan, 99,999 rows removed, 1,640 buffer hits | 2.048 ms, Bitmap Index Scan + Heap Scan using `ix_vehicles_vehicle_id_trgm`, 76 buffer hits | Added `pg_trgm` GIN index. |
| Open alerts by fleet ordered newest, limit 100 | 0.116 ms, backward `ix_alerts_created_at` scan | 0.098 ms, same plan and index | No schema change; runtime variation at this scale is noise. |
| Vehicle detail by primary key | 0.061 ms, `vehicles_pkey` Index Scan | 0.058 ms, same plan | No schema change; runtime variation at this scale is noise. |

The alert table is small in this run, so the measurement does not justify adding another index. For robust comparison, repeat with warm/cold cache controls and realistic multi-tenant cardinality. The trigram index increases write/storage overhead; retain it only if substring search is a product requirement.

The first query plans were captured at migration `0002_alert_dedupe`; after startup advanced to `0003_vehicle_search`. Exact commands are:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT vehicle_id FROM vehicles WHERE vehicle_id ILIKE '%EV-000001%';

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, vehicle_id, severity, created_at FROM alerts
WHERE fleet_id = (SELECT id FROM fleets WHERE code = 'fleet-demo')
  AND status = 'open' ORDER BY created_at DESC LIMIT 100;

EXPLAIN (ANALYZE, BUFFERS)
SELECT vehicle_id, make, model, model_year FROM vehicles
WHERE vehicle_id = 'EV-000001';
```
