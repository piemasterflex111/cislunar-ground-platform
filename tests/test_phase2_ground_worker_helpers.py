from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("redis")
pytest.importorskip("sqlalchemy")

from fastapi import HTTPException

from mission_ground.services.ground_api import (
    _bearer_identity,
    _bounded_body,
    _canonical_uuid4,
    _iso_from_us,
    _queue_one,
    _telemetry_values,
)
from mission_ground.services.payload_worker import (
    _fault_names,
    _return_or_finish_unclaimed,
    _stored_values,
)
from mission_ground.telemetry.codec import FaultFlag, OperatingMode, TelemetryFrame
from mission_ground.telemetry.config import GroundSettings
from mission_ground.telemetry.queue import QueueMessage
from mission_ground.telemetry.storage import ProcessingJobState


def run[T](operation: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(operation)


def settings() -> GroundSettings:
    return GroundSettings(
        database_url="postgresql+asyncpg://ground:password@postgres/ground",
        redis_url="redis://redis:6379/0",
        vendor_token="vendor-secret",
        operator_token="operator-secret",
        admin_token="administrator-secret",
    )


def frame() -> TelemetryFrame:
    return TelemetryFrame(
        payload_id=1,
        boot_id=42,
        sequence_number=1,
        timestamp_us=1_784_203_200_000_000,
        temperature_centi_c=2_840,
        voltage_mv=28_100,
        operating_mode=OperatingMode.SCIENCE,
        fault_flags=FaultFlag.UNDER_VOLTAGE | FaultFlag.INTERNAL_PAYLOAD_FAULT,
    )


@pytest.mark.parametrize("presented", [None, "not-a-uuid", "5B0EF52E-1517-4B70-8E9D-F49EF2F72D8D"])
def test_canonical_uuid4_rejects_missing_malformed_and_noncanonical_text(
    presented: str | None,
) -> None:
    assert _canonical_uuid4(presented) is None


def test_canonical_uuid4_accepts_only_canonical_version_four() -> None:
    version_four = "5b0ef52e-1517-4b70-8e9d-f49ef2f72d8d"
    version_one = str(uuid.uuid1())

    assert _canonical_uuid4(version_four) == uuid.UUID(version_four)
    assert _canonical_uuid4(version_one) is None


def test_wire_timestamp_and_frame_mapping_are_exact() -> None:
    values = _telemetry_values(frame())

    assert _iso_from_us(frame().timestamp_us) == "2026-07-16T12:00:00Z"
    assert values.packet_id == "TLM-P01-B0000002A-S00000001"
    assert values.sample_timestamp_us == frame().timestamp_us
    assert values.operating_mode == int(OperatingMode.SCIENCE)
    assert values.fault_flags == int(frame().fault_flags)


def test_bearer_identity_distinguishes_operator_administrator_and_invalid_callers() -> None:
    configured = settings()

    assert _bearer_identity("Bearer operator-secret", configured) == "operator"
    assert _bearer_identity("Bearer administrator-secret", configured) == "administrator"
    for authorization in (None, "operator-secret", "Bearer wrong-secret"):
        with pytest.raises(HTTPException) as raised:
            _bearer_identity(authorization, configured)
        assert raised.value.status_code == 401


class StreamingRequest:
    def __init__(self, chunks: list[bytes], *, content_length: str | None = None) -> None:
        self.headers = {}
        if content_length is not None:
            self.headers["content-length"] = content_length
        self._chunks = chunks

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def test_bounded_body_preserves_chunks_exactly_at_limit() -> None:
    request = StreamingRequest([b"abc", b"def"], content_length="6")
    assert run(_bounded_body(request, 6)) == b"abcdef"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("streaming_request", "maximum", "status_code"),
    [
        (StreamingRequest([], content_length="7"), 6, 413),
        (StreamingRequest([b"abcd", b"efg"]), 6, 413),
        (StreamingRequest([], content_length="not-an-integer"), 6, 400),
    ],
)
def test_bounded_body_rejects_declared_streamed_and_malformed_lengths(
    streaming_request: StreamingRequest,
    maximum: int,
    status_code: int,
) -> None:
    with pytest.raises(HTTPException) as raised:
        run(_bounded_body(streaming_request, maximum))  # type: ignore[arg-type]
    assert raised.value.status_code == status_code


class RecordingQueue:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.messages: list[QueueMessage] = []

    async def enqueue(self, message: QueueMessage) -> bool:
        self.events.append("redis-enqueue")
        self.messages.append(message)
        return True


class RecordingRepository:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def mark_job_queued(self, job_id: uuid.UUID, *, queue_message_id: str) -> bool:
        self.events.append("database-mark")
        self.calls.append((job_id, queue_message_id))
        return True


def test_queue_one_uses_only_identifiers_and_marks_database_after_redis_acceptance() -> None:
    events: list[str] = []
    queue = RecordingQueue(events)
    repository = RecordingRepository(events)
    job_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    packet_id = "TLM-P01-B0000002A-S00000001"

    assert run(
        _queue_one(
            repository,  # type: ignore[arg-type]
            queue,  # type: ignore[arg-type]
            job_id=job_id,
            packet_id=packet_id,
        )
    )
    assert events == ["redis-enqueue", "database-mark"]
    assert queue.messages == [QueueMessage(job_id=str(job_id), packet_id=packet_id)]
    assert repository.calls == [(job_id, str(job_id))]


def test_worker_stored_values_match_gateway_mapping_and_fault_names_are_stable() -> None:
    assert _stored_values(frame()) == _telemetry_values(frame())
    assert _fault_names(frame().fault_flags) == ["UNDER_VOLTAGE", "INTERNAL_PAYLOAD_FAULT"]
    assert _fault_names(FaultFlag.NONE) == []


class ResolutionQueue:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def ack(self, message: QueueMessage) -> None:
        self.actions.append(f"ack:{message.job_id}")

    async def dead_letter(self, message: QueueMessage) -> None:
        self.actions.append(f"dead:{message.job_id}")

    async def retry(self, message: QueueMessage) -> None:
        self.actions.append(f"retry:{message.job_id}")


class JobRepository:
    def __init__(self, state: ProcessingJobState | None) -> None:
        self.state = state

    async def get_job(self, job_id: uuid.UUID) -> Any:
        del job_id
        return None if self.state is None else SimpleNamespace(state=self.state)


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (None, "ack"),
        (ProcessingJobState.COMPLETE, "ack"),
        (ProcessingJobState.FAILED, "dead"),
        (ProcessingJobState.QUEUED, "retry"),
    ],
)
def test_unclaimed_work_is_resolved_from_durable_database_state(
    state: ProcessingJobState | None,
    action: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_wait(delay: float) -> None:
        del delay

    monkeypatch.setattr("mission_ground.services.payload_worker.asyncio.sleep", no_wait)
    queue = ResolutionQueue()
    item = QueueMessage(
        job_id="00000000-0000-0000-0000-000000000001",
        packet_id="TLM-P01-B0000002A-S00000001",
    )

    run(
        _return_or_finish_unclaimed(
            queue,  # type: ignore[arg-type]
            item,
            JobRepository(state),  # type: ignore[arg-type]
        )
    )

    assert queue.actions == [f"{action}:{item.job_id}"]
