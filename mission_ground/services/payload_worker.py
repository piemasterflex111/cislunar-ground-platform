"""Phase 2 worker that turns saved raw telemetry into engineering results."""

from __future__ import annotations

import argparse
import asyncio
import math
import socket
import uuid
from contextlib import suppress
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from mission_ground.telemetry.codec import (
    FaultFlag,
    TelemetryFrame,
    TelemetryValidationError,
    decode_telemetry,
)
from mission_ground.telemetry.config import WorkerSettings
from mission_ground.telemetry.health import assess_health
from mission_ground.telemetry.metrics import TelemetryMetrics
from mission_ground.telemetry.queue import (
    QueueMessage,
    QueueMessageError,
    QueueStateError,
    TelemetryQueue,
)
from mission_ground.telemetry.storage import (
    DecodedTelemetryValues,
    HealthClassification,
    ProcessingJobState,
    ProcessingResultValues,
    TelemetryRepository,
    create_engine_and_session_factory,
)
from mission_ground.telemetry.structured_logging import configure_logging

LOG = configure_logging("payload-worker")


def _stored_values(frame: TelemetryFrame) -> DecodedTelemetryValues:
    return DecodedTelemetryValues(
        protocol_version=frame.protocol_version,
        payload_id=frame.payload_id,
        boot_id=frame.boot_id,
        sequence_number=frame.sequence_number,
        sample_timestamp_us=frame.timestamp_us,
        temperature_centi_c=frame.temperature_centi_c,
        voltage_mv=frame.voltage_mv,
        operating_mode=int(frame.operating_mode),
        fault_flags=int(frame.fault_flags),
    )


def _fault_names(flags: FaultFlag) -> list[str]:
    return [
        str(flag.name)
        for flag in FaultFlag
        if flag is not FaultFlag.NONE and flags & flag
    ]


async def _increment_safely(metrics: TelemetryMetrics, name: str) -> None:
    try:
        await metrics.increment(name)
    except Exception:
        LOG.exception(
            "metric increment failed",
            extra={"event_name": "metric.increment.failed", "metric_name": name},
        )


async def _return_or_finish_unclaimed(
    queue: TelemetryQueue,
    message: QueueMessage,
    repository: TelemetryRepository,
) -> None:
    """Resolve a queue reference whose database job could not be claimed yet."""

    job = await repository.get_job(uuid.UUID(message.job_id))
    if job is None or job.state is ProcessingJobState.COMPLETE:
        await queue.ack(message)
        return
    if job.state is ProcessingJobState.FAILED:
        await queue.dead_letter(message)
        return
    # A recovered process may encounter the old worker's unexpired lease or a
    # retry whose backoff time has not arrived.  Keep the reference recoverable.
    await asyncio.sleep(1)
    await queue.retry(message)


