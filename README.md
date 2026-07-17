# Secure Payload Command and Telemetry Gateway

## Current learning status: Phase 2 telemetry path

The active build is deliberately narrow: one simulated payload sends one fixed
binary telemetry format through a third-party vendor simulator into a ground
gateway. The gateway authenticates the vendor, preserves the received bytes,
validates the packet, queues accepted work, and lets a separate worker store an
operator-readable health result.

The command path is not active yet. Transport Layer Security (TLS), mutual TLS
(mTLS), certificate identity, and production secret handling are also deferred.
This repository is a local learning demonstration, not production-ready ground
software and not flight software.

## Architecture in plain language

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
          |                  exact raw bytes and receipt evidence
          |
          | saved record identifier
          v
        Redis
          |
          v
   payload-worker ---------> PostgreSQL
                             engineering values and health classification
                                      |
                                      v
                      operator retrieves through ground-api
```

Hypertext Transfer Protocol (HTTP) is the request-and-response protocol used by
the local web interfaces. An Application Programming Interface (API) is a
defined doorway through which one program requests data or an action from
another. The APIs make the vendor handoff and operator retrieval visible and
testable.

PostgreSQL is the permanent evidence store. Redis is the waiting line; it holds
a reference to saved work, not the only copy of mission data. Saving before
queueing means a temporary queue or worker failure cannot erase the original
vendor delivery.

The five active services are:

| Service | One responsibility |
|---|---|
| `vendor-simulator` | Create a payload frame and forward its exact bytes |
| `ground-api` | Authenticate, preserve, validate, queue, and retrieve telemetry |
| `payload-worker` | Decode accepted raw data and save one idempotent result |
| `postgres` | Preserve raw, processed, sequence, job, and audit evidence |
| `redis` | Hold processing work until the worker claims it |

Idempotent means that repeated delivery of the same logical work cannot create a
second logical result. It is like checking a work-order number before performing
the same task again.

## Run Phase 2 locally

Requirements: Linux, Docker Engine, Docker Compose, `curl`, and Python 3.12 or
newer.

From this repository:

```bash
test -f .env || cp .env.example .env
./scripts/up.sh
./scripts/smoke_test.sh
```

The smoke script uses only `curl`, Bash, and Python's standard library. It:

1. confirms gateway readiness and vendor-simulator health;
2. submits deterministic nominal telemetry through the vendor simulator;
3. requires downstream status 202, proving this is newly queued work;
4. retrieves the exact raw record with the operator token;
5. waits for the worker's processed `NOMINAL` result;
6. proves the stored raw bytes match the submitted bytes;
7. corrupts the Cyclic Redundancy Check (CRC); and
8. requires the gateway to reject that frame with `CRC_MISMATCH`.

A CRC is a calculated value that detects accidental byte corruption. This
system needs it to stop a changed frame from entering processing; it is like
checking a shipment's tamper-evident seal number, and its implementation lives
under `mission_ground/telemetry/`.

The final line appears only if every check succeeds:

```text
PASS: Phase 2 telemetry smoke proof
```

Run the automated suite with Python 3.12, the version used by the services and
continuous-integration workflow:

```bash
python3.12 -m venv .venv312
.venv312/bin/pip install -e '.[dev]'
.venv312/bin/ruff check .
.venv312/bin/mypy mission_ground
.venv312/bin/pytest -q
```

The current expected test summary is `146 passed`. A different total is not
automatically a failure after tests are added or removed; the command's exit
status and failure report are the authority.

Inspect service state:

```bash
docker compose --env-file .env -f deployment/compose.yaml ps
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8081/healthz
```

Stop the services while preserving the named data volumes:

```bash
./scripts/down.sh
```

For the manual send, retrieval, failure, and recovery commands, follow
[`docs/PHASE_2_TELEMETRY_PATH.md`](docs/PHASE_2_TELEMETRY_PATH.md).

## What Phase 2 is designed to teach

After personally operating this phase, you should be able to explain:

- every field and byte boundary in the telemetry packet;
- how the vendor is authenticated in this local phase;
- why checksum validation and authentication solve different problems;
- why original raw bytes are preserved before processing;
- why the queue contains a saved record identifier;
- how the worker converts bytes into engineering values;
- how one payload health classification is stored idempotently;
- how boot and sequence identifiers expose duplicates, gaps, and restarts;
- what remains safe when PostgreSQL, Redis, or the worker is unavailable; and
- which security controls are still demonstrations or deferred work.

## Security boundary

Phase 2 uses static environment-based tokens and unencrypted HTTP. Those tokens
are teaching credentials, not production-grade authentication. The two host
listeners bind only to `127.0.0.1`; PostgreSQL and Redis publish no host ports;
and the vendor simulator does not join the internal data network.

HTTP does not protect a token or packet from another party able to observe the
connection. Phase 5 must add Hypertext Transfer Protocol Secure (HTTPS), in
which HTTP travels inside TLS encryption, and mTLS, in which both machines
present and verify certificates. Phase 5 must also verify network policy and
exercise certificate rejection and rotation.

Never commit `.env`, print tokens in logs, or describe these static values as
production secrets.

## Current documents

- [`docs/INTERFACE_CONTROL_DOCUMENT.md`](docs/INTERFACE_CONTROL_DOCUMENT.md) —
  the normative Interface Control Document (ICD) for telemetry, future
  commands, acknowledgement states, timing, and security boundaries.
- [`docs/PHASE_2_TELEMETRY_PATH.md`](docs/PHASE_2_TELEMETRY_PATH.md) — the
  Phase 2 operation, network, failure, and recovery lesson.

An ICD is the controlled agreement defining exactly what crosses a system
boundary. It lets payload, vendor, and ground engineers implement independently
and compare the result against the same bytes and rules.

## Repository map for the active phase

```text
mission_ground/telemetry/       Packet codec, health rules, sequence rules, storage
mission_ground/services/        Ground API, payload worker, vendor simulator
deployment/compose.yaml         Five-service Phase 2 runtime
tests/                          Domain, storage, queue, service-helper, and simulator tests
scripts/up.sh                   Build, start, and wait for all services
scripts/smoke_test.sh           Concise manual telemetry proof
scripts/down.sh                 Stop while retaining evidence volumes
docs/INTERFACE_CONTROL_DOCUMENT.md
docs/PHASE_2_TELEMETRY_PATH.md
```

The focused suite covers domain rules, persistence, queue transitions,
configuration, metrics, service-boundary helpers, and simulator behavior. The
live smoke script covers the actual HTTP gateway, PostgreSQL, Redis, worker, and
simulator together. More exhaustive dependency-outage and retry-injection tests
belong to the controlled failure phase; do not claim those demonstrations until
you personally execute them.

## Legacy broad-platform reference — not the active runtime

The repository also retains an earlier, broader ground-platform prototype for
historical study. It modeled User Datagram Protocol (UDP) telemetry, a NATS
JetStream message broker, a mission API, command dispatch, Prometheus metrics,
Grafana dashboards, Kubernetes examples, and an optional local artificial
intelligence (AI) advisor. UDP sends independent network datagrams without a
delivery connection; that older design used it to imitate a low-level packet
link. The active Phase 2 path does not use UDP or NATS.

The earlier packet was inspired by standards from the Consultative Committee
for Space Data Systems (CCSDS), but it was not a complete CCSDS implementation.
The current 28-byte project-specific protocol is defined only by the normative
ICD above and makes no CCSDS-compliance claim.

Legacy material remains available here:

| Retained reference | Status |
|---|---|
| `mission_ground/services/spacecraft_sim.py`, `telemetry_ingest.py`, `telemetry_processor.py`, `mission_api.py`, `command_dispatcher.py`, `ai_advisor.py` | Earlier service implementation; not started by current Compose |
| [`docs/architecture.md`](docs/architecture.md), [`docs/interface-control.md`](docs/interface-control.md), [`docs/operations-runbook.md`](docs/operations-runbook.md), [`docs/verification-plan.md`](docs/verification-plan.md) | Earlier broad-platform documents; not normative for Phase 2 |
| [`deployment/k8s/`](deployment/k8s/) | Retained Kubernetes deployment reference; explicitly outside the active project scope |
| [`observability/`](observability/) | Retained Prometheus and Grafana configuration; not started in Phase 2 |
| [`requirements/`](requirements/) | Earlier traceability material; it must not be cited as Phase 2 evidence without being remapped |

Do not use a legacy component as interview evidence for the active gateway until
you have personally run it, tested it against a current requirement, and can
explain its boundary and failure behavior.
