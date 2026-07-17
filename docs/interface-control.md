# Interface Control Document

## Telemetry UDP interface

Default endpoint: `telemetry-ingest:5005/udp`.

### Primary header

The six-byte primary header is CCSDS-inspired:

| Field | Bits | Meaning |
|---|---:|---|
| Version | 3 | Must be zero |
| Type | 1 | `0` telemetry, `1` command packet |
| Secondary header flag | 1 | Set |
| APID | 11 | Application identifier |
| Sequence flags | 2 | `11`, unsegmented |
| Sequence count | 14 | Wraps after 16383 |
| Data length | 16 | Data-field byte count minus one |

A four-byte CRC32 covers the primary header and application payload.

### APID 100: health telemetry

Network byte order: `!QHhiBB`

| Value | Encoding |
|---|---|
| Timestamp | unsigned 64-bit nanoseconds |
| Bus voltage | unsigned 16-bit millivolts |
| Battery temperature | signed 16-bit centi-degrees C |
| Reaction wheel speed | signed 32-bit RPM |
| Mode | unsigned 8-bit |
| Fault flags | unsigned 8-bit bitmask |

### APID 101: command acknowledgement

Network byte order: `!IBiQ`: command ID, status, applied value, timestamp nanoseconds.

## Command UDP interface

Default endpoint: `spacecraft-sim:5006/udp`.

Command payload format: `!IBiQ` followed by CRC32: command ID, opcode, argument, issued time.

| Command | Opcode | Valid argument |
|---|---:|---:|
| SET_MODE | 1 | 0 through 3 |
| SET_WHEEL_RPM | 2 | -6000 through 6000 |
| RESET_FAULTS | 3 | 0 |

## Message subjects

- `telemetry.health`
- `telemetry.command_ack`
- `telemetry.processed`
- `alerts.limit`
- `commands.dispatch`
- `commands.status`
- `advisories.alert`
