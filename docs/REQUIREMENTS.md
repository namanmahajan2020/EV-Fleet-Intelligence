# Requirements and traceability

The source documents are the supplied `Motorq_Hackathon_Problem_Statement.pdf` and `Motorq_Hackathon_Solution_Document_Template.docx`. The PDF defines challenge targets and deliverables; the Word document defines the required 17-section submission structure. The approved project brief chooses synthetic EV fleet charging and battery visibility. Requirements remain goals until tested with evidence.

## Product scope

Primary user: fleet manager. Other stakeholders include drivers, fleet operators, OEM data teams, and charging operators. This is an independent academic exercise, not affiliated with Motorq. Product fixtures and telemetry are synthetic and contain no vehicle-owner data.

## Functional requirements

| ID | Requirement | Current code / evidence | Status |
|---|---|---|---|
| FR-01 | Generate and seed at least 100K synthetic vehicles with reproducible movement, battery state, faults, events, and burst controls | `services/simulator/`, `services/api/app/seed.py`; 100,000 registered and simulator state count verified | Implemented locally; event throughput not measured |
| FR-02 | Validate canonical events and handle duplicate, stale, malformed, missing, unknown input | `packages/event_schemas/`, `services/ingestion/`; contract tests; malformed payload observed in DLQ | Implemented locally |
| FR-03 | MQTT intake, keyed Kafka topics, retries, DLQ, backpressure, recovery | `services/ingestion/`; six telemetry partitions and local valid/DLQ events observed | Implemented locally; broker outage recovery not measured |
| FR-04 | Process events into history, latest state, derived fields, alerts, and live updates | `services/stream_processor/`; live writes observed; live-stack integration suite covers Mongo/Redis | Implemented locally; latency target not measured |
| FR-05 | Relational core, telemetry history, latest-state cache, historical batch analytics | PostgreSQL/MongoDB/Redis storage; bounded on-demand Mongo consumption aggregation with timestamp index | Partial: no scheduled/distributed warehouse pipeline |
| FR-06 | Fleet-wide charge timing/placement plan plus live map, battery/range, charger alternatives, alerts | `apps/web/`; `/api/v1/fleet/charging-plan`; authenticated UI and API integration checks | Implemented locally on reporting synthetic vehicles; screenshot not captured |
| FR-07 | SoH, temperature risk, charging observations, range baseline; compare learned model if justified | `services/stream_processor/app/domain.py`, `/battery-health`, `/range-estimate`; no labeled data/model | Partial baseline only |
| FR-08 | Recommend when to charge and the lowest estimated bill at a reachable compatible available station | `services/api/app/charging.py`; `/api/v1/fleet/charging-plan` and vehicle charging endpoint | Implemented locally using explicit urgency thresholds, active-session and safety-hold states, 5 km range reserve, detour energy, flat seeded tariffs, and charge efficiency; route and dynamic tariff inputs absent |
| FR-09 | Alert creation, status lifecycle, audit, offline and charger alerts | Stream rules and API transitions/audit; no offline or charger availability detector | Partial |
| FR-10 | Versioned secure, paginated APIs and live dashboard data | FastAPI `/api/v1`, bearer tokens, React polling | Partial; SSE/WebSocket absent |
| FR-11 | JWT, RBAC, fleet authorization, rate limit, secure config, audit | `services/api/app/security.py`, `main.py`, `SECURITY.md`; auth and 401 smoke | Partial; tenant row enforcement/OIDC/TLS absent |
| FR-12 | Metrics/logs/traces for API and stream performance | API Prometheus counter/histogram, Compose scrape, service logs | Partial; processor lag/latency metrics and traces absent |

## Challenge NFRs

| Target | Evidence state |
|---|---|
| 100,000+ events/s and 3x burst for five minutes | Not yet measured. 100K seeded vehicles are not events/s evidence. |
| Event-to-dashboard under 2s; critical alert under 5s | Not yet measured. |
| API p95 <200ms, p99 <500ms | One 60-second 5-RPS fleet-summary test measured p95 12.61 ms / p99 13.84 ms; modest local API test only. |
| 99.9% availability and broker/pod recovery | Not yet measured; local services are single instance. |
| Horizontal scaling and cloud portability | Compose runs locally; no cloud manifests, IaC, or scale test. |
| Historical batch, hot/warm/cold tiers and capacity | Bounded 30-day on-demand Mongo aggregation exists; hot/warm/cold costed tiers are absent. |
| Three PostgreSQL query plans and optimization | Captured in `docs/SQL_OPTIMIZATION.md`; one measured index improvement, two plans unchanged. |
| Core code coverage â‰¥80% | 50 unit and 7 integration checks pass; selected five modules 88% line coverage, but ingestion topics are 27%; branch coverage is unmeasured and aggregate is not 80% across all core services. The unit-test command writes an ignored local XML report to `docs/evidence/coverage.xml`. |

## Template traceability

`docs/SOLUTION_DOCUMENT_CONTENT.md` follows the official headings 1â€“17. It links architecture diagrams, ADRs, tests, performance notes, security/STRIDE, SQL plans, demo runbook, and declarations. Team metadata, product screenshot, video, and exported PDF remain submission-time items.

The detailed per-requirement file paths and statuses are in [`FINAL_REQUIREMENTS_AUDIT.md`](FINAL_REQUIREMENTS_AUDIT.md). The current five-minute demo plan is in [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md); its timestamps are script pacing, not recorded-video timestamps.
