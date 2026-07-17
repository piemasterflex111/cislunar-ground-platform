# Operations Runbook

## Start

```bash
cp .env.example .env
./scripts/up.sh
./scripts/smoke_test.sh
```

## Confirm telemetry

```bash
curl -fsS 'http://localhost:8080/telemetry/latest?limit=3' | jq
```

Expected: recent rows with increasing `sequence_count`.

## Investigate an alert

```bash
curl -fsS 'http://localhost:8080/alerts?limit=10' | jq
```

1. Confirm the alert is backed by current telemetry.
2. Check packet-gap and ingest-error metrics.
3. Compare vehicle fault flags with voltage and temperature.
4. Use a dry-run command first.
5. Arm a live command only after the deterministic checks are complete.

## Command recovery states

- `DRY_RUN_VALIDATED`: validated but never transmitted.
- `QUEUED`: stored and published for dispatch.
- `DISPATCHED`: sent to the simulated spacecraft UDP endpoint.
- `ACKED`: spacecraft accepted and applied it.
- `REJECTED`: spacecraft rejected it.
- `DISPATCH_ERROR`: dispatcher failed before confirmed send.

## Service failure drill

```bash
./scripts/chaos_restart.sh telemetry-processor
```

Expected: telemetry continues entering JetStream; after restart, the durable consumer resumes processing.

## Stop

```bash
./scripts/down.sh
```
