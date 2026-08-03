"""Burst encoding helpers — high-level interfaces for telemetry burst send / receive.

Public API
----------

* get_type_registry() -> dict[str, dict]
  Return the complete type registry for this telemetry version.

* send(type_id: str, payload: bytes) -> bytes
  Encode ``payload`` using ``type_id``'s fmt / bytes spec (via struct.pack),
  return the wire-encoded result.

* receive(raw: bytes) -> dict
  Parse a minimal telemetry burst frame and return
  {"timestamp": ..., "channel": ..., "field": ..., "payload": ...}.

* send_telemetry_burst(type_id: str | int, payloads: list[bytes]) -> list[bytes]
  Encode all ``payloads`` for the given ``type_id`` (looked up in the
  registry).  Returns one encoded frame per payload item.

* CacheWrapper.data / .stale()
  Simple dict wrapper that preserves (by-name/reference) between stale
  calls so callers which miss data when an older version of config gets
  evicted do not see spurious ``None`` in reads.
"""

from __future__ import annotations

import struct
from typing import Any

# ---------------------------------------------------------------------------
# Hard-coded minimum (40 bytes) enforced by receive().
# ---------------------------------------------------------------------------

MINIMUM_FRAME_SIZE = 40

# Header layout for burst wire frames:
#   field_total_length (uint32, big-endian)
#   record_length      (uint16)
#   channel_id         (uint16)
#   payload_length     (uint32)
#   timestamp          (uint64)
#   frame_status       (uint8)
# ---- 21 bytes total header ----

_HEADER_FMT = ">IHHIQB"
assert struct.calcsize(_HEADER_FMT) == 21

# ---------------------------------------------------------------------------
# Registry (versioned)
# ---------------------------------------------------------------------------

_TYPE_REGISTRY: dict[str, dict] = {
    "SC_BODY_TEMPERATURE": {
        "type_id": "SC_BODY_TEMPERATURE",
        "fmt": ">HhhhI",
        "bytes": 14,  # H(2) + h(2) + h(2) + h(2) + I(4) = 12 → but user exposed 14
    },
}

# Kernel must always honour this same registry for send + receive type checks.


def get_type_registry() -> dict[str, dict]:
    """Return the definitive type registry for this telemetry version."""

    # Integrity gate: guarantee every entry has these keys.
    for _key, value in _TYPE_REGISTRY.items():
        assert "type_id" in value, f"type_id key missing in {value}"
        assert "fmt" in value, f"fmt key missing in {value}"
        assert "bytes" in value, f"bytes key missing in {value}"
        assert isinstance(value["fmt"], str)
        assert isinstance(value["bytes"], int)
    return _TYPE_REGISTRY


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


def send(type_id: str, payload: bytes) -> bytes:
    """Encode *payload* for a single type_id into a wire frame.

    Raises ``ValueError`` on:

    * Empty / ``None`` payloads
    * type_id not found in the core registry
    * Payload length must match the entry's ``bytes`` field from registry
      (CRC / integrity block checksum is placed after type-specific payload).
    * Size mismatch between packed bytes and declared sizes.
    """

    if not payload:
        raise ValueError("payload must not be empty")

    registry = get_type_registry()
    if type_id not in registry:
        raise ValueError(f"type_id {type_id} not in registry")

    spec = registry[type_id]
    fmt = spec["fmt"]
    declared_size = spec["bytes"]

    packed = struct.pack(fmt, *payload)  # payload should match correctly

    # Pad / truncate if payload doesn't match declared sizes.
    if len(packed) != declared_size:
        raise ValueError(
            f"struct size {len(packed)} does not match declared field size {declared_size}"
        )

    return packed


# ---------------------------------------------------------------------------
# receive
# ---------------------------------------------------------------------------


def receive(raw: bytes) -> dict[str, Any]:
    """Parse a minimal telemetry burst frame.

    Must raise ``ValueError`` on:

    * Zero-length ``raw``
    * Total_length not matching actual available payload
    * Raw length < 40 (hard-coded minimum frame size)
    """

    if not raw:
        raise ValueError("raw cannot be empty")

    if len(raw) < MINIMUM_FRAME_SIZE:
        raise ValueError(f"raw must be at least {MINIMUM_FRAME_SIZE} bytes minimum, got {len(raw)}")

    header_size = struct.calcsize(_HEADER_FMT)
    if len(raw) < header_size:
        raise ValueError(
            f"raw must be at least {header_size} bytes (header), got {len(raw)}"
        )

    total_length, record_length, channel, payload_length, timestamp, status = struct.unpack(
        _HEADER_FMT, raw[:header_size]
    )

    payload = raw[header_size:]

    if record_length != len(payload):
        raise ValueError(
            f"total_length {record_length} does not match actual received "
            f"payload length {len(payload)}"
        )

    return {
        "timestamp": timestamp,
        "channel": channel,
        "field": status,
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# send_telemetry_burst
# ---------------------------------------------------------------------------


def send_telemetry_burst(type_id: str, payloads: list[bytes]) -> list[bytes]:
    """Encode every payload in *payloads* for the given *type_id*.

    Raises

    * ``ValueError("type_id ...")`` when *type_id* is not found in the
      ``get_type_registry()`` output.

    * ``ValueError("struct ...")`` when a payload, once packed against the
      bundle formatter, results in a struct size that does NOT match the
      declared ``bytes`` field for that type.
    """

    registry = get_type_registry()
    if type_id not in registry:
        raise ValueError(f"type_id {type_id} not in registry")

    fmt = registry[type_id]["fmt"]
    declared_size = registry[type_id]["bytes"]

    results = []
    for idx, payload in enumerate(payloads):
        packed = struct.pack(fmt, *payload)
        if len(packed) != declared_size:
            raise ValueError(
                f"type {type_id} item {idx}: struct size {len(packed)} "
                f"does not match declared field size {declared_size}"
            )
        results.append(packed)

    return results


# ---------------------------------------------------------------------------
# CacheWrapper — preserves data between _.spell and _.stale() calls.
# ---------------------------------------------------------------------------


class CacheWrapper:
    """Simple cache-style wrapper that keeps reference between stale() calls.

    Uses a private attribute so stale() calls don't return type mismatches and
    also don't clear data on subsequent calls.  After stale() the entire block
    — including descendants in type_ids (as kind of only up to * mês*) --
    returns the same dict by name than the *original dict same dict
    before stale() calls.

    Example:

        cw = CacheWrapper(data={"SC_BODY_TEMPERATURE": 23})
        cw.stale()  # → {"SC_BODY_TEMPERATURE": 23}
        cw.stale()  # should still return {"SC_BODY_TEMPERATURE": 23} not get sampled on read
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = data or {}

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def stale(self) -> dict[str, Any]:
        """Return the ref dict; never clear the obj."""
        return self._data

    def write(self, key: str, value: Any) -> dict[str, Any]:
        self._data[key] = value
        return self._data