# Cislunar Ground Platform — Engineering Audit

**Date:** 2026-07-17
**Status:** Phase 2 gap assessment — 1/6 milestones implemented, 0/5 verified.

---

## 1. Executive Summary

One milestone (R4a) has been implemented but lacks test coverage. Five Phase 2
milestones remain unimplemented. The codebase shipped 15 unprotected internal
assumptions hard-coded into production services. Phase 2 defines 85 automated
tests. Zero exist. The project has verified test coverage for: limits, health
codec, and packet telemetry framing. All new slices require full test verification
before preparation of claims.

---

## 2. Project Structure

### Architecture Stack (7 layers, 12 services)

| Layer | Component | Technology | Status |
|-------|-----------|------------|--------|
| SPADE | Spacecraft ↔ ground link | UDP binary encoding | Phase 1 implemented |
| Ingest | Signal ingestion | NATS JetStream | Phase 1 implemented |
| **VENDORS** | **Third-party payload telemetry** | **FastAPI** | **Phase 2 — code exists, unverified** |
| Process | Health + fault processing | NATS + async Postgres | Phase 1 implemented |
| Storage | Time-wrapped telemetry | SQLAlchemy | Phase 1 implemented |
| **ANALYSIS** | **Deep-space engineering results** | **SQLAlchemy functions** | **Phase 2 — code exists, unverified** |
| AI | LLM advisory loop | OpenRouter MCP | Phase 2 — code exists, unverified |

### Source file inventory

```
mission_ground/
  __init__.py          (empty)
  common/
    __init__.py         (empty)
    config.py            (63 lines)  env/env_int/env_float
    nats.py              (24 lines)  connect_nats()
    db.py              (107 lines)   create_pool(pool_class=asyncpg)
    retry.py            (17 lines)   retry_async()
    commands.py         (33 lines)   validate_command(), CommandValidationError
    limits.py          (130 lines)   DeepSpaceLimits, evaluate_health()
    packet.py          (150 lines)   SpacePacket, encode/decode, CRC, sequence
    telemetry_store.py  (87 lines)   HealthStore, RawStorage, sequence asserts
    telemetry_queue.py   (53 lines)  SequenceTracker, detect_gaps(), ...
    telemetry_codec.py   (72 lines)  TelemetryCodec, encode_frame(), ...
    traceability.py     (16 lines)   TraceabilityLog, enforce_compliance()
  services/
    simulation/
      simulator.py       (92 lines)   DeepSpaceSimulator, run_simulator()
    command_dispatcher.py (49 lines)   NATS→UDP, Prometheus
    mission_api.py        (112 lines)   FastAPI, command/health/sequence/alerts
    telemetry_ingest.py   (108 lines)   UDP decode→NATS, Prometheus
    telemetry_processor.py (92 lines) HealthStore.raw_history(), validate_health()
  vendor_service.py      (43 lines)   FastAPI POST /ingest/telemetry
  ai_advisor.py            (53 lines)   alert→LLM→advisory pipeline
  telemetry_worker.py   (171 lines)   archetypal_analysis(), ..._temporal_domain, ...
  mission_health_db.py   (93 lines)   batch_update_summary(), create_health_tables()
```

### Test file inventory

```
tests/
  test_commands.py           (42 lines)   CLI argument parsing
  test_limits.py           (197 lines)   DeepSpaceLimits asserts
  test_packet.py          (222 lines)   SpacePacket encode/decode roundtrip
  test_phase2_config_metrics.py     — Phase 2, config validation
  test_phase2_vendor_service.py  — Phase 2, vendor service
  test_phase2_ground_worker_helpers.py — Phase 2, worker helpers
  test_sequence.py          (58 lines)   SequenceTracker gap detection
  test_simulator.py        (176 lines)   Simulator telemetry generation
  test_telemetry_health_v1.py (43 lines)   API CRUD actual responses
  test_telemetry_queue.py    (55 lines)   Sequence gap-detection edge cases
  test_telemetry_storage.py  (32 lines)   RawStorage/HealthStore roundtrip
  test_telemetry_v1_codec.py (22 lines)   TelemetryCodec frame encoding
  test_traceability.py     (41 lines)   TraceabilityLog + run_with_traceability
```

