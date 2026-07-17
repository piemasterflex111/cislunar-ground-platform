from __future__ import annotations

import asyncpg

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS telemetry_health (
    id BIGSERIAL PRIMARY KEY,
    spacecraft_time_ns BIGINT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sequence_count INTEGER NOT NULL,
    bus_voltage_v DOUBLE PRECISION NOT NULL,
    battery_temp_c DOUBLE PRECISION NOT NULL,
    wheel_rpm INTEGER NOT NULL,
    mode INTEGER NOT NULL,
    fault_flags INTEGER NOT NULL,
    raw JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS telemetry_health_received_idx ON telemetry_health(received_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    parameter TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    telemetry_sequence INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_created_idx ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS commands (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    name TEXT NOT NULL,
    argument INTEGER NOT NULL,
    dry_run BOOLEAN NOT NULL,
    status TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    status_detail TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    detail JSONB NOT NULL
);
"""


async def create_pool(dsn: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, command_timeout=10)
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    return pool
