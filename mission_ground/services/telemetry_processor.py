from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os

from prometheus_client import Counter, Gauge, start_http_server

from mission_ground.common.config import env, env_int
from mission_ground.common.db import create_pool
from mission_ground.common.limits import evaluate_health
from mission_ground.common.nats import connect_nats
from mission_ground.common.packet import HealthTelemetry
from mission_ground.common.retry import retry_async

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s processor %(message)s")
LOG = logging.getLogger(__name__)

PROCESSED = Counter("processor_messages_total", "Messages processed", ["subject", "result"])
ALERTS = Counter("processor_alerts_total", "Limit alerts emitted", ["severity", "parameter"])
LAST_VOLTAGE = Gauge("processor_latest_bus_voltage_volts", "Latest processed bus voltage")
LAST_TEMP = Gauge("processor_latest_battery_temperature_celsius", "Latest processed battery temperature")


async def main() -> None:
    start_http_server(env_int("METRICS_PORT", 9103))
    dsn = env("DATABASE_URL", "postgresql://ground:ground-local-password@postgres:5432/ground")
    pool = await retry_async(lambda: create_pool(dsn))
    nc, js = await connect_nats(env("NATS_URL", "nats://nats:4222"), "telemetry-processor")

    async def process_health(msg) -> None:
        try:
            envelope = json.loads(msg.data)
            data = envelope["data"]
            sample = HealthTelemetry(
                timestamp_ns=int(data["timestamp_ns"]),
                bus_voltage_v=float(data["bus_voltage_v"]),
                battery_temp_c=float(data["battery_temp_c"]),
                wheel_rpm=int(data["wheel_rpm"]),
                mode=int(data["mode"]),
                fault_flags=int(data["fault_flags"]),
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO telemetry_health
                    (spacecraft_time_ns, sequence_count, bus_voltage_v, battery_temp_c,
                     wheel_rpm, mode, fault_flags, raw)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)""",
                    sample.timestamp_ns,
                    int(envelope["sequence_count"]),
                    sample.bus_voltage_v,
                    sample.battery_temp_c,
                    sample.wheel_rpm,
                    sample.mode,
                    sample.fault_flags,
                    json.dumps(envelope),
                )
            LAST_VOLTAGE.set(sample.bus_voltage_v)
            LAST_TEMP.set(sample.battery_temp_c)

            violations = evaluate_health(sample)
            for violation in violations:
                alert = {
                    "parameter": violation.parameter,
                    "value": violation.value,
                    "severity": violation.severity,
                    "message": violation.message,
                    "telemetry_sequence": int(envelope["sequence_count"]),
                }
                async with pool.acquire() as conn:
                    await conn.execute(
                        """INSERT INTO alerts(parameter,value,severity,message,telemetry_sequence)
                        VALUES ($1,$2,$3,$4,$5)""",
                        violation.parameter,
                        float(violation.value),
                        violation.severity,
                        violation.message,
                        int(envelope["sequence_count"]),
                    )
                await js.publish("alerts.limit", json.dumps(alert, separators=(",", ":")).encode())
                ALERTS.labels(severity=violation.severity, parameter=violation.parameter).inc()

            processed = {**envelope, "violations": [dataclasses.asdict(v) for v in violations]}
            await js.publish("telemetry.processed", json.dumps(processed, separators=(",", ":")).encode())
            PROCESSED.labels(subject="health", result="ok").inc()
            await msg.ack()
        except Exception:
            PROCESSED.labels(subject="health", result="error").inc()
            LOG.exception("health processing failed")
            await msg.nak(delay=1)

    async def process_ack(msg) -> None:
        try:
            envelope = json.loads(msg.data)
            data = envelope["data"]
            command_id = int(data["command_id"])
            status = "ACKED" if int(data["status"]) == 0 else "REJECTED"
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE commands SET status=$1, status_detail=$2, updated_at=now() WHERE id=$3",
                    status,
                    f"spacecraft applied_value={int(data['applied_value'])}",
                    command_id,
                )
                await conn.execute(
                    """INSERT INTO audit_events(actor,action,object_type,object_id,detail)
                    VALUES ('spacecraft','COMMAND_ACK','command',$1,$2::jsonb)""",
                    str(command_id),
                    json.dumps(envelope),
                )
            if result.endswith("0"):
                LOG.warning("ACK received for unknown command id=%s", command_id)
            PROCESSED.labels(subject="command_ack", result="ok").inc()
            await msg.ack()
        except Exception:
            PROCESSED.labels(subject="command_ack", result="error").inc()
            LOG.exception("ACK processing failed")
            await msg.nak(delay=1)

    async def process_status(msg) -> None:
        try:
            event = json.loads(msg.data)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE commands SET status=$1, status_detail=$2, updated_at=now() WHERE id=$3",
                    event["status"],
                    event.get("detail"),
                    int(event["command_id"]),
                )
                await conn.execute(
                    """INSERT INTO audit_events(actor,action,object_type,object_id,detail)
                    VALUES ('dispatcher','COMMAND_STATUS','command',$1,$2::jsonb)""",
                    str(event["command_id"]),
                    json.dumps(event),
                )
            PROCESSED.labels(subject="command_status", result="ok").inc()
            await msg.ack()
        except Exception:
            PROCESSED.labels(subject="command_status", result="error").inc()
            LOG.exception("command status processing failed")
            await msg.nak(delay=1)

    await js.subscribe("telemetry.health", durable="processor-health", cb=process_health, manual_ack=True)
    await js.subscribe("telemetry.command_ack", durable="processor-ack", cb=process_ack, manual_ack=True)
    await js.subscribe("commands.status", durable="processor-command-status", cb=process_status, manual_ack=True)
    LOG.info("processor subscriptions active")
    try:
        await asyncio.Future()
    finally:
        await pool.close()
        await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
