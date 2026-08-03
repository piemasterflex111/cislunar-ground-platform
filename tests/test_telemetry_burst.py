"""Failures: burst type IDs, struct sizes, and cache stale behavior.

Covers:

* ``send()`` raises on zero-length payload
* ``receive()`` validates ``total_length`` against actual payload
* ``receive()`` tests at least 40 bytes minimum
* ``get_type_registry()`` returns correct dict structure
* ``send_telemetry_burst()`` raises when type_id not in registry
* ``send_telemetry_burst()`` raises when struct size does not match actual
* ``CacheWrapper`` stale cycle does not lose data
"""

from __future__ import annotations

import struct

import pytest

# ---------------------------------------------------------------------------
# Helpers — build full wire frames (>= 40 bytes) for the receive() tests.
# ---------------------------------------------------------------------------

_HEADER_FMT = ">IHHIQB"  # total_length, record_length, channel, p len, timestamp, status


def _build_test_frame(
    payload: bytes,
    record_length: int | None = None,
    channel: int = 0,
    timestamp: int = 1660000000000000000,
    status: int = 0x00,
) -> bytes:
    """Build a wire frame where record_length may differ from actual payload."""
    rec = record_length if record_length is not None else len(payload)

    header_bytes = struct.pack(
        _HEADER_FMT,
        len(payload) + 20,  # total_length
        rec,                # record_length
        channel,
        0xDEAD0000,         # checksum placeholder
        timestamp,
        status,
    )

    frame = header_bytes + payload

    # Pad to at least 40 bytes.
    if len(frame) < 40:
        frame += b'\x00' * (40 - len(frame))

    return frame


# ---------------------------------------------------------------------------
# CacheWrapper tests (stale / by-id / by-name)
# ---------------------------------------------------------------------------

class TestCacheWrapperStaleCycle:
    """CacheWrapper.n must return the same dict across multiple stale calls.

    Also verify that stale does NOT empty the data, otherwise callers
    downstream may miss config data between parses and reads.
    """

    def test_stale_does_not_clear_data(self) -> None:
        from mission_ground.telemetry.burst import CacheWrapper

        data = {"SC_BODY_TEMPERATURE": "original-data"}
        wrapper = CacheWrapper(data=data)

        stale_val = wrapper.stale()
        assert stale_val is not None
        assert len(stale_val) > 0

        # Call stale again - data must still be present (not cleared).
        stale_val2 = wrapper.stale()
        assert len(stale_val2) > 0
        assert "SC_BODY_TEMPERATURE" in stale_val2

    def test_stale_returns_same_dict_identity(self) -> None:
        from mission_ground.telemetry.burst import CacheWrapper

        data = {"SC_BODY_TEMPERATURE": "original-data"}
        wrapper = CacheWrapper(data=data)

        first = wrapper.stale()
        second = wrapper.stale()

        # Must be same object (by-name contract).
        assert first is second

    def test_stale_returns_mutable_ref(self) -> None:
        """Stale should return the actual internal reference, so
        caller mutations are visible on next call."""
        from mission_ground.telemetry.burst import CacheWrapper

        data = {"SC_BODY_TEMPERATURE": "original-data"}
        wrapper = CacheWrapper(data=data)

        snapshot = wrapper.stale()
        snapshot["added_after_stale"] = "value"

        snapshot2 = wrapper.stale()
        assert snapshot2["added_after_stale"] == "value"


# ---------------------------------------------------------------------------
# send() - zero-payload rejection
# ---------------------------------------------------------------------------

class TestSendZeroPayload:
    """``send()`` must raise on zero-length payloads."""

    def test_send_rejects_zero_length_bytes(self) -> None:
        from mission_ground.telemetry.burst import send

        # Zero-length (empty bytes) must raise:
        with pytest.raises(ValueError, match="empty"):
            send(type_id="SC_BODY_TEMPERATURE", payload=b"")

    def test_send_rejects_none_payload(self) -> None:
        from mission_ground.telemetry.burst import send

        with pytest.raises(ValueError, match="empty"):
            send(type_id="SC_BODY_TEMPERATURE", payload=None)


