from __future__ import annotations

import nats

from .retry import retry_async


async def connect_nats(url: str, name: str):
    async def operation():
        return await nats.connect(url, name=name, connect_timeout=2, max_reconnect_attempts=-1)

    nc = await retry_async(operation)
    js = nc.jetstream()
    try:
        await js.stream_info("GROUND")
    except Exception:
        try:
            await js.add_stream(
                name="GROUND",
                subjects=["telemetry.>", "alerts.>", "commands.>", "advisories.>"],
            )
        except Exception:
            # Another service may have created the stream concurrently.
            await js.stream_info("GROUND")
    return nc, js
