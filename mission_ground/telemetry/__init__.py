"""Versioned telemetry-domain rules for the Secure Payload Gateway.

This package contains no network or database operations.  It turns the
Interface Control Document into deterministic functions that can be reused by
the payload simulator, ground receiver, and payload worker.
"""

from .codec import (
    FaultFlag,
    OperatingMode,
    TelemetryFrame,
    TelemetryRejectionReason,
    TelemetryValidationError,
    crc32_iso_hdlc,
    decode_telemetry,
    encode_telemetry,
    format_packet_id,
)
from .health import (
    Freshness,
    HealthAssessment,
    HealthClassification,
    assess_health,
    classify_faults,
    classify_staleness,
    classify_temperature,
    classify_voltage,
)
from .sequence import (
    SequenceDecision,
    SequenceDisposition,
    SequenceStateError,
    decide_sequence,
)

__all__ = [
    "FaultFlag",
    "Freshness",
    "HealthAssessment",
    "HealthClassification",
    "OperatingMode",
    "SequenceDecision",
    "SequenceDisposition",
    "SequenceStateError",
    "TelemetryFrame",
    "TelemetryRejectionReason",
    "TelemetryValidationError",
    "assess_health",
    "classify_faults",
    "classify_staleness",
    "classify_temperature",
    "classify_voltage",
    "crc32_iso_hdlc",
    "decide_sequence",
    "decode_telemetry",
    "encode_telemetry",
    "format_packet_id",
]
