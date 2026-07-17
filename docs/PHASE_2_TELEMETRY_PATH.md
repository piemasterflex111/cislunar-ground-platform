# Phase 2: Telemetry Path

## Phase boundary

Phase 2 implements only telemetry traveling from the simulated payload to an
operator-visible processed result. It does not implement command dispatch,
payload acknowledgements, Transport Layer Security (TLS), or mutual Transport
Layer Security (mTLS). Those command and security features belong to later
phases.

The local Phase 2 path is:

```text
logical payload
      |
      | 28-byte binary telemetry frame
      v
vendor-simulator
      |
      | HTTP + local vendor token
      v
ground-api --------> PostgreSQL
      |               raw bytes, receipt, sequence evidence
      |
      | saved record identifier
      v
    Redis
      |
      v
payload-worker ----> PostgreSQL
                      decoded engineering values and health result
                            |
                            v
                    operator reads through ground-api
```

The payload is a logical module inside `vendor-simulator`, not another running
service. This keeps the payload/vendor ownership boundary visible without
creating an unnecessary seventh container.

The byte-level contract is defined in
[`INTERFACE_CONTROL_DOCUMENT.md`](INTERFACE_CONTROL_DOCUMENT.md). That document
is normative: if the implementation and the Interface Control Document differ,
the difference is a defect that must be resolved explicitly.

An Application Programming Interface (API) is a defined doorway through which
one program requests data or an action from another. This phase needs APIs for
the vendor handoff and operator retrieval; they are like controlled receiving
desks and exist in `mission_ground/services/ground_api.py` and
`mission_ground/services/vendor_simulator.py`.

## Service responsibilities

### `vendor-simulator`

The simulator creates the exact 28-byte telemetry frame defined by the
Interface Control Document and forwards those bytes without decoding and
rebuilding them. Its `boot_id` file is stored at
`/var/lib/ground/vendor/boot_id` in the `vendor-sim-data` named Docker volume,
so an ordinary container restart does not silently reuse a source-session
identity. It exposes a local control interface on port 8081 for deterministic
exercises.

### `ground-api`

The gateway authenticates the local vendor token, applies a request-size cap,
preserves the received bytes, validates the frame, records sequence evidence,
and queues only a valid unique packet. It also exposes raw and processed records
to an authenticated operator. It listens on container port 8080, published only
as `127.0.0.1:8080` by default.

### `payload-worker`

The worker removes a saved record identifier from Redis, loads the authoritative
raw bytes from PostgreSQL, decodes engineering values, assigns `NOMINAL`,
`WARNING`, or `CRITICAL`, and saves one idempotent processed result. Idempotent
means that receiving the same work reference again cannot create a second
logical result.

The worker health check performs the Structured Query Language (SQL) statement
`SELECT 1` against PostgreSQL and `PING` against Redis. SQL is the language used
to request relational-database operations; this harmless query is like asking a
records clerk to answer without changing a record, and the check exists in
`mission_ground/services/payload_worker.py`. This proves dependency readiness at
one instant; it does not prove that the worker is keeping up. Queue age, queue
depth, and the age of the latest processed record are the operational progress
signals added later.

### `postgres`

PostgreSQL holds durable evidence: delivery attempts, exact raw frames,
validation outcomes, sequence observations, processed results, job state, and
audit events. Durable means the records survive a normal process or container
restart. The named `postgres-data` volume is deliberately retained by
`scripts/down.sh`.

### `redis`

Redis is the waiting line between reception and processing. It holds references
to records, never the only copy of a telemetry frame. Append-only persistence is
enabled and the `redis-data` volume survives normal restarts, but PostgreSQL
remains the authoritative evidence store.

## Database records and relationships

Persistence means saving information so it remains after a program restarts.
This phase needs persistence because a queue, worker, or web process may stop
after ground has accepted custody of telemetry. PostgreSQL is the controlled
logbook, and the table definitions exist in
`mission_ground/telemetry/storage.py`.

```text
telemetry_delivery_receipts        one row for every authenticated handoff attempt
             |
             | accepted unique delivery becomes exactly one
             v
logical_telemetry_packets          one row per payload + boot + sequence identity
        |                 |
        |                 +----> telemetry_sequence_states
        |                         high-water and missing-count evidence per boot
        v
telemetry_processing_jobs          retry, lease, queue, complete, or failed state
        |
        | successful idempotent completion creates exactly one
        v
processed_telemetry                engineering values and health classification

telemetry_audit_events             append-only events linked to receipt, packet,
                                   or job when such an identifier exists
telemetry_request_identities       maps one vendor request identifier to its first body
```

