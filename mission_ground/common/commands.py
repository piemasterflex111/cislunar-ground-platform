from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    opcode: int
    minimum: int
    maximum: int


COMMANDS: dict[str, CommandDefinition] = {
    "SET_MODE": CommandDefinition(1, 0, 3),
    "SET_WHEEL_RPM": CommandDefinition(2, -6000, 6000),
    "RESET_FAULTS": CommandDefinition(3, 0, 0),
}
OPCODE_TO_NAME = {definition.opcode: name for name, definition in COMMANDS.items()}


class CommandValidationError(ValueError):
    pass


def validate_command(name: str, argument: int) -> CommandDefinition:
    definition = COMMANDS.get(name)
    if definition is None:
        raise CommandValidationError(f"unsupported command: {name}")
    if not definition.minimum <= argument <= definition.maximum:
        raise CommandValidationError(
            f"argument {argument} outside [{definition.minimum}, {definition.maximum}] for {name}"
        )
    return definition
