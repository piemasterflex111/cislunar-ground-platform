# Secure Payload Command and Telemetry Gateway

## Interface Control Document

| Document field | Value |
|---|---|
| Document identifier | SPC-TG-ICD-001 |
| Version | 0.1 |
| Status | Phase 1 target interface; not yet implemented |
| Date | 2026-07-16 |
| Project scope | One simulated payload, one vendor, one ground gateway |
| Change rule | Change this document and its golden vectors before changing interface code |

This Interface Control Document, or ICD, defines how the simulated payload, third-party vendor simulator, ground gateway, command dispatcher, and operator exchange information.

It is the normative target for the narrowed Secure Payload Command and Telemetry Gateway. The current lowercase docs/interface-control.md describes the repository's older User Datagram Protocol (UDP) and NATS message-broker implementation. It remains legacy evidence until later phases replace the implementation; it is not the contract for new work.

No application code conforms to this document yet. Phase 1 defines the contract before implementation.

## 1. How to read this document

The words MUST, MUST NOT, SHOULD, and MAY have precise meanings:

- MUST or MUST NOT identifies a requirement.
- SHOULD identifies a recommended choice that needs a recorded reason if not followed.
- MAY identifies an allowed option.

A byte is eight binary digits. A bit is one binary digit. Offset zero is the first byte transmitted.

Network byte order means the most significant byte is transmitted first. It is also called big-endian byte order. In a physical analogy, it is an agreed connector pinout: both sides must assign the same meaning to the same position.

## 2. Scope and ownership boundaries

The project contains one logical payload module inside the vendor-simulator deployment. This preserves the six-service deployment while keeping the payload-to-vendor interface visible.

~~~text
LOGICAL VENDOR SIDE                          GROUND SIDE

(payload module)
      |
      | 28-byte binary telemetry
      v
[vendor-simulator] == HTTPS with mTLS ==> [ground-api]
                                                |
                                                +--> exact raw bytes --> [postgres]
                                                |
                                                +--> job reference ----> [redis]
                                                                            |
                                                                            v
                                                                    [payload-worker]
                                                                            |
                                                                            +--> result --> [postgres]

Operator == HTTPS + operator identity ==> [ground-api]
                                                |
                                                +--> command first --> [postgres]
                                                |
                                                +--> reference ------> [redis]
                                                                            |
                                                                            v
                                                               [command-dispatcher]
                                                                            |
                                                                            | binary command
                                                                            | HTTPS with mTLS
                                                                            v
                                                                  [vendor-simulator]
                                                                            |
                                                                     (payload module)
                                                                            |
                                                                            | binary ACK
                                                                            v
                                                                  [vendor-simulator]
                                                                            |
                                                                            | authenticated
                                                                            | callback
                                                                            v
                                                                       [ground-api]
                                                                            |
                                                                            +--> evidence
                                                                                 and state
                                                                                 [postgres]
~~~

The logical payload/vendor boundary models a real ownership boundary even though both are code in one container. A boundary is a handoff where responsibility changes. It is like a spacecraft-to-test-equipment connector: both sides need an agreed format even when they sit beside each other.

### 2.1 In scope

- A project-specific fixed binary telemetry frame.
- A small binary command frame.
- A fixed binary payload acknowledgement frame.
- Exact command states and legal transitions.
- Exact duplicate, sequence-gap, timeout, and late-acknowledgement behavior.
- Hypertext Transfer Protocol Secure, or HTTPS, for network exchange.
- Mutual Transport Layer Security, or mTLS, for machine-to-machine identity.
- Exact raw-byte preservation at the ground boundary.

### 2.2 Out of scope

- Radio-frequency communications.
- A complete Consultative Committee for Space Data Systems standard.
- Flight certification or safety certification.
- Multiple payloads or multiple vendors.
- End-to-end cryptographic signatures made by real flight hardware.
- Production certificate authorities, certificate revocation infrastructure, or hardware security modules.
- Kubernetes, artificial intelligence, and production Amazon Web Services deployment.

## 3. Terms used at the interfaces

| Term | Plain meaning | Why it exists here | Physical analogy | Future implementation location |
|---|---|---|---|---|
| Application Programming Interface (API) | A defined software doorway for data or actions. | Vendors and operators need controlled entry points. | A receiving desk with required forms. | mission_ground/services/ground_api.py |
| Hypertext Transfer Protocol (HTTP) | Request-and-response rules used by web services. | The vendor and operator interfaces need defined requests, responses, and status results. | A courier handoff with a delivery form and receipt. | Ground and vendor API routes |
| JavaScript Object Notation (JSON) | Human-readable text made of named values, objects, and lists. | Operators need a readable command request and response format. | A labeled electronic procedure form. | Operator API schemas |
| Cyclic Redundancy Check (CRC) | A number calculated from bytes to detect accidental change. | Corrupted telemetry, commands, and acknowledgements must be rejected. | Comparing a shipment seal number. | mission_ground/common/packet.py |
| Transport Layer Security (TLS) | A protocol that encrypts a network connection and authenticates a server certificate. | Network traffic must not travel as readable or easily altered plaintext. | A locked, tamper-evident courier case. | Phase 5 proxy and certificate configuration |
| Mutual Transport Layer Security (mTLS) | TLS in which client and server both present certificates. | The vendor and ground machine endpoints must prove their identities to each other. | Both people at a controlled handoff checking badges. | Phase 5 certificate and listener configuration |
| Universally Unique Identifier (UUID) | A 128-bit identifier designed to be unique without one central counter. | Commands and network deliveries need stable correlation identifiers. | A serialized work-order number. | Command model and API schemas |
| Secure Hash Algorithm 256-bit (SHA-256) digest | A fixed-size fingerprint calculated from bytes. | Raw deliveries need an efficient way to detect identical or conflicting bodies. | Comparing a recorded fingerprint without replacing the evidence. | Raw-delivery database operations |
| Coordinated Universal Time (UTC) | The common time reference used by this project. | Payload and ground timestamps need one agreed interpretation. | Every test station using the same master clock. | Packet codecs and database timestamps |
| Idempotency | Repeating the same request produces no second intended effect. | Lost responses and duplicate queue jobs must not execute work twice. | Checking a completed work-order number before repeating a procedure. | Command API, database uniqueness rules, payload ledger |
| Audit event | A permanent record of who did what, when, and with what result. | Operators must reconstruct command and rejection history. | A signed configuration-control log entry. | PostgreSQL audit and transition tables |
| PostgreSQL | A relational database that stores connected records in tables. | Raw evidence, commands, transitions, and audit history must survive restarts. | A controlled permanent logbook. | Database models and operations |
| Redis | An in-memory data store used here for waiting work. | Reception and dispatch must be separated from background work. | A work tray containing references to permanent records. | Queue adapter and workers |

CRC is not authentication. A person who can alter a packet can calculate a new valid CRC. mTLS proves the immediate network peer and protects the connection. It does not prove that uncompromised spacecraft hardware originated the bytes. This demonstration trusts the authenticated vendor as the payload's representative.

## 4. Network interface summary

Transmission Control Protocol, or TCP, provides an ordered network connection between two programs. HTTPS runs over TCP in this project.

