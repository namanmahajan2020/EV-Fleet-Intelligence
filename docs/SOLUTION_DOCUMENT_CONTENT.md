# Connected Vehicle Intelligence Hackathon â€” Solution Document

Submission format: export this document to PDF after team metadata, screenshots, and video are supplied.

To be submitted by: `[Team Name]`
Team members & roles: `[Names, roles, emails]`
Problem space: EV fleet battery visibility and charging recommendations
Repository URL: `https://github.com/namanmahajan2020/EV-Fleet-Intelligence`
Demo video URL (â‰¤ 5 min): `Not recorded`
Date of submission: `[DD/MM/YYYY]`

## Table of Contents

1. Executive Summary
2. Problem Statement & Validation
3. Solution Description
4. Feature List
5. Solution Architecture (High-Level Design)
6. Low-Level Design
7. Non-Functional Requirements & Performance Benchmarks
8. Security & Compliance
9. Test Strategy
10. Observability
11. AI / ML Component
12. Architecture Decisions, Risks & Future Enhancements
13. Demo Video (5 Minutes Maximum)
14. Repository Checklist
15. Conclusion
16. Declarations
17. Appendix

## 1. Executive Summary

This prototype helps a fleet manager inspect synthetic EV fleet state, identify battery and low-charge signals, and compare reachable compatible charging stations. Its working local path is a deterministic synthetic event stream through MQTT, schema validation, Kafka, storage and stream processing, FastAPI, and a React map/dashboard. The local seed registers 100,000 synthetic vehicles. A single local PostgreSQL query test reduced a vehicle contains-search plan from 43.465 ms sequential scan to 2.048 ms using a trigram index in one run. The latest automated unit run passed 48 tests. A 60-second API test at 5 requests/second measured p95 12.61 ms and p99 13.84 ms for the fleet-summary endpoint; this modest local run does not establish the challenge-scale throughput or end-to-end latency targets. Model accuracy and business impact have not been measured.

## 2. Problem Statement & Validation

### 2.1 Problem Statement

**Primary user:** EV fleet manager. The manager needs a way to identify vehicles with low charge or battery-health warning signals and choose reachable compatible chargers because telemetry, charging capacity, and vehicle state are difficult to act on together. The actual financial or safety cost for a customer fleet is not measured in this prototype.

**Secondary stakeholders:** drivers, fleet operators, OEM data teams, and charging operators.

### 2.2 Evidence & Validation

The supplied case study describes the challenge of high-volume connected-vehicle streams and calls for a 100,000+ vehicle simulator, real-time processing, polyglot storage, secure APIs, and a usable web UI. Those are case-study requirements, not independent proof of a particular fleet's battery or charging pain. The project validates pipeline and interface behavior with synthetic data only. No interviews, public fleet dataset, or customer baseline was used. Existing portals and telematics tools were not independently evaluated here.

| Evidence / assumption | Source or method | What it shows | Confidence |
|---|---|---|---|
| Case-study scale and capability targets | Supplied PDF | Required demonstration scope | High as a statement of the brief |
| Fleet includes low-charge vehicles and station alternatives | Deterministic simulator and seeded station registry | Plumbing and recommendation path work on generated values | High for generated fixtures only |
| This saves customer operating cost | Not measured | No savings conclusion can be made | Not established |

### 2.3 Impact & Success Metrics

The 100,000-vehicle registry is verified. Events/second, 10K-versus-100K service capacity, avoided breakdowns, charging savings, safety improvement, and emissions impact are **Not yet measured**. The local stack is one broker and one processor instance; it does not establish the scale targets. Synthetic data has no real drivers or vehicle owners.

## 3. Solution Description

### 3.1 Solution Overview & User Journey

The simulator emits telemetry, ingestion validates the shared JSON event contract and writes valid messages to keyed Kafka topics. The stream processor stores event history and updates the latest cache, evaluates transparent alerts, and derives battery signals. A fleet manager signs in, reviews fleet counts/map/alerts, selects a vehicle, and inspects the range baseline and reachable station options with estimated charge time and cost.

Screenshot: **Not captured in this work session.** The live dashboard is available at `http://localhost:5173` while the local Compose stack is running. Capture an actual screen before exporting a submission PDF; no screenshot is represented here as evidence.

### 3.2 Key Value Proposition

