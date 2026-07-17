"""Redis-backed counters rendered in Prometheus's text exposition format.

Redis lets both the gateway process and the worker process contribute to one
small teaching metric set.  PostgreSQL remains the evidence source; these
counters are operational measurements and may be rebuilt or reset.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRIC_HASH_KEY = "ground:telemetry:metrics"

COUNTERS: Mapping[str, str] = {
    "payload_messages_received_total": "Authenticated telemetry deliveries received",
    "payload_messages_rejected_total": "Authenticated telemetry deliveries rejected",
    "payload_duplicate_messages_total": "Exact duplicate telemetry deliveries",
    "payload_sequence_gaps_total": "Telemetry sequence values observed missing",
    "payload_processing_successes_total": "Telemetry records processed successfully",
    "payload_processing_failures_total": "Telemetry processing attempts that failed",
    "payload_stale_results_total": "Valid telemetry results more than 24 hours old at receipt",
    "vendor_authentication_failures_total": "Vendor requests rejected by authentication",
    "database_errors_total": "Database operations that failed",
}


class TelemetryMetrics:
    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def increment(self, name: str, amount: int = 1) -> None:
        if name not in COUNTERS:
            raise ValueError(f"unknown telemetry metric: {name}")
        await self._redis.hincrby(METRIC_HASH_KEY, name, amount)

    async def snapshot(self) -> dict[str, int]:
        raw = await self._redis.hgetall(METRIC_HASH_KEY)
        decoded: dict[str, int] = {}
        for key, value in raw.items():
            name = key.decode() if isinstance(key, bytes) else str(key)
            decoded[name] = int(value)
        return decoded


def render_prometheus(*, counters: Mapping[str, int], queue_depth: int) -> bytes:
    lines: list[str] = []
    for name, help_text in COUNTERS.items():
        lines.extend(
            (
                f"# HELP {name} {help_text}",
                f"# TYPE {name} counter",
                f"{name} {counters.get(name, 0)}",
            )
        )
    lines.extend(
        (
            "# HELP payload_processing_queue_depth References waiting for the payload worker",
            "# TYPE payload_processing_queue_depth gauge",
            f"payload_processing_queue_depth {queue_depth}",
        )
    )
    return ("\n".join(lines) + "\n").encode()
