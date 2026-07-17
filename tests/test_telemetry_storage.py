from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import func, select

from mission_ground.telemetry.storage import (
    DecodedTelemetryValues,
    DeliveryQueueStatus,
    DeliveryValidationStatus,
    HealthClassification,
    LogicalTelemetryPacket,
    ProcessingJobState,
    ProcessingResultValues,
    SequenceDisposition,
    TelemetryDeliveryReceipt,
    TelemetryProcessingJob,
    TelemetryRepository,
    TelemetrySequenceState,
    create_engine_and_session_factory,
    create_schema,
    utc_now,
)

GOLDEN_TELEMETRY = bytes.fromhex(
    "01010000002a00000001000656b92df170000b186dc40200cc6d0205"
)


def _values(*, boot_id: int = 42, sequence_number: int = 1) -> DecodedTelemetryValues:
    return DecodedTelemetryValues(
        protocol_version=1,
        payload_id=1,
        boot_id=boot_id,
        sequence_number=sequence_number,
        sample_timestamp_us=1_784_203_200_000_000,
        temperature_centi_c=2_840,
        voltage_mv=28_100,
        operating_mode=2,
        fault_flags=0,
    )


def _raw_for_sequence(sequence_number: int) -> bytes:
    raw = bytearray(GOLDEN_TELEMETRY)
    raw[6:10] = sequence_number.to_bytes(4, "big")
    # Persistence tests do not decode or validate the checksum; their concern
    # is preserving these exact bytes and enforcing database identities.
    return bytes(raw)


async def _new_database():
    engine, sessions = create_engine_and_session_factory("sqlite+aiosqlite:///:memory:")
    await create_schema(engine)
    return engine, sessions