The prototype combines a vehicle's current synthetic state with compatibility, reachability, station availability, indicative time, and indicative cost in one path. It demonstrates the user journey and the underlying interfaces. It does not yet prove savings or differentiate against commercial products through a measured comparison.

### 3.3 Innovative Ideas

1. Vehicle-keyed durable stream with duplicate-safe sinks and monotonic Redis state: exercised by unit checks and a live local pipeline.
2. Explainable battery categories with reason codes: computed by explicit rules, not an opaque model.
3. Charger candidates ranked with a stated 70% distance / 30% price heuristic after range, connector, and port filtering: tested on deterministic station fixtures and smoke-called through the API.

## 4. Feature List

| ID | Feature | User story | Priority | Status | Code path | Demo timestamp |
|---|---|---|---|---|---|---|
| F-01 | Synthetic registry and simulator | As a fleet manager, I need reproducible synthetic fleet state for a demonstration | Must | Done locally; load target partial | `services/api/app/seed.py`, `services/simulator/` | Not recorded |
| F-02 | MQTT, validation, Kafka and DLQ | As a platform operator, I need valid events routed and invalid events isolated | Must | Done locally; scale not measured | `services/ingestion/`, `packages/event_schemas/` | Not recorded |
| F-03 | Stream processing and polyglot persistence | As an operator, I need latest state, history, and operational alerts | Must | Done locally; recovery target not measured | `services/stream_processor/` | Not recorded |
| F-04 | Fleet API and bearer login | As a fleet manager, I need protected fleet data | Must | Partial: single-demo-fleet scope only | `services/api/app/` | Not recorded |
| F-05 | Map, alerts, vehicle detail | As a fleet manager, I need live operational context | Must | Done locally | `apps/web/src/main.tsx` | Not recorded |
| F-06 | Range and charger recommendation | As a driver manager, I need reachable compatible options and estimated cost | Must | Partial: heuristic estimates, limited station data | `services/api/app/charging.py`, `services/api/app/main.py` | Not recorded |
| F-07 | Battery remaining-life model | As a fleet manager, I need calibrated health predictions | Should | Planned; no labeled data/model | `docs/ML_EVALUATION.md` | â€” |
| F-08 | 100K events/s, multi-node recovery | As an operator, I need challenge-scale service | Must | Not measured / not demonstrated | `docs/PERFORMANCE.md` | â€” |

## 5. Solution Architecture (High-Level Design)

### 5.1 Architecture Overview

The context, container, relational ER, telemetry sequence, and broker recovery diagrams are in [`diagrams/architecture.md`](diagrams/architecture.md). Expected hop latency has not been instrumented end to end; API request latency metrics exist, but no latency percentile claim is made.

### 5.2 Technology Stack & Justification

| Layer | Choice | Reason / trade-off |
|---|---|---|
| Ingestion / messaging | Mosquitto MQTT, Kafka | Lightweight publisher protocol then durable partitioned replay; local brokers are single-node |
| Stream processing | Python consumer | Small event-specific state and alert logic; no distributed stream framework in this prototype |
| Storage | PostgreSQL, MongoDB, Redis | Relational ownership/audit; telemetry history; hot latest state |
| Backend / frontend | FastAPI, React, Leaflet | Typed API and map dashboard; security is local prototype only |
| ML | None | No independent labeled dataset; transparent rules and energy-balance baseline instead |
| Observability | Prometheus, Grafana | API request metrics; no distributed traces or processor metrics yet |

### 5.3 Data Architecture

PostgreSQL stores normalized tenant, fleet, vehicle, role/membership, charger, alert, session, and audit records. MongoDB stores time-stamped event history with unique event IDs, a 90-day TTL, and a timestamp index for bounded on-demand consumption aggregation. Redis stores a sequence-guarded latest state and Pub/Sub updates. This is deliberate polyglot storage; history and cache are eventually consistent with the relational registry. Kafka partitions by `vehicle_id`. One canonical event is approximately a few hundred bytes in this synthetic schema; no production byte-volume profile has been measured. At the case-study assumption of 1 KB and 100,000 events/s, raw volume is 8.64 TB/day before compression; that is a scenario estimate from the brief, not measured output.

Actual local query plans and their single-run times are recorded in [`SQL_OPTIMIZATION.md`](SQL_OPTIMIZATION.md). A trigram GIN index reduced one `%contains%` lookup in the seeded registry; other tested queries already use indexes.

