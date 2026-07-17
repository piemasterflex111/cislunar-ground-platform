from __future__ import annotations

import asyncio
import logging
import math
import os
import random
import socket

from prometheus_client import Counter, Gauge, start_http_server

from mission_ground.common.commands import OPCODE_TO_NAME
from mission_ground.common.config import env, env_float, env_int
from mission_ground.common.packet import (
    CommandAck,
    HealthTelemetry,
    PacketError,
    decode_command,
    encode_ack,
    encode_health,
    now_ns,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s simulator %(message)s")
LOG = logging.getLogger(__name__)

PACKETS_SENT = Counter("spacecraft_packets_sent_total", "Telemetry packets emitted", ["apid"])
COMMANDS_RECEIVED = Counter("spacecraft_commands_received_total", "Commands received", ["status"])
BUS_VOLTAGE = Gauge("spacecraft_bus_voltage_volts", "Simulated bus voltage")
BATTERY_TEMP = Gauge("spacecraft_battery_temperature_celsius", "Simulated battery temperature")


class SpacecraftState:
    def __init__(self) -> None:
        self.mode = 1
        self.wheel_rpm = 1200
        self.fault_flags = 0
        self.health_sequence = 0
        self.ack_sequence = 0
        self.command_results: dict[int, CommandAck] = {}

    def apply(self, raw: bytes) -> CommandAck:
        frame = decode_command(raw)
        cached = self.command_results.get(frame.command_id)
        if cached is not None:
            return cached

        status = 0
        applied = frame.argument
        name = OPCODE_TO_NAME.get(frame.opcode)
        if name == "SET_MODE" and 0 <= frame.argument <= 3:
            self.mode = frame.argument
        elif name == "SET_WHEEL_RPM" and -6000 <= frame.argument <= 6000:
            self.wheel_rpm = frame.argument
        elif name == "RESET_FAULTS" and frame.argument == 0:
            self.fault_flags = 0
        else:
            status = 1

        ack = CommandAck(frame.command_id, status, applied, now_ns())
        self.command_results[frame.command_id] = ack
        return ack


class CommandProtocol(asyncio.DatagramProtocol):
    def __init__(self, state: SpacecraftState, telemetry_target: tuple[str, int]) -> None:
        self.state = state
        self.telemetry_target = telemetry_target
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport
        LOG.info("command receiver ready on UDP %s", env_int("SIM_COMMAND_PORT", 5006))

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            ack = self.state.apply(data)
            status = "accepted" if ack.status == 0 else "rejected"
            COMMANDS_RECEIVED.labels(status=status).inc()
            raw_ack = encode_ack(self.state.ack_sequence, ack)
            self.state.ack_sequence = (self.state.ack_sequence + 1) & 0x3FFF
            assert self.transport is not None
            self.transport.sendto(raw_ack, self.telemetry_target)
            PACKETS_SENT.labels(apid="command_ack").inc()
            LOG.info("command id=%s status=%s from=%s", ack.command_id, status, addr)
        except PacketError as exc:
            COMMANDS_RECEIVED.labels(status="malformed").inc()
            LOG.warning("dropped malformed command from %s: %s", addr, exc)


async def telemetry_loop(state: SpacecraftState) -> None:
    target = (env("TELEMETRY_TARGET_HOST", "telemetry-ingest"), env_int("TELEMETRY_TARGET_PORT", 5005))
    rate_hz = env_float("SIM_RATE_HZ", 20.0)
    anomaly_every = env_int("SIM_ANOMALY_EVERY", 200)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tick = 0
    LOG.info("telemetry target=%s:%s rate_hz=%s", *target, rate_hz)
    while True:
        tick += 1
        anomaly = anomaly_every > 0 and tick % anomaly_every == 0
        voltage = 22.8 if anomaly else 28.4 + math.sin(tick / 30) * 0.3
        temperature = 69.0 if anomaly else 31.0 + random.uniform(-0.4, 0.4)
        if anomaly:
            state.fault_flags |= 0x01
        sample = HealthTelemetry(now_ns(), voltage, temperature, state.wheel_rpm, state.mode, state.fault_flags)
        sock.sendto(encode_health(state.health_sequence, sample), target)
        state.health_sequence = (state.health_sequence + 1) & 0x3FFF
        PACKETS_SENT.labels(apid="health").inc()
        BUS_VOLTAGE.set(voltage)
        BATTERY_TEMP.set(temperature)
        await asyncio.sleep(1.0 / rate_hz)


async def main() -> None:
    start_http_server(env_int("METRICS_PORT", 9101))
    state = SpacecraftState()
    loop = asyncio.get_running_loop()
    target = (env("TELEMETRY_TARGET_HOST", "telemetry-ingest"), env_int("TELEMETRY_TARGET_PORT", 5005))
    transport, _ = await loop.create_datagram_endpoint(
        lambda: CommandProtocol(state, target),
        local_addr=("0.0.0.0", env_int("SIM_COMMAND_PORT", 5006)),
    )
    try:
        await telemetry_loop(state)
    finally:
        transport.close()


if __name__ == "__main__":
    asyncio.run(main())
