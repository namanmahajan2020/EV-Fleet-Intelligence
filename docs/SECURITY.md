# Security design and current limits

## Implemented locally

- Demo login uses PBKDF2-HMAC-SHA256 password hashes (`services/api/app/security.py`), short-lived HMAC-SHA256 signed bearer tokens (one hour), and role claims from the membership table.
- `/api/v1` routes require a bearer token except system readiness and token issuance. State-changing methods reject the analyst role. Alert state transitions are recorded in `audit_logs`.
- API inputs use Pydantic/FastAPI validation; response headers include `X-Content-Type-Options`, `X-Frame-Options`, and `Referrer-Policy`. CORS only allows the two local web origins and required methods/headers.
- Redis-backed fixed-window request limits allow 10 token attempts/minute and 1,000 other API requests/minute per client IP in the local prototype. If Redis is unavailable, API requests fail with 503.
- The MQTT listener uses anonymous access on a host-published development broker. It must not be exposed outside a trusted local network.

## Not implemented / production requirements

The demo secret and demo password defaults are for local use only. Set unique secrets before use. There is no refresh/revocation or OIDC integration, TLS/mTLS, encryption-at-rest configuration, secrets manager, complete tenant row enforcement, location masking/erasure workflow, or external DAST/container scan. The simple per-IP rate limits are not a substitute for a managed gateway/WAF and distributed abuse controls. Do not deploy this Compose profile to the public internet. API role checks do not yet replace fleet-level row-level authorization for a multi-tenant deployment.

For production, use an OIDC provider, short-lived asymmetric tokens with key rotation, database-enforced tenant scopes, TLS 1.3 at ingress and device gateways, managed encryption keys, secret rotation, rate limiting/WAF, private broker networking and per-device identities, and audited erasure/retention controls aligned to applicable privacy rules.

## STRIDE threat model

| Threat | Example | Current control | Remaining work |
|---|---|---|---|
| Spoofing | Stolen demo credentials or forged telemetry | Password hash; signed expiring API token; event schema | OIDC/MFA, device certificates and per-device credentials |
| Tampering | Modify telemetry or alert state | Kafka delivery validation; API token on state changes; DB transaction | TLS, broker ACLs, immutable audit export |
| Repudiation | Deny acknowledging alert | Alert transition inserted into audit log | Actor identity on every audit row; append-only remote sink |
| Information disclosure | Read fleet locations | Bearer token required for fleet APIs; synthetic location data | Tenant row authorization, masking, least privilege, encryption |
| Denial of service | MQTT/Kafka/API flood | Bounded ingestion queue and Kafka backpressure | Rate limits, quotas, HA capacity and chaos tests |
| Elevation of privilege | Analyst tries to change state | Middleware restricts writes by token role | Fine-grained authorization policy tests and OIDC claims mapping |