### 5.4 Deployment View

Docker Compose runs all local components on one host and network. Services do not use Kubernetes, cloud autoscaling, multi-zone replicas, or a managed secret store. Docker images and standard protocols reduce cloud lock-in, but a second-cloud deployment has not been demonstrated and would require deployment/IaC work.

## 6. Low-Level Design

### 6.1 Layering & Separation of Concerns

The repository separates API, simulator, ingestion, stream processor, event contract, UI, infrastructure, and tests. Domain calculations such as `recommend_stations` and `assess_battery_health` are pure functions; adapters own database/broker access. The codebase is service-oriented, but it is not a fully enforced hexagonal architecture.

### 6.2 Design Principles Applied

Configuration is read from environment variables. Event IDs and alert dedupe keys make replay idempotent; Redis only advances a vehicle state for newer sequence numbers. Validation is fail-fast at ingestion. The stream processor commits offsets after sink writes. Least privilege is incomplete: local MQTT is anonymous and API tenant row scoping is not generalized.

### 6.3 Design Patterns Used

| Pattern | Purpose | Location |
|---|---|---|
| Adapter | External broker/store access separated from domain event rules | `services/ingestion/app/main.py`, `services/stream_processor/app/storage.py` |
| Retry / bounded queue | Absorb broker backpressure without acknowledging before delivery | `services/ingestion/app/main.py` |
| Idempotent sink | Avoid duplicate history and alert rows during Kafka replay | `services/stream_processor/app/storage.py` |
| Monotonic cache update | Reject stale vehicle sequence writes | `services/api/app/repositories/redis_state.py` |

### 6.4 Interfaces, Contracts & Runtime Flows

REST API is versioned under `/api/v1`; OpenAPI is exposed at `/docs`. List endpoints use bounded limit/offset where applicable. Bearer tokens protect feature paths; readiness and token issuance remain public. Redis applies local per-IP request limits, described in `SECURITY.md`. The canonical JSON contract lives in `packages/event_schemas/telemetry.py` and `telemetry.schema.json`; Kafka uses `vehicle_id` as the partition key. Delivery semantics are at least once, not exactly once. Sequence and broker recovery diagrams are in the architecture doc.

### 6.5 Algorithms & Data Structures

Charger selection uses Haversine great-circle distance, filters incompatible connectors, full stations, and chargers beyond reported range, then scores candidates as 70% normalized distance and 30% normalized price. For `n` station connector records, filtering and sort is `O(n log n)` time and `O(n)` output memory. The local seeded network has five station records; no large-network benchmark was run. Energy-balance range is `SoC Ã— SoH-adjusted usable kWh Ã· consumption Ã— 100`; it excludes route and environmental effects.

## 7. Non-Functional Requirements & Performance Benchmarks

Compose and functional paths were exercised locally. A 60-second k6 run sent five fleet-summary requests per second and measured p95 12.61 ms and p99 13.84 ms with 0 failed requests. The case-study goals (100K+ events/s, 3Ã— burst for five minutes, dashboard <2s, and critical alert <5s) are **Not yet measured**. The API percentiles apply only to this low-rate local endpoint test. One query-plan comparison is not representative throughput evidence. Full details and limitations are in [`PERFORMANCE.md`](PERFORMANCE.md).

## 8. Security & Compliance

Local login hashes passwords with PBKDF2-HMAC-SHA256, issues one-hour HMAC signed bearer tokens, and checks roles for writes. Redis enforces local per-IP request limits. The API adds browser security headers, has restricted local CORS, validates input, and stores alert transitions in audit logs. STRIDE review is in [`SECURITY.md`](SECURITY.md). OIDC/MFA, token revocation, tenant row enforcement, TLS/mTLS, encryption-at-rest key policy, secret vault, privacy masking/erasure, and external security scans are not implemented. No GDPR/DPDP or UNECE compliance claim is made.

## 9. Test Strategy