async def run_worker(settings: WorkerSettings) -> None:
    engine, sessions = create_engine_and_session_factory(settings.database_url)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
    queue = TelemetryQueue(redis_client)
    metrics = TelemetryMetrics(redis_client)
    worker_id = f"{socket.gethostname()}:{uuid.uuid4()}"
    recovered = await queue.recover_processing()
    LOG.info(
        "payload worker started",
        extra={
            "event_name": "worker.started",
            "worker_id": worker_id,
            "recovered_jobs": recovered,
        },
    )
    try:
        while True:
            try:
                message = await queue.claim(
                    timeout_seconds=settings.claim_timeout_seconds
                )
            except QueueMessageError as exc:
                LOG.error(
                    "malformed queue entry moved to failed work",
                    extra={
                        "event_name": "telemetry.queue.poison_quarantined",
                        "error_reason": str(exc),
                    },
                )
                continue
            except RedisError as exc:
                LOG.warning(
                    "Redis unavailable while claiming work",
                    extra={
                        "event_name": "telemetry.queue.unavailable",
                        "error_reason": type(exc).__name__,
                    },
                )
                await asyncio.sleep(1)
                continue
            if message is None:
                continue
            work = None
            try:
                async with sessions() as session:
                    repository = TelemetryRepository(session)
                    work = await repository.claim_processing_job(
                        uuid.UUID(message.job_id),
                        worker_id=worker_id,
                    )
                    if work is None:
                        await _return_or_finish_unclaimed(queue, message, repository)
                        continue

                # Decode the authoritative database bytes again.  The queue did
                # not carry telemetry and therefore cannot replace this check.
                frame = decode_telemetry(work.raw_body, ground_received_at=work.received_at)
                if _stored_values(frame) != work.values:
                    raise ValueError("stored decoded fields do not match the preserved raw bytes")
                assessment = assess_health(frame, ground_received_at=work.received_at)
                result = ProcessingResultValues(
                    temperature_c=frame.temperature_c,
                    voltage_v=frame.voltage_v,
                    operating_mode=frame.operating_mode.name,
                    fault_names=_fault_names(frame.fault_flags),
                    classification=HealthClassification(assessment.classification.value),
                    is_stale=assessment.is_stale,
                )
                async with sessions() as session:
                    repository = TelemetryRepository(session)
                    await repository.complete_processing_job(
                        work.job_id,
                        result,
                        worker_id=worker_id,
                    )
                # Database completion is committed before the queue reference
                # disappears.  A crash between the two is an idempotent replay.
                try:
                    await queue.ack(message)
                except Exception as exc:
                    LOG.warning(
                        "processed result committed but queue acknowledgement failed",
                        extra={
                            "event_name": "telemetry.queue.ack_failed",
                            "message_id": work.packet_id,
                            "job_id": str(work.job_id),
                            "error_reason": type(exc).__name__,
                        },
                    )
                    # The database already says COMPLETE.  Do not rewrite that
                    # success as a processing failure.  The processing-list
                    # reference remains recoverable after Redis returns.
                    continue
                await _increment_safely(metrics, "payload_processing_successes_total")
                if assessment.is_stale:
                    await _increment_safely(metrics, "payload_stale_results_total")
                LOG.info(
                    "telemetry processing completed",
                    extra={
                        "event_name": "telemetry.processing.completed",
                        "message_id": work.packet_id,
                        "job_id": str(work.job_id),
                        "classification": result.classification.value,
                        "freshness": (
                            "STALE" if assessment.is_stale else "CURRENT"
                        ),
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _increment_safely(metrics, "payload_processing_failures_total")
                LOG.exception(
                    "telemetry processing failed",
                    extra={
                        "event_name": "telemetry.processing.failed",
                        "message_id": message.packet_id,
                        "job_id": message.job_id,
                        "error_reason": type(exc).__name__,
                    },
                )
                if work is None:
                    # The database may be unavailable.  Redis still retains the
                    # reference so reception evidence is not silently lost.
                    await asyncio.sleep(1)
                    with suppress(QueueStateError):
                        await queue.retry(message)
                    continue
                temporary = not isinstance(exc, TelemetryValidationError | ValueError)
                try:
                    async with sessions() as session:
                        repository = TelemetryRepository(session)
                        failure = await repository.fail_processing_attempt(
                            work.job_id,
                            worker_id=worker_id,
                            reason=f"{type(exc).__name__}: {exc}",
                            temporary=temporary,
                        )
                except SQLAlchemyError as storage_exc:
                    await _increment_safely(metrics, "database_errors_total")
                    LOG.warning(
                        "could not record processing failure while database unavailable",
                        extra={
                            "event_name": "telemetry.database.failed",
                            "message_id": message.packet_id,
                            "job_id": message.job_id,
                            "error_reason": type(storage_exc).__name__,
                        },
                    )
                    # Keep the Redis processing entry.  Startup recovery plus
                    # the database lease makes this attempt safe to resume.
                    await asyncio.sleep(1)
                    continue
                if failure.state is ProcessingJobState.FAILED:
                    await queue.dead_letter(message)
                else:
                    delay = 1
                    if failure.next_attempt_at is not None:
                        delay = max(
                            0,
                            math.ceil(
                                (failure.next_attempt_at - datetime.now(UTC)).total_seconds()
                            ),
                        )
                    await asyncio.sleep(delay)
                    await queue.retry(message)
    finally:
        await redis_client.aclose()
        await engine.dispose()


async def check_dependencies(settings: WorkerSettings) -> None:
    engine, _ = create_engine_and_session_factory(settings.database_url)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        if not await redis_client.ping():
            raise RuntimeError("Redis PING did not return success")
    finally:
        await redis_client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued payload telemetry")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    settings = WorkerSettings.from_environment()
    if args.healthcheck:
        asyncio.run(check_dependencies(settings))
    else:
        asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
