"""Binary telemetry frame version 1 from the Interface Control Document.

The codec deliberately works in exact wire units: microseconds, hundredths of
a degree Celsius, and millivolts.  This prevents floating-point rounding from
changing a packet at an interface boundary.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, IntEnum, IntFlag

PROTOCOL_VERSION = 1
SUPPORTED_PAYLOAD_ID = 1
TELEMETRY_V1_BODY = struct.Struct(">BBIIQhHBB")
CRC32_BIG_ENDIAN = struct.Struct(">I")
TELEMETRY_V1_BODY_SIZE = TELEMETRY_V1_BODY.size
TELEMETRY_V1_FRAME_SIZE = TELEMETRY_V1_BODY_SIZE + CRC32_BIG_ENDIAN.size

UINT32_MAX = (1 << 32) - 1
PROJECT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
PROJECT_EPOCH_US = int(PROJECT_EPOCH.timestamp() * 1_000_000)
MAX_FUTURE_SKEW_US = int(timedelta(minutes=5).total_seconds() * 1_000_000)
MIN_TEMPERATURE_CENTI_C = -10_000
MAX_TEMPERATURE_CENTI_C = 15_000
MIN_VOLTAGE_MV = 0
MAX_VOLTAGE_MV = 50_000


class OperatingMode(IntEnum):
    """Payload operating modes defined for telemetry protocol version 1."""

    SAFE = 0
    STANDBY = 1
    SCIENCE = 2
    CALIBRATION = 3


class FaultFlag(IntFlag):
    """Defined payload fault bits; multiple flags may be combined."""

    NONE = 0
    OVER_TEMPERATURE = 0x01
    UNDER_VOLTAGE = 0x02
    OVER_VOLTAGE = 0x04
    TEMPERATURE_SENSOR_FAULT = 0x08
    VOLTAGE_SENSOR_FAULT = 0x10
    INTERNAL_PAYLOAD_FAULT = 0x20


DEFINED_FAULT_MASK = int(
    FaultFlag.OVER_TEMPERATURE
    | FaultFlag.UNDER_VOLTAGE
    | FaultFlag.OVER_VOLTAGE
    | FaultFlag.TEMPERATURE_SENSOR_FAULT
    | FaultFlag.VOLTAGE_SENSOR_FAULT
    | FaultFlag.INTERNAL_PAYLOAD_FAULT
)
RESERVED_FAULT_MASK = 0xC0


class TelemetryRejectionReason(str, Enum):
    """Stable reasons that the receiver can persist with rejected evidence."""

    EMPTY_BODY = "EMPTY_BODY"
    UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION"
    INVALID_FRAME_LENGTH = "INVALID_FRAME_LENGTH"
    CRC_MISMATCH = "CRC_MISMATCH"
    UNSUPPORTED_PAYLOAD_ID = "UNSUPPORTED_PAYLOAD_ID"
    INVALID_BOOT_ID = "INVALID_BOOT_ID"
    TIMESTAMP_BEFORE_PROJECT_EPOCH = "TIMESTAMP_BEFORE_PROJECT_EPOCH"
    TIMESTAMP_TOO_FAR_IN_FUTURE = "TIMESTAMP_TOO_FAR_IN_FUTURE"
    TEMPERATURE_OUT_OF_RANGE = "TEMPERATURE_OUT_OF_RANGE"
    VOLTAGE_OUT_OF_RANGE = "VOLTAGE_OUT_OF_RANGE"
    INVALID_OPERATING_MODE = "INVALID_OPERATING_MODE"
    RESERVED_FAULT_BITS_SET = "RESERVED_FAULT_BITS_SET"


class TelemetryValidationError(ValueError):
    """A telemetry frame failed one named Interface Control Document rule."""

    def __init__(self, reason: TelemetryRejectionReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TelemetryFrame:
    """Decoded telemetry values in their exact version 1 wire units."""

    payload_id: int
    boot_id: int
    sequence_number: int
    timestamp_us: int
    temperature_centi_c: int
    voltage_mv: int
    operating_mode: OperatingMode
    fault_flags: FaultFlag = FaultFlag.NONE
    protocol_version: int = PROTOCOL_VERSION

    @property
    def temperature_c(self) -> float:
        """Return the temperature in degrees Celsius for display."""

        return self.temperature_centi_c / 100.0

    @property
    def voltage_v(self) -> float:
        """Return the voltage in volts for display."""

        return self.voltage_mv / 1_000.0

    @property
    def sample_time(self) -> datetime:
        """Return the sample timestamp as an aware Coordinated Universal Time value."""

        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=self.timestamp_us)

    @property
    def packet_id(self) -> str:
        """Return the human-readable persistent telemetry identifier."""

        return format_packet_id(self.payload_id, self.boot_id, self.sequence_number)


def crc32_iso_hdlc(data: bytes) -> int:
    """Calculate CRC-32/ISO-HDLC using the profile required by the interface."""

    return zlib.crc32(data) & UINT32_MAX


def _timestamp_us(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ground_received_at must include a timezone")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _reject(reason: TelemetryRejectionReason, detail: str) -> None:
    raise TelemetryValidationError(reason, detail)


def _validate_fields(
    frame: TelemetryFrame,
    *,
    ground_received_at: datetime | None,
) -> None:
    """Apply version 1 field rules in Interface Control Document order."""

    if frame.protocol_version != PROTOCOL_VERSION:
        _reject(
            TelemetryRejectionReason.UNSUPPORTED_PROTOCOL_VERSION,
            f"protocol_version must be {PROTOCOL_VERSION}, got {frame.protocol_version}",
        )
    if frame.payload_id != SUPPORTED_PAYLOAD_ID:
        _reject(
            TelemetryRejectionReason.UNSUPPORTED_PAYLOAD_ID,
            f"payload_id must be {SUPPORTED_PAYLOAD_ID}, got {frame.payload_id}",
        )
    if not 1 <= frame.boot_id <= UINT32_MAX:
        _reject(
            TelemetryRejectionReason.INVALID_BOOT_ID,
            f"boot_id must be between 1 and {UINT32_MAX}, got {frame.boot_id}",
        )
    if not 0 <= frame.sequence_number <= UINT32_MAX:
        raise ValueError(
            f"sequence_number must be between 0 and {UINT32_MAX}, "
            f"got {frame.sequence_number}"
        )
    if not 0 <= frame.timestamp_us < (1 << 64):
        raise ValueError("timestamp_us must fit an unsigned 64-bit integer")
    if frame.timestamp_us < PROJECT_EPOCH_US:
        _reject(
            TelemetryRejectionReason.TIMESTAMP_BEFORE_PROJECT_EPOCH,
            "timestamp_us is before 2026-01-01T00:00:00Z",
        )
    if ground_received_at is not None:
        received_us = _timestamp_us(ground_received_at)
        if frame.timestamp_us > received_us + MAX_FUTURE_SKEW_US:
            _reject(
                TelemetryRejectionReason.TIMESTAMP_TOO_FAR_IN_FUTURE,
                "sample timestamp is more than five minutes after ground receipt",
            )
    if not MIN_TEMPERATURE_CENTI_C <= frame.temperature_centi_c <= MAX_TEMPERATURE_CENTI_C:
        _reject(
            TelemetryRejectionReason.TEMPERATURE_OUT_OF_RANGE,
            "temperature_centi_c must be between -10000 and 15000",
        )
    if not MIN_VOLTAGE_MV <= frame.voltage_mv <= MAX_VOLTAGE_MV:
        _reject(
            TelemetryRejectionReason.VOLTAGE_OUT_OF_RANGE,
            "voltage_mv must be between 0 and 50000",
        )
    try:
        OperatingMode(int(frame.operating_mode))
    except (TypeError, ValueError):
        _reject(
            TelemetryRejectionReason.INVALID_OPERATING_MODE,
            f"operating_mode is not defined in version 1: {frame.operating_mode!r}",
        )
    fault_value = int(frame.fault_flags)
    if not 0 <= fault_value <= 0xFF:
        raise ValueError("fault_flags must fit an unsigned 8-bit integer")
    if fault_value & RESERVED_FAULT_MASK:
        _reject(
            TelemetryRejectionReason.RESERVED_FAULT_BITS_SET,
            f"reserved fault bits must be zero: 0x{fault_value:02x}",
        )


def encode_telemetry(
    frame: TelemetryFrame,
    *,
    ground_received_at: datetime | None = None,
) -> bytes:
    """Encode and validate one exact 28-byte telemetry version 1 frame.

    ``ground_received_at`` is optional for the payload-side encoder because the
    ground has not received the sample yet.  Receiver-side callers should use
    :func:`decode_telemetry`, which requires the receipt time.
    """

    _validate_fields(frame, ground_received_at=ground_received_at)
    body = TELEMETRY_V1_BODY.pack(
        frame.protocol_version,
        frame.payload_id,
        frame.boot_id,
        frame.sequence_number,
        frame.timestamp_us,
        frame.temperature_centi_c,
        frame.voltage_mv,
        int(frame.operating_mode),
        int(frame.fault_flags),
    )
    return body + CRC32_BIG_ENDIAN.pack(crc32_iso_hdlc(body))


def decode_telemetry(raw: bytes, *, ground_received_at: datetime) -> TelemetryFrame:
    """Decode one frame and strictly validate all version 1 interface fields."""

    if not raw:
        _reject(TelemetryRejectionReason.EMPTY_BODY, "telemetry body is empty")

    # The version is intentionally inspected before length and checksum, as
    # specified by the receiver validation order in the interface document.
    if raw[0] != PROTOCOL_VERSION:
        _reject(
            TelemetryRejectionReason.UNSUPPORTED_PROTOCOL_VERSION,
            f"protocol_version must be {PROTOCOL_VERSION}, got {raw[0]}",
        )
    if len(raw) != TELEMETRY_V1_FRAME_SIZE:
        _reject(
            TelemetryRejectionReason.INVALID_FRAME_LENGTH,
            f"telemetry version 1 must be {TELEMETRY_V1_FRAME_SIZE} bytes, got {len(raw)}",
        )

    body = raw[:TELEMETRY_V1_BODY_SIZE]
    expected_crc = CRC32_BIG_ENDIAN.unpack(raw[TELEMETRY_V1_BODY_SIZE:])[0]
    actual_crc = crc32_iso_hdlc(body)
    if expected_crc != actual_crc:
        _reject(
            TelemetryRejectionReason.CRC_MISMATCH,
            f"expected CRC 0x{expected_crc:08X}, calculated 0x{actual_crc:08X}",
        )

    (
        protocol_version,
        payload_id,
        boot_id,
        sequence_number,
        timestamp_us,
        temperature_centi_c,
        voltage_mv,
        operating_mode_value,
        fault_flags_value,
    ) = TELEMETRY_V1_BODY.unpack(body)

    # Keep the raw integer until the ordered field validation has run.  If a
    # packet has multiple bad fields, this preserves the interface's promised
    # rejection priority (payload, time/ranges, mode, then reserved flags).
    frame = TelemetryFrame(
        protocol_version=protocol_version,
        payload_id=payload_id,
        boot_id=boot_id,
        sequence_number=sequence_number,
        timestamp_us=timestamp_us,
        temperature_centi_c=temperature_centi_c,
        voltage_mv=voltage_mv,
        operating_mode=operating_mode_value,  # type: ignore[arg-type]
        fault_flags=FaultFlag(fault_flags_value),
    )
    _validate_fields(frame, ground_received_at=ground_received_at)
    return TelemetryFrame(
        protocol_version=frame.protocol_version,
        payload_id=frame.payload_id,
        boot_id=frame.boot_id,
        sequence_number=frame.sequence_number,
        timestamp_us=frame.timestamp_us,
        temperature_centi_c=frame.temperature_centi_c,
        voltage_mv=frame.voltage_mv,
        operating_mode=OperatingMode(operating_mode_value),
        fault_flags=frame.fault_flags,
    )


def format_packet_id(payload_id: int, boot_id: int, sequence_number: int) -> str:
    """Format the version 1 display identifier defined by the interface."""

    if not 0 <= payload_id <= 99:
        raise ValueError("payload_id must fit the two-digit display field")
    if not 0 <= boot_id <= UINT32_MAX:
        raise ValueError("boot_id must fit an unsigned 32-bit integer")
    if not 0 <= sequence_number <= UINT32_MAX:
        raise ValueError("sequence_number must fit an unsigned 32-bit integer")
    return f"TLM-P{payload_id:02d}-B{boot_id:08X}-S{sequence_number:08X}"