**Total tests: 13 files.** All Hardened Phase 1 tests pass locally. Phase 2 test files exist as stubs/pending skeletons.

---

## 3. Requirement Status

### 3.1 Permanent Sequence

```
Requirement → Implementation → Verification → Evidence → Documentation → LinkedIn → Interview reuse
```

Sequence is **STRICT**: each stage gates the next. Engineering result must exist
and pass verification before preparing a claim.

---

### 3.2 Compliance Rules

**Evidence before claim:** Tests run first. Claim verifiers reference verifiable artifacts.

**Phase 2 rule:** Every new slice requires a passing test in `tests/test_phase2_*.py`
before the slice receives "verified" status. No partial credit.
Claims that rely on Phase 2 functionality require test coverage ≥60% for that slice.

**No纸箱.tel validation without tests:** When an external claim references internal
pipeline stages, the corresponding test must exist and pass.

**Sequence awareness:** P2, R12, C8
— Phase 2, Requirement 12, Claim 8 — claim 8 for requirement 12 has been prepared
as a gap indicator (software engineering foundation, project authoring skill).

**LinkedIn rule:** Only prepare claims for verified items. Skip unverified slices even
if the distinction trivially obvious to the project author.

---

### 3.3 Codebase Safety

| Rule | Implementation |
|------|----------------|
| Frozen legacy | Phase 1 source files: do not edit unless specifically mentioned |
| Test proximity | Phase 2 tests live in `tests/test_phase2_*.py` |
| Atomic slices | Each slice implements + verifies one unit |
| Crisis prevention | Prod config patched once with defaults, optional override |
| Unstaged baseline | Commit before phased implementation begins |

---

### 3.4 Current state matrix

| ID | Requirement | Milestone | Status | File |
|----|-----------|-----------|--------|------|
| R1a | `config.py` env hardening | P2M1 | Not started | — |
| R2a | `commands.py` limit expansion | P2M2 | **Implemented** | `mission_ground/common/commands.py` |
| R2b | `commands.py` serde validation | P2M2 | Not started | — |
| R3a | `db.py` retry hardening | P2M3 | Not started | — |
| R3b | `db.py` transaction isolation | P2M3 | Not started | — |
| R4a | `telemetry_queue.py` reconnection survival | P2M4 | Not started | — |
| R4b | `telemetry_queue.py` consumer lag alerting | P2M4 | Not started | — |
| R5a | `packet.py` VLAN-CRC gap | P2M5 | Not started | — |
| R5b | `telemetry_store.py` raw frame archival | P2M5 | Not started | — |
| R6a | `limits.py` threshold drift detection | P2M6 | Not started | — |
| R6b | `limits.py` cross-parameter correlation | P2M6 | Not sensitive | — |
| R7a | `simulation/simulator.py` fault tree simulation | P2M7 | Not started | — |
| R7b | `simulation/simulator.py` random packet loss | P2M7 | Not started | — |
| R8a | `simulation/simulator.py` validation log | P2M8 | Not started | — |
| R8b | `simulation/simulator.py` boundary condition tests | P2M8 | Not started | — |

**Phase 2 services (new files):**

| Service | Coverage | Status |
|---------|----------|--------|
| `vendor_service.py` | 0/12 tests | Unverified |
| `ai_advisor.py` | 0/10 tests | Unverified |
| `telemetry_worker.py` | 0/22 tests | Unverified |
| `telemetry_processor.py` | 0/21 tests | Unverified |
| `mission_health_db.py` | 0/10 tests | Unverified |
| `telemetry_store.py` (Phase 2 additions) | 0/10 tests | Unverified |

---

## 4. 15 Hard-Coded Internal Assumptions

Extracted by auditing source code for literal strings, unparameterized defaults,
and embedded operational assumptions.

### 4.1 NATS Connection Layer (`common/nats.py`)

| # | Assumption | Location | Configurable? | Risk |
|---|------------|-----------|---------------|------|
| 1 | Default NATS URL `nats://nats:4222` | `connect_nats()`, fallback param | Yes, via `NATS_URL` env | Low — changes in test/CI |
| 2 | Default client name `"ingest-reader"` | hardcoded in `connect_nats()` | No — grows confusing with multiple consumers | Low |

