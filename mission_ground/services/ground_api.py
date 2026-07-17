"""Secure-boundary teaching gateway for the Phase 2 telemetry path.

Phase 2 uses a local static vendor token and plain HTTP so the data behavior is
visible.  Those controls are explicitly not production-grade; HTTPS and mutual
Transport Layer Security belong to Phase 5.
"""

from __future__ import annotations

import hmac
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from mission_ground.telemetry.codec import (
    TelemetryFrame,
    TelemetryValidationError,
    decode_telemetry,
)
from mission_ground.telemetry.config import GroundSettings
from mission_ground.telemetry.metrics import TelemetryMetrics, render_prometheus
from mission_ground.telemetry.queue import QueueMessage, TelemetryQueue
from mission_ground.telemetry.storage import (
    DecodedTelemetryValues,
    DeliveryQueueStatus,
    DeliveryValidationStatus,
    ProcessingJobState,
    TelemetryDeliveryReceipt,
    TelemetryRepository,
    create_engine_and_session_factory,
    create_schema,
)
from mission_ground.telemetry.structured_logging import configure_logging

LOG = configure_logging("ground-api")
MESSAGE_ID_PATTERN = re.compile(r"TLM-P\d{2}-B[0-9A-F]{8}-S[0-9A-F]{8}\Z")
VENDOR_IDENTITY = "local-token:vendor-simulator"


def _canonical_uuid4(presented: str | None) -> uuid.UUID | None:
    if presented is None:
        return None
    try:
        parsed = uuid.UUID(presented)
    except (ValueError, AttributeError):
        return None
    if parsed.version != 4 or str(parsed) != presented:
        return None
    return parsed


def _telemetry_values(frame: TelemetryFrame) -> DecodedTelemetryValues:
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


def _iso_from_us(timestamp_us: int) -> str:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return (epoch + timedelta(microseconds=timestamp_us)).isoformat().replace("+00:00", "Z")


async def _bounded_body(request: Request, maximum: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > maximum:
                raise HTTPException(status_code=413, detail="telemetry body exceeds 1024 bytes")
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Content-Length must be an integer"
            ) from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=413, detail="telemetry body exceeds 1024 bytes")
    return bytes(body)


