# Architecture

## Operational data path

The simulated vehicle emits health packets through a UDP link. Telemetry Ingest is the protocol boundary: it validates packet structure and CRC, decodes known APIDs, detects sequence gaps, and publishes normalized JSON events to a durable message bus.

NATS JetStream decouples link reception from processing. Telemetry Processor consumes with a durable name and explicit acknowledgements, persists data, performs deterministic limit checking, and emits processed telemetry and alerts. Mission API reads from PostgreSQL and streams bus events to operators.

## Command path

1. An operator submits a command to Mission API.
2. The API checks the allowlist and argument range.
3. Dry-run commands stop after validation and audit recording.
4. Live commands require an operator token and a separate arm token.
5. The API publishes `commands.dispatch` with a database-generated command ID.
6. Command Dispatcher encodes a binary command with CRC and sends it over UDP.
7. The simulator applies a command once per command ID and emits an ACK packet.
8. The ACK traverses the telemetry path and updates command state to `ACKED` or `REJECTED`.

## Failure boundaries

- If the processor is unavailable, JetStream retains telemetry until its durable consumer resumes.
- If PostgreSQL is unavailable, the processor negatively acknowledges messages for redelivery.
- If the API is unavailable, telemetry collection continues.
- If the AI advisor is unavailable, deterministic telemetry, alerts, and commands continue.
- Containers restart automatically in Compose; Kubernetes deployments include readiness/liveness probes and rolling updates.

## AI isolation

AI output is advisory only. The service subscribes to `alerts.>` and publishes to `advisories.>`. It has no operator credentials, no database write role, no UDP command socket, and no subscription or publish path to `commands.>`.
