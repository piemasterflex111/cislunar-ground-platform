from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mission_ground.telemetry.codec import FaultFlag, OperatingMode, TelemetryFrame
from mission_ground.telemetry.health import (
    Freshness,
    HealthClassification,
    assess_health,
    classify_faults,
    classify_staleness,
    classify_temperature,
    classify_voltage,
)


@pytest.mark.parametrize(
    ("centi_c", "expected"),
    [
        (-4_001, HealthClassification.CRITICAL),
        (-4_000, HealthClassification.WARNING),
        (-2_001, HealthClassification.WARNING),
        (-2_000, HealthClassification.NOMINAL),
        (6_199, HealthClassification.NOMINAL),
        (6_200, HealthClassification.WARNING),
        (8_499, HealthClassification.WARNING),
        (8_500, HealthClassification.CRITICAL),
    ],
)
def test_temperature_boundaries(centi_c: int, expected: HealthClassification) -> None:
    assert classify_temperature(centi_c) is expected


@pytest.mark.parametrize(
    ("millivolts", "expected"),
    [
        (21_999, HealthClassification.CRITICAL),
        (22_000, HealthClassification.WARNING),
        (25_999, HealthClassification.WARNING),
        (26_000, HealthClassification.NOMINAL),
        (30_000, HealthClassification.NOMINAL),
        (30_001, HealthClassification.WARNING),
        (34_000, HealthClassification.WARNING),
        (34_001, HealthClassification.CRITICAL),
    ],
)
def test_voltage_boundaries(millivolts: int, expected: HealthClassification) -> None:
    assert classify_voltage(millivolts) is expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (FaultFlag.NONE, HealthClassification.NOMINAL),
        (FaultFlag.OVER_TEMPERATURE, HealthClassification.WARNING),
        (FaultFlag.UNDER_VOLTAGE | FaultFlag.OVER_VOLTAGE, HealthClassification.WARNING),
        (FaultFlag.TEMPERATURE_SENSOR_FAULT, HealthClassification.CRITICAL),
        (
            FaultFlag.OVER_TEMPERATURE | FaultFlag.INTERNAL_PAYLOAD_FAULT,
            HealthClassification.CRITICAL,
        ),
    ],
)
def test_fault_severity(flags: FaultFlag, expected: HealthClassification) -> None:
    assert classify_faults(flags) is expected


def test_fault_classifier_does_not_silently_accept_reserved_bits() -> None:
    with pytest.raises(ValueError, match="not defined"):
        classify_faults(0x40)


def test_health_uses_most_severe_component_and_keeps_component_results() -> None:
    received = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    frame = TelemetryFrame(
        payload_id=1,
        boot_id=1,
        sequence_number=0,
        timestamp_us=1_784_203_200_000_000,
        temperature_centi_c=6_200,
        voltage_mv=28_000,
        operating_mode=OperatingMode.SCIENCE,
        fault_flags=FaultFlag.INTERNAL_PAYLOAD_FAULT,
    )

    result = assess_health(frame, ground_received_at=received)

    assert result.classification is HealthClassification.CRITICAL
    assert result.temperature is HealthClassification.WARNING
    assert result.voltage is HealthClassification.NOMINAL
    assert result.faults is HealthClassification.CRITICAL
    assert result.freshness is Freshness.CURRENT


def test_exactly_24_hours_old_is_current_and_one_microsecond_older_is_stale() -> None:
    received = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    exactly_24_hours = received - timedelta(hours=24)
    exact_us = int(exactly_24_hours.timestamp() * 1_000_000)

    assert (
        classify_staleness(exact_us, ground_received_at=received)
        is Freshness.CURRENT
    )
    assert (
        classify_staleness(exact_us - 1, ground_received_at=received)
        is Freshness.STALE
    )