| Interface | Source | Destination | Port | Protocol and identity | Purpose |
|---|---|---|---:|---|---|
| IF-TLM-01 | vendor-simulator | ground-api vendor listener | 8443 | HTTPS over TCP with mTLS vendor identity | Submit one binary telemetry frame |
| IF-ACK-01 | vendor-simulator | ground-api vendor listener | 8443 | HTTPS over TCP with mTLS vendor identity | Submit one binary payload acknowledgement |
| IF-CMD-01 | command-dispatcher | vendor-simulator | 9443 | HTTPS over TCP with mTLS dispatcher identity | Push one recorded binary command |
| IF-OP-01 | operator or administrator | ground-api operator listener | 8444 | HTTPS over TCP with local bearer credential | Create commands and retrieve records |
| IF-DB-01 | ground-api | postgres | 5432 | TCP on internal Docker network | Save operational and audit records |
| IF-Q-01 | ground-api | redis | 6379 | TCP on internal Docker network | Add saved record references to queues |
| IF-WORK-01 | payload-worker | postgres and redis | 5432 and 6379 | TCP on internal Docker network | Load jobs and store results |
| IF-DISP-01 | command-dispatcher | postgres and redis | 5432 and 6379 | TCP on internal Docker network | Load commands, claim dispatch, and store state |

The ground-api service has two listeners so the vendor listener can require client certificates without forcing a human operator to possess a machine certificate.

PostgreSQL and Redis MUST NOT publish host ports and MUST NOT join the vendor-facing Docker network. Docker network membership and firewall behavior will be implemented and demonstrated in Phase 5.

### 4.1 Target certificate identities

The Phase 5 local certificate setup MUST map these identities:

A Uniform Resource Identifier (URI) is a structured identity string; it distinguishes the vendor and dispatcher client roles like a unique badge number, and it will exist in the Phase 5 client certificates. A Domain Name System (DNS) name identifies a network host; it lets a client verify that a server certificate belongs to the intended service like checking a facility address, and it will exist in the Phase 5 server certificates.

| Connection role | Required certificate identity |
|---|---|
| Vendor client calling ground-api | URI subject alternative name urn:cislunar-demo:vendor-simulator |
| Ground vendor listener server | DNS subject alternative name ground-api |
| Dispatcher client calling vendor | URI subject alternative name urn:cislunar-demo:command-dispatcher |
| Vendor command listener server | DNS subject alternative name vendor-simulator |
| Ground operator listener server | DNS subject alternative names localhost and ground-api |

An expired or untrusted client certificate MUST fail during the TLS handshake. A client MUST reject a server whose certificate name does not match the requested server. These failures occur before an HTTP request reaches application code, so there is no HTTP response and normally no raw-payload database record. Evidence comes from client TLS output and server TLS logs.

A certificate may chain to the trusted local certificate authority but contain a URI identity that is valid for another role. After the TLS handshake, the listener MUST compare the presented URI identity with the role required by the route. A wrong-role certificate receives 403 Forbidden, creates a security audit event, and creates no payload or command record. Base mTLS chain validation does not perform this project-specific authorization by itself.

Operator and administrator credentials are separate environment-based bearer tokens for this local demonstration. Static local tokens are not production-grade security.

After mTLS maps the vendor certificate to the vendor identity, authorization still applies. The vendor listener on port 8443 exposes /commands only as a negative authorization test route; a vendor identity requesting it MUST receive 403 Forbidden and create an audit event. Normal operators use the bearer-token listener on port 8444. This makes the required vendor-credential demonstration deterministic instead of depending on a 404 route result.

## 5. Telemetry submission over HTTPS

### 5.1 Request

~~~http
POST /vendor/payload-data HTTP/1.1
Host: ground-api:8443
Content-Type: application/octet-stream
X-Request-ID: 8bdfbe87-6329-47e1-9dcb-a9533d8010cf

<exactly one 28-byte telemetry frame>
~~~

application/octet-stream means the body contains raw bytes rather than JSON text. The vendor MUST forward the payload frame unchanged. It MUST NOT decode and rebuild the frame.

X-Request-ID MUST be a canonical UUID version 4 string. A retry of the same delivery after a lost HTTP response MUST reuse the same request identifier and exact body.

The ground listener MUST cap an authenticated request body at 1,024 bytes. Bodies within the cap are preserved exactly, including malformed bodies. A body above the cap is rejected before normal decoding and receives an oversized-request security record.

### 5.2 Success and failure responses

| Result | HTTP status | Meaning |
|---|---:|---|
| New valid frame saved and queued | 202 Accepted | Raw evidence is durable and Redis accepted the saved record reference; processing is not complete |
| Same request ID and exact body retry after successful queueing | 200 OK | Existing delivery receipt is returned and no second processing job is created |
| New request ID carrying an exact packet duplicate | 200 OK | A new duplicate-delivery receipt points to the original logical packet; no second processing job is created |
| Malformed header or missing required header | 400 Bad Request | Request cannot be correlated or interpreted as the interface requires |
| Vendor identity valid but not permitted for payload ID | 403 Forbidden | Authentication succeeded; authorization failed |
| Body over configured cap | 413 Content Too Large | Request exceeds the defensive evidence limit |
| Request ID reused with different bytes or sequence key reused with different bytes | 409 Conflict | The same claimed identity refers to conflicting evidence |
| Authenticated packet rejected by version, length, CRC, or field rules | 422 Unprocessable Content | Raw bytes and rejection reason are saved; packet is not queued |
| PostgreSQL unavailable | 503 Service Unavailable | Ground cannot prove durable receipt; vendor retries the same request |
| Redis unavailable after PostgreSQL save | 503 Service Unavailable | Receipt remains PENDING_QUEUE; retry safely resumes queueing |

Payload field validity is evaluated before payload authorization. In version 1, payload_id values other than 1 receive 422 as unsupported. A 403 response is used when a recognized payload ID is presented by an authenticated machine identity that lacks permission for it.

Example 202 response:

~~~json
{
  "receipt_id": "37db0e7d-3159-48a7-b9e5-93a03c395d42",
  "packet_id": "TLM-P01-B0000002A-S00000001",
  "status": "QUEUED",
  "duplicate": false
}
~~~

An HTTP success never means processing completed. It only reports the durable-receipt and queue result stated above.

A delivery receipt records one vendor-to-ground attempt. A logical packet record represents one unique payload, boot, and sequence key. Keeping these as separate concepts preserves evidence that a duplicate delivery occurred without processing the same logical packet twice.

If an idempotent retry finds PENDING_QUEUE, ground-api MUST attempt the enqueue again using an atomic queue marker. It returns 503 while the record is still pending, returns 202 when recovery first reaches QUEUED, and returns 200 only for later replays. Recovery never creates two logical queue jobs.

ground-api generates receipt_id before parsing application headers. A missing or malformed X-Request-ID therefore produces a stored rejection receipt whose presented_request_id field is null or contains the invalid presented text.

## 6. Telemetry Frame version 1

### 6.1 Packet diagram

~~~text
Byte offset
 0       1       2               6               10
 +-------+-------+---------------+---------------+-------------------------------+
 | ver   | payld | boot_id       | sequence      | sample timestamp              |
 | 1 B   | 1 B   | 4 B           | 4 B           | 8 B                           |
 +-------+-------+---------------+---------------+-------------------------------+
 18              20              22      23      24                      28
 +---------------+---------------+-------+-------+-----------------------+
 | temperature   | voltage       | mode  | faults| CRC-32                |
 | 2 B signed    | 2 B unsigned  | 1 B   | 1 B   | 4 B                   |
 +---------------+---------------+-------+-------+-----------------------+

 Total: 28 bytes
 B means byte.
~~~

The reference structure notation is:

~~~python
TELEMETRY_V1_BODY = struct.Struct(">BBIIQhHBB")  # 24 bytes
CRC32 = struct.Struct(">I")                     # 4 bytes
~~~

The greater-than character selects big-endian byte order and disables native padding.

### 6.2 Field definitions

