from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from prometheus_client import Counter, make_asgi_app

from mission_ground.common.commands import CommandValidationError, validate_command
from mission_ground.common.config import env
from mission_ground.common.db import create_pool
from mission_ground.common.nats import connect_nats
from mission_ground.common.retry import retry_async

COMMAND_REQUESTS = Counter("mission_api_command_requests_total", "Command requests", ["result", "dry_run"])


class CommandRequest(BaseModel):
    name: str = Field(examples=["SET_WHEEL_RPM"])
    argument: int = Field(examples=[1200])
    dry_run: bool = True
    operator_id: str = Field(default="local-operator", min_length=1, max_length=80)


@asynccontextmanager
async def lifespan(app: FastAPI):
    dsn = env("DATABASE_URL", "postgresql://ground:ground-local-password@postgres:5432/ground")
    app.state.pool = await retry_async(lambda: create_pool(dsn))
    app.state.nc, app.state.js = await connect_nats(env("NATS_URL", "nats://nats:4222"), "mission-api")
    yield
    await app.state.pool.close()
    await app.state.nc.drain()


app = FastAPI(title="Cislunar Mission API", version="0.1.0", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    async with app.state.pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    if not app.state.nc.is_connected:
        raise HTTPException(503, "NATS disconnected")
    return {"status": "ready"}


@app.get("/telemetry/latest")
async def latest_telemetry(limit: int = Query(20, ge=1, le=500)):
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT received_at, spacecraft_time_ns, sequence_count, bus_voltage_v,
            battery_temp_c, wheel_rpm, mode, fault_flags
            FROM telemetry_health ORDER BY received_at DESC LIMIT $1""",
            limit,
        )
    return [dict(row) for row in rows]


@app.get("/alerts")
async def alerts(limit: int = Query(50, ge=1, le=500)):
    async with app.state.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [dict(row) for row in rows]


@app.get("/commands/{command_id}")
async def command_status(command_id: int):
    async with app.state.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM commands WHERE id=$1", command_id)
    if row is None:
        raise HTTPException(404, "command not found")
    return dict(row)


@app.post("/commands", status_code=202)
async def create_command(
    request: CommandRequest,
    x_operator_token: str | None = Header(default=None),
    x_command_arm: str | None = Header(default=None),
):
    try:
        definition = validate_command(request.name, request.argument)
    except CommandValidationError as exc:
        COMMAND_REQUESTS.labels(result="invalid", dry_run=str(request.dry_run).lower()).inc()
        raise HTTPException(422, str(exc)) from exc

    if not request.dry_run:
        if x_operator_token != env("OPERATOR_TOKEN", "local-operator-token"):
            COMMAND_REQUESTS.labels(result="unauthorized", dry_run="false").inc()
            raise HTTPException(401, "invalid operator token")
        if x_command_arm != env("COMMAND_ARM_TOKEN", "local-arm-token"):
            COMMAND_REQUESTS.labels(result="unarmed", dry_run="false").inc()
            raise HTTPException(409, "command path is not armed")

    status = "DRY_RUN_VALIDATED" if request.dry_run else "QUEUED"
    async with app.state.pool.acquire() as conn:
        command_id = await conn.fetchval(
            """INSERT INTO commands(name,argument,dry_run,status,operator_id)
            VALUES ($1,$2,$3,$4,$5) RETURNING id""",
            request.name,
            request.argument,
            request.dry_run,
            status,
            request.operator_id,
        )
        await conn.execute(
            """INSERT INTO audit_events(actor,action,object_type,object_id,detail)
            VALUES ($1,'COMMAND_CREATED','command',$2,$3::jsonb)""",
            request.operator_id,
            str(command_id),
            request.model_dump_json(),
        )

    if not request.dry_run:
        event = {
            "command_id": command_id,
            "name": request.name,
            "opcode": definition.opcode,
            "argument": request.argument,
            "operator_id": request.operator_id,
        }
        await app.state.js.publish("commands.dispatch", json.dumps(event, separators=(",", ":")).encode())

    COMMAND_REQUESTS.labels(result="accepted", dry_run=str(request.dry_run).lower()).inc()
    return {"command_id": command_id, "status": status, "dry_run": request.dry_run}


@app.websocket("/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)

    async def callback(msg) -> None:
        event = {"subject": msg.subject, "payload": json.loads(msg.data)}
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)

    subscriptions = [
        await app.state.nc.subscribe("telemetry.processed", cb=callback),
        await app.state.nc.subscribe("alerts.>", cb=callback),
        await app.state.nc.subscribe("advisories.>", cb=callback),
    ]
    try:
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        for sub in subscriptions:
            await sub.unsubscribe()