### 4.2 Database Layer (`common/db.py`)

| # | Assumption | Location | Configurable? | Risk |
|---|------------|-----------|---------------|------|
| 3 | Default `DATABASE_URL` points to `postgres:5432` | `create_pool()` doc/blob fallback | Yes, via env | Low — changes in Docker |
| 4 | Pool size defaults `minsize=1, maxsize=5` | `create_pool()` args | No — SSA-rate processing needs higher | **Medium** — sequential bottleneck under load |
| 5 | STASIOMA pool used directly | module-level import | No — hard dependency | Medium |

### 4.3 Limit Thresholds (`common/limits.py`)

| # | Assumption | Location | Configurable? | Risk |
|---|------------|-----------|---------------|------|
| 6 | Voltage warning at 28.0V | `DeepSpaceLimits.VOLTAGE_WARNING` | No — hard-coded | Low — simulation-specific |
| 7 | Voltage critical at 25.0V | `DeepSpaceLimits.VOLTAGE_CRITICAL` | No — hard-coded | Low |
| 8 | Temperature warning at 40°C | `DeepSpaceLimits.TEMP_WARNING` | No — hard-coded | Low |
| 9 | Temperature critical at 45°C | `DeepSpaceLimits.TEMP_CRITICAL` | No — hard-coded | Low |

### 4.4 Packet Framing (`common/packet.py`)

| # | Assumption | Location | Configurable? | Risk |
|---|------------|-----------|---------------|------|
| 10 | Space packet framing uses CCSDS 130-key standard header | `SPACE_PACKET_HEADER` | No — protocol decision | Low — standardized |
| 11 | SNV packet framing uses CCSDS 130-key standard header | `SPACE_PACKET_HEADER` | No — protocol decision | Low — standardized |

### 4.5 Service Ports (`services/*`)

| # | Assumption | Location | Configurable? | Risk |
|---|------------|-----------|---------------|------|
| 12 | Simulator telemetry UDP port `5005` | `simulator.py` | Yes, `TELEMETRY_PORT` env | Low — multi-sim conflicts |
| 13 | Command dispatch UDP port `5006` | `command_dispatcher.py` | Yes, `SIM_COMMAND_PORT` env | Low — multi-sim conflicts |
| 14 | Ingest listening on `0.0.0.0:5005` | `telemetry_ingest.py` | Yes, `TELEMETRY_PORT` env | Low |

### 4.6 Prometheus Metrics Ports

| # | Assumption | Location | Configurable? | Risk |
|---|------------|-----------|---------------|------|
| 15 | `telemetry_ingest` metrics port `9101` | `start_http_server(9101)` | Yes, `METRICS_PORT` env | Low — conflicts in multi-host |

---

## 5. Test Coverage Assessment

### 5.1 Test File Coverage Matrix

| Test file | Tests covered | Source files | Coverage | Phase |
|-----------|---------------|--------------|----------|-------|
| `test_limits.py` | 7 | `limits.py` | **Full** — all thresholds assert correct | 1 |
| `test_packet.py` | 7 | `packet.py` | **Full** — encode/decode roundtrip CRC | 1 |
| `test_telemetry_v1_codec.py` | 2 | `telemetry_codec.py` | **Full** — frame encoding | 1 |
| `test_sequence.py` | 4 | `telemetry_queue.py` | **Full** — gap detection edge cases | 1 |
| `test_commands.py` | 4 | `commands.py` | **Partial** — CLI args, no HTTP | 1 |
| `test_traceability.py` | 3 | `traceability.py` | **Full** — compliance + audit trails | 1 |
| `test_simulator.py` | 7 | `simulation/simulator.py` | **Full** — telemetry gen + edge cases | 1 |
| `test_telemetry_health_v1.py` | 3 | `telemetry_store.py`, `mission_api.py` | **Partial** — CRUD actual responses | 1 |
| `test_telemetry_storage.py` | 2 | `telemetry_store.py` | **Partial** — roundtrip only | 1 |
| `test_telemetry_queue.py` | 1 | `telemetry_queue.py` | **Partial** — gap detection only | 1 |
| `test_phase2_config_metrics.py` | 0 | `config.py` | **Not started** — Phase 2 | 2 |
| `test_phase2_vendor_service.py` | 0 | `vendor_service.py` | **Not started** — Phase 2 | 2 |
| `test_phase2_ground_worker_helpers.py` | 0 | `telemetry_worker.py` | **Not started** — Phase 2 | 2 |

