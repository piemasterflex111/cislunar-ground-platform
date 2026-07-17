"""Deterministic telemetry health and staleness classification rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .codec import DEFINED_FAULT_MASK, FaultFlag, TelemetryFrame, _timestamp_us

STALE_AFTER_US = int(timedelta(hours=24).total_seconds() * 1_000_000)


class HealthClassification(str, Enum):
    """Ordered names used in processed telemetry results."""

    NOMINAL = "NOMINAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Freshness(str, Enum):
    """Whether a valid sample is more than 24 hours old at receipt."""

    CURRENT = "CURRENT"
    STALE = "STALE"


_SEVERITY_RANK = {
    HealthClassification.NOMINAL: 0,
    HealthClassification.WARNING: 1,
    HealthClassification.CRITICAL: 2,
}


@dataclass(frozen=True, slots=True)
class HealthAssessment:
    """The component decisions and their most-severe combined result."""

    classification: HealthClassification
    freshness: Freshness
    temperature: HealthClassification
    voltage: HealthClassification
    faults: HealthClassification

    @property
    def is_stale(self) -> bool:
        return self.freshness is Freshness.STALE


def classify_temperature(temperature_centi_c: int) -> HealthClassification:
    """Classify temperature using the exact centidegree boundaries."""

    if temperature_centi_c < -4_000 or temperature_centi_c >= 8_500:
        return HealthClassification.CRITICAL
    if temperature_centi_c < -2_000 or temperature_centi_c >= 6_200:
        return HealthClassification.WARNING
    return HealthClassification.NOMINAL


def classify_voltage(voltage_mv: int) -> HealthClassification:
    """Classify voltage using the exact millivolt boundaries."""

    if voltage_mv < 22_000 or voltage_mv > 34_000:
        return HealthClassification.CRITICAL
    if voltage_mv < 26_000 or voltage_mv > 30_000:
        return HealthClassification.WARNING
    return HealthClassification.NOMINAL


def classify_faults(fault_flags: FaultFlag | int) -> HealthClassification:
    """Return the minimum severity required by the reported fault bits."""

    flag_value = int(fault_flags)
    if not 0 <= flag_value <= 0xFF or flag_value & ~DEFINED_FAULT_MASK:
        raise ValueError("fault_flags contains a bit not defined by telemetry version 1")
    flags = FaultFlag(flag_value)
    critical = (
        FaultFlag.TEMPERATURE_SENSOR_FAULT
        | FaultFlag.VOLTAGE_SENSOR_FAULT
        | FaultFlag.INTERNAL_PAYLOAD_FAULT
    )
    warning = FaultFlag.OVER_TEMPERATURE | FaultFlag.UNDER_VOLTAGE | FaultFlag.OVER_VOLTAGE
    if flags & critical:
        return HealthClassification.CRITICAL
    if flags & warning:
        return HealthClassification.WARNING
    return HealthClassification.NOMINAL


def classify_staleness(
    sample_timestamp_us: int,
    *,
    ground_received_at: datetime,
) -> Freshness:
    """Mark a sample stale only when it is strictly more than 24 hours old."""

    age_us = _timestamp_us(ground_received_at) - sample_timestamp_us
    if age_us > STALE_AFTER_US:
        return Freshness.STALE
    return Freshness.CURRENT


def assess_health(
    frame: TelemetryFrame,
    *,
    ground_received_at: datetime,
) -> HealthAssessment:
    """Combine temperature, voltage, and faults using most-severe-wins."""

    temperature = classify_temperature(frame.temperature_centi_c)
    voltage = classify_voltage(frame.voltage_mv)
    faults = classify_faults(frame.fault_flags)
    classification = max((temperature, voltage, faults), key=_SEVERITY_RANK.__getitem__)
    return HealthAssessment(
        classification=classification,
        freshness=classify_staleness(
            frame.timestamp_us,
            ground_received_at=ground_received_at,
        ),
        temperature=temperature,
        voltage=voltage,
        faults=faults,
    )
