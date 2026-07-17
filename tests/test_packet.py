import dataclasses
import time

import pytest

from mission_ground.common.packet import (
    CommandFrame,
    HealthTelemetry,
    PacketError,
    decode_command,
    decode_health,
    decode_space_packet,
    encode_command,
    encode_health,
)


def test_health_round_trip() -> None:
    sample = HealthTelemetry(time.time_ns(), 28.456, -12.34, -2200, 2, 3)
    decoded = decode_health(decode_space_packet(encode_health(42, sample)))
    assert decoded.timestamp_ns == sample.timestamp_ns
    assert decoded.bus_voltage_v == pytest.approx(28.456)
    assert decoded.battery_temp_c == pytest.approx(-12.34)
    assert decoded.wheel_rpm == -2200
    assert decoded.mode == 2
    assert decoded.fault_flags == 3


def test_crc_corruption_is_rejected() -> None:
    sample = HealthTelemetry(time.time_ns(), 28.0, 30.0, 1000, 1, 0)
    raw = bytearray(encode_health(1, sample))
    raw[8] ^= 0x40
    with pytest.raises(PacketError, match="CRC"):
        decode_space_packet(bytes(raw))


def test_command_crc_corruption_is_rejected() -> None:
    raw = bytearray(encode_command(CommandFrame(12, 2, 1500, time.time_ns())))
    raw[2] ^= 0x01
    with pytest.raises(PacketError, match="CRC"):
        decode_command(bytes(raw))


def test_length_mismatch_is_rejected() -> None:
    sample = HealthTelemetry(time.time_ns(), 28.0, 30.0, 1000, 1, 0)
    raw = encode_health(1, sample)
    with pytest.raises(PacketError, match="length mismatch"):
        decode_space_packet(raw[:-1])