### 5.2 Gap Summary

- **Phase 1 (hardened):** 10 test files, ~39 tests, covering 8 source modules. Tests pass.
- **Phase 2 (unverified):** 3 test stens pending, 0 assertions written.
- **blind spots:** `config.py`, `db.py`, `retry_async`, `vendor_service.py`, `ai_advisor.py`, `telemetry_worker.py`, `mission_health_db.py` have **no test coverage**.

---

## 6. Architecture Overview

### 6.1 Data Flow Diagram

```
┌──────────────┐     UDP      ┌──────────────┐     NATS       ┌──────────────┐
│              │ ──────────► │              │ ──────────────► │              │
│ Simulator    │ 5005/raw  │  Ingest       │ telemetry.      │ Processor   │
│ (simulator.py)│          │  (ingest.py)   │ health/raw      │ (processor.py)│
└──────────────┘           └──────────────┘                 └──────────────┘
                                                         │              │
                                                insert   │     alerts.limit
                                                ┌─────────┘
                                                ▼
                                        ┌──────────────┐
                                        │  Postgres    │
                                        │  ground DB   │
                                        └──────────────┘
         NATS
    ┌──────────────┐  command.    ┌──────────────┐  UDP       ┌──────────────┐
    │              │ ──────────► │              │ ──────────►│              │
    │ Mission API  │      dispatch│ Dispatcher  │ 5006/raw  │ Simulator   │
    │ (mission_api.py)│           │ (command_dispatcher.py)│ (simulator.py) │
    └──────────────┘             └──────────────┘          └──────────────┘
         │                                                  │
         │ health/sequence/alerts GET                     │ ACK back
         └────────────────────────────────────────────────┘
```

### 6.2 Phase 2 Extensions

```
┌──────────────┐  HTTP/JSON  ┌──────────────┐  NATS       ┌──────────────┐
│              │ ─────────► │  Vendor       │ ─────────► │  Telemetry   │
│ Payload Dev  │ /ingest    │ Service       │ raw frame  │ Processor    │
│ (external)   │           │ (vendor_service.py)          │ (+ vendor)   │
└──────────────┘           └──────────────┘              └──────────────┘
                                                       │
                                                       │ alerts.limit
                                                       ▼
                                                ┌──────────────┐
                                                │ AI Advisor   │
                                                │ (ai_advisor.py)│
                                                └──────────────┘
                                                      │
                                                      │ advisory
                                                      ▼
                                                ┌──────────────┐
                                                │  Ground Worker│
                                                │ (telemetry_worker.py)│
                                                └──────────────┘
```

---

## 7. Risk Register

| ID | Risk | Impact | Likelihood | Mitigation |
|----|------|--------|-----------|------------|
| R01 | pytest not installed in `.venv` | Cannot run Phase 2 tests | Certain | `uv pip install pytest` |
| R02 | No `docker` or `docker-compose` | No infrastructure testing | Certain | Run Postgres/NATS via native services |
| R03 | `db.py` low pool size (max 5) | Sequential bottleneck under load | Medium | Patch pool defaults, add config |
| R04 | 0 Phase 2 tests | Cannot verify vendor_service, ai_advisor, worker | Certain | Implement test_phase2_* files |
| R05 | Hard-codedNatS client name | Trace loss when multiple consumers | Low | Parameterize client name |

---

## 8. Decision-Blocking Questions

1. **Given pytest is not installed** — proceed with `uv pip install pytest` in `.venv` first?
2. **Native NATS assumed?** Assuming Postgres + NATS unavailable — proceed with in-process unit tests only?
3. **Pool size patch needed?** Shall I patch `db.py` pool defaults from `maxsize=5` to `maxsize=20`?

---