def test_request_id_replay_is_idempotent_and_conflict_preserves_evidence() -> None:
    async def scenario() -> None:
        engine, sessions = await _new_database()
        try:
            request_id = uuid.uuid4()
            async with sessions() as session:
                repository = TelemetryRepository(session)
                first = await repository.record_delivery(
                    raw_body=GOLDEN_TELEMETRY,
                    peer_certificate_identity="spiffe://demo/vendor-simulator",
                    presented_request_id=str(request_id),
                    canonical_request_id=request_id,
                    content_type="application/octet-stream",
                )
                replay = await repository.record_delivery(
                    raw_body=GOLDEN_TELEMETRY,
                    peer_certificate_identity="spiffe://demo/vendor-simulator",
                    presented_request_id=str(request_id),
                    canonical_request_id=request_id,
                    content_type="application/octet-stream",
                )
                altered = GOLDEN_TELEMETRY[:-1] + bytes([GOLDEN_TELEMETRY[-1] ^ 1])
                conflict = await repository.record_delivery(
                    raw_body=altered,
                    peer_certificate_identity="spiffe://demo/vendor-simulator",
                    presented_request_id=str(request_id),
                    canonical_request_id=request_id,
                    content_type="application/octet-stream",
                )

                assert replay.receipt_id == first.receipt_id
                assert replay.request_replay is True
                assert conflict.receipt_id != first.receipt_id
                assert conflict.request_conflict is True
                assert conflict.validation_status is DeliveryValidationStatus.CONFLICT
                stored_conflict = await repository.get_delivery(conflict.receipt_id)
                assert stored_conflict is not None
                assert stored_conflict.raw_body == altered
                assert stored_conflict.duplicate_of_receipt_id == first.receipt_id

            async with sessions() as session:
                receipt_count = await session.scalar(
                    select(func.count(TelemetryDeliveryReceipt.receipt_id))
                )
                assert receipt_count == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unique_packet_duplicate_and_gap_update_persistent_sequence_state() -> None:
    async def scenario() -> None:
        engine, sessions = await _new_database()
        try:
            async with sessions() as session:
                repository = TelemetryRepository(session)
                first_delivery = await repository.record_delivery(
                    raw_body=_raw_for_sequence(10),
                    peer_certificate_identity="vendor",
                    presented_request_id=str(uuid.uuid4()),
                    canonical_request_id=uuid.uuid4(),
                    content_type="application/octet-stream",
                )
                first = await repository.accept_packet_and_create_job(
                    first_delivery.receipt_id, _values(sequence_number=10)
                )
                assert first.sequence_disposition is SequenceDisposition.FIRST
                assert first.missing_before == 10
                assert first.queue_status is DeliveryQueueStatus.PENDING_QUEUE

                gap_delivery = await repository.record_delivery(
                    raw_body=_raw_for_sequence(13),
                    peer_certificate_identity="vendor",
                    presented_request_id=str(uuid.uuid4()),
                    canonical_request_id=uuid.uuid4(),
                    content_type="application/octet-stream",
                )
                gap = await repository.accept_packet_and_create_job(
                    gap_delivery.receipt_id, _values(sequence_number=13)
                )
                assert gap.sequence_disposition is SequenceDisposition.GAP
                assert gap.missing_before == 2

                duplicate_delivery = await repository.record_delivery(
                    raw_body=_raw_for_sequence(13),
                    peer_certificate_identity="vendor",
                    presented_request_id=str(uuid.uuid4()),
                    canonical_request_id=uuid.uuid4(),
                    content_type="application/octet-stream",
                )
                duplicate = await repository.accept_packet_and_create_job(
                    duplicate_delivery.receipt_id, _values(sequence_number=13)
                )
                assert duplicate.duplicate is True
                assert duplicate.job_id == gap.job_id
                assert duplicate.duplicate_of_receipt_id == gap_delivery.receipt_id

            async with sessions() as session:
                packet_count = await session.scalar(
                    select(func.count(LogicalTelemetryPacket.packet_id))
                )
                job_count = await session.scalar(
                    select(func.count(TelemetryProcessingJob.job_id))
                )
                state = await session.get(TelemetrySequenceState, (1, 42))
                assert packet_count == 2
                assert job_count == 2
                assert state is not None
                assert state.high_water_sequence == 13
                assert state.accepted_packet_count == 2
                assert state.missing_packet_count == 12
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rejected_delivery_never_creates_sequence_or_processing_work() -> None:
    async def scenario() -> None:
        engine, sessions = await _new_database()
        try:
            async with sessions() as session:
                repository = TelemetryRepository(session)
                delivery = await repository.record_delivery(
                    raw_body=b"",
                    peer_certificate_identity="vendor",
                    presented_request_id=None,
                    canonical_request_id=None,
                    content_type="application/octet-stream",
                )
                rejected = await repository.reject_delivery(
                    delivery.receipt_id, reason="EMPTY_BODY"
                )
                assert rejected.validation_status is DeliveryValidationStatus.REJECTED
                assert await repository.processing_queue_depth() == 0

            async with sessions() as session:
                sequence_count = await session.scalar(
                    select(func.count()).select_from(TelemetrySequenceState)
                )
                job_count = await session.scalar(
                    select(func.count()).select_from(TelemetryProcessingJob)
                )
                assert sequence_count == 0
                assert job_count == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_worker_claim_and_completion_are_idempotent() -> None:
    async def scenario() -> None:
        engine, sessions = await _new_database()
        try:
            async with sessions() as session:
                repository = TelemetryRepository(session)
                delivery = await repository.record_delivery(
                    raw_body=GOLDEN_TELEMETRY,
                    peer_certificate_identity="vendor",
                    presented_request_id=str(uuid.uuid4()),
                    canonical_request_id=uuid.uuid4(),
                    content_type="application/octet-stream",
                )
                accepted = await repository.accept_packet_and_create_job(
                    delivery.receipt_id, _values()
                )
                assert accepted.job_id is not None
                assert await repository.mark_job_queued(
                    accepted.job_id, queue_message_id=str(accepted.job_id)
                )
                assert not await repository.mark_job_queued(
                    accepted.job_id, queue_message_id=str(accepted.job_id)
                )
                work = await repository.claim_processing_job(
                    accepted.job_id, worker_id="worker-1"
                )
                assert work is not None
                assert work.raw_body == GOLDEN_TELEMETRY
                assert work.received_at.tzinfo is not None
                assert work.attempt_count == 1

                result = ProcessingResultValues(
                    temperature_c=28.4,
                    voltage_v=28.1,
                    operating_mode="SCIENCE",
                    fault_names=[],
                    classification=HealthClassification.NOMINAL,
                    is_stale=False,
                )
                stored = await repository.complete_processing_job(
                    accepted.job_id, result, worker_id="worker-1"
                )
                replay = await repository.complete_processing_job(
                    accepted.job_id, result, worker_id="worker-1"
                )
                assert replay.packet_id == stored.packet_id
                assert await repository.claim_processing_job(
                    accepted.job_id, worker_id="worker-2"
                ) is None
                assert await repository.processing_queue_depth() == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_temporary_failures_back_off_then_reach_failed_work_state() -> None:
    async def scenario() -> None:
        engine, sessions = await _new_database()
        try:
            async with sessions() as session:
                repository = TelemetryRepository(session)
                delivery = await repository.record_delivery(
                    raw_body=GOLDEN_TELEMETRY,
                    peer_certificate_identity="vendor",
                    presented_request_id=str(uuid.uuid4()),
                    canonical_request_id=uuid.uuid4(),
                    content_type="application/octet-stream",
                )
                accepted = await repository.accept_packet_and_create_job(
                    delivery.receipt_id, _values(), max_attempts=2
                )
                assert accepted.job_id is not None
                await repository.mark_job_queued(
                    accepted.job_id, queue_message_id="attempt-1"
                )
                assert await repository.claim_processing_job(
                    accepted.job_id, worker_id="worker-1"
                )
                failure_time = utc_now() - timedelta(seconds=5)
                retry = await repository.fail_processing_attempt(
                    accepted.job_id,
                    worker_id="worker-1",
                    reason="temporary database disconnect",
                    temporary=True,
                    base_backoff_seconds=1,
                    now=failure_time,
                )
                assert retry.state is ProcessingJobState.RETRY_WAIT
                assert await repository.pending_queue_jobs() == [accepted.job_id]

                await repository.mark_job_queued(
                    accepted.job_id, queue_message_id="attempt-2"
                )
                second = await repository.claim_processing_job(
                    accepted.job_id, worker_id="worker-1"
                )
                assert second is not None
                assert second.attempt_count == 2
                failed = await repository.fail_processing_attempt(
                    accepted.job_id,
                    worker_id="worker-1",
                    reason="database still unavailable",
                    temporary=True,
                )
                assert failed.state is ProcessingJobState.FAILED
                assert failed.next_attempt_at is None
                assert await repository.processing_queue_depth() == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recoverable_jobs_can_rebuild_a_lost_redis_reference() -> None:
    async def scenario() -> None:
        engine, sessions = await _new_database()
        try:
            async with sessions() as session:
                repository = TelemetryRepository(session)
                delivery = await repository.record_delivery(
                    raw_body=GOLDEN_TELEMETRY,
                    peer_certificate_identity="vendor",
                    presented_request_id=str(uuid.uuid4()),
                    canonical_request_id=uuid.uuid4(),
                    content_type="application/octet-stream",
                )
                accepted = await repository.accept_packet_and_create_job(
                    delivery.receipt_id, _values()
                )
                assert accepted.job_id is not None
                await repository.mark_job_queued(
                    accepted.job_id, queue_message_id=str(accepted.job_id)
                )

                assert await repository.pending_queue_jobs() == []
                assert await repository.recoverable_queue_jobs() == [accepted.job_id]

                assert await repository.claim_processing_job(
                    accepted.job_id, worker_id="worker-1"
                )
                assert await repository.recoverable_queue_jobs() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_standalone_security_audit_event_is_persistent() -> None:
    async def scenario() -> None:
        engine, sessions = await _new_database()
        try:
            async with sessions() as session:
                repository = TelemetryRepository(session)
                recorded = await repository.record_audit_event(
                    service_name="ground-api",
                    event_name="telemetry.delivery.security_rejected",
                    actor_identity="local-token:vendor-simulator",
                    outcome="REJECTED",
                    reason="BODY_TOO_LARGE",
                    event_data={"configured_limit": 1_024},
                )
                events = await repository.list_audit_events()

                assert [event.event_id for event in events] == [recorded.event_id]
                assert events[0].reason == "BODY_TOO_LARGE"
                assert events[0].event_data == {"configured_limit": 1_024}
        finally:
            await engine.dispose()

    asyncio.run(scenario())
