# ADR 0001: separate transactional, history, and hot-state stores

- Status: Accepted
- Context: fleet ownership, alerts, and audit need relational constraints; high-volume telemetry history and live state have different access patterns.
- Options: one relational store; PostgreSQL plus document history and cache; one distributed NoSQL store.
- Decision: PostgreSQL for the normalized core, MongoDB for append-style telemetry, Redis for latest state and Pub/Sub.
- Consequences: simple local operations with Compose; telemetry and cached state are eventually consistent with the SQL registry. Mongo TTL expires history after 90 days. HA and cost characteristics have not been benchmarked.