| Offset | Size | Field | Wire type | Version 1 meaning and rule |
|---:|---:|---|---|---|
| 0 | 1 | protocol_version | Unsigned 8-bit | MUST equal 1 |
| 1 | 1 | payload_id | Unsigned 8-bit | 1 means PAYLOAD-01; 0 and 2 through 255 are unsupported |
| 2 | 4 | boot_id | Unsigned 32-bit | Nonzero persistent payload source-session counter |
| 6 | 4 | sequence_number | Unsigned 32-bit | Packet number within one source session |
| 10 | 8 | timestamp_us | Unsigned 64-bit | Sample time in microseconds since 1970-01-01 00:00:00 UTC |
| 18 | 2 | temperature_centi_c | Signed 16-bit | Degrees Celsius multiplied by 100 |
| 20 | 2 | voltage_mv | Unsigned 16-bit | Millivolts |
| 22 | 1 | operating_mode | Unsigned 8-bit | Enumerated mode |
| 23 | 1 | fault_flags | Unsigned 8-bit | Bit mask |
| 24 | 4 | crc32 | Unsigned 32-bit | CRC over bytes 0 through 23, stored big-endian |

There is no magic value, message type, or packet-length field in version 1. The telemetry endpoint identifies the frame type, and one HTTP body contains one fixed 28-byte frame. A future multiplexed stream requires a newly specified envelope rather than guessed framing.

### 6.3 Field ranges and engineering conversion

| Field | Representable range | Interface-valid range | Conversion or note |
|---|---|---|---|
| boot_id | 0 through 4,294,967,295 | 1 through 4,294,967,295 | Simulator increments a persistently stored counter at restart or before either sequence counter is exhausted; it never wraps in this demonstration |
| sequence_number | 0 through 4,294,967,295 | Full representable range | It never reuses a value within one boot_id |
| timestamp_us | Unsigned 64-bit | Not before 2026-01-01; not more than five minutes after ground receipt | Old valid backlog is marked stale rather than silently discarded |
| temperature_centi_c | -32,768 through 32,767 | -10,000 through 15,000 | Divide by 100; valid physical range is -100.00 to 150.00 degrees Celsius |
| voltage_mv | 0 through 65,535 | 0 through 50,000 | Divide by 1,000; valid physical range is 0.000 to 50.000 volts |

Interface validity and payload health are different decisions. For example, 85.00 degrees Celsius is validly encoded telemetry even though the later processor will classify it as CRITICAL. An out-of-limit measurement MUST NOT be mislabeled as a malformed packet.

The payload timestamp is sample time. The vendor-send time and ground-receipt time are separate database fields. Unix time in this project does not represent leap seconds separately.

A valid sample more than 24 hours older than ground receipt is STALE. Staleness is not malformed data: the raw frame is accepted, queued, processed once, and marked stale in the result, log, and metric. A timestamp before the fixed project epoch of 2026-01-01 remains invalid.

### 6.4 Operating modes

| Wire value | Name | Meaning |
|---:|---|---|
| 0 | SAFE | Minimum-risk payload behavior |
| 1 | STANDBY | Powered and waiting, not collecting science data |
| 2 | SCIENCE | Normal science collection |
| 3 | CALIBRATION | Payload-controlled calibration behavior |
| 4 through 255 | Invalid in version 1 | Packet is rejected |

CALIBRATION is reportable in telemetry but is not directly commandable in this demonstration.

### 6.5 Fault flags

| Bit mask | Name | Meaning |
|---:|---|---|
| 0x01 | OVER_TEMPERATURE | Payload detected an over-temperature condition |
| 0x02 | UNDER_VOLTAGE | Payload detected an under-voltage condition |
| 0x04 | OVER_VOLTAGE | Payload detected an over-voltage condition |
| 0x08 | TEMPERATURE_SENSOR_FAULT | Temperature measurement may be unreliable |
| 0x10 | VOLTAGE_SENSOR_FAULT | Voltage measurement may be unreliable |
| 0x20 | INTERNAL_PAYLOAD_FAULT | Payload detected another internal fault |
| 0x40 | Reserved | MUST be zero in version 1 |
| 0x80 | Reserved | MUST be zero in version 1 |

More than one defined bit MAY be set. A disagreement between a measurement and a flag is preserved and surfaced; the ground system MUST NOT silently correct either value.

### 6.6 Checksum definition

CRC-32/ISO-HDLC is used with these exact parameters. ISO refers to the International Organization for Standardization, and HDLC means High-Level Data Link Control. The full profile name identifies the exact CRC calculation rather than merely saying CRC-32.

| Parameter | Value |
|---|---|
| Polynomial | 0x04C11DB7 |
| Reflected polynomial | 0xEDB88320 |
| Initial value | 0xFFFFFFFF |
| Input reflected | Yes |
| Output reflected | Yes |
| Final exclusive-or | 0xFFFFFFFF |
| Standard check | CRC of the nine one-byte characters 123456789 equals 0xCBF43926 |
| Covered bytes | Telemetry bytes 0 through 23 exactly as transmitted |
| Excluded bytes | The four checksum bytes |
| Stored byte order | Big-endian |
| Python reference | zlib.crc32(body) & 0xFFFFFFFF |

### 6.7 Golden telemetry vector

Decoded values:

- Protocol version: 1
- Payload: PAYLOAD-01
- Boot identifier: 42
- Sequence number: 1
- Sample time: 2026-07-16T12:00:00Z
- Timestamp integer: 1,784,203,200,000,000 microseconds
- Temperature: 28.40 degrees Celsius
- Voltage: 28.100 volts
- Mode: SCIENCE
- Fault flags: none
- CRC: 0xCC6D0205

Annotated hexadecimal:

~~~text
01 | 01 | 0000002a | 00000001 | 000656b92df17000 |
0b18 | 6dc4 | 02 | 00 | cc6d0205
~~~

Continuous 28-byte hexadecimal vector:

~~~text
01010000002a00000001000656b92df170000b186dc40200cc6d0205
~~~

This vector MUST become a cross-language golden-vector test in Phase 2.

### 6.8 Demonstration health limits

The following limits are project-defined teaching values, not values supplied by a flight mission authority. They make the processor deterministic and testable. A real mission would require approved limits from payload engineering and configuration control.

Temperature severity:

| Temperature | Severity |
|---|---|
| Less than -40.00 degrees Celsius | CRITICAL |
| -40.00 through less than -20.00 degrees Celsius | WARNING |
| -20.00 through less than 62.00 degrees Celsius | NOMINAL |
| 62.00 through less than 85.00 degrees Celsius | WARNING |
| 85.00 degrees Celsius or greater | CRITICAL |

Voltage severity:

| Voltage | Severity |
|---|---|
| Less than 22.000 volts | CRITICAL |
| 22.000 through less than 26.000 volts | WARNING |
| 26.000 through 30.000 volts inclusive | NOMINAL |
| Greater than 30.000 through 34.000 volts inclusive | WARNING |
| Greater than 34.000 volts | CRITICAL |

Fault severity:

| Fault condition | Minimum severity |
|---|---|
| OVER_TEMPERATURE, UNDER_VOLTAGE, or OVER_VOLTAGE | WARNING |
| TEMPERATURE_SENSOR_FAULT, VOLTAGE_SENSOR_FAULT, or INTERNAL_PAYLOAD_FAULT | CRITICAL |
| No defined fault bits | NOMINAL |

The processed classification is the most severe result from temperature, voltage, and fault flags, using CRITICAL above WARNING above NOMINAL. Therefore, with nominal voltage and no faults, 28.00 degrees Celsius is NOMINAL, 62.00 is WARNING, and 85.00 is CRITICAL.

## 7. Telemetry validation and sequence behavior

### 7.1 Validation order

For an authenticated request, ground-api MUST:

