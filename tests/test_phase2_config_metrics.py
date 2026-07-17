from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine
from typing import Any

import pytest

from mission_ground.telemetry.config import GroundSettings, VendorSettings, WorkerSettings
from mission_ground.telemetry.metrics import (
    COUNTERS,
    METRIC_HASH_KEY,
    TelemetryMetrics,
    render_prometheus,
)
from mission_ground.telemetry.structured_logging import JsonFormatter


def run[T](operation: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(operation)


def ground_settings(**changes: object) -> GroundSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://ground:password@postgres/ground",
        "redis_url": "redis://redis:6379/0",
        "vendor_token": "vendor-secret",
        "operator_token": "operator-secret",
        "admin_token": "administrator-secret",
        "max_telemetry_body_bytes": 1_024,
    }
    values.update(changes)
    return GroundSettings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("token_name", ["vendor_token", "operator_token", "admin_token"])
def test_ground_settings_rejects_empty_identity_tokens(token_name: str) -> None:
    with pytest.raises(ValueError):
        ground_settings(**{token_name: ""})


@pytest.mark.parametrize(
    "changes",
    [
        {"operator_token": "vendor-secret"},
        {"admin_token": "vendor-secret"},
        {"admin_token": "operator-secret"},
    ],
)
def test_ground_settings_requires_pairwise_distinct_identity_tokens(
    changes: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        ground_settings(**changes)


@pytest.mark.parametrize("body_cap", [0, -1, 1_025])
def test_ground_settings_rejects_body_cap_outside_evidence_limit(body_cap: int) -> None:
    with pytest.raises(ValueError):
        ground_settings(max_telemetry_body_bytes=body_cap)


@pytest.mark.parametrize("body_cap", [1, 1_024])
def test_ground_settings_accepts_body_cap_boundaries(body_cap: int) -> None:
    assert ground_settings(max_telemetry_body_bytes=body_cap).max_telemetry_body_bytes == body_cap


def test_ground_settings_requires_positive_processing_attempt_limit() -> None:
    with pytest.raises(ValueError):
        ground_settings(processing_maximum_attempts=0)


def test_ground_settings_from_environment_applies_constructor_security_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VENDOR_TOKEN", "same-secret")
    monkeypatch.setenv("OPERATOR_TOKEN", "same-secret")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")

    with pytest.raises(ValueError):
        GroundSettings.from_environment()


def test_worker_settings_requires_positive_claim_timeout() -> None:
    with pytest.raises(ValueError):
        WorkerSettings(
            database_url="postgresql+asyncpg://ground:password@postgres/ground",
            redis_url="redis://redis:6379/0",
            claim_timeout_seconds=0,
        )


@pytest.mark.parametrize("ground_url", ["postgres://ground-api", "ground-api:8080", ""])
def test_vendor_settings_requires_http_ground_url(ground_url: str) -> None:
    with pytest.raises(ValueError):
        VendorSettings(
            ground_api_url=ground_url,
            vendor_token="vendor-secret",
            boot_id_file="/tmp/vendor-boot-id",
            auto_send_interval_seconds=0,
        )


def test_vendor_settings_rejects_empty_token_and_negative_interval() -> None:
    with pytest.raises(ValueError):
        VendorSettings(
            ground_api_url="http://ground-api:8080",
            vendor_token="",
            boot_id_file="/tmp/vendor-boot-id",
            auto_send_interval_seconds=0,
        )
    with pytest.raises(ValueError):
        VendorSettings(
            ground_api_url="https://ground-api:8443",
            vendor_token="vendor-secret",
            boot_id_file="/tmp/vendor-boot-id",
            auto_send_interval_seconds=-0.1,
        )


class FakeMetricRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.increments: list[tuple[str, str, int]] = []

    async def hincrby(self, key: str, name: str, amount: int) -> None:
        self.increments.append((key, name, amount))
        self.values[name] = self.values.get(name, 0) + amount

    async def hgetall(self, key: str) -> dict[bytes | str, bytes | int]:
        assert key == METRIC_HASH_KEY
        return {
            name.encode() if index % 2 == 0 else name: str(value).encode()
            for index, (name, value) in enumerate(self.values.items())
        }


def test_telemetry_metrics_increment_and_decode_redis_snapshot() -> None:
    redis = FakeMetricRedis()
    metrics = TelemetryMetrics(redis)

    run(metrics.increment("payload_messages_received_total"))
    run(metrics.increment("payload_messages_received_total", 2))
    run(metrics.increment("payload_messages_rejected_total", 4))

    assert redis.increments == [
        (METRIC_HASH_KEY, "payload_messages_received_total", 1),
        (METRIC_HASH_KEY, "payload_messages_received_total", 2),
        (METRIC_HASH_KEY, "payload_messages_rejected_total", 4),
    ]
    assert run(metrics.snapshot()) == {
        "payload_messages_received_total": 3,
        "payload_messages_rejected_total": 4,
    }


def test_telemetry_metrics_rejects_unknown_counter_before_calling_redis() -> None:
    redis = FakeMetricRedis()
    metrics = TelemetryMetrics(redis)

    with pytest.raises(ValueError, match="unknown telemetry metric"):
        run(metrics.increment("invented_counter_total"))
    assert redis.increments == []


def test_prometheus_render_includes_every_counter_zero_defaults_and_queue_gauge() -> None:
    rendered = render_prometheus(
        counters={"payload_messages_received_total": 7},
        queue_depth=3,
    ).decode("ascii")

    for name, help_text in COUNTERS.items():
        assert f"# HELP {name} {help_text}\n" in rendered
        assert f"# TYPE {name} counter\n" in rendered
        expected = 7 if name == "payload_messages_received_total" else 0
        assert f"{name} {expected}\n" in rendered
    assert "# TYPE payload_processing_queue_depth gauge\n" in rendered
    assert "payload_processing_queue_depth 3\n" in rendered
    assert rendered.endswith("\n")


def test_json_formatter_adds_service_event_timestamp_and_structured_context() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="packet rejected",
        args=(),
        exc_info=None,
    )
    record.event_name = "telemetry.validation.rejected"
    record.message_id = "TLM-P01-B0000002A-S00000001"
    record.error_reason = "CRC_MISMATCH"

    decoded = json.loads(JsonFormatter("ground-api").format(record))

    assert decoded["service_name"] == "ground-api"
    assert decoded["level"] == "WARNING"
    assert decoded["event_name"] == "telemetry.validation.rejected"
    assert decoded["message"] == "packet rejected"
    assert decoded["message_id"] == "TLM-P01-B0000002A-S00000001"
    assert decoded["error_reason"] == "CRC_MISMATCH"
    assert decoded["timestamp"].endswith("Z")
