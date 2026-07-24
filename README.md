# Secure Payload Command and Telemetry Gateway

A local five-service Python system that receives a fixed binary telemetry frame, preserves the original bytes, validates the interface contract, coordinates durable processing, and returns operator-readable results.

The active implementation is intentionally narrow. It demonstrates one payload, one 28-byte protocol, one vendor handoff, and one ground-processing path. It is not production flight software.

## End-to-end verification

```bash
make verify-demo
```

The verification command starts an isolated Docker Compose project on ports `8180` and `8181`, then checks:

- nominal telemetry ingestion and processing;
- byte-for-byte raw-frame preservation;
- idempotent handling of an exact duplicate;
- rejection of a deliberately corrupted CRC before processing.

See [`docs/END_TO_END_VERIFICATION.md`](docs/END_TO_END_VERIFICATION.md) for the architecture, implementation scope, design decisions, verification matrix, and production gaps.

## Architecture

```text
logical payload inside vendor-simulator
          |
          | fixed 28-byte telemetry frame
          v
  vendor-simulator
          |
          | local HTTP + vendor teaching token
          v
      ground-api ----------> PostgreSQL
          |                  raw bytes, receipt evidence, durable state
          |
          | saved job identifier
          v
        Redis
          |
          v
   payload-worker ---------> PostgreSQL
                             decoded values and health classification
                                      |
                                      v
                      operator retrieves through ground-api
```

PostgreSQL is the permanent evidence store. Redis coordinates waiting work but does not hold the only copy of telemetry. Saving before queueing means a temporary queue or worker failure cannot erase the original vendor delivery.

## Active services

| Service | Responsibility |
|---|---|
| `vendor-simulator` | Create a payload frame and forward its exact bytes |
| `ground-api` | Authenticate, preserve, validate, queue, and retrieve telemetry |
| `payload-worker` | Decode accepted raw data and save one idempotent result |
| `postgres` | Preserve raw, processed, sequence, job, and audit evidence |
| `redis` | Hold processing work until the worker claims it |

## Interface behavior

The packet codec implements:

- a versioned 28-byte binary frame;
- explicit big-endian field boundaries;
- a golden byte vector;
- CRC-32 integrity validation;
- payload, boot, sequence, timestamp, temperature, voltage, mode, and fault fields;
- specific rejection reasons for malformed or unsupported frames.

Persistent boot and sequence state distinguishes:

- new packets;
- exact duplicates;
- conflicting bytes under the same packet identity;
- sequence gaps;
- payload restarts.

## Run locally

Requirements: Linux, Docker Engine, Docker Compose, `curl`, and Python 3.12 or newer.

```bash
test -f .env || cp .env.example .env
./scripts/up.sh
./scripts/smoke_test.sh
```

The smoke path:

1. confirms gateway readiness and vendor-simulator health;
2. submits deterministic nominal telemetry;
3. requires downstream HTTP 202 for newly queued work;
4. retrieves the exact raw record;
5. waits for the worker's `NOMINAL` result;
6. verifies stored bytes match submitted bytes;
7. corrupts the CRC;
8. requires rejection with `CRC_MISMATCH`.

Expected final line:

```text
PASS: Phase 2 telemetry smoke proof
```

## Automated checks

```bash
python3.12 -m venv .venv312
.venv312/bin/pip install -e '.[dev]'
.venv312/bin/ruff check .
.venv312/bin/mypy mission_ground
.venv312/bin/pytest -q
```

The verified baseline at the time of this update is `146 passed`. The command exit status remains the authority as tests evolve.

## Service inspection

```bash
docker compose --env-file .env -f deployment/compose.yaml ps
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8081/healthz
```

Stop the services while preserving named data volumes:

```bash
./scripts/down.sh
```

## Security boundary

The current phase uses static environment-based teaching tokens and unencrypted local HTTP. These are not production-grade controls.

Current safeguards:

- host listeners bind only to `127.0.0.1`;
- PostgreSQL and Redis publish no host ports;
- the vendor simulator does not join the internal data network;
- `.env` is excluded from version control;
- raw and processed evidence are separated from queue state.

Production deployment would require TLS, mutual TLS, certificate identity, rotation, managed secrets, network policy, backup and restore procedures, service-level objectives, migration policy, and security review.

## Documentation

- [`docs/INTERFACE_CONTROL_DOCUMENT.md`](docs/INTERFACE_CONTROL_DOCUMENT.md) — normative packet, command, acknowledgement, timing, and security contract.
- [`docs/PHASE_2_TELEMETRY_PATH.md`](docs/PHASE_2_TELEMETRY_PATH.md) — operation, network, failure, and recovery behavior.
- [`docs/END_TO_END_VERIFICATION.md`](docs/END_TO_END_VERIFICATION.md) — isolated verification path and design tradeoffs.

## Repository map

```text
mission_ground/telemetry/       Packet codec, health rules, sequence rules, storage
mission_ground/services/        Ground API, payload worker, vendor simulator
deployment/compose.yaml         Five-service runtime
tests/                          Domain, persistence, queue, service, and simulator tests
scripts/up.sh                   Build, start, and wait for services
scripts/smoke_test.sh           Primary telemetry smoke path
scripts/verify_demo.sh          Isolated nominal, duplicate, and CRC verification
scripts/down.sh                 Stop services while retaining evidence volumes
docs/INTERFACE_CONTROL_DOCUMENT.md
docs/PHASE_2_TELEMETRY_PATH.md
docs/END_TO_END_VERIFICATION.md
```

## Retained legacy reference

The repository retains an earlier broad ground-platform prototype for historical comparison. It modeled UDP telemetry, NATS JetStream, a mission API, command dispatch, Prometheus metrics, Grafana dashboards, Kubernetes examples, and an optional local AI advisor.

Those components are not part of the active five-service runtime. The current project-specific 28-byte protocol is defined by the normative interface document and does not claim full CCSDS compliance.

| Retained reference | Status |
|---|---|
| `mission_ground/services/spacecraft_sim.py`, `telemetry_ingest.py`, `telemetry_processor.py`, `mission_api.py`, `command_dispatcher.py`, `ai_advisor.py` | Earlier implementation; not started by current Compose |
| `docs/architecture.md`, `docs/interface-control.md`, `docs/operations-runbook.md`, `docs/verification-plan.md` | Earlier broad-platform documents; not normative for the active phase |
| `deployment/k8s/` | Historical deployment reference; outside active scope |
| `observability/` | Retained Prometheus and Grafana configuration; not started in the active phase |
| `requirements/` | Earlier traceability material; not current evidence unless remapped |