1. Enforce the request-body cap.
2. Generate a server receipt ID and save the exact body, ground receipt time, certificate identity, and presented request ID, which is nullable.
3. Validate the required Content-Type and X-Request-ID headers.
4. Reject an empty body.
5. Read byte zero and reject any value other than version 1.
6. Require exactly 28 bytes.
7. Verify the CRC.
8. Decode all fields.
9. Validate that payload_id is supported, then verify that the authenticated vendor may send it.
10. Validate boot ID, timestamp, physical ranges, mode, and reserved fault bits.
11. Apply duplicate and sequence rules.
12. Queue only an accepted, unique record.

An unsupported-version test MUST change the version to 2 and recompute the CRC. Otherwise the test changes two conditions and does not isolate version handling.

An invalid packet never advances sequence tracking.

### 7.2 Persistent packet identity

The logical packet key is:

~~~text
(payload_id, boot_id, sequence_number)
~~~

The displayed packet identifier is:

~~~text
TLM-P01-B0000002A-S00000001
~~~

boot_id distinguishes a payload restart or new source session from a replay or sequence reset. The payload simulator starts sequence_number at zero for each new nonzero boot_id.

### 7.3 Sequence algorithm

For the last high-water sequence L and current sequence S:

~~~text
forward_distance = (S - L) modulo 2^32
~~~

| Condition | Ground behavior |
|---|---|
| First observed frame for a boot and S is zero | Accept and establish high-water mark zero |
| First observed frame for a boot and S is greater than zero | Accept, record S earlier packets as not observed, and establish high-water mark |
| forward_distance equals 1 | Accept in order and advance high-water mark |
| forward_distance is 2 through 2^31 minus 1 | Accept, record forward_distance minus 1 missing packets, and advance |
| forward_distance equals 0 and bytes are identical | Record idempotent duplicate; do not queue again |
| Same packet key but different valid bytes | Preserve, reject as SEQUENCE_CONFLICT, and emit a security/protocol event |
| forward_distance is at least 2^31 | Preserve and process once as late or out of order; do not move high-water mark backward |
| sequence_number reaches 0xFFFFFFFF | The payload MUST increment boot_id and start a new source session before sending another telemetry frame |

Sequence state MUST be persistent across ground-api restart. A process-local dictionary is insufficient. Reusing a sequence value in the same boot would collide with permanent packet identity, so version 1 deliberately performs session rollover rather than same-boot sequence wrap.

A gap proves the ground dataset did not observe one or more sequence values. It does not prove whether loss occurred between payload and vendor or between vendor and ground.

## 8. Operator command interface

### 8.1 Request

~~~http
POST /commands HTTP/1.1
Host: ground-api:8444
Authorization: Bearer <local-operator-or-administrator-token>
Idempotency-Key: example-0001
Content-Type: application/json
~~~

~~~json
{
  "payload_id": "PAYLOAD-01",
  "command_name": "SET_PAYLOAD_MODE",
  "arguments": {
    "mode": "SCIENCE"
  }
}
~~~

Rules:

- payload_id MUST be PAYLOAD-01.
- command_name and argument values are case-sensitive.
- arguments MUST exist, including when it is an empty object.
- Unknown JSON properties MUST be rejected.
- The caller MUST NOT supply operator identity, command ID, state, or timestamps.
- Operator identity MUST come from the authenticated credential.
- Idempotency-Key MUST be an opaque 8 through 128 character value.

Authentication proves the caller's identity. Authorization decides what that identity may do. A valid vendor machine identity attempting an operator command is authenticated but not authorized and receives 403 Forbidden.

### 8.2 Command definitions

| Command | Opcode | Exact JSON arguments | Payload argument bytes | Permission | Automatic resend after possible transmission |
|---|---:|---|---|---|---|
| SET_PAYLOAD_MODE | 0x01 | mode is SAFE, STANDBY, or SCIENCE | One byte: 0x00, 0x01, or 0x02 | Operator or administrator | Never |
| RESET_PAYLOAD | 0x02 | Empty object | No bytes | Administrator only | Never |
| REQUEST_STATUS | 0x03 | Empty object | No bytes | Operator or administrator | At most two bounded retries using the same command ID and bytes |

RESET_PAYLOAD is disruptive and treated as irreversible for retry decisions. SET_PAYLOAD_MODE is also effectful and receives the conservative no-automatic-resend rule. REQUEST_STATUS is read-only.

### 8.3 API idempotency

The ground service generates a UUID version 4 command ID.

Idempotency-Key is a separate operator-request identity scoped by:

~~~text
(authenticated_operator_identity, idempotency_key)
~~~

- The same identity, key, and normalized command returns the existing command and creates no new job.
- The same identity and key with different command content returns 409 Conflict.
- A new key represents a new operator intent.
- A lost acknowledgement MUST NOT cause software to invent a new key.

The normalized command contains only the validated payload ID, command name, and arguments. JSON key order and whitespace do not create a new command.

### 8.4 Operator API responses

| Result | HTTP status | Meaning |
|---|---:|---|
| New command saved and queued | 202 Accepted | Command record and transitions exist; dispatch has not necessarily occurred |
| Exact idempotency replay after successful queueing | 200 OK | Original command is returned; no new queue job |
| Missing or invalid operator credential | 401 Unauthorized | Authentication failed |
| Valid identity lacks command permission | 403 Forbidden | Authorization failed |
| Invalid command name or arguments | 422 Unprocessable Content | No executable command is created; an audit event records the attempt |
| Idempotency key reused for different content | 409 Conflict | Existing operator intent cannot be redefined |
| PostgreSQL unavailable | 503 Service Unavailable | No success is returned because the command was not durably recorded |
| Redis unavailable after save | 503 Service Unavailable | Command remains VALIDATED and can be queued by an idempotent retry or recovery |

Example 202 response:

~~~json
{
  "command_id": "52f274b5-508a-4f46-9022-05992e7c96a3",
  "state": "QUEUED",
  "idempotency_replayed": false,
  "status_url": "/commands/52f274b5-508a-4f46-9022-05992e7c96a3"
}
~~~

Authenticated, authorized, structurally valid requests are recorded as CREATED and transitioned to VALIDATED in one PostgreSQL transaction. Invalid submissions receive an audit event but no executable command ID.

If an idempotent command retry finds the command in VALIDATED because Redis previously failed, ground-api MUST retry the atomic enqueue. It returns 503 while still pending, returns 202 when recovery first reaches QUEUED, and returns 200 only after the command was already queued. It never creates a second command or logical queue job.

### 8.5 Ground API endpoint summary

The derived packet identifier, such as TLM-P01-B0000002A-S00000001, is the message_id path value used below.

| Endpoint | Caller and input | Checks | Storage effect | Success result | Main failures |
|---|---|---|---|---|---|
| POST /vendor/payload-data | Authenticated vendor; 28-byte frame and request ID | Machine identity, body cap, headers, frame validation, payload permission, duplicate and sequence rules | Always stores an authenticated bounded delivery; queues valid unique packet | 202 for new queued work or 200 duplicate | TLS failure, 400, 403, 409, 413, 422, 503 |
| GET /payloads/{message_id}/raw | Operator or administrator; derived packet ID | Bearer identity, read permission, identifier syntax | No mission-data mutation; access MAY be audited | 200 JSON metadata with exact raw bytes represented as lowercase hexadecimal | 401, 403, 404, 503 |
| GET /payloads/{message_id}/processed | Operator or administrator; derived packet ID | Bearer identity and read permission | No mission-data mutation | 200 processed engineering values and classification | 202 while pending, 422 if raw packet was rejected, 401, 403, 404, 503 |
| POST /commands | Operator or administrator; idempotency key and command JSON | Identity, role, schema, allowlist, arguments, duplicate intent | Saves command and transitions before queueing | 202 new command or 200 exact replay | 401, 403, 409, 422, 503 |
| GET /commands/{command_id} | Creating operator or administrator; command UUID | Identity, ownership or administrator role, UUID syntax | No command-state mutation | 200 immutable request, current state, transitions, dispatch attempts, and ACK evidence | 401, 403, 404, 503 |
| GET /audit-events | Administrator only; optional limit and time filters | Administrator identity; limit is 1 through 100 | Read access itself is audited | 200 newest-first audit records | 400, 401, 403, 503 |
| GET /healthz | Local health probe; no credential | Only confirms the ground-api process can answer | None | 200 with status healthy | Connection failure if process is down |
| GET /readyz | Local readiness probe; no credential | Confirms required PostgreSQL and Redis operations succeed | None beyond dependency probe | 200 ready | 503 with non-secret dependency names when work cannot be performed |
| GET /metrics | Administrator or configured local metric scraper | Authorized monitoring identity | None | 200 Prometheus-compatible text measurements | 401, 403, 503 |

