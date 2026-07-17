from __future__ import annotations

import dataclasses
import struct
import time
import zlib

PRIMARY_HEADER = struct.Struct("!HHH")
CRC = struct.Struct("!I")
HEALTH_PAYLOAD = struct.Struct("!QHhiBB")
ACK_PAYLOAD = struct.Struct("!IBiQ")
COMMAND_PAYLOAD = struct.Struct("!IBiQ")

APID_HEALTH = 100
APID_COMMAND_ACK = 101
MAX_SEQUENCE = (1 << 14) - 1


class PacketError(ValueError):
    """Raised when a packet is malformed or fails integrity checks."""


@dataclasses.dataclass(frozen=True, slots=True)
class SpacePacket:
    apid: int
    sequence_count: int
    payload: bytes
    packet_type: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class HealthTelemetry:
    timestamp_ns: int
    bus_voltage_v: float
    battery_temp_c: float
    wheel_rpm: int
    mode: int
    fault_flags: int


@dataclasses.dataclass(frozen=True, slots=True)
class CommandAck:
    command_id: int
    status: int
    applied_value: int
    timestamp_ns: int


@dataclasses.dataclass(frozen=True, slots=True)
class CommandFrame:
    command_id: int
    opcode: int
    argument: int
    issued_ns: int


def _validate_apid(apid: int) -> None:
    if not 0 <= apid <= 0x7FF:
        raise PacketError(f"APID out of range: {apid}")


def encode_space_packet(apid: int, sequence_count: int, payload: bytes, packet_type: int = 0) -> bytes:
    _validate_apid(apid)
    if packet_type not in (0, 1):
        raise PacketError("packet_type must be 0 (telemetry) or 1 (command)")
    if not 0 <= sequence_count <= MAX_SEQUENCE:
        raise PacketError(f"sequence_count out of range: {sequence_count}")
    if not payload:
        raise PacketError("payload must not be empty")

    first_word = (packet_type << 12) | (1 << 11) | apid
    second_word = (0b11 << 14) | sequence_count
    data_length = len(payload) + CRC.size - 1
    header = PRIMARY_HEADER.pack(first_word, second_word, data_length)
    checksum = zlib.crc32(header + payload) & 0xFFFFFFFF
    return header + payload + CRC.pack(checksum)


def decode_space_packet(raw: bytes) -> SpacePacket:
    if len(raw) < PRIMARY_HEADER.size + CRC.size + 1:
        raise PacketError("packet is too short")

    first_word, second_word, data_length = PRIMARY_HEADER.unpack(raw[: PRIMARY_HEADER.size])
    expected_total = PRIMARY_HEADER.size + data_length + 1
    if len(raw) != expected_total:
        raise PacketError(f"length mismatch: expected {expected_total}, got {len(raw)}")

    expected_crc = CRC.unpack(raw[-CRC.size :])[0]
    actual_crc = zlib.crc32(raw[:-CRC.size]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        raise PacketError("CRC mismatch")

    version = (first_word >> 13) & 0b111
    if version != 0:
        raise PacketError(f"unsupported packet version: {version}")

    packet_type = (first_word >> 12) & 0b1
    apid = first_word & 0x7FF
    sequence_count = second_word & MAX_SEQUENCE
    payload = raw[PRIMARY_HEADER.size : -CRC.size]
    return SpacePacket(apid, sequence_count, payload, packet_type)


def encode_health(sequence_count: int, sample: HealthTelemetry) -> bytes:
    payload = HEALTH_PAYLOAD.pack(
        sample.timestamp_ns,
        round(sample.bus_voltage_v * 1000),
        round(sample.battery_temp_c * 100),
        sample.wheel_rpm,
        sample.mode,
        sample.fault_flags,
    )
    return encode_space_packet(APID_HEALTH, sequence_count, payload)


def decode_health(packet: SpacePacket) -> HealthTelemetry:
    if packet.apid != APID_HEALTH:
        raise PacketError(f"expected health APID {APID_HEALTH}, got {packet.apid}")
    if len(packet.payload) != HEALTH_PAYLOAD.size:
        raise PacketError("invalid health payload length")
    ts, millivolts, centidegrees, rpm, mode, flags = HEALTH_PAYLOAD.unpack(packet.payload)
    return HealthTelemetry(ts, millivolts / 1000.0, centidegrees / 100.0, rpm, mode, flags)


def encode_ack(sequence_count: int, ack: CommandAck) -> bytes:
    payload = ACK_PAYLOAD.pack(ack.command_id, ack.status, ack.applied_value, ack.timestamp_ns)
    return encode_space_packet(APID_COMMAND_ACK, sequence_count, payload)


def decode_ack(packet: SpacePacket) -> CommandAck:
    if packet.apid != APID_COMMAND_ACK:
        raise PacketError(f"expected ACK APID {APID_COMMAND_ACK}, got {packet.apid}")
    if len(packet.payload) != ACK_PAYLOAD.size:
        raise PacketError("invalid ACK payload length")
    return CommandAck(*ACK_PAYLOAD.unpack(packet.payload))


def encode_command(frame: CommandFrame) -> bytes:
    payload = COMMAND_PAYLOAD.pack(frame.command_id, frame.opcode, frame.argument, frame.issued_ns)
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    return payload + CRC.pack(checksum)


def decode_command(raw: bytes) -> CommandFrame:
    if len(raw) != COMMAND_PAYLOAD.size + CRC.size:
        raise PacketError("invalid command frame length")
    payload, crc_raw = raw[:-CRC.size], raw[-CRC.size :]
    expected = CRC.unpack(crc_raw)[0]
    actual = zlib.crc32(payload) & 0xFFFFFFFF
    if expected != actual:
        raise PacketError("command CRC mismatch")
    return CommandFrame(*COMMAND_PAYLOAD.unpack(payload))


def now_ns() -> int:
    return time.time_ns()
