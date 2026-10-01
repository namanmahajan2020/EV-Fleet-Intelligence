# ADR 0002: MQTT edge intake and Kafka durable replay

- Status: Accepted
- Context: synthetic vehicles need a lightweight pub/sub protocol; downstream processing requires replay and per-vehicle order.
- Options: direct HTTP writes; MQTT only; MQTT followed by Kafka.
- Decision: simulator publishes QoS 1 to Mosquitto; ingestion validates then publishes Kafka keyed by vehicle ID. Invalid input goes to a DLQ. MQTT is acknowledged after Kafka delivery.
- Consequences: at-least-once delivery with idempotent event/alert persistence. Exactly-once across external stores is not claimed. Local broker has one node and anonymous development access.
