from mission_ground.common.limits import evaluate_health
from mission_ground.common.packet import HealthTelemetry


def test_critical_health_limits() -> None:
    sample = HealthTelemetry(1, 22.5, 70.0, 5800, 1, 1)
    violations = evaluate_health(sample)
    assert {v.parameter for v in violations} == {
        "bus_voltage_v",
        "battery_temp_c",
        "wheel_rpm",
        "fault_flags",
    }
    assert all(v.severity == "CRITICAL" for v in violations)


def test_nominal_health_has_no_violations() -> None:
    assert evaluate_health(HealthTelemetry(1, 28.0, 32.0, 1200, 1, 0)) == []