The separation between a delivery receipt and a logical packet is important.
If the vendor sends the same packet twice with two request identifiers, both
handoffs remain visible, but there is still one logical packet, one processing
job, and one processed result. The request-identity table handles a different
case: the vendor repeats the same HTTP request after losing the response. Exact
bytes return the original receipt; different bytes under that request identity
become conflict evidence.

A relationship is a database rule connecting records by identity. Here it is
like a hardware traveler that points to the exact incoming unit, its work
order, and its test result without copying the unit itself into every form.

## Network and port boundaries

| Source | Destination | Port | Protocol in Phase 2 | Purpose |
|---|---|---:|---|---|
| Host operator | `ground-api` | 8080 | HTTP over Transmission Control Protocol | Readiness and telemetry retrieval |
| Host operator | `vendor-simulator` | 8081 | HTTP over Transmission Control Protocol | Controlled telemetry generation |
| `vendor-simulator` | `ground-api` | 8080 | HTTP over Transmission Control Protocol | Submit exact telemetry bytes |
| `ground-api` | `postgres` | 5432 | PostgreSQL over Transmission Control Protocol | Preserve operational evidence |
| `ground-api` | `redis` | 6379 | Redis over Transmission Control Protocol | Queue saved record references |
| `payload-worker` | `postgres` | 5432 | PostgreSQL over Transmission Control Protocol | Load raw records and save results |
| `payload-worker` | `redis` | 6379 | Redis over Transmission Control Protocol | Claim processing work |

Transmission Control Protocol (TCP) is the network transport that provides an
ordered byte stream between two programs. The web and data protocols above use
it so both ends agree on byte order and delivery within one connection.

Hypertext Transfer Protocol (HTTP) defines request and response messages between
web programs. Phase 2 uses it as the visible vendor and operator handoff, like a
courier form with a response receipt; the route definitions exist in the two API
service modules. JavaScript Object Notation (JSON) is a text representation of
named values. The simulator control API uses JSON because a person can easily
read and change its test inputs; the actual vendor telemetry body remains the
binary frame defined by the Interface Control Document.

`postgres` and `redis` have no host-published ports and join only the
`ground-internal` Docker network. `vendor-simulator` joins only `vendor-edge`.
`ground-api` joins both because it is the controlled boundary. This is useful
structural isolation, but Phase 5 still must verify the network policy, minimize
all connections, and document firewall behavior.

## Local security limitation

Phase 2 deliberately uses plain HTTP and static environment-based tokens. HTTP
does not encrypt the token or frame in transit. A token proves only that the
caller possesses a shared string; it does not provide the stronger machine
identity, certificate lifecycle, or protected transport required for a real
vendor interface.

Therefore:

- both published listeners bind to `127.0.0.1`, the local machine only;
- the example tokens are local teaching credentials and must never be called
  production-grade security;
- logs must not print tokens;
- `.env` must not be committed;
- Phase 5 replaces the vendor link with Hypertext Transfer Protocol Secure
  (HTTPS) and mTLS, verifies certificates in both directions, exercises an
  invalid vendor certificate, and demonstrates certificate rotation.

HTTPS means HTTP carried inside TLS encryption. mTLS means both peers present
and verify certificates: the vendor verifies the gateway, and the gateway
verifies the vendor.

## Start, inspect, stop

From the repository root:

```bash
cp .env.example .env          # only when .env does not already exist
./scripts/up.sh
docker compose --env-file .env -f deployment/compose.yaml ps
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/readyz
curl -fsS http://127.0.0.1:8081/healthz
```

Expected behavior:

- `scripts/up.sh` reports that `ground-api` and `vendor-simulator` are ready;
- PostgreSQL, Redis, the gateway, the worker, and the simulator report healthy;
- `/healthz` confirms that a web process can answer;
- `/readyz` confirms that the gateway can perform its required PostgreSQL and
  Redis dependency probes.

Stop the services without deleting evidence:

```bash
./scripts/down.sh
```

`down.sh` intentionally omits `--volumes`. Deleting named volumes destroys local
evidence and is a separate, explicit maintenance action.

## Exercise the telemetry path

Load the local teaching credentials and submit one deterministic nominal frame:

