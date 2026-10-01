# ADR 0003: consistency by data purpose

- Status: Accepted
- Context: the demo combines transactional fleet data with high-rate telemetry.
- Decision: use PostgreSQL transactions and unique dedupe constraints for registry and alert lifecycle (CP preference); use append-style Mongo history and Redis latest-state cache with sequence monotonicity for telemetry (availability and eventual convergence preference). Redis rejects older sequence updates. Kafka offsets commit after processor writes.
- Consequences: duplicate delivery is expected and handled idempotently. A crash between Mongo/Redis/SQL side effects and Kafka commit can replay the event; sinks must remain idempotent. No cross-store transaction exists.