Prometheus is a monitoring system that reads numeric measurements in a defined text format. This system needs that format so queue growth, rejection, timeout, and database-error behavior can be measured; it is like a test console collecting counters, and the future implementation belongs in the ground-api metrics module.

## 9. Binary Command Frame version 1

The dispatcher converts the validated command into one binary frame. It sends the frame to:

~~~http
POST https://vendor-simulator:9443/commands
Content-Type: application/octet-stream
X-Request-ID: <UUID version 4 dispatch-attempt identifier>
~~~

The vendor MUST forward the exact command bytes unchanged.

### 9.1 Command packet diagram

~~~text
Offset  0   1   2                              18  19  20  21  22
       +---+---+--------------------------------+---+---+---+---+----------------+
       |ver|typ| command UUID                   |pid|opc|len|res| issued time    |
       |1 B|1 B| 16 B                           |1 B|1 B|1 B|1 B| 8 B            |
       +---+---+--------------------------------+---+---+---+---+----------------+
        30                              38          38+N                42+N
       +--------------------------------+-----------+--------------------+
       | expires time                   | arguments | CRC-32             |
       | 8 B                            | N B       | 4 B                 |
       +--------------------------------+-----------+--------------------+

 Total: 42 bytes with no arguments; 43 bytes for SET_PAYLOAD_MODE.
~~~

### 9.2 Command fields

| Offset | Size | Field | Rule |
|---:|---:|---|---|
| 0 | 1 | protocol_version | MUST equal 1 |
| 1 | 1 | frame_type | MUST equal 1 for command |
| 2 | 16 | command_id | UUID bytes in canonical hexadecimal order, generated by ground |
| 18 | 1 | payload_id | MUST equal 1 for PAYLOAD-01 |
| 19 | 1 | opcode | 0x01, 0x02, or 0x03 |
| 20 | 1 | argument_length | 1 for SET_PAYLOAD_MODE; 0 otherwise |
| 21 | 1 | reserved | MUST equal zero |
| 22 | 8 | issued_time_us | Unsigned microseconds since Unix epoch UTC |
| 30 | 8 | expires_time_us | Unsigned microseconds since Unix epoch UTC and later than issued time |
| 38 | N | arguments | Exact command-specific bytes |
| 38+N | 4 | crc32 | CRC-32/ISO-HDLC over every preceding frame byte |

Default expiry intervals:

| Command | expires_time_us minus issued_time_us |
|---|---:|
| SET_PAYLOAD_MODE | 30 seconds |
| RESET_PAYLOAD | 10 seconds |
| REQUEST_STATUS | 10 seconds |

The payload rejects an expired command before acting. Expiration prevents a delayed queue or vendor path from executing stale operator intent.

UUID wire order is the 32 hexadecimal digits from the canonical UUID string after removing hyphens, from left to right. For example:

~~~text
UUID string: 52f274b5-508a-4f46-9022-05992e7c96a3
Wire bytes:  52 f2 74 b5 50 8a 4f 46 90 22 05 99 2e 7c 96 a3
~~~

Mixed-endian Globally Unique Identifier encodings are prohibited. Implementations must use the canonical UUID byte order shown above.

issued_time_us is captured once when the dispatcher is ready to begin its first vendor attempt. expires_time_us is calculated once from that value. The dispatcher MUST build the frame once, save the exact frame bytes and SHA-256 digest in PostgreSQL, and then persist SENT before transmitting the first application byte. A restart or retry MUST load those saved bytes; it MUST NOT regenerate timestamps or a checksum under the same command ID.

Ground and payload simulator clocks MUST differ by no more than two seconds. Readiness reports failure when the local demonstration detects a larger offset. The payload permits issued_time_us to be at most two seconds in its future and treats expiry as expires_time_us plus the same two-second skew allowance. A larger future-issued offset is CLOCK_SKEW; a time beyond the permitted expiry is COMMAND_EXPIRED.

### 9.3 Golden command vector

Values:

- Command ID: 52f274b5-508a-4f46-9022-05992e7c96a3
- Payload: PAYLOAD-01
- Command: SET_PAYLOAD_MODE
- Argument: SCIENCE
- Issued time: 2026-07-16T12:00:00Z
- Expires time: 30 seconds later
- CRC: 0x360D5332

Continuous 43-byte hexadecimal vector:

~~~text
010152f274b5508a4f46902205992e7c96a301010100000656b92df17000000656b92fbb338002360d5332
~~~

### 9.4 Payload-side command idempotency

- Same command ID and identical frame bytes: payload MUST NOT apply it again and MUST return its cached latest or final acknowledgement.
- Same command ID with different bytes: payload MUST reject it as COMMAND_ID_CONFLICT and MUST NOT apply the new interpretation.
- The command-result ledger for RESET_PAYLOAD MUST survive the simulated reset. An in-memory-only dictionary is insufficient.

## 10. Vendor custody response

The vendor MUST persist the dispatch request ID, command ID, exact frame bytes, SHA-256 digest, and forwarding state before returning success. This local durable custody store survives vendor-simulator restart.

After restart, the vendor resumes forwarding any accepted-but-not-confirmed frame. A crash can cause the same frame to reach the payload again, so the payload's persistent command-ID ledger remains the final duplicate-execution defense.

The vendor command listener caps the request body at 128 bytes.

### 10.1 Response contract

| Result | HTTP status | Command-state effect |
|---|---:|---|
| New structurally valid frame durably accepted | 202 Accepted | Vendor custody evidence is recorded; command remains SENT awaiting payload ACK |
| Same request ID and exact frame retry, or same command ID and exact frame | 200 OK | Existing custody result is returned; vendor forwards the logical command once |
| Missing or malformed Content-Type or X-Request-ID | 400 Bad Request | Vendor guarantees no forwarding; command moves to REJECTED with source VENDOR |
| Trusted certificate has the wrong machine role | 403 Forbidden | Vendor guarantees no forwarding; command moves to REJECTED with source VENDOR |
| Request ID or command ID reused with different bytes | 409 Conflict | Vendor guarantees no forwarding; command moves to REJECTED with source VENDOR |
| Body exceeds 128 bytes | 413 Content Too Large | Vendor guarantees no forwarding; command moves to REJECTED with source VENDOR |
| Invalid version, frame type, length, reserved byte, UUID encoding, or CRC | 422 Unprocessable Content | Vendor saves rejection evidence, guarantees no forwarding, and command moves to REJECTED |
| Vendor durable custody store unavailable | 503 Service Unavailable | Vendor guarantees no custody and no forwarding; command moves to REJECTED with source VENDOR |
| Connection or response is lost | No reliable HTTP result | Delivery is ambiguous; command remains SENT and later becomes TIMED_OUT then UNKNOWN without an authoritative payload result |