```bash
source .env
curl -fsS \
  -X POST http://127.0.0.1:8081/simulator/telemetry \
  -H 'Content-Type: application/json' \
  -d '{
    "temperature_c": 28.4,
    "voltage_v": 28.1,
    "operating_mode": "SCIENCE",
    "fault_flags": 0,
    "failure_mode": "NONE"
  }' \
  -o /tmp/phase2-telemetry-response.json
python3 -m json.tool /tmp/phase2-telemetry-response.json
MESSAGE_ID="$(python3 -c '
import json
body = json.load(open("/tmp/phase2-telemetry-response.json"))
assert body["ground_status"] == 202, body
assert body["ground_response"]["status"] == "QUEUED", body
assert body["ground_response"]["duplicate"] is False, body
print(body["message_id"])
')"
printf 'message_id=%s\n' "$MESSAGE_ID"
```

The response contains `message_id`, `raw_hex`, and `ground_status`.
The outer simulator request returns HTTP 200 when the simulator successfully
performed its control action and received a response from the gateway. That
outer status does **not** prove that ground accepted the telemetry.

`ground_status` is the downstream gateway's HTTP result. For this new nominal
frame, `ground_status` 202 plus `ground_response.status` equal to `QUEUED` and
`duplicate` equal to `false` proves that the raw record is durable and Redis
accepted its saved identifier. A downstream status 200 means a request replay
or exact packet duplicate, 422 means validation rejection, and 503 means a
required dependency was unavailable. None of these statuses claims that the
background worker completed processing.

Retrieve the exact raw evidence:

```bash
curl -fsS \
  -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
  "http://127.0.0.1:8080/payloads/${MESSAGE_ID}/raw" \
  | python3 -m json.tool
```

Then poll for the separately processed result:

```bash
PROCESSED=false
for _ in $(seq 1 30); do
  if curl -fsS \
    -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
    "http://127.0.0.1:8080/payloads/${MESSAGE_ID}/processed" \
    -o /tmp/phase2-processed.json; then
    python3 -m json.tool /tmp/phase2-processed.json
    PROCESSED=true
    break
  fi
  sleep 1
done
if [[ "$PROCESSED" != true ]]; then
  echo "FAIL: processed result did not become available within 30 seconds" >&2
  exit 1
fi
```

For the nominal values above, the stored health classification should be
`NOMINAL`. That result is evidence produced by the worker, while `raw_hex` is
evidence of what crossed the vendor-to-ground boundary.

The simulator also supports controlled telemetry failures through the same
request's `failure_mode` field:

| `failure_mode` | Simulator action | Ground behavior to verify |
|---|---|---|
| `NONE` | Sends the requested valid frame | One raw record is queued and processed |
| `CORRUPT_CRC` | Changes the checksum after frame construction | Rejected for checksum mismatch; no processing work |
| `UNSUPPORTED_VERSION` | Sends a version outside the supported contract | Rejected for protocol version; no processing work |
| `SKIP_SEQUENCE` | Advances past one sequence value before sending | Later frame is accepted and a sequence gap is recorded |
| `DUPLICATE_LAST` | Resends the preceding exact logical packet | Duplicate attempt is visible; no second logical result |

Cyclic Redundancy Check (CRC) is a calculated value used to detect accidental
byte corruption. This phase needs it to reject a changed binary frame; it is like
checking a tamper-evident shipment number and is implemented by the telemetry
codec under `mission_ground/telemetry/`.

For example, change only the final field in the send request:

```json
"failure_mode": "CORRUPT_CRC"
```

Always inspect the HTTP response, stored raw or rejection evidence, logs,
metrics, and database/job state together. One signal alone does not prove the
complete failure behavior.

A rejection receipt does not create its own logical packet, so it is not
retrievable through the `GET /payloads/{message_id}/raw` or `processed` routes.
For a newly rejected identifier those routes return 404. If rejected bytes claim
an identifier that already belongs to an accepted packet, those routes still
describe the accepted packet, never the rejection. Inspect a rejection through
its structured log, administrator audit event, and delivery receipt in
PostgreSQL.

The audit event exposes the rejection reason and receipt identifier:

```bash
curl -fsS \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  'http://127.0.0.1:8080/audit-events?limit=10' \
  | python3 -m json.tool
```

The exact rejected bytes remain in the internal database. This explicit local
inspection shows the receipt, reason, and hexadecimal evidence without
publishing the PostgreSQL port:

```bash
docker compose --env-file .env -f deployment/compose.yaml exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT receipt_id, rejection_reason, encode(raw_body, 'hex') AS raw_hex FROM telemetry_delivery_receipts WHERE validation_status = 'REJECTED' ORDER BY received_at DESC LIMIT 5;"
```