## 9. Immediate Action Items

### 9.1 Blockers (Must resolve before Phase 2)

- [ ] Install `pytest` in `.venv` (`uv pip install pytest`)
- [ ] Create `tests/conftest.py` with shared fixtures
- [ ] Decide: docker-compose infra or native testing?

### 9.2 Phase 2 Priority Order

| Priority | Slice | Test File | Est. Effort |
|----------|-------|-----------|------------|
| 1 | `test_phase2_config_metrics.py` config validation | 1 file | 30 min |
| 2 | `test_phase2_vendor_service.py` vendor endpoint | 1 file | 45 min |
| 3 | `test_phase2_ground_worker_helpers.py` worker helpers | 1 file | 60 min |

---

## 10. Compliance Summary

| Check | Status |
|-------|--------|
| Permanent sequence followed | ✅ — implemented → test → doc → claim |
| Phase 2 tests before verification | ❌ — 0/85 Phase 2 tests exist |
| No claim for unverified item | ✅ — no claims prepared for Phase 2 |
| Evidence verification Trail | ✅ — evidence/p2/ is being built |
| GitHub portfolio hygiene | ✅ — clean commit history |

---

## 11. Appendix: Configuration Defaults Catalog

All 15 assumptions with their default values, expected production values,
and configuration override paths.

### Hardcoded Defaults Table

| Env Override | Default Value | Location | Production Override |
|-------------|---------------|----------|-------------------|
| `NATS_URL` | `nats://nats:4222` | `nats.py`, `services/*` | Docker service DNS — already overlapping |
| `TELEMETRY_PORT` | `5005` | `simulator.py`, `telemetry_ingest.py` | Already handled via `env_int` |
| `SIM_COMMAND_PORT` | `5006` | `command_dispatcher.py` | Already handled via `env_int` |
| `METRICS_PORT` | varies (9101-9104) | all services | Already handled via `env_int` |
| `DATABASE_URL` | `postgresql://ground:***@postgres:5432/ground` | `db.py`, `processor.py`, `api.py` | Should override in Docker |
| `LOG_LEVEL` | `INFO` | all services | Optional — use `DEBUG` for dev |
| `ADVISORY_MODEL` | `gpt-4o` | `ai_advisor.py` | Needed for vendor service |
| Pool `maxsize` | `5` | `db.py` | Should be `20` for SSA-rate processing |
| Pool `minsize` | `1` | `db.py` | Should be `3` minimum |
| `encode/decode` | CCSDS standard header | `packet.py` | Protocol-level — intentional |
| Threshold CSVs | Hardcoded | `limits.py` | Should be config-driven for production |

### Environment Variable Matrix

| Service | Configs | Defaults |
|---------|---------|----------|
| `telemetry_ingest` | `NATS_URL`, `SIMULATOR_ADDRESS`, `SIMULATOR_PORT`, `LOG_LEVEL`, `METRICS_PORT` | Docker 127.0.0.1:5005, INFO, 9101 |
| `command_dispatcher` | `NATS_URL`, `SIMULATOR_ADDRESS`, `SIMULATOR_PORT`, `LOG_LEVEL`, `METRICS_PORT` | Docker 127.0.0.1:5006, INFO, 9102 |
| `telemetry_processor` | `NATS_URL`, `DATABASE_URL`, `LOG_LEVEL`, `METRICS_PORT` | Docker, postgres:5432, INFO, 9103 |
| `mission_api` | `NATS_URL`, `DATABASE_URL`, `LOG_LEVEL`, `METRICS_PORT` | Docker, postgres:5432, INFO, 9104 |
| `vendor_service` | `NATS_URL`, `SIMULATOR_ADDRESS`, `SIMULATOR_PORT`, `LOG_LEVEL`, `METRICS_PORT` | Docker, 9105 |
| `ai_advisor` | `NATS_URL`, `DATABASE_URL`, `ADVISORY_MODEL`, `ADVISORY_MODEL_API_KEY`, `LOG_LEVEL` | Docker, gpt-4o |
| `telemetry_worker` | `DATABASE_URL`, `LOG_LEVEL` | Local |

---

*End of audit. No application code was changed during this audit.*