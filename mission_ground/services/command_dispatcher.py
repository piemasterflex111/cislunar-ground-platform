from __future__ import annotations

import asyncio
import json
import logging
import os
import socket

from prometheus_client import Counter, start_http_server

from mission_ground.common.config import env, env_int
from mission_ground.common.nats import connect_nats
from mission_ground.common.packet import CommandFrame, encode_command, now_ns

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s dispatcher %(message)s")
LOG = logging.getLogger(__name__)

DISPATCH = Counter("command_dispatch_total", "Command dispatch outcomes", ["result", "name"])


async def main() -> None:
    start_http_server(env_int("METRICS_PORT", 9104))
    nc, js = await connect_nats(env("NATS_URL", "nats://nats:4222"), "command-dispatcher")
    target = (env("COMMAND_TARGET_HOST", "spacecraft-sim"), env_int("COMMAND_TARGET_PORT", 5006))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    async def dispatch(msg) -> None:
        name = "unknown"
        try:
            event = json.loads(msg.data)
            name = str(event["name"])
            frame = CommandFrame(
                command_id=int(event["command_id"]),
                opcode=int(event["opcode"]),
                argument=int(event["argument"]),
                issued_ns=now_ns(),
            )
            sock.sendto(encode_command(frame), target)
            status = {
                "command_id": frame.command_id,
                "status": "DISPATCHED",
                "detail": f"UDP target {target[0]}:{target[1]}",
            }
            await js.publish("commands.status", json.dumps(status, separators=(",", ":")).encode())
            DISPATCH.labels(result="sent", name=name).inc()
            LOG.info("dispatched command id=%s name=%s", frame.command_id, name)
            await msg.ack()
        except Exception as exc:
            DISPATCH.labels(result="error", name=name).inc()
            LOG.exception("command dispatch failed")
            try:
                event = json.loads(msg.data)
                status = {"command_id": int(event["command_id"]), "status": "DISPATCH_ERROR", "detail": str(exc)}
                await js.publish("commands.status", json.dumps(status, separators=(",", ":")).encode())
            finally:
                await msg.nak(delay=2)

    await js.subscribe("commands.dispatch", durable="command-dispatcher", cb=dispatch, manual_ack=True)
    LOG.info("command target=%s:%s", *target)
    try:
        await asyncio.Future()
    finally:
        sock.close()
        await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