# ---------------------------------------------------------------------------
# get_type_registry() - must return dict[str, dict]
# ---------------------------------------------------------------------------

class TestGetTypeRegistry:
    """``get_type_registry()`` returns the correct structure."""

    def test_returns_dict_of_dicts(self) -> None:
        from mission_ground.telemetry.burst import get_type_registry

        result = get_type_registry()
        assert isinstance(result, dict)
        assert len(result) > 0
        for key, value in result.items():
            assert isinstance(key, str)
            assert "type_id" in value
            assert "fmt" in value
            assert "bytes" in value

    def test_registry_has_required_type(self) -> None:
        """The burst module must include the bus type name for SC_BODY_TEMPERATURE."""
        from mission_ground.telemetry.burst import get_type_registry

        registry = get_type_registry()
        assert "SC_BODY_TEMPERATURE" in registry


# ---------------------------------------------------------------------------
# receive() - validates total_length against actual payload
# ---------------------------------------------------------------------------

class TestReceivePayloadLengthValidation:
    """``receive()`` validates ``total_length`` against actual payload length."""

    def test_receive_rejects_mismatched_total_length(self) -> None:
        """When record_length in header does not match actual payload,
        receive() must reject the frame."""
        payload = b"\x0f\x0f\x0f\x0f\x0f\x0f"  # 6 bytes

        # Build frame with corrupted record_length
        frame_bytes = _build_test_frame(
            payload, record_length=len(payload) + 100
        )

        from mission_ground.telemetry.burst import receive

        with pytest.raises(ValueError, match="length"):
            receive(frame_bytes)  # MUST raise on mismatched total_length


class TestReceiveMinimumFrameSize:
    """``receive()`` must reject frames < 40 bytes (minimum frame size)."""

    def test_rejects_less_than_40_bytes(self) -> None:
        from mission_ground.telemetry.burst import receive

        short_frame = b"\x00" * 39  # 39 bytes, just under minimum

        with pytest.raises(ValueError, match="40"):
            receive(short_frame)

    def test_rejects_empty_frame(self) -> None:
        from mission_ground.telemetry.burst import receive

        with pytest.raises(ValueError, match="empty"):
            receive(b"")


# ---------------------------------------------------------------------------
# send_telemetry_burst - type_id not in registry
# ---------------------------------------------------------------------------

class TestSendTelemetryBurstNotDefined:
    """``send_telemetry_burst()`` must reject a type_id not in registry."""

    def test_raises_on_unknown_type(self) -> None:
        from mission_ground.telemetry.burst import send_telemetry_burst

        bad_type_id = "__NONEXISTENT_TYPE_NAME__"
        payloads = [b"\x0f\x0f", b"\x00\xff"]

        with pytest.raises(ValueError, match="in registry"):
            send_telemetry_burst(type_id=bad_type_id, payloads=payloads)


# ---------------------------------------------------------------------------
# send_telemetry_burst - struct size mismatch between declared and actual
# ---------------------------------------------------------------------------

class TestSendTelemetryBurstStructSizeMismatch:
    """``send_telemetry_burst()`` must reject type whose struct size doesn't
    match actual packed bytes from fmt.

    The burst module is expected to validate this internally and reject.

    Note: SC_BODY_TEMPERATURE has fmt ">HhhhI" (12 bytes) with declared 14.
    So any correctly-formatted payload will fail the 12-line14 check.

    The burst layer is the one that does the struct packing check.
    """

    def test_raises_when_struct_size_mismatch(self) -> None:
        """When struct size doesn't match declared field size, raise."""
        from mission_ground.telemetry.burst import send_telemetry_burst

        type_id = "SC_BODY_TEMPERATURE"
        # This payload WILL pack to 12 bytes but declared size is 14 - mismatch
        payloads = [(1, 2, 3, 4, 5)]  # packs as 12 via struct, not 14

        with pytest.raises(ValueError, match="struct"):
            send_telemetry_burst(type_id=type_id, payloads=payloads)