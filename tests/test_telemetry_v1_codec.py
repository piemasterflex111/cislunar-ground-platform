from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mission_ground.telemetry.codec import (
    CRC32_BIG_ENDIAN,
    TELEMETRY_V1_BODY_SIZE,
    FaultFlag,
    OperatingMode,
    TelemetryFrame,
    TelemetryRejectionReason,
    TelemetryValidationError,
    crc32_iso_hdlc,
    decode_telemetry,
    encode_telemetry,
)

GOLDEN_RECEIPT_TIME = datetime(2026, 7, 16, 12, 0, 1, tzinfo=UTC)
GOLDEN_HEX = "01010000002a00000001000656b92df170000b186dc40200cc6d0205"


def golden_frame(**changes: object) -> TelemetryFrame:
    values: dict[str, object] = {
        "payload_id": 1,
        "boot_id": 42,
        "sequence_number": 1,
        "timestamp_us": 1_784_203_200_000_000,
        "temperature_centi_c": 2_840,
        "voltage_mv": 28_100,
        "operating_mode": OperatingMode.SCIENCE,
        "fault_flags": FaultFlag.NONE,
    }
    values.update(changes)
    return TelemetryFrame(**values)  # type: ignore[arg-type]


def replace_crc(raw: bytes) -> bytes:
    body = raw[:TELEMETRY_V1_BODY_SIZE]
    return body + CRC32_BIG_ENDIAN.pack(crc32_iso_hdlc(body))


def assert_rejected(
    raw: bytes,
    reason: TelemetryRejectionReason,
    *,
    received_at: datetime = GOLDEN_RECEIPT_TIME,
) -> None:
    with pytest.raises(TelemetryValidationError) as raised:
        decode_telemetry(raw, ground_received_at=received_at)
    assert raised.value.reason is reason


def test_crc_profile_standard_check() -> None:
    assert crc32_iso_hdlc(b"123456789") == 0xCBF43926


def test_golden_vector_encodes_exactly_and_decodes_all_fields() -> None:
    encoded = encode_telemetry(golden_frame())

    assert encoded.hex() == GOLDEN_HEX
    assert len(encoded) == 28
    decoded = decode_telemetry(encoded, ground_received_at=GOLDEN_RECEIPT_TIME)
    assert decoded == golden_frame()
    assert decoded.packet_id == "TLM-P01-B0000002A-S00000001"
    assert decoded.temperature_c == 28.4
    assert decoded.voltage_v == 28.1
    assert decoded.sample_time == datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_multibyte_fields_use_big_endian_offsets() -> None:
    encoded = encode_telemetry(golden_frame())

    assert encoded[2:6] == bytes.fromhex("0000002a")
    assert encoded[6:10] == bytes.fromhex("00000001")
    assert encoded[10:18] == bytes.fromhex("000656b92df17000")
    assert encoded[18:20] == bytes.fromhex("0b18")
    assert encoded[20:22] == bytes.fromhex("6dc4")
    assert encoded[24:28] == bytes.fromhex("cc6d0205")


def test_changed_body_byte_without_new_crc_is_rejected() -> None:
    corrupted = bytearray(bytes.fromhex(GOLDEN_HEX))
    corrupted[18] ^= 0x01

    assert_rejected(bytes(corrupted), TelemetryRejectionReason.CRC_MISMATCH)


def test_version_two_with_recomputed_crc_is_rejected_only_for_version() -> None:
    unsupported = bytearray(bytes.fromhex(GOLDEN_HEX))
    unsupported[0] = 2

    assert_rejected(
        replace_crc(bytes(unsupported)),
        TelemetryRejectionReason.UNSUPPORTED_PROTOCOL_VERSION,
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"", TelemetryRejectionReason.EMPTY_BODY),
        (bytes.fromhex(GOLDEN_HEX)[:-1], TelemetryRejectionReason.INVALID_FRAME_LENGTH),
        (bytes.fromhex(GOLDEN_HEX) + b"\x00", TelemetryRejectionReason.INVALID_FRAME_LENGTH),
    ],
)
def test_empty_and_wrong_length_frames_are_rejected(
    raw: bytes,
    reason: TelemetryRejectionReason,
) -> None:
    assert_rejected(raw, reason)


@pytest.mark.parametrize(
    ("offset", "replacement", "reason"),
    [
        (1, b"\x02", TelemetryRejectionReason.UNSUPPORTED_PAYLOAD_ID),
        (2, b"\x00\x00\x00\x00", TelemetryRejectionReason.INVALID_BOOT_ID),
        (22, b"\x04", TelemetryRejectionReason.INVALID_OPERATING_MODE),
        (23, b"\x40", TelemetryRejectionReason.RESERVED_FAULT_BITS_SET),
    ],
)
def test_invalid_fields_with_valid_crc_have_specific_reasons(
    offset: int,
    replacement: bytes,
    reason: TelemetryRejectionReason,
) -> None:
    changed = bytearray(bytes.fromhex(GOLDEN_HEX))
    changed[offset : offset + len(replacement)] = replacement

    assert_rejected(replace_crc(bytes(changed)), reason)


@pytest.mark.parametrize("temperature", [-10_000, 15_000])
def test_temperature_interface_boundaries_are_valid(temperature: int) -> None:
    raw = encode_telemetry(golden_frame(temperature_centi_c=temperature))
    decoded = decode_telemetry(raw, ground_received_at=GOLDEN_RECEIPT_TIME)
    assert decoded.temperature_centi_c == temperature


@pytest.mark.parametrize("temperature", [-10_001, 15_001])
def test_temperature_outside_interface_boundaries_is_rejected(temperature: int) -> None:
    with pytest.raises(TelemetryValidationError) as raised:
        encode_telemetry(golden_frame(temperature_centi_c=temperature))
    assert raised.value.reason is TelemetryRejectionReason.TEMPERATURE_OUT_OF_RANGE


@pytest.mark.parametrize("voltage", [0, 50_000])
def test_voltage_interface_boundaries_are_valid(voltage: int) -> None:
    raw = encode_telemetry(golden_frame(voltage_mv=voltage))
    assert decode_telemetry(raw, ground_received_at=GOLDEN_RECEIPT_TIME).voltage_mv == voltage


def test_timestamp_before_project_epoch_is_rejected() -> None:
    with pytest.raises(TelemetryValidationError) as raised:
        encode_telemetry(golden_frame(timestamp_us=1_767_225_599_999_999))
    assert raised.value.reason is TelemetryRejectionReason.TIMESTAMP_BEFORE_PROJECT_EPOCH


def test_timestamp_exactly_five_minutes_ahead_is_valid_but_one_microsecond_more_is_not() -> None:
    receipt = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    receipt_us = 1_784_203_200_000_000
    allowed = encode_telemetry(golden_frame(timestamp_us=receipt_us + 300_000_000))
    decode_telemetry(allowed, ground_received_at=receipt)

    too_far = encode_telemetry(golden_frame(timestamp_us=receipt_us + 300_000_001))
    assert_rejected(
        too_far,
        TelemetryRejectionReason.TIMESTAMP_TOO_FAR_IN_FUTURE,
        received_at=receipt,
    )


def test_ground_receipt_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        decode_telemetry(
            bytes.fromhex(GOLDEN_HEX),
            ground_received_at=datetime(2026, 7, 16, 12, 0) + timedelta(seconds=1),
        )
