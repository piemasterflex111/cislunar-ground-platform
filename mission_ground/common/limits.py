from __future__ import annotations

from dataclasses import dataclass

from .packet import HealthTelemetry


@dataclass(frozen=True, slots=True)
class LimitViolation:
    parameter: str
    value: float | int
    severity: str
    message: str


def evaluate_health(sample: HealthTelemetry) -> list[LimitViolation]:
    violations: list[LimitViolation] = []
    if sample.bus_voltage_v < 24.0:
        violations.append(LimitViolation("bus_voltage_v", sample.bus_voltage_v, "CRITICAL", "Bus voltage below 24.0 V"))
    elif sample.bus_voltage_v < 26.0:
        violations.append(LimitViolation("bus_voltage_v", sample.bus_voltage_v, "WARNING", "Bus voltage below 26.0 V"))
    if sample.battery_temp_c > 65.0:
        violations.append(LimitViolation("battery_temp_c", sample.battery_temp_c, "CRITICAL", "Battery temperature above 65 C"))
    elif sample.battery_temp_c > 55.0:
        violations.append(LimitViolation("battery_temp_c", sample.battery_temp_c, "WARNING", "Battery temperature above 55 C"))
    if abs(sample.wheel_rpm) > 5500:
        violations.append(LimitViolation("wheel_rpm", sample.wheel_rpm, "CRITICAL", "Reaction wheel speed exceeds 5500 RPM"))
    if sample.fault_flags:
        violations.append(LimitViolation("fault_flags", sample.fault_flags, "CRITICAL", "Spacecraft fault flag is set"))
    return violations