HTTP 202 from the vendor means only:

~~~text
The vendor accepted custody of these bytes.
~~~

It does not mean the payload received, accepted, or executed the command. It does not move the command state to RECEIVED.

An explicit vendor response moves SENT to REJECTED only when this contract guarantees no forwarding. A lost response is ambiguous because forwarding may already have occurred.

## 11. Payload Acknowledgement Frame version 1

Acknowledgement is often shortened to ACK. An ACK is evidence about command progress; it is not a request to perform the command.

The payload emits ACK frames. The vendor forwards the exact bytes through:

~~~http
POST /vendor/command-acknowledgements HTTP/1.1
Host: ground-api:8443
Content-Type: application/octet-stream
X-Request-ID: <UUID version 4 ACK-delivery identifier>

<exactly one 44-byte acknowledgement frame>
~~~

The vendor MUST save each payload ACK in a durable callback outbox before attempting delivery. It removes the outbox item only after a 2xx ground response. The outbox and retry metadata survive vendor-simulator restart, and every retry uses the same request ID and exact bytes.

The ground ACK callback uses the same 1,024-byte authenticated body cap as telemetry.

### 11.1 Callback response contract

| Result | HTTP status | Meaning |
|---|---:|---|
| New valid ACK durably saved | 202 Accepted | Ground stores the evidence and commits a legal transition when applicable; valid late, orphan, duplicate-order, or non-resolving evidence may leave current state unchanged |
| Same request ID and exact body retry | 200 OK | Existing delivery receipt and transition result are returned |
| New request ID carrying an exact ACK event duplicate | 200 OK | New duplicate-delivery receipt is saved; no second transition occurs |
| Missing or malformed Content-Type or X-Request-ID | 400 Bad Request | Rejection receipt is saved when possible; unchanged delivery is not blindly retried |
| Trusted certificate has the wrong machine role | 403 Forbidden | No ACK state update occurs |
| Request ID or ACK event identity reused with different bytes | 409 Conflict | Conflict evidence is saved; command state is unchanged |
| Body exceeds the authenticated cap | 413 Content Too Large | Security rejection; command state is unchanged |
| Invalid version, length, frame type, reserved byte, CRC, or command-field correlation | 422 Unprocessable Content | Raw ACK evidence and reason are saved; command state is unchanged |
| PostgreSQL unavailable | 503 Service Unavailable | Ground cannot prove durable receipt; vendor retains and retries the same outbox item |

Any 2xx response confirms durable ACK evidence, so the vendor may remove that item from its active outbox. A network failure or 503 causes automatic retry with the same request ID and bytes. A 4xx response is a permanent result for the unchanged request: the vendor moves it to a failed-callback record, emits an alert, and requires correction or manual requeue rather than retrying forever.

### 11.2 Acknowledgement packet diagram

~~~text
Offset  0   1   2                              18  19  20  21  22
       +---+---+--------------------------------+---+---+---+---+----------+
       |ver|typ| command UUID                   |pid|opc|ACK|res| reason   |
       |1 B|1 B| 16 B                           |1 B|1 B|1 B|1 B| 2 B      |
       +---+---+--------------------------------+---+---+---+---+----------+
        24              28              32                              40      44
       +----------------+---------------+--------------------------------+--------+
       | boot_id        | ACK sequence  | payload event time             | CRC-32 |
       | 4 B            | 4 B           | 8 B                            | 4 B     |
       +----------------+---------------+--------------------------------+--------+

 Total: 44 bytes.
~~~

### 11.3 Acknowledgement fields

| Offset | Size | Field | Rule |
|---:|---:|---|---|
| 0 | 1 | protocol_version | MUST equal 1 |
| 1 | 1 | frame_type | MUST equal 2 for acknowledgement |
| 2 | 16 | command_id | MUST match a saved command before state change |
| 18 | 1 | payload_id | MUST match the command payload |
| 19 | 1 | opcode | MUST match the saved command |
| 20 | 1 | acknowledgement_type | RECEIVED, ACCEPTED, REJECTED, or EXECUTED |
| 21 | 1 | reserved | MUST equal zero |
| 22 | 2 | reason_code | Zero except for REJECTED |
| 24 | 4 | boot_id | Current nonzero payload boot identifier |
| 28 | 4 | acknowledgement_sequence | Counter within boot; it MUST NOT reuse a value |
| 32 | 8 | payload_event_time_us | Unsigned Unix epoch microseconds UTC |
| 40 | 4 | crc32 | CRC-32/ISO-HDLC over bytes 0 through 39 |

The derived acknowledgement event identity is:

~~~text
(payload_id, boot_id, acknowledgement_sequence)
~~~

The ground stores the payload event time and ground receipt time separately. Audit ordering uses ground receipt order; a payload clock is useful evidence but is not the sole ordering authority.

### 11.4 Golden acknowledgement vector

Values:

- Command ID: 52f274b5-508a-4f46-9022-05992e7c96a3
- Payload: PAYLOAD-01
- Opcode: SET_PAYLOAD_MODE
- ACK type: EXECUTED
- Reason: NONE
- Boot identifier: 42
- ACK sequence: 7
- Payload event time: 2026-07-16T12:00:01Z
- CRC: 0xEF1CAE4D

Continuous 44-byte hexadecimal vector:

~~~text
010252f274b5508a4f46902205992e7c96a30101040000000000002a00000007000656b92e00b240ef1cae4d
~~~

### 11.5 Acknowledgement types

| Value | Type | Exact meaning |
|---:|---|---|
| 0x01 | RECEIVED | Payload received a complete frame and verified length, CRC, protocol layout, and command ID; it has not approved or executed it |
| 0x02 | ACCEPTED | Payload validated opcode, arguments, expiry, payload identity, and operating preconditions and committed it for execution; it has not completed |
| 0x03 | REJECTED | Payload explicitly states it will not complete the command; a nonzero reason is required |
| 0x04 | EXECUTED | Payload reports the commanded action completed |

For REQUEST_STATUS, EXECUTED means the payload produced a status telemetry sample. The acknowledgement itself is not that telemetry sample.

### 11.6 Rejection reason codes

| Code | Name | Meaning |
|---:|---|---|
| 0x0000 | NONE | Required for non-rejection ACKs |
| 0x0001 | UNSUPPORTED_PROTOCOL_VERSION | Payload does not implement the command protocol version |
| 0x0002 | WRONG_PAYLOAD_ID | Frame targets another payload |
| 0x0003 | UNKNOWN_OPCODE | Command opcode is not defined |
| 0x0004 | INVALID_ARGUMENT_LENGTH | Argument byte count does not match command |
| 0x0005 | INVALID_ARGUMENT_VALUE | Argument value is outside the command definition |
| 0x0006 | COMMAND_EXPIRED | expires_time_us passed before acceptance |
| 0x0007 | MODE_INHIBIT | Current payload mode prevents command execution |
| 0x0008 | PAYLOAD_BUSY | Payload cannot accept command now |
| 0x0009 | COMMAND_ID_CONFLICT | Existing command ID was reused with different bytes |
| 0x000A | EXECUTION_ABORTED | Payload accepted but could not complete the action |
| 0x000B | CLOCK_SKEW | issued_time_us is too far ahead of the payload clock |

A corrupt command frame receives no ACK because its command ID cannot be trusted. A corrupt ACK is saved as rejected evidence but MUST NOT change command state.

An ACK with an unknown command ID is stored as orphan evidence and raises an audit event. It MUST NOT create a command record.

Duplicate ACK deliveries are idempotent. The same acknowledgement event identity and identical bytes create no second state transition. The same event identity with different valid bytes is ACK_SEQUENCE_CONFLICT evidence and cannot change command state. Out-of-order ACKs are stored without moving current state backward.