def _bearer_identity(authorization: str | None, settings: GroundSettings) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="missing bearer credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ")
    if hmac.compare_digest(token, settings.operator_token):
        return "operator"
    if hmac.compare_digest(token, settings.admin_token):
        return "administrator"
    raise HTTPException(
        status_code=401,
        detail="invalid bearer credential",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _metric_safely(metrics: TelemetryMetrics, name: str, amount: int = 1) -> None:
    try:
        await metrics.increment(name, amount)
    except Exception:
        LOG.exception(
            "metric update failed",
            extra={"event_name": "metric.increment.failed", "metric_name": name},
        )


async def _audit_safely(
    app: FastAPI,
    *,
    event_name: str,
    actor_identity: str,
    outcome: str,
    reason: str,
    event_data: dict[str, object] | None = None,
) -> None:
    """Try to preserve a boundary event without changing the HTTP result."""

    try:
        async with app.state.sessions() as session:
            await TelemetryRepository(session).record_audit_event(
                service_name="ground-api",
                event_name=event_name,
                actor_identity=actor_identity,
                outcome=outcome,
                reason=reason,
                event_data=event_data,
            )
    except SQLAlchemyError:
        LOG.exception(
            "security audit event could not be persisted",
            extra={
                "event_name": "telemetry.audit.persistence_failed",
                "error_reason": reason,
            },
        )


async def _queue_one(
    repository: TelemetryRepository,
    queue: TelemetryQueue,
    *,
    job_id: uuid.UUID,
    packet_id: str,
) -> bool:
    message = QueueMessage(job_id=str(job_id), packet_id=packet_id)
    await queue.enqueue(message)
    # The UUID string is also the stable queue-message identity.  If Redis
    # accepted before a process exit, re-enqueue returns False and this durable
    # state transition can still be completed safely.
    return await repository.mark_job_queued(job_id, queue_message_id=str(job_id))


async def _recover_pending_jobs(app: FastAPI, *, limit: int = 10_000) -> int:
    recovered = 0
    async with app.state.sessions() as session:
        repository = TelemetryRepository(session)
        job_ids = await repository.recoverable_queue_jobs(limit=limit)
        for job_id in job_ids:
            job = await repository.get_job(job_id)
            if job is None:
                continue
            if await _queue_one(
                repository,
                app.state.queue,
                job_id=job.job_id,
                packet_id=job.packet_id,
            ):
                recovered += 1
    return recovered


def create_app(settings: GroundSettings | None = None) -> FastAPI:
    configured = settings or GroundSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine, sessions = create_engine_and_session_factory(configured.database_url)
        redis_client = Redis.from_url(configured.redis_url, decode_responses=False)
        app.state.engine = engine
        app.state.sessions = sessions
        app.state.redis = redis_client
        app.state.queue = TelemetryQueue(redis_client)
        app.state.metrics = TelemetryMetrics(redis_client)
        await create_schema(engine)
        recovered = await _recover_pending_jobs(app)
        LOG.info(
            "ground API started",
            extra={"event_name": "service.started", "recovered_queue_jobs": recovered},
        )
        try:
            yield
        finally:
            await redis_client.aclose()
            await engine.dispose()

    app = FastAPI(title="Secure Payload Telemetry Gateway", version="0.2.0", lifespan=lifespan)
    app.state.settings = configured

    @app.post("/vendor/payload-data")
    async def receive_payload_data(
        request: Request,
        x_vendor_token: str | None = Header(default=None, alias="X-Vendor-Token"),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ) -> Response:
        if x_vendor_token is None or not hmac.compare_digest(
            x_vendor_token, configured.vendor_token
        ):
            LOG.warning(
                "vendor authentication failed",
                extra={
                    "event_name": "vendor.authentication.failed",
                    "request_id": x_request_id,
                    "error_reason": "INVALID_VENDOR_TOKEN",
                },
            )
            await _metric_safely(
                app.state.metrics, "vendor_authentication_failures_total"
            )
            await _audit_safely(
                app,
                event_name="vendor.authentication.failed",
                actor_identity="unauthenticated",
                outcome="REJECTED",
                reason="INVALID_VENDOR_TOKEN",
                event_data={"presented_request_id": x_request_id},
            )
            return JSONResponse(
                status_code=401,
                content={"status": "REJECTED", "reason": "INVALID_VENDOR_TOKEN"},
                headers={"WWW-Authenticate": "X-Vendor-Token"},
            )

        try:
            raw_body = await _bounded_body(request, configured.max_telemetry_body_bytes)
        except HTTPException as exc:
            reason = (
                "BODY_TOO_LARGE"
                if exc.status_code == 413
                else "INVALID_CONTENT_LENGTH"
            )
            LOG.warning(
                "telemetry body rejected at size boundary",
                extra={
                    "event_name": "telemetry.delivery.rejected",
                    "request_id": x_request_id,
                    "error_reason": reason,
                },
            )
            await _audit_safely(
                app,
                event_name="telemetry.delivery.security_rejected",
                actor_identity=VENDOR_IDENTITY,
                outcome="REJECTED",
                reason=reason,
                event_data={"presented_request_id": x_request_id},
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={"status": "REJECTED", "reason": reason},
            )

        received_at = datetime.now(UTC)
        canonical_request_id = _canonical_uuid4(x_request_id)
        content_type = request.headers.get("content-type")
        try:
            async with app.state.sessions() as session:
                repository = TelemetryRepository(session)
                delivery = await repository.record_delivery(
                    raw_body=raw_body,
                    peer_certificate_identity=VENDOR_IDENTITY,
                    presented_request_id=x_request_id,
                    canonical_request_id=canonical_request_id,
                    content_type=content_type,
                    received_at=received_at,
                )
                await _metric_safely(
                    app.state.metrics, "payload_messages_received_total"
                )

                if delivery.request_conflict:
                    await _metric_safely(
                        app.state.metrics, "payload_messages_rejected_total"
                    )
                    return JSONResponse(
                        status_code=409,
                        content={
                            "receipt_id": str(delivery.receipt_id),
                            "status": "CONFLICT",
                            "reason": "REQUEST_ID_REUSED_WITH_DIFFERENT_BODY",
                        },
                    )

                existing_receipt: TelemetryDeliveryReceipt | None = None
                if delivery.request_replay:
                    existing_receipt = await repository.get_delivery(delivery.receipt_id)
                    if existing_receipt is None:
                        raise RuntimeError("request replay points to missing receipt")
                    if existing_receipt.validation_status is DeliveryValidationStatus.REJECTED:
                        return JSONResponse(
                            status_code=422,
                            content={
                                "receipt_id": str(existing_receipt.receipt_id),
                                "status": "REJECTED",
                                "reason": existing_receipt.rejection_reason,
                                "request_replay": True,
                            },
                        )
                    if (
                        existing_receipt.validation_status
                        in {DeliveryValidationStatus.ACCEPTED, DeliveryValidationStatus.DUPLICATE}
                        and existing_receipt.queue_status is DeliveryQueueStatus.QUEUED
                    ):
                        return JSONResponse(
                            status_code=200,
                            content={
                                "receipt_id": str(existing_receipt.receipt_id),
                                "packet_id": existing_receipt.logical_packet_id,
                                "status": "QUEUED",
                                "duplicate": existing_receipt.validation_status
                                is DeliveryValidationStatus.DUPLICATE,
                                "request_replay": True,
                            },
                        )

                if content_type is None or content_type.split(";", 1)[0].strip().lower() != (
                    "application/octet-stream"
                ):
                    rejected = await repository.reject_delivery(
                        delivery.receipt_id, reason="INVALID_CONTENT_TYPE"
                    )
                    await _metric_safely(
                        app.state.metrics, "payload_messages_rejected_total"
                    )
                    return JSONResponse(
                        status_code=400,
                        content={
                            "receipt_id": str(rejected.receipt_id),
                            "status": "REJECTED",
                            "reason": "INVALID_CONTENT_TYPE",
                        },
                    )
                if canonical_request_id is None:
                    rejected = await repository.reject_delivery(
                        delivery.receipt_id, reason="INVALID_REQUEST_ID"
                    )
                    await _metric_safely(
                        app.state.metrics, "payload_messages_rejected_total"
                    )
                    return JSONResponse(
                        status_code=400,
                        content={
                            "receipt_id": str(rejected.receipt_id),
                            "status": "REJECTED",
                            "reason": "INVALID_REQUEST_ID",
                        },
                    )

                if existing_receipt is not None and (
                    existing_receipt.validation_status is DeliveryValidationStatus.ACCEPTED
                    and existing_receipt.queue_status is DeliveryQueueStatus.PENDING_QUEUE
                    and existing_receipt.logical_packet_id is not None
                ):
                    job = await repository.get_job_for_packet(
                        existing_receipt.logical_packet_id
                    )
                    if job is None:
                        raise RuntimeError("accepted packet has no processing job")
                    transitioned = await _queue_one(
                        repository,
                        app.state.queue,
                        job_id=job.job_id,
                        packet_id=job.packet_id,
                    )
                    return JSONResponse(
                        status_code=202 if transitioned else 200,
                        content={
                            "receipt_id": str(existing_receipt.receipt_id),
                            "packet_id": existing_receipt.logical_packet_id,
                            "status": "QUEUED",
                            "duplicate": False,
                            "request_replay": True,
                        },
                    )

                try:
                    frame = decode_telemetry(raw_body, ground_received_at=received_at)
                except TelemetryValidationError as exc:
                    await repository.reject_delivery(
                        delivery.receipt_id,
                        reason=exc.reason.value,
                    )
                    await _metric_safely(
                        app.state.metrics, "payload_messages_rejected_total"
                    )
                    LOG.warning(
                        "telemetry validation failed",
                        extra={
                            "event_name": "telemetry.validation.rejected",
                            "request_id": x_request_id,
                            "receipt_id": str(delivery.receipt_id),
                            "error_reason": exc.reason.value,
                        },
                    )
                    return JSONResponse(
                        status_code=422,
                        content={
                            "receipt_id": str(delivery.receipt_id),
                            "status": "REJECTED",
                            "reason": exc.reason.value,
                            "detail": exc.detail,
                        },
                    )

                accepted = await repository.accept_packet_and_create_job(
                    delivery.receipt_id,
                    _telemetry_values(frame),
                    max_attempts=configured.processing_maximum_attempts,
                )
                if accepted.conflict:
                    await _metric_safely(
                        app.state.metrics, "payload_messages_rejected_total"
                    )
                    return JSONResponse(
                        status_code=409,
                        content={
                            "receipt_id": str(accepted.receipt_id),
                            "status": "CONFLICT",
                            "reason": "SEQUENCE_CONFLICT",
                        },
                    )
                if accepted.duplicate:
                    if (
                        accepted.queue_status is DeliveryQueueStatus.PENDING_QUEUE
                        and accepted.job_id is not None
                        and accepted.packet_id is not None
                    ):
                        await _queue_one(
                            repository,
                            app.state.queue,
                            job_id=accepted.job_id,
                            packet_id=accepted.packet_id,
                        )
                    await _metric_safely(
                        app.state.metrics, "payload_duplicate_messages_total"
                    )
                    return JSONResponse(
                        status_code=200,
                        content={
                            "receipt_id": str(accepted.receipt_id),
                            "packet_id": accepted.packet_id,
                            "status": "DUPLICATE",
                            "duplicate": True,
                        },
                    )
                if accepted.job_id is None or accepted.packet_id is None:
                    raise RuntimeError("accepted unique packet did not create a job")
                if accepted.missing_before:
                    await _metric_safely(
                        app.state.metrics,
                        "payload_sequence_gaps_total",
                        accepted.missing_before,
                    )
                await _queue_one(
                    repository,
                    app.state.queue,
                    job_id=accepted.job_id,
                    packet_id=accepted.packet_id,
                )
                LOG.info(
                    "telemetry saved and queued",
                    extra={
                        "event_name": "telemetry.delivery.queued",
                        "request_id": x_request_id,
                        "receipt_id": str(accepted.receipt_id),
                        "message_id": accepted.packet_id,
                        "job_id": str(accepted.job_id),
                        "sequence_disposition": (
                            accepted.sequence_disposition.value
                            if accepted.sequence_disposition
                            else None
                        ),
                    },
                )
                return JSONResponse(
                    status_code=202,
                    content={
                        "receipt_id": str(accepted.receipt_id),
                        "packet_id": accepted.packet_id,
                        "status": "QUEUED",
                        "duplicate": False,
                    },
                )
        except RedisError as exc:
            LOG.warning(
                "telemetry saved but queue unavailable",
                extra={
                    "event_name": "telemetry.queue.unavailable",
                    "request_id": x_request_id,
                    "error_reason": type(exc).__name__,
                },
            )
            return JSONResponse(
                status_code=503,
                content={"status": "PENDING_QUEUE", "reason": "REDIS_UNAVAILABLE"},
            )
        except SQLAlchemyError as exc:
            await _metric_safely(app.state.metrics, "database_errors_total")
            LOG.exception(
                "telemetry database operation failed",
                extra={
                    "event_name": "telemetry.database.failed",
                    "request_id": x_request_id,
                    "error_reason": type(exc).__name__,
                },
            )
            return JSONResponse(
                status_code=503,
                content={"status": "UNAVAILABLE", "reason": "POSTGRESQL_UNAVAILABLE"},
            )

    @app.get("/payloads/{message_id}/raw")
    async def get_raw_payload(
        message_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Response:
        _bearer_identity(authorization, configured)
        if MESSAGE_ID_PATTERN.fullmatch(message_id) is None:
            raise HTTPException(status_code=404, detail="telemetry message not found")
        try:
            async with app.state.sessions() as session:
                repository = TelemetryRepository(session)
                packet = await repository.get_packet(message_id)
                if packet is None:
                    raise HTTPException(status_code=404, detail="telemetry message not found")
                receipt = await repository.get_delivery(packet.source_receipt_id)
                job = await repository.get_job_for_packet(message_id)
                if receipt is None:
                    raise RuntimeError("logical packet has no source receipt")
                return JSONResponse(
                    content={
                        "message_id": packet.packet_id,
                        "receipt_id": str(receipt.receipt_id),
                        "raw_hex": bytes(receipt.raw_body).hex(),
                        "body_sha256": receipt.body_sha256,
                        "received_at": receipt.received_at.isoformat(),
                        "protocol_version": packet.protocol_version,
                        "payload_id": f"PAYLOAD-{packet.payload_id:02d}",
                        "boot_id": packet.boot_id,
                        "sequence_number": packet.sequence_number,
                        "sample_time": _iso_from_us(packet.sample_timestamp_us),
                        "sequence_disposition": packet.sequence_disposition.value,
                        "missing_before": packet.missing_before,
                        "processing_state": job.state.value if job else None,
                    }
                )
        except SQLAlchemyError as exc:
            await _metric_safely(app.state.metrics, "database_errors_total")
            raise HTTPException(status_code=503, detail="PostgreSQL unavailable") from exc

    @app.get("/payloads/{message_id}/processed")
    async def get_processed_payload(
        message_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Response:
        _bearer_identity(authorization, configured)
        if MESSAGE_ID_PATTERN.fullmatch(message_id) is None:
            raise HTTPException(status_code=404, detail="telemetry message not found")
        try:
            async with app.state.sessions() as session:
                repository = TelemetryRepository(session)
                packet = await repository.get_packet(message_id)
                if packet is None:
                    raise HTTPException(status_code=404, detail="telemetry message not found")
                processed = await repository.get_processed(message_id)
                job = await repository.get_job_for_packet(message_id)
                if processed is None:
                    state = job.state.value if job else "UNKNOWN"
                    status_code = 422 if job and job.state is ProcessingJobState.FAILED else 202
                    return JSONResponse(
                        status_code=status_code,
                        content={"message_id": message_id, "processing_state": state},
                    )
                return JSONResponse(
                    content={
                        "message_id": processed.packet_id,
                        "temperature_c": processed.temperature_c,
                        "voltage_v": processed.voltage_v,
                        "operating_mode": processed.operating_mode,
                        "fault_names": processed.fault_names,
                        "classification": processed.classification.value,
                        "freshness": "STALE" if processed.is_stale else "CURRENT",
                        "processor_version": processed.processor_version,
                        "processed_at": processed.processed_at.isoformat(),
                    }
                )
        except SQLAlchemyError as exc:
            await _metric_safely(app.state.metrics, "database_errors_total")
            raise HTTPException(status_code=503, detail="PostgreSQL unavailable") from exc

    @app.get("/audit-events")
    async def get_audit_events(
        limit: int = Query(default=100, ge=1, le=100),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> list[dict[str, object]]:
        identity = _bearer_identity(authorization, configured)
        if identity != "administrator":
            raise HTTPException(status_code=403, detail="administrator permission required")
        try:
            async with app.state.sessions() as session:
                events = await TelemetryRepository(session).list_audit_events(limit=limit)
                return [
                    {
                        "event_id": str(event.event_id),
                        "timestamp": event.occurred_at.isoformat(),
                        "service_name": event.service_name,
                        "event_name": event.event_name,
                        "actor_identity": event.actor_identity,
                        "outcome": event.outcome,
                        "reason": event.reason,
                        "receipt_id": str(event.receipt_id) if event.receipt_id else None,
                        "message_id": event.packet_id,
                        "job_id": str(event.job_id) if event.job_id else None,
                        "event_data": event.event_data,
                    }
                    for event in events
                ]
        except SQLAlchemyError as exc:
            await _metric_safely(app.state.metrics, "database_errors_total")
            raise HTTPException(status_code=503, detail="PostgreSQL unavailable") from exc

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "ground-api"}

    @app.get("/readyz")
    async def readiness() -> Response:
        dependencies = {"postgresql": False, "redis": False}
        with suppress(Exception):
            async with app.state.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            dependencies["postgresql"] = True
        with suppress(Exception):
            dependencies["redis"] = await app.state.queue.ping()
        ready = all(dependencies.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "not_ready",
                "dependencies": dependencies,
            },
        )

    @app.get("/metrics")
    async def metrics(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Response:
        identity = _bearer_identity(authorization, configured)
        if identity != "administrator":
            raise HTTPException(status_code=403, detail="administrator permission required")
        try:
            counters = await app.state.metrics.snapshot()
            depth = await app.state.queue.queue_depth()
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="Redis unavailable") from exc
        return Response(
            content=render_prometheus(counters=counters, queue_depth=depth),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


app = create_app()
