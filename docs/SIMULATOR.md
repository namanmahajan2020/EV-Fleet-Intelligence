# Synthetic EV Simulator

The simulator maintains state for a configurable number of synthetic vehicles and publishes canonical JSON telemetry to MQTT topic `evfleet/telemetry/v1/{vehicle_id}`. It produces Bengaluru-area movement, speed, SoC, SoH drift, temperature, range, odometer, charging transitions, and OBD-II fault codes. Event IDs are deterministic for a given vehicle sequence; timestamps reflect runtime UTC.

## Local development defaults

`docker compose up --build -d` runs `DEMO_MODE` across 100,000 simulator vehicle states at a target base rate of 20 event-generation ticks/second. It visits each synthetic vehicle in round-robin order; at the default rate, one complete fleet pass takes about 83 minutes. This is enough to exercise a 100K vehicle population, not the 100K events/second challenge throughput target. The publisher uses an in-memory state table, so 100K mode needs more memory than a small local smoke run.

## Configure 100K simulator vehicles

Copy `.env.example` to `.env`; the default is already set to 100K synthetic states. For an explicit configuration, use:

```dotenv
SIMULATOR_MODE=DEMO_MODE
SIMULATOR_SEED=20260930
SIMULATOR_VEHICLE_COUNT=100000
SIMULATOR_EVENTS_PER_SECOND=20
```

Restart the simulator after changing configuration:

```sh
docker compose up -d --force-recreate simulator
docker compose logs -f simulator
```

Vehicle 1 starts at 18% SoC for a repeatable low-battery demo case. Other initial values and event progression use the configured random seed. The timestamp is runtime UTC, so complete payloads differ across runs.

## Fault and burst controls

Rates are probabilities from 0 through 1. `SIMULATOR_DUPLICATE_RATE` republishes the same event ID; `SIMULATOR_OUT_OF_ORDER_RATE` places an older event after a newer one; `SIMULATOR_MALFORMED_RATE` removes a required field; `SIMULATOR_UNKNOWN_EVENT_RATE` injects an unrecognised event type; and `SIMULATOR_FAULT_RATE` controls valid DTC-bearing fault events. Invalid payload generation is off by default.

`LOAD_TEST_MODE` applies `SIMULATOR_BURST_MULTIPLIER` for `SIMULATOR_BURST_DURATION_SECONDS` every `SIMULATOR_BURST_INTERVAL_SECONDS`; the first burst starts immediately. Defaults are a 3× multiplier and 300-second duration. Run load scenarios only on a suitably configured machine with broker and consumer monitoring.

The simulator log reports local MQTT queue accepts, not broker acknowledgements or end-to-end consumed events. Use consumer counts and lag from the stream pipeline before claiming delivery throughput. Challenge-scale results remain **Not yet measured**.

## Inspect an event

```sh
docker compose exec -T mosquitto mosquitto_sub -h localhost -t 'evfleet/telemetry/v1/#' -C 1 -W 5
```
