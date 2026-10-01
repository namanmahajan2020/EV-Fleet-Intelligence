# Performance evidence

## Targets from the case study

100,000 events/s sustained, 3x burst for five minutes, event-to-dashboard under 2 seconds, critical alerts under 5 seconds, API p95 under 200 ms and p99 under 500 ms.

## Measured results

The 100,000 events/s target and 3x burst test are **Not yet measured**. The 100,000 seeded vehicle registry is not evidence of 100,000 events/s. The local stack uses one Kafka broker, a single ingestion process, and a single stream processor and is not configured for high availability. The simulator's console counters are publish-queue acceptance counts, not broker acknowledgements or end-to-end throughput.

### Local API smoke-load run

| Item | Observation |
|---|---|
| Run | 2026-09-30, 60 seconds, k6 `constant-arrival-rate` |
| Scenario | 5 fleet-summary requests/second; 10 preallocated VUs, max 20 |
| Environment | Docker Desktop Linux VM reported 20 CPUs and 8,177,168,384 bytes memory; single local Compose stack and one API instance |
| Requests | 302 HTTP requests total including login; 300 scheduled summary iterations; 5.027 HTTP requests/second including setup |
| Checks / errors | 603/603 checks passed; 0/302 failed HTTP requests |
| `http_req_duration` | average 10.74 ms; p95 12.61 ms; p99 13.84 ms; max 63.14 ms |
| Thresholds | API p95 <200 ms and p99 <500 ms passed for this scenario |

Raw k6 summary: [`evidence/k6-api-summary.json`](evidence/k6-api-summary.json). This is a low-rate local summary-endpoint test, not a dashboard end-to-end, soak, or production benchmark. It does not establish 100K events/s, 3x burst tolerance, or behavior under multi-tenant concurrency. No CPU/RAM utilization samples were captured during the run.

An earlier k6 attempt used environment names reserved by k6 itself, which selected the default looping-VU executor and triggered local rate limits. It was stopped and excluded. The recorded result above is from the corrected custom `LOAD_*` scenario; the script and Compose profile now use those names.

## Reproducible smoke evidence

Local API responses and pipeline operation have been smoke-checked. These are functional checks, not performance benchmarks. Before making performance claims, record host CPU/RAM, Compose image versions, broker partitions, simulator mode/rate, run duration, consumer lag, end-to-end latency distribution, and resource utilization. Do not compare an unmeasured run to the case-study targets.

## Load scripts

`infra/load/api.js` is a k6 API load script. With the local stack running, execute `docker compose --profile load run --rm k6`; set `LOAD_RATE`, `LOAD_DURATION`, `LOAD_PREALLOCATED_VUS`, `LOAD_MAX_VUS`, `DEMO_OPERATOR_EMAIL`, and `DEMO_OPERATOR_PASSWORD` in `.env` as needed. It reports API latency and checks the local fleet summary. Its default thresholds are the case-study p95/p99 API targets; a threshold failure is a result, not a reason to edit the expected outcome. For event-load work, configure the simulator's `LOAD_TEST_MODE` via Compose environment overrides and collect processor counts, Kafka lag, Mongo/Redis changes, hardware utilization, and actual run duration; simulator queue counters alone are not broker throughput.