Metrics require the administrator token. An invalid vendor token is evidenced
by its HTTP 401 response, structured `vendor.authentication.failed` log,
persisted security audit event, and
`vendor_authentication_failures_total` counter. It creates no telemetry packet
or processing job.

## Failure boundaries and expected behavior

Use this isolation pattern for every failure:

```text
expected behavior
-> last working step
-> first failing step
-> inspect that boundary
-> correct the cause
-> repeat the complete path
```

| Failure | Expected safe behavior | Evidence to inspect | Recovery |
|---|---|---|---|
| Invalid vendor token | Gateway rejects before creating processing work | HTTP 401, security audit event, authentication metric, and `vendor.authentication.failed` log; no queue job | Correct the local token, then resend |
| Wrong frame length, version, field, or checksum | Exact bounded bytes and rejection reason are preserved; no processing job is created | Delivery receipt, raw hexadecimal bytes, rejection reason, rejection metric | Correct the producer or interface version |
| Duplicate payload/boot/sequence | Delivery attempt is visible but no second logical packet or processing job is created | Duplicate receipt, one raw logical record, duplicate metric | No data repair; investigate why delivery repeated |
| Sequence gap | Later valid packet is accepted and the missing range is recorded | Sequence observation and gap metric | Investigate payload-to-vendor and vendor-to-ground boundaries; do not invent the missing packet |
| PostgreSQL unavailable | Gateway returns unavailable because it cannot prove durable receipt | Gateway readiness and database-error log/metric | Restore PostgreSQL and resend the identical frame |
| Redis unavailable after database save | Raw evidence remains pending; gateway does not claim processing completed | Raw record/job state, gateway readiness, queue-error log | Restore Redis; an idempotent retry or recovery queues the saved record once |
| Worker stopped | Reception and raw preservation continue while queued work waits | Growing queue depth, older pending database jobs, raw record present, processed record pending | Restart worker; it resumes saved work safely |
| Permanent post-claim integrity error | Packet validation failure or disagreement between preserved bytes and stored decoded fields moves the work to failed state on its first attempt | Error reason, failed-work state, failed Redis list, processing-failure metric | Investigate possible data corruption or software-contract defect before deliberate requeue |
| Transient post-claim failure | Work enters retry-wait with exponential backoff; after the stored maximum attempts it moves to failed state | Attempt count, next-attempt time, retry audit event, processing-failure metric | Restore the dependency; the same saved job retries without creating another logical result |
| Gateway restarted | Named-volume evidence and dependency state survive; readiness is false until dependencies work | Container state, readiness, PostgreSQL records | Restart gateway and repeat retrieval |
| Simulator restarted | Persistent `boot_id` storage prevents silent session-identity reuse | `vendor-sim-data` volume and next frame identity | Confirm session identity, then send the next frame |

A downstream gateway `ground_status` of 200 or 202 means the raw record is
durable and the saved identifier was already queued or was newly queued. The
simulator's outer HTTP 200 alone does not mean that. Queue acceptance still does
not mean processing finished; the operator must retrieve the processed result
or observe its processing state.

The worker distinguishes a permanent integrity problem from a temporary
operational failure. A telemetry validation error or a mismatch between the
preserved bytes and stored decoded fields is permanent because repeating the
same bytes cannot correct it; the first attempt enters failed-work state. Other
post-claim failures are treated as temporary. Their database job enters
`RETRY_WAIT`; successive retry delays are one second, two seconds, four seconds,
and so on, capped at five minutes. The local default allows three stored
attempts, so it waits after attempts one and two and enters failed-work state if
attempt three also fails. The raw record remains in PostgreSQL throughout, and
no retry creates a second logical payload result.

## Phase 2 completion evidence

Phase 2 is complete only after the implementation and tests demonstrate:

1. the golden telemetry frame encodes and decodes exactly;
2. valid vendor delivery preserves byte-for-byte raw evidence;
3. corrupt and unsupported frames are rejected and never processed;
4. duplicates create delivery evidence but one logical result;
5. a sequence gap is detected without rejecting the later valid packet;
6. Redis separates reception from processing;
7. a worker restart completes queued work without a duplicate result;
8. an operator retrieves both the raw record and processed result;
9. ordinary service restart preserves PostgreSQL, Redis, and simulator state;
10. exact shell commands and observed evidence are recorded in the learning log.

This phase is a local engineering demonstration. It is not production-ready,
and it is not production cloud or production spacecraft experience.
