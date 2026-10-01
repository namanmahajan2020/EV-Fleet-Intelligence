# Five-minute local demonstration runbook

This is a planned, repeatable script, not a recording. Durations are target pacing; capture a real screen recording before adding video timestamps to the solution document.

## Before the demo

1. Run `docker compose up --build -d`, wait until `docker compose ps` reports API healthy, then open `http://localhost:5173`.
2. Confirm simulator, ingestion, and stream-processor containers are running. Check `http://localhost:8000/api/v1/system/health` and `http://localhost:9090/targets`.
3. Keep the synthetic demo account credentials configured in your ignored repository `.env` ready. Do not expose the development MQTT broker to an external network.

## Script

| Time | Show / say |
|---|---|
| 0:00â€“0:30 | Frame the case-study problem: high-rate vehicle events are difficult to convert into safe, actionable charging decisions. Explain that all displayed data is synthetic. |
| 0:30â€“1:00 | Sign in and introduce the fleet overview and 100,000-vehicle registry. Clarify that registry size is not a throughput measurement. |
| 1:00â€“1:35 | Point to the live map and reporting vehicle list. Wait for refresh if simulator data is still arriving. Select a vehicle with low SoC. |
| 1:35â€“2:10 | Explain the current SoH and the transparent category. Open the range estimate and explain the energy-balance inputs and exclusions. |
| 2:10â€“2:40 | Review the fleet charge plan: vehicles marked charge now, plan soon, monitor, or service review. Compare the lowest estimated energy bill and alternatives; explain reachability reserve and 90% charging efficiency. |
| 2:40â€“3:00 | Show the open alert row and describe its rule trigger. Avoid resolving an alert during the presentation unless using a disposable seeded record. |
| 3:00â€“3:45 | Show the architecture diagram and trace one event across MQTT, ingestion validation, keyed Kafka, stream processing, MongoDB/Redis/PostgreSQL, and the API. |
| 3:45â€“4:15 | Show readiness and Prometheus API metrics. The local stack is single-node; describe the broker outage/backpressure design as implemented behavior, not as a measured recovery result. |
| 4:15â€“5:00 | State actual evidence: 50 unit tests and seven integration checks passed; a 5-RPS API check measured p95 12.61 ms / p99 13.84 ms; one local SQL search-plan comparison. State explicitly that 100K events/s, end-to-end latency SLOs, model accuracy, savings, and HA are not measured. Close with the next steps from the solution document. |

## Useful commands

```powershell
docker compose ps
docker compose logs --tail 30 simulator ingestion stream-processor api
docker compose --profile test run --build --rm unit-tests
```

## Recovery notes

If the map is empty, check simulator, ingestion, Kafka, and processor logs; confirm that the stream processor reports processed events and that `/api/v1/live-vehicles` returns items after login. If login fails after changing `.env`, use the seeded operator credentials actually configured in Compose; an already-created account keeps its original password until intentionally updated in the database.
