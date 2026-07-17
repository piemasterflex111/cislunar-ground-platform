from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
from prometheus_client import Counter, start_http_server

from mission_ground.common.config import env, env_int
from mission_ground.common.nats import connect_nats

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s ai-advisor %(message)s")
LOG = logging.getLogger(__name__)

ADVISORIES = Counter("ai_advisor_requests_total", "AI advisory outcomes", ["result"])

SYSTEM = """You are an advisory-only spacecraft operations assistant. Summarize the supplied alert,
identify the deterministic checks an operator should perform, and explicitly state that you cannot
approve or transmit commands. Never invent telemetry or claim a fault is resolved."""


async def main() -> None:
    start_http_server(env_int("METRICS_PORT", 9105))
    nc, js = await connect_nats(env("NATS_URL", "nats://nats:4222"), "ai-advisor")
    base_url = env("VLLM_BASE_URL", "http://host.docker.internal:8000/v1").rstrip("/")
    model = env("VLLM_MODEL", "qwen3.6-27b-nvfp4-mtp")
    client = httpx.AsyncClient(timeout=20)

    async def advise(msg) -> None:
        try:
            alert = json.loads(msg.data)
            response = await client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 220,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": json.dumps(alert, sort_keys=True)},
                    ],
                },
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            advisory = {"source_alert": alert, "model": model, "advisory": text}
            await js.publish("advisories.alert", json.dumps(advisory, separators=(",", ":")).encode())
            ADVISORIES.labels(result="ok").inc()
            await msg.ack()
        except Exception:
            ADVISORIES.labels(result="error").inc()
            LOG.exception("advisory generation failed")
            await msg.nak(delay=10)

    await js.subscribe("alerts.>", durable="ai-advisor", cb=advise, manual_ack=True)
    LOG.info("advisory-only service active model=%s", model)
    try:
        await asyncio.Future()
    finally:
        await client.aclose()
        await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