Latest test command `docker compose --profile test run --build --rm unit-tests` passed **48 unit tests**. It covers schema validation, simulator behavior, ingestion/processor rules, range arithmetic, charging ranking, password hashing, and token validation. The unit-test command writes an ignored local `docs/evidence/coverage.xml` report with **85% line coverage across five selected modules** (processor domain 100%, simulator generator 90%, charger ranking 88%, security primitives 88%, ingestion topic helper 27%). Branch coverage is not measured, and this is not 80% coverage across all core services. Six live-stack integration checks passed for PostgreSQL seed/migration, MongoDB history, Redis latest state, Kafka topic metadata, authenticated analytics, and alert lifecycle/audit behavior. Frontend TypeScript checks/build pass; npm audit reported zero vulnerabilities at the time recorded. BDD acceptance, performance/soak, SAST/DAST, container scanning, erasure, and chaos/failure tests remain incomplete.

## 10. Observability

Prometheus scrapes API request counters and histograms at `/metrics`; Grafana is included. Local processor logs show event counts and failures. No dashboard screenshot, distributed trace, consumer-lag metric, or centralized structured log walkthrough has been captured. A latency investigation would correlate API histogram labels, service logs, Kafka consumer lag (currently not exposed as a metric), and dependency readiness.

## 11. AI / ML Component

No learned model or agent is used. Threshold rules are sufficient for this prototype's explicit alerts; no labeled battery aging data exists to justify a predictive model. The range energy-balance calculation is a baseline only. Evaluation, leakage prevention, and model selection notes are in [`ML_EVALUATION.md`](ML_EVALUATION.md). ML accuracy and inference latency: **Not applicable / not measured**.

## 12. Architecture Decisions, Risks & Future Enhancements

Three ADRs cover polyglot storage, MQTT/Kafka, and consistency choices in [`adr/`](adr/). Main risks are single-node local services, demo-only auth, no tenant row-level isolation, approximate range/time calculations, very small station catalog, and unmeasured challenge-scale load. Next steps: add tenant enforcement and production identity; run repeatable integration/chaos/load suites on a documented cluster; source permitted labeled data and implement an independently evaluated model only if it beats the baseline.

## 13. Demo Video (5 Minutes Maximum)

Video link: **Not recorded**. No timestamps are asserted. See [`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) for a repeatable timed script; capture and review the actual recording before submission.

## 14. Repository Checklist

- README and `.env.example`: present.
- One-command local stack: `docker compose up --build -d`; actual full-stack startup was exercised.
- Service folders, docs, infra, tests: present.
- CI: Compose config, Ruff, unit suite with selected-module coverage, live-stack integration checks, frontend typecheck/build, and npm audit; SAST/DAST and image scanning are absent.
- No `.env` secrets should be committed; local defaults are explicitly development-only.
- Final tag `v1.0-submission`: not created.

## 15. Conclusion

The work demonstrates a functioning synthetic telemetry-to-dashboard path, per-vehicle stream partitioning, duplicate-aware persistence, explainable health signals, and a reachable-charger heuristic. The most useful engineering learning is to separate registry, history, and hot state and to treat replay as normal. The main remaining challenge is proving scale, security, model usefulness, and tenant safety with more than a local functional demonstration.

## 16. Declarations

- Data: generated synthetic vehicle and station data only; no real vehicle-owner data.
- Open-source components include Python/FastAPI, SQLAlchemy, Alembic, Pydantic, Paho MQTT, Confluent Kafka client, PyMongo, Redis client, React, Vite, Leaflet/React Leaflet, PostgreSQL, MongoDB, Redis, Kafka, Mosquitto, Prometheus, and Grafana. Refer to package/image license notices and produce an SBOM before formal submission.
- AI tools: OpenAI Codex was used as a coding assistant for implementation and documentation. No LLM API or AI agent is part of the running product.
- Independent academic exercise; Motorq is referenced as the hackathon context only. No affiliation is claimed.

## 17. Appendix

- Architecture: [`diagrams/architecture.md`](diagrams/architecture.md)
- Requirements and final evidence: [`FINAL_REQUIREMENTS_AUDIT.md`](FINAL_REQUIREMENTS_AUDIT.md)
- Simulator: [`SIMULATOR.md`](SIMULATOR.md)
- Stream ingestion: [`STREAM_INGESTION.md`](STREAM_INGESTION.md)
- Security and STRIDE: [`SECURITY.md`](SECURITY.md)
- SQL plans: [`SQL_OPTIMIZATION.md`](SQL_OPTIMIZATION.md)
- Product/API screenshots and video are not yet available. Coverage and raw low-rate API load evidence are in `docs/evidence/`; no benchmark charts have been generated.
