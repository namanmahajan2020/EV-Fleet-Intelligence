# Architecture diagrams

## C4 Level 1: context

```mermaid
flowchart LR
  FM[Fleet manager] -->|HTTPS / browser| WEB[EV Fleet Intelligence]
  SIM[Synthetic EV simulator] -->|MQTT QoS 1| ING[MQTT to Kafka ingestion]
  WEB -->|JSON REST + bearer token| API[FastAPI]
  ING -->|Kafka telemetry topic| PROC[Stream processor]
  PROC --> PG[(PostgreSQL: fleet registry, alerts, audit)]
  PROC --> MG[(MongoDB: telemetry history)]
  PROC --> RD[(Redis: latest state, Pub/Sub)]
  API --> PG
  API --> MG
  API --> RD
  PR[Prometheus] -->|scrape| API
```

## C4 Level 2: containers and protocols

```mermaid
flowchart LR
  subgraph Compose[Docker Compose local deployment]
    S[Simulator] -->|MQTT| M[ Mosquitto ]
    M -->|Paho subscriber| I[Ingestion]
    I -->|Kafka producer; key=vehicle_id| K[Kafka, 6 telemetry partitions]
    K -->|consumer group| P[Stream processor]
    P -->|SQL / psycopg| DB[(PostgreSQL)]
    P -->|Mongo driver| MD[(MongoDB)]
    P -->|Redis protocol| C[(Redis)]
    U[React + Leaflet] -->|HTTP JSON + JWT| A[FastAPI]
    A --> DB
    A --> MD
    A --> C
    PM[Prometheus] -->|HTTP /metrics| A
  end
```

## Relational model (3NF core)

```mermaid
erDiagram
  TENANTS ||--o{ FLEETS : owns
  FLEETS ||--o{ VEHICLES : groups
  USERS ||--o{ FLEET_MEMBERSHIPS : assigned
  FLEETS ||--o{ FLEET_MEMBERSHIPS : grants
  ROLES ||--o{ FLEET_MEMBERSHIPS : defines
  VEHICLES ||--o{ ALERTS : triggers
  FLEETS ||--o{ ALERTS : scopes
  CHARGING_STATIONS ||--o{ STATION_CONNECTORS : offers
  VEHICLES ||--o{ CHARGING_SESSIONS : charges
  CHARGING_STATIONS ||--o{ CHARGING_SESSIONS : hosts
  USERS ||--o{ AUDIT_LOGS : performs
```

## Telemetry flow

```mermaid
sequenceDiagram
  participant S as Simulator
  participant M as MQTT
  participant I as Ingestion
  participant K as Kafka
  participant P as Processor
  participant R as Redis
  participant DB as Mongo + PostgreSQL
  S->>M: canonical JSON (QoS 1)
  M->>I: topic vehicle ID
  I->>I: validate Pydantic contract
  alt invalid payload
    I->>K: publish reason and raw record to DLQ
    I-->>M: acknowledge after broker delivery
  else valid payload
    I->>K: publish keyed event
    K->>P: deliver event
    P->>DB: append unique event / idempotent alert
    P->>R: advance state only for newer sequence
    P->>K: commit offset after writes
  end
```

## Failure/recovery flow

```mermaid
sequenceDiagram
  participant I as Ingestion
  participant K as Kafka
  participant M as MQTT broker
  I->>K: produce event
  K--xI: broker unavailable
  Note over I: bounded queue fills; no MQTT acknowledgement yet
  I->>I: pause intake / retry producer
  K->>I: broker recovers
  I->>K: queued event delivered
  I-->>M: acknowledge QoS 1 message
```

## Deployment view

```mermaid
flowchart TB
  subgraph Host[One developer workstation / Docker host]
    subgraph Net[Compose private bridge network]
      Web[Web container]
      API[API container]
      Sim[Simulator container]
      Ingest[Ingestion container]
      Proc[Processor container]
      PG[(PostgreSQL volume)]
      Mongo[(MongoDB volume)]
      Redis[(Redis volume)]
      Kafka[(Kafka volume)]
      MQTT[(Mosquitto volume)]
      Prom[Prometheus]
      Graf[Grafana]
    end
  end
  User[Local browser] -->|5173| Web
  User -->|8000| API
  Prom --> API
  Graf --> Prom
  Sim --> MQTT --> Ingest --> Kafka --> Proc
  API --> PG
  API --> Redis
  Proc --> PG
  Proc --> Mongo
  Proc --> Redis
```

This local deployment has one host, one instance per service, development credentials, no device identity, no TLS/mTLS, and no autoscaling. Production replicas, managed services, network policies, secrets, and zone placement remain a deployment design task.

## Charging recommendation flow

```mermaid
flowchart TD
  A[Authenticated vehicle selection] --> B[Read latest Redis state]
  B --> C[Read vehicle connector, onboard power, capacity]
  C --> D[Load active station connector records]
  D --> E[Filter connector compatibility and available ports]
  E --> F[Haversine distance from current location]
  F --> G{Within reported range?}
  G -- no --> H[Discard candidate]
  G -- yes --> I[Estimate energy, time, and cost]
  I --> J[Rank: 70% normalized distance + 30% normalized price]
  J --> K[Return recommendation and alternatives with reasons]
```

## Battery and range baseline flow (no trained ML model)

```mermaid
flowchart LR
  Event[Validated telemetry] --> B[Threshold health assessment: SoH + temperature + DTC]
  Event --> R[Energy balance: SoC × SoH-adjusted capacity / consumption]
  B --> Out[Health category and reason codes]
  R --> Out2[Range baseline with explicit limitations]
  Note[No independent labeled dataset or learned model] -.-> Out
```

The Compose profile is a single-node development deployment. It does not provide the multi-zone replicas or failure-domain protections of production. End-to-end hop latency has not been measured.
