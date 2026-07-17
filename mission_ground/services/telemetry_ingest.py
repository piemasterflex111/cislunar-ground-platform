from __future__ import annotations

import asyncio
import json
import logging
import os

from prometheus_client import Counter, Histogram, start_http_server

from mission_ground.common.config import env, env_int
from mission_ground.common.nats import connect_nats
from mission_ground.common.packet import (
    APID_COMMAND_ACK,
    APID_HEALTH,
    PacketError,
    decode_ack,
    decode_health,
    decode_space_packet,
    now_ns,
)
from mission_ground.common.sequence import SequenceTracker

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s ingest %(message)s")
LOG = logging.getLogger(__name__)

PACKETS = Counter("ingest_packets_total", "Packets processed", ["result", "apid"])
GAPS = Counter("ingest_sequence_gaps_total", "Missing sequence counts", ["apid"])
LATENCY = Histogram("ingest_packet_latency_seconds", "Spacecraft timestamp to ingest latency")


class IngestProtocol(asyncio.DatagramProtocol):
    def __init__(self, js) -> None:
        self.js = js
        self.tracker = SequenceTracker()

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.create_task(self._process(data, addr))

    async def _process(self, raw: bytes, addr) -> None:
        try:
            packet = decode_space_packet(raw)
            gaps = self.tracker.observe(packet.apid, packet.sequence_count)
            if gaps:
                GAPS.labels(apid=str(packet.apid)).inc(gaps)
                LOG.warning("sequence gap apid=%s missing=%s", packet.apid, gaps)

            envelope: dict[str, object] = {
                "apid": packet.apid,
                "sequence_count": packet.sequence_count,
                "received_ns": now_ns(),
                "source": f"{addr[0]}:{addr[1]}",
            }
            if packet.apid == APID_HEALTH:
                health = decode_health(packet)
                envelope["data"] = {
                    "timestamp_ns": health.timestamp_ns,
                    "bus_voltage_v": health.bus_voltage_v,
                    "battery_temp_c": health.battery_temp_c,
                    "wheel_rpm": health.wheel_rpm,
                    "mode": health.mode,
                    "fault_flags": health.fault_flags,
                }
                LATENCY.observe(max(0.0, (now_ns() - health.timestamp_ns) / 1_000_000_000))
                subject = "telemetry.health"
                label = "health"
            elif packet.apid == APID_COMMAND_ACK:
                ack = decode_ack(packet)
                envelope["data"] = {
                    "command_id": ack.command_id,
                    "status": ack.status,
                    "applied_value": ack.applied_value,
                    "timestamp_ns": ack.timestamp_ns,
                }
                subject = "telemetry.command_ack"
                label = "command_ack"
            else:
                PACKETS.labels(result="unsupported", apid=str(packet.apid)).inc()
                return

            await self.js.publish(subject, json.dumps(envelope, separators=(",", ":")).encode())
            PACKETS.labels(result="accepted", apid=label).inc()
        except PacketError as exc:
            PACKETS.labels(result="rejected", apid="unknown").inc()
            LOG.warning("rejected packet from=%s reason=%s", addr, exc)
        except Exception:
            PACKETS.labels(result="error", apid="unknown").inc()
            LOG.exception("ingest processing failure")


async def main() -> None:
    start_http_server(env_int("METRICS_PORT", 9102))
    nc, js = await connect_nats(env("NATS_URL", "nats://nats:4222"), "telemetry-ingest")
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: IngestProtocol(js),
        local_addr=("0.0.0.0", env_int("TELEMETRY_PORT", 5005)),
    )
    LOG.info("listening for telemetry on UDP %s", env_int("TELEMETRY_PORT", 5005))
    try:
        await asyncio.Future()
    finally:
        transport.close()
        await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
