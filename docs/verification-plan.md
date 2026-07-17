# Verification Plan

Verification is evidence-driven:

1. Unit tests verify packet integrity, engineering-unit conversion, sequence handling, deterministic limits, command bounds, and idempotency.
2. `scripts/verify_traceability.py` verifies each requirement points to an existing test and document.
3. `scripts/perf_packet_codec.py` measures deterministic packet encode/decode throughput.
4. `scripts/smoke_test.sh` verifies deployed telemetry, dry-run commands, live command dispatch, spacecraft acknowledgement, alerts, and metrics.
5. CI runs static checks, unit tests, traceability, container build, and the Compose smoke test.

Artifacts should be captured from CI logs or redirected into an `artifacts/<timestamp>/` directory for interview evidence.
