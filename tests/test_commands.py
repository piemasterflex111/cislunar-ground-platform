import pytest

from mission_ground.common.commands import CommandValidationError, validate_command


def test_command_allowlist_and_bounds() -> None:
    assert validate_command("SET_MODE", 3).opcode == 1
    assert validate_command("SET_WHEEL_RPM", -6000).opcode == 2
    with pytest.raises(CommandValidationError, match="unsupported"):
        validate_command("FORMAT_STORAGE", 0)
    with pytest.raises(CommandValidationError, match="outside"):
        validate_command("SET_MODE", 9)
