import time

from mission_ground.common.packet import CommandFrame, encode_command
from mission_ground.services.spacecraft_sim import SpacecraftState


def test_command_idempotency() -> None:
    state = SpacecraftState()
    raw = encode_command(CommandFrame(44, 2, 2500, time.time_ns()))
    first = state.apply(raw)
    state.wheel_rpm = 999
    second = state.apply(raw)
    assert first == second
    assert state.wheel_rpm == 999