ACK sequence state is persistent by payload_id and boot_id. The first valid ACK establishes a high-water mark. A forward distance of one is in order; a larger forward distance records missing ACK sequence values. A zero distance is a duplicate event key. A value behind the high-water mark is preserved as late evidence without moving the mark backward. Invalid ACKs never advance the mark. Before acknowledgement_sequence would move past 0xFFFFFFFF, the payload MUST increment boot_id, reset both telemetry and ACK sequences to zero, and begin a new source session.

## 12. Command state model

### 12.1 Normal path

~~~mermaid
stateDiagram-v2
    [*] --> CREATED
    CREATED --> VALIDATED
    VALIDATED --> QUEUED
    QUEUED --> SENT
    SENT --> RECEIVED
    SENT --> ACCEPTED: intermediate ACK lost
    SENT --> EXECUTED: intermediate ACKs lost
    SENT --> REJECTED
    SENT --> TIMED_OUT
    RECEIVED --> ACCEPTED
    RECEIVED --> EXECUTED: ACCEPTED ACK lost
    RECEIVED --> REJECTED
    RECEIVED --> TIMED_OUT
    ACCEPTED --> EXECUTED
    ACCEPTED --> REJECTED
    ACCEPTED --> TIMED_OUT
    TIMED_OUT --> UNKNOWN
    UNKNOWN --> EXECUTED: late final evidence
    UNKNOWN --> REJECTED: late pre-execution rejection
    EXECUTED --> [*]
    REJECTED --> [*]
~~~

### 12.2 State meanings

| State | Exact meaning |
|---|---|
| CREATED | Ground assigned a command ID and persisted the immutable operator request |
| VALIDATED | Authentication, authorization, schema, allowlist, arguments, and ground mission rules passed |
| QUEUED | Redis confirmed receipt of a reference to the saved command |
| SENT | Application bytes may have crossed the ground-to-vendor boundary |
| RECEIVED | A valid payload RECEIVED acknowledgement arrived |
| ACCEPTED | A valid payload ACCEPTED acknowledgement arrived |
| EXECUTED | A valid payload EXECUTED acknowledgement arrived |
| REJECTED | Vendor or payload explicitly proved the command will not complete; the rejecting actor is recorded |
| TIMED_OUT | A required acknowledgement deadline elapsed; this is a timing observation, not proof of failure |
| UNKNOWN | Ground cannot prove whether the payload acted |

RECEIVED does not mean EXECUTED.

TIMED_OUT does not mean FAILED.

UNKNOWN means the command may have affected the payload even though ground lacks final evidence.

### 12.3 Allowed transitions

| Current state | Allowed next state |
|---|---|
| CREATED | VALIDATED |
| VALIDATED | QUEUED |
| QUEUED | SENT |
| SENT | RECEIVED, ACCEPTED, EXECUTED, REJECTED, TIMED_OUT |
| RECEIVED | ACCEPTED, EXECUTED, REJECTED, TIMED_OUT |
| ACCEPTED | EXECUTED, REJECTED, TIMED_OUT |
| TIMED_OUT | UNKNOWN |
| UNKNOWN | EXECUTED, or REJECTED only when the reason proves execution never began |
| EXECUTED | None |
| REJECTED | None |

Direct SENT to ACCEPTED, SENT to EXECUTED, and RECEIVED to EXECUTED transitions are allowed because intermediate acknowledgements can be lost. The ground MUST NOT invent a missing state.

A late RECEIVED or ACCEPTED ACK while the command is UNKNOWN is saved as evidence but does not resolve whether execution occurred. A final EXECUTED ACK resolves UNKNOWN. A final REJECTED ACK resolves UNKNOWN only for a pre-execution reason such as invalid argument, expiry, mode inhibit, or payload busy.

A REJECTED acknowledgement received after ACCEPTED can describe an aborted execution. It proves that completion failed, but it may not prove that no partial physical effect occurred. If the command is UNKNOWN, EXECUTION_ABORTED evidence leaves its physical-outcome state UNKNOWN. The rejection reason and later telemetry must be investigated.

Every accepted transition MUST append a command-transition record. Current state and transition history MUST update in one PostgreSQL transaction. An illegal or regressive transition creates an audit event and metric instead of silently overwriting state.

## 13. Timeouts, retries, and restart behavior

### 13.1 Acknowledgement deadlines

| Starting state | Expected next evidence | Default deadline |
|---|---|---:|
| SENT | First payload ACK | 5 seconds |
| RECEIVED | ACCEPTED, REJECTED, or EXECUTED | 5 seconds |
| ACCEPTED for SET_PAYLOAD_MODE | EXECUTED or REJECTED | 10 seconds |
| ACCEPTED for RESET_PAYLOAD | EXECUTED or REJECTED | 30 seconds |
| ACCEPTED for REQUEST_STATUS | EXECUTED or REJECTED | 5 seconds |

When a deadline expires, ground MUST:

1. Append a TIMED_OUT transition containing the expected evidence, deadline, last known state, and dispatch-attempt count.
2. Append TIMED_OUT to UNKNOWN.
3. Leave the current state as UNKNOWN.
4. Continue accepting late authoritative final ACKs.

### 13.2 Queue retry versus command retransmission

A queue retry repeats internal bookkeeping. A command retransmission sends action-causing bytes across the vendor boundary. They are not the same risk.

- Database, Redis, certificate-handshake, and connection-setup failures that occur before any application command byte can leave MAY be retried using the same command ID.
- The dispatcher MUST persist SENT before the first application byte can be transmitted.
- Once SENT, SET_PAYLOAD_MODE and RESET_PAYLOAD MUST NOT be automatically retransmitted.
- REQUEST_STATUS MAY be retransmitted at most twice, after one second and then two seconds, using the exact same command ID and bytes and before expiry.
- A restarted dispatcher seeing QUEUED MAY continue dispatch.
- A restarted dispatcher seeing SENT MUST NOT retransmit an effectful command; it resumes acknowledgement monitoring.
- Duplicate Redis command jobs MUST be harmless. Only one atomic QUEUED-to-SENT claim may win.
- ACK callback delivery MAY be retried because an ACK reports evidence rather than causes payload action.

For an unknown reset, the recovery action is to inspect telemetry and issue REQUEST_STATUS. Software MUST NOT silently create another RESET_PAYLOAD command.

### 13.3 RESET_PAYLOAD behavior

The payload simulator performs RESET_PAYLOAD in this exact order:

1. Validate the command and persist its command ID, exact frame digest, and ACCEPTED result in a ledger outside resettable runtime memory.
2. Persist and emit RECEIVED and ACCEPTED ACK frames.
3. Perform the simulated reset.
4. Increment the persistent boot_id and reset telemetry and ACK sequence counters to zero.
5. Restore the persistent command ledger.
6. Persist the final EXECUTED result and exact EXECUTED ACK bytes before attempting ACK delivery.
7. Emit the EXECUTED ACK using the new boot_id and ACK sequence zero.
8. Resume telemetry using the new boot_id and telemetry sequence zero.

A duplicate RESET_PAYLOAD frame with the same command ID and bytes returns the persisted latest or final ACK and never performs another reset. If the final ACK is lost, the new telemetry boot_id is useful diagnostic evidence, but ground command state remains UNKNOWN until an authoritative final ACK is stored.

## 14. Raw evidence requirements

For every authenticated telemetry or acknowledgement delivery within the size cap, ground MUST preserve:

- Exact original request-body bytes.
- Ground receipt timestamp.
- Peer certificate identity.
- Presented X-Request-ID when valid; the field is nullable for a missing header.
- Server-generated receipt ID.
- Calculated SHA-256 body digest.
- Validation status.
- Rejection reason, when applicable.
- Parsed identifiers only after safe parsing.

The exact bytes are evidence. Parsed values are an interpretation. Neither replaces the other.

Invalid authenticated telemetry and acknowledgements are stored with a rejection reason and never enter normal processing or state updates.

An unauthenticated connection that fails mTLS does not reach application storage. The TLS failure log is the evidence.

## 15. Normative requirements and planned verification

The detailed Verification Plan will be created in Phase 6. These identifiers establish traceability now.

| Requirement | Required behavior | Future verification | Expected evidence |
|---|---|---|---|
| ICD-TLM-001 | Telemetry version 1 is exactly 28 bytes with the offsets in Section 6 | Golden-vector unit test | Exact hexadecimal equality |
| ICD-TLM-002 | All multibyte fields use big-endian byte order | Encode/decode boundary tests | Known integers at expected offsets |
| ICD-TLM-003 | CRC-32/ISO-HDLC covers bytes 0 through 23 | Corrupt-one-byte test | 422 response, rejected receipt, no queue job |
| ICD-TLM-004 | Unsupported protocol versions are rejected | Version 2 packet with recomputed CRC | UNSUPPORTED_PROTOCOL_VERSION evidence |
| ICD-TLM-005 | Raw authenticated bytes are saved before decoding | Database integration test | Exact byte-for-byte database comparison |
| ICD-TLM-006 | Exact duplicates create no second processing job | Duplicate delivery integration test | One processed row and duplicate receipt |
| ICD-TLM-007 | Sequence gaps, session rollover, late frames, and conflicts follow Section 7 | Sequence unit and database restart tests | Gap count and persistent high-water evidence |
| ICD-TLM-008 | Invalid packets never enter processing | Queue integration test | Rejection row and absent queue job |
| ICD-PROC-001 | Health classification uses the exact inclusive limits in Section 6.8 | Boundary-value unit tests | NOMINAL, WARNING, and CRITICAL results at every boundary |
| ICD-CMD-001 | Only the three commands in Section 8 are accepted | Command validation tests | Allowed and rejected API responses |
| ICD-CMD-002 | Command permission follows the role table | Authentication and authorization tests | Vendor 403; operator reset 403; administrator reset accepted |
| ICD-CMD-003 | A command is stored before queueing | PostgreSQL/Redis failure test | VALIDATED record remains when Redis is unavailable |
| ICD-CMD-004 | Operator idempotency returns one command for one intent | Duplicate submission tests | Same ID and one queue job |
| ICD-CMD-005 | Command frame fields and CRC follow Section 9 | Golden-vector and corruption tests | Exact bytes and payload rejection of corruption |
| ICD-CMD-006 | Payload duplicate command protection survives simulated reset | Reset and replay test | Cached ACK and one application of effect |
| ICD-CMD-007 | Exact command bytes and timestamps are saved once before SENT | Dispatcher restart test | Byte-identical frame after restart |
| ICD-VND-001 | Vendor returns 202 only after durable custody storage | Vendor restart test | Accepted pending command forwards after restart |
| ICD-VND-002 | Vendor command responses have the exact no-forward and ambiguity effects in Section 10 | HTTP contract tests | Status response and matching command transition |
| ICD-ACK-001 | ACK frame is exactly 44 bytes and matches saved command fields | ACK codec and correlation tests | Accepted valid ACK; rejected mismatch |
| ICD-ACK-002 | RECEIVED, ACCEPTED, and EXECUTED remain distinct | End-to-end transition test | Append-only ordered transition rows |
| ICD-ACK-003 | Invalid, duplicate, late, and orphan ACKs cannot regress state | State-machine tests | Evidence stored and state unchanged when required |
| ICD-ACK-004 | Vendor retains ACK outbox items until a ground 2xx response | Ground database outage and vendor restart test | Same ACK delivered after recovery |
| ICD-SAFE-001 | Ambiguous acknowledgement timeout becomes TIMED_OUT then UNKNOWN | Lost-ACK demonstration | Current UNKNOWN plus both transition rows |
| ICD-SAFE-002 | Effectful commands are not automatically resent after SENT | Dispatcher restart test | One vendor delivery attempt |
| ICD-SAFE-003 | Late EXECUTION_ABORTED evidence cannot falsely resolve possible partial effects | Late-ACK state test | Current UNKNOWN plus stored abort evidence |
| ICD-SEC-001 | Vendor and dispatcher machine links require correct mTLS identities | Certificate tests | Valid connection and invalid-certificate handshake failure |
| ICD-SEC-002 | Vendor identity cannot exercise operator permission | Authorization demonstration | 403 Forbidden and audit event |
| ICD-NET-001 | PostgreSQL and Redis are unreachable from the vendor network | Docker network test | Failed connection from vendor container |
| ICD-API-001 | Ground endpoints enforce the contracts in Section 8.5 | API contract tests | Expected status, body, authorization, and storage evidence |
| ICD-TIME-001 | Ground and payload clocks remain within the two-second command allowance | Readiness and expiry tests | Ready within limit; CLOCK_SKEW outside limit |

## 16. Controlled examples

### 16.1 Corrupted telemetry

Action: change one temperature byte without changing the CRC.

Expected result: ground saves the authenticated raw bytes, rejects them for CRC mismatch, increments the rejected-packet measurement, and creates no processing job.

### 16.2 Unsupported telemetry version

Action: set protocol_version to 2 and recompute the CRC.

Expected result: ground rejects only for unsupported version, proving the version rule was isolated.

### 16.3 Sequence gap

Action: send sequence 10 followed by sequence 13 within the same boot.

Expected result: sequence 13 is accepted and two missing sequence values are recorded.

### 16.4 Exact duplicate

Action: resend the exact bytes for the same payload, boot, and sequence.

Expected result: the existing result is returned and no second processing job is created.

### 16.5 Lost command acknowledgement

Action: allow RESET_PAYLOAD to reach the payload and drop its ACK callbacks.

Expected result: command history records TIMED_OUT then UNKNOWN. The dispatcher does not resend the reset.

### 16.6 Late final acknowledgement

Action: deliver a valid EXECUTED ACK after the command became UNKNOWN.

Expected result: the ACK is stored, UNKNOWN moves to EXECUTED, and the complete uncertainty interval remains in audit history.

## 17. Future implementation map

| Contract area | Existing foundation to reuse | Required later change |
|---|---|---|
| Binary serialization | mission_ground/common/packet.py | Replace legacy packet with the version 1 layouts in this ICD |
| CRC tests | tests/test_packet.py | Add golden vectors, exact offsets, version isolation, and ACK frame |
| Sequence tracking | mission_ground/common/sequence.py | Persist by payload and boot; distinguish duplicate, gap, late, conflict, and session rollover |
| Command allowlist | mission_ground/common/commands.py | Replace old command names and add structured arguments and role rules |
| Save before dispatch | mission_ground/services/mission_api.py | Use PostgreSQL transaction plus Redis recovery and idempotency |
| Payload duplicate cache | mission_ground/services/spacecraft_sim.py | Persist the ledger across simulated reset |
| Command state | Current commands.status text | Add enforced current state and append-only transition table |
| Transport | Current UDP sockets | Add HTTPS and mTLS at the vendor interfaces in Phase 5 |

## 18. Change control

Any change to field size, offset, signedness, scale, byte order, checksum, time basis, command opcode, acknowledgement meaning, or state meaning requires:

1. A document version change.
2. An updated golden vector.
3. Updated requirement traceability.
4. Compatibility behavior for the earlier version.
5. Tests written or updated before interface implementation is considered complete.

Protocol version 1 is project-specific. The project MUST NOT claim full CCSDS compliance or production flight readiness.
