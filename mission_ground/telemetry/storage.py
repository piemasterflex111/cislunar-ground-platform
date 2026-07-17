"""Durable telemetry records and transactional repository operations.

This module deliberately keeps a *delivery receipt* separate from a *logical
telemetry packet*.  A vendor can deliver the same packet more than once, and
each new delivery is evidence, but the logical packet is processed only once.

PostgreSQL is the source of truth.  Redis queue messages should contain only
``TelemetryProcessingJob.job_id``; losing or duplicating a Redis message must
not lose the raw bytes or produce a second processed result.
"""

from __future__ import annotations

import enum
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    or_,
    select,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MAX_UINT32 = 0xFFFFFFFF
HALF_UINT32_RANGE = 1 << 31
MAX_EVIDENCE_BODY_BYTES = 1_024


def utc_now() -> datetime:
    """Return an aware Coordinated Universal Time timestamp."""

    return datetime.now(UTC)


class DeliveryValidationStatus(str, enum.Enum):
    """Result of validating one authenticated, size-bounded delivery."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


class DeliveryQueueStatus(str, enum.Enum):
    """Whether this delivery's one logical processing job reached Redis."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    PENDING_QUEUE = "PENDING_QUEUE"
    QUEUED = "QUEUED"


class SequenceDisposition(str, enum.Enum):
    """Compact persisted name for the packet's position in its boot session."""

    FIRST = "FIRST"
    IN_ORDER = "IN_ORDER"
    GAP = "GAP"
    LATE = "LATE"


class ProcessingJobState(str, enum.Enum):
    """Durable lifecycle for asynchronous payload-processing work."""

    PENDING_QUEUE = "PENDING_QUEUE"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    RETRY_WAIT = "RETRY_WAIT"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class HealthClassification(str, enum.Enum):
    NOMINAL = "NOMINAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def _enum_column(enum_class: type[enum.Enum], name: str) -> SqlEnum:
    return SqlEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda values: [item.value for item in values],
    )


class Base(DeclarativeBase):
    """Declarative base for the Phase 2 telemetry tables."""


class TelemetryDeliveryReceipt(Base):
    """Exact evidence for one vendor-to-ground request body."""

    __tablename__ = "telemetry_delivery_receipts"
    __table_args__ = (
        CheckConstraint("raw_body_size >= 0", name="ck_tlm_receipt_nonnegative_size"),
        CheckConstraint(
            f"raw_body_size <= {MAX_EVIDENCE_BODY_BYTES}",
            name="ck_tlm_receipt_bounded_size",
        ),
        Index("ix_tlm_receipt_request_id", "canonical_request_id"),
        Index("ix_tlm_receipt_packet_id", "logical_packet_id"),
        Index("ix_tlm_receipt_received_at", "received_at"),
    )

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    presented_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    peer_certificate_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    vendor_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    raw_body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    raw_body_size: Mapped[int] = mapped_column(Integer, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[DeliveryValidationStatus] = mapped_column(
        _enum_column(DeliveryValidationStatus, "telemetry_delivery_validation_status"),
        nullable=False,
        default=DeliveryValidationStatus.PENDING,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_identifiers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    duplicate_of_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telemetry_delivery_receipts.receipt_id"),
        nullable=True,
    )
    # This identifier is intentionally not a foreign key.  The receipt is saved
    # before a logical packet exists, while the packet has a real foreign key
    # back to its source receipt.  Avoiding a circular constraint keeps local
    # SQLite tests and PostgreSQL schema creation equally straightforward.
    logical_packet_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queue_status: Mapped[DeliveryQueueStatus] = mapped_column(
        _enum_column(DeliveryQueueStatus, "telemetry_delivery_queue_status"),
        nullable=False,
        default=DeliveryQueueStatus.NOT_APPLICABLE,
    )


class TelemetryRequestIdentity(Base):
    """Idempotency ledger for canonical vendor request identifiers.

    Only the first body owns a request identifier.  An exact retry returns its
    original receipt.  Reuse with different bytes still creates a new conflict
    receipt so the conflicting evidence is not discarded.
    """

    __tablename__ = "telemetry_request_identities"

    request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    first_receipt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telemetry_delivery_receipts.receipt_id"),
        nullable=False,
        unique=True,
    )
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LogicalTelemetryPacket(Base):
    """One processable packet for a unique payload/boot/sequence key."""

    __tablename__ = "logical_telemetry_packets"
    __table_args__ = (
        UniqueConstraint(
            "payload_id",
            "boot_id",
            "sequence_number",
            name="uq_tlm_packet_source_key",
        ),
        CheckConstraint("payload_id > 0", name="ck_tlm_packet_payload_positive"),
        CheckConstraint("boot_id > 0", name="ck_tlm_packet_boot_positive"),
        CheckConstraint(
            f"sequence_number >= 0 AND sequence_number <= {MAX_UINT32}",
            name="ck_tlm_packet_sequence_uint32",
        ),
        Index("ix_tlm_packet_sample_time", "sample_timestamp_us"),
    )

    packet_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_receipt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telemetry_delivery_receipts.receipt_id"),
        nullable=False,
        unique=True,
    )
    protocol_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_id: Mapped[int] = mapped_column(Integer, nullable=False)
    boot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sample_timestamp_us: Mapped[int] = mapped_column(BigInteger, nullable=False)
    temperature_centi_c: Mapped[int] = mapped_column(Integer, nullable=False)
    voltage_mv: Mapped[int] = mapped_column(Integer, nullable=False)
    operating_mode: Mapped[int] = mapped_column(Integer, nullable=False)
    fault_flags: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_disposition: Mapped[SequenceDisposition] = mapped_column(
        _enum_column(SequenceDisposition, "telemetry_sequence_disposition"), nullable=False
    )
    missing_before: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TelemetrySequenceState(Base):
    """Persistent sequence high-water mark for one payload boot session."""

    __tablename__ = "telemetry_sequence_states"
    __table_args__ = (
        CheckConstraint("payload_id > 0", name="ck_tlm_sequence_payload_positive"),
        CheckConstraint("boot_id > 0", name="ck_tlm_sequence_boot_positive"),
        CheckConstraint(
            f"high_water_sequence >= 0 AND high_water_sequence <= {MAX_UINT32}",
            name="ck_tlm_sequence_high_water_uint32",
        ),
    )

    payload_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    boot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    high_water_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    accepted_packet_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    missing_packet_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_high_water_packet_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("logical_telemetry_packets.packet_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class TelemetryProcessingJob(Base):
    """Durable work state; Redis carries this row's identifier, not raw data."""

    __tablename__ = "telemetry_processing_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_tlm_job_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_tlm_job_max_attempt_positive"),
        Index("ix_tlm_job_recovery", "state", "next_attempt_at", "created_at"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    packet_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("logical_telemetry_packets.packet_id"),
        nullable=False,
        unique=True,
    )
    state: Mapped[ProcessingJobState] = mapped_column(
        _enum_column(ProcessingJobState, "telemetry_processing_job_state"),
        nullable=False,
        default=ProcessingJobState.PENDING_QUEUE,
    )
    queue_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    claimed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ProcessedTelemetry(Base):
    """One idempotent engineering result for one logical packet."""

    __tablename__ = "processed_telemetry"

    packet_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("logical_telemetry_packets.packet_id"),
        primary_key=True,
    )
    processing_job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telemetry_processing_jobs.job_id"),
        nullable=False,
        unique=True,
    )
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    voltage_v: Mapped[float] = mapped_column(Float, nullable=False)
    operating_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    fault_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    classification: Mapped[HealthClassification] = mapped_column(
        _enum_column(HealthClassification, "telemetry_health_classification"), nullable=False
    )
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TelemetryAuditEvent(Base):
    """Permanent telemetry-specific history of significant state changes."""

    __tablename__ = "telemetry_audit_events"
    __table_args__ = (
        Index("ix_tlm_audit_occurred_at", "occurred_at"),
        Index("ix_tlm_audit_packet_id", "packet_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    service_name: Mapped[str] = mapped_column(String(64), nullable=False)
    event_name: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telemetry_delivery_receipts.receipt_id"),
        nullable=True,
    )
    packet_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("logical_telemetry_packets.packet_id"), nullable=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telemetry_processing_jobs.job_id"),
        nullable=True,
    )
    event_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


@dataclass(frozen=True, slots=True)
class DeliveryRecordResult:
    receipt_id: uuid.UUID
    validation_status: DeliveryValidationStatus
    queue_status: DeliveryQueueStatus
    request_replay: bool
    request_conflict: bool
    duplicate_of_receipt_id: uuid.UUID | None
    logical_packet_id: str | None


@dataclass(frozen=True, slots=True)
class DecodedTelemetryValues:
    """Validated wire values passed from the codec into persistence."""

    protocol_version: int
    payload_id: int
    boot_id: int
    sequence_number: int
    sample_timestamp_us: int
    temperature_centi_c: int
    voltage_mv: int
    operating_mode: int
    fault_flags: int

    @property
    def packet_id(self) -> str:
        return (
            f"TLM-P{self.payload_id:02d}-B{self.boot_id:08X}"
            f"-S{self.sequence_number:08X}"
        )

    def identifiers(self) -> dict[str, int | str]:
        return {
            "packet_id": self.packet_id,
            "protocol_version": self.protocol_version,
            "payload_id": self.payload_id,
            "boot_id": self.boot_id,
            "sequence_number": self.sequence_number,
            "sample_timestamp_us": self.sample_timestamp_us,
        }


@dataclass(frozen=True, slots=True)
class PacketAcceptanceResult:
    receipt_id: uuid.UUID
    packet_id: str | None
    job_id: uuid.UUID | None
    validation_status: DeliveryValidationStatus
    queue_status: DeliveryQueueStatus
    sequence_disposition: SequenceDisposition | None
    missing_before: int
    duplicate: bool
    conflict: bool
    duplicate_of_receipt_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class TelemetryWorkItem:
    job_id: uuid.UUID
    packet_id: str
    raw_body: bytes
    received_at: datetime
    values: DecodedTelemetryValues
    attempt_count: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class ProcessingResultValues:
    temperature_c: float
    voltage_v: float
    operating_mode: str
    fault_names: list[str]
    classification: HealthClassification
    is_stale: bool
    processor_version: str = "telemetry-v1"


@dataclass(frozen=True, slots=True)
class ProcessingFailureResult:
    job_id: uuid.UUID
    state: ProcessingJobState
    attempt_count: int
    next_attempt_at: datetime | None


class TelemetryStorageError(RuntimeError):
    """Base class for repository state and consistency errors."""


class DeliveryNotFoundError(TelemetryStorageError):
    pass


class InvalidDeliveryStateError(TelemetryStorageError):
    pass


class ProcessingJobNotFoundError(TelemetryStorageError):
    pass


class ProcessingResultConflictError(TelemetryStorageError):
    pass


def create_engine_and_session_factory(
    database_url: str, *, echo: bool = False
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an async engine and sessions that keep values after commit.

    Production uses a ``postgresql+asyncpg://`` URL.  Tests may use
    ``sqlite+aiosqlite://`` without changing repository behavior.
    """

    engine = create_async_engine(database_url, echo=echo, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


async def create_schema(engine: AsyncEngine) -> None:
    """Create telemetry tables for local development and tests."""

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


class TelemetryRepository:
    """Transaction boundary for telemetry receipt and processing state.

    A repository owns one ``AsyncSession`` and must not be used concurrently.
    Each public mutating operation commits one complete database transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _audit(
        self,
        *,
        service_name: str,
        event_name: str,
        actor_identity: str,
        outcome: str,
        reason: str | None = None,
        receipt_id: uuid.UUID | None = None,
        packet_id: str | None = None,
        job_id: uuid.UUID | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> TelemetryAuditEvent:
        event = TelemetryAuditEvent(
            service_name=service_name,
            event_name=event_name,
            actor_identity=actor_identity,
            outcome=outcome,
            reason=reason,
            receipt_id=receipt_id,
            packet_id=packet_id,
            job_id=job_id,
            event_data=event_data or {},
        )
        self._session.add(event)
        return event

    async def record_audit_event(
        self,
        *,
        service_name: str,
        event_name: str,
        actor_identity: str,
        outcome: str,
        reason: str | None = None,
        receipt_id: uuid.UUID | None = None,
        packet_id: str | None = None,
        job_id: uuid.UUID | None = None,
        event_data: dict[str, Any] | None = None,
    ) -> TelemetryAuditEvent:
        """Persist an event that is not part of another repository transaction."""

        async with self._session.begin():
            event = self._audit(
                service_name=service_name,
                event_name=event_name,
                actor_identity=actor_identity,
                outcome=outcome,
                reason=reason,
                receipt_id=receipt_id,
                packet_id=packet_id,
                job_id=job_id,
                event_data=event_data,
            )
            await self._session.flush()
            return event

    @staticmethod
    def _delivery_result(
        receipt: TelemetryDeliveryReceipt,
        *,
        request_replay: bool,
        request_conflict: bool,
    ) -> DeliveryRecordResult:
        return DeliveryRecordResult(
            receipt_id=receipt.receipt_id,
            validation_status=receipt.validation_status,
            queue_status=receipt.queue_status,
            request_replay=request_replay,
            request_conflict=request_conflict,
            duplicate_of_receipt_id=receipt.duplicate_of_receipt_id,
            logical_packet_id=receipt.logical_packet_id,
        )

    async def record_delivery(
        self,
        *,
        raw_body: bytes,
        peer_certificate_identity: str,
        presented_request_id: str | None,
        canonical_request_id: uuid.UUID | None,
        content_type: str | None,
        received_at: datetime | None = None,
        vendor_sent_at: datetime | None = None,
        receipt_id: uuid.UUID | None = None,
    ) -> DeliveryRecordResult:
        """Persist exact bytes before application-level validation.

        A canonical request identifier and identical bytes return the first
        receipt.  Reusing that identifier with different bytes creates a new
        ``CONFLICT`` receipt linked to the first receipt.
        """

        if len(raw_body) > MAX_EVIDENCE_BODY_BYTES:
            raise ValueError(
                f"raw body exceeds {MAX_EVIDENCE_BODY_BYTES}-byte evidence limit"
            )
        if not peer_certificate_identity:
            raise ValueError("peer_certificate_identity must not be empty")

        digest = hashlib.sha256(raw_body).hexdigest()
        receipt_time = received_at or utc_now()

        # A uniqueness race is possible when two workers see a new request ID
        # simultaneously.  The request ledger primary key chooses one winner;
        # the loser retries and observes the committed winner.
        for attempt in range(2):
            try:
                async with self._session.begin():
                    identity: TelemetryRequestIdentity | None = None
                    first_receipt: TelemetryDeliveryReceipt | None = None
                    if canonical_request_id is not None:
                        identity = await self._session.get(
                            TelemetryRequestIdentity,
                            canonical_request_id,
                            with_for_update=True,
                        )
                        if identity is not None:
                            first_receipt = await self._session.get(
                                TelemetryDeliveryReceipt, identity.first_receipt_id
                            )
                            if first_receipt is None:
                                raise TelemetryStorageError(
                                    "request identity points to a missing receipt"
                                )
                            if (
                                identity.body_sha256 == digest
                                and first_receipt.raw_body == raw_body
                            ):
                                return self._delivery_result(
                                    first_receipt,
                                    request_replay=True,
                                    request_conflict=False,
                                )

                    conflict = first_receipt is not None
                    receipt = TelemetryDeliveryReceipt(
                        receipt_id=receipt_id or uuid.uuid4(),
                        presented_request_id=presented_request_id,
                        canonical_request_id=canonical_request_id,
                        peer_certificate_identity=peer_certificate_identity,
                        content_type=content_type,
                        vendor_sent_at=vendor_sent_at,
                        received_at=receipt_time,
                        updated_at=receipt_time,
                        raw_body=bytes(raw_body),
                        raw_body_size=len(raw_body),
                        body_sha256=digest,
                        validation_status=(
                            DeliveryValidationStatus.CONFLICT
                            if conflict
                            else DeliveryValidationStatus.PENDING
                        ),
                        rejection_reason=(
                            "REQUEST_ID_REUSED_WITH_DIFFERENT_BODY" if conflict else None
                        ),
                        duplicate_of_receipt_id=(
                            first_receipt.receipt_id if first_receipt else None
                        ),
                        queue_status=DeliveryQueueStatus.NOT_APPLICABLE,
                    )
                    self._session.add(receipt)
                    await self._session.flush()

                    if canonical_request_id is not None and identity is None:
                        self._session.add(
                            TelemetryRequestIdentity(
                                request_id=canonical_request_id,
                                first_receipt_id=receipt.receipt_id,
                                body_sha256=digest,
                                created_at=receipt_time,
                            )
                        )

                    self._audit(
                        service_name="ground-api",
                        event_name=(
                            "telemetry.delivery.request_id_conflict"
                            if conflict
                            else "telemetry.delivery.received"
                        ),
                        actor_identity=peer_certificate_identity,
                        outcome="CONFLICT" if conflict else "RECORDED",
                        reason=receipt.rejection_reason,
                        receipt_id=receipt.receipt_id,
                        event_data={"body_sha256": digest, "raw_body_size": len(raw_body)},
                    )
                    return self._delivery_result(
                        receipt,
                        request_replay=False,
                        request_conflict=conflict,
                    )
            except IntegrityError:
                # ``begin`` has rolled back.  Retry once to read the request-ID
                # ledger row inserted by the competing transaction.
                if attempt == 1:
                    raise

        raise AssertionError("unreachable request-id retry state")

    async def reject_delivery(
        self,
        receipt_id: uuid.UUID,
        *,
        reason: str,
        parsed_identifiers: dict[str, Any] | None = None,
        actor_identity: str = "ground-api",
    ) -> DeliveryRecordResult:
        """Mark pending raw evidence rejected without creating sequence state."""

        async with self._session.begin():
            receipt = await self._session.get(
                TelemetryDeliveryReceipt, receipt_id, with_for_update=True
            )
            if receipt is None:
                raise DeliveryNotFoundError(str(receipt_id))
            if receipt.validation_status is DeliveryValidationStatus.REJECTED:
                if receipt.rejection_reason != reason:
                    raise InvalidDeliveryStateError(
                        "delivery was already rejected for a different reason"
                    )
                return self._delivery_result(
                    receipt, request_replay=True, request_conflict=False
                )
            if receipt.validation_status is not DeliveryValidationStatus.PENDING:
                raise InvalidDeliveryStateError(
                    f"cannot reject a {receipt.validation_status.value} delivery"
                )

            receipt.validation_status = DeliveryValidationStatus.REJECTED
            receipt.rejection_reason = reason
            receipt.parsed_identifiers = parsed_identifiers
            receipt.queue_status = DeliveryQueueStatus.NOT_APPLICABLE
            receipt.updated_at = utc_now()
            self._audit(
                service_name="ground-api",
                event_name="telemetry.delivery.rejected",
                actor_identity=actor_identity,
                outcome="REJECTED",
                reason=reason,
                receipt_id=receipt_id,
                event_data=parsed_identifiers,
            )
            return self._delivery_result(
                receipt, request_replay=False, request_conflict=False
            )

    async def accept_packet_and_create_job(
        self,
        receipt_id: uuid.UUID,
        values: DecodedTelemetryValues,
        *,
        max_attempts: int = 3,
        actor_identity: str = "ground-api",
    ) -> PacketAcceptanceResult:
        """Atomically apply duplicate/sequence rules and create at most one job."""

        if not 0 <= values.sequence_number <= MAX_UINT32:
            raise ValueError("sequence_number must be an unsigned 32-bit value")
        if values.boot_id <= 0:
            raise ValueError("boot_id must be nonzero")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

        for attempt in range(2):
            try:
                return await self._accept_packet_transaction(
                    receipt_id,
                    values,
                    max_attempts=max_attempts,
                    actor_identity=actor_identity,
                )
            except IntegrityError:
                # Unique packet/session constraints resolve concurrent delivery
                # races.  A second pass observes the committed packet as a
                # duplicate or conflict instead of creating duplicate work.
                if attempt == 1:
                    raise
        raise AssertionError("unreachable packet acceptance retry state")

    async def _accept_packet_transaction(
        self,
        receipt_id: uuid.UUID,
        values: DecodedTelemetryValues,
        *,
        max_attempts: int,
        actor_identity: str,
    ) -> PacketAcceptanceResult:
        async with self._session.begin():
            receipt = await self._session.get(
                TelemetryDeliveryReceipt, receipt_id, with_for_update=True
            )
            if receipt is None:
                raise DeliveryNotFoundError(str(receipt_id))
            if receipt.validation_status in {
                DeliveryValidationStatus.ACCEPTED,
                DeliveryValidationStatus.DUPLICATE,
            }:
                job = None
                packet = None
                if receipt.logical_packet_id is not None:
                    packet = await self._session.get(
                        LogicalTelemetryPacket, receipt.logical_packet_id
                    )
                    job = await self._session.scalar(
                        select(TelemetryProcessingJob).where(
                            TelemetryProcessingJob.packet_id == receipt.logical_packet_id
                        )
                    )
                return PacketAcceptanceResult(
                    receipt_id=receipt.receipt_id,
                    packet_id=receipt.logical_packet_id,
                    job_id=job.job_id if job else None,
                    validation_status=receipt.validation_status,
                    queue_status=(
                        job_state_to_queue_status(job.state)
                        if job
                        else receipt.queue_status
                    ),
                    sequence_disposition=packet.sequence_disposition if packet else None,
                    missing_before=packet.missing_before if packet else 0,
                    duplicate=(
                        receipt.validation_status is DeliveryValidationStatus.DUPLICATE
                    ),
                    conflict=False,
                    duplicate_of_receipt_id=receipt.duplicate_of_receipt_id,
                )
            if receipt.validation_status is not DeliveryValidationStatus.PENDING:
                raise InvalidDeliveryStateError(
                    f"cannot accept a {receipt.validation_status.value} delivery"
                )

            existing_packet = await self._session.scalar(
                select(LogicalTelemetryPacket)
                .where(
                    LogicalTelemetryPacket.payload_id == values.payload_id,
                    LogicalTelemetryPacket.boot_id == values.boot_id,
                    LogicalTelemetryPacket.sequence_number == values.sequence_number,
                )
                .with_for_update()
            )
            if existing_packet is not None:
                origin = await self._session.get(
                    TelemetryDeliveryReceipt, existing_packet.source_receipt_id
                )
                exact_duplicate = bool(
                    origin is not None
                    and existing_packet.raw_sha256 == receipt.body_sha256
                    and origin.raw_body == receipt.raw_body
                )
                receipt.logical_packet_id = existing_packet.packet_id
                receipt.duplicate_of_receipt_id = existing_packet.source_receipt_id
                receipt.parsed_identifiers = values.identifiers()
                receipt.updated_at = utc_now()
                if exact_duplicate:
                    receipt.validation_status = DeliveryValidationStatus.DUPLICATE
                    receipt.queue_status = DeliveryQueueStatus.NOT_APPLICABLE
                    receipt.rejection_reason = None
                    existing_job = await self._session.scalar(
                        select(TelemetryProcessingJob).where(
                            TelemetryProcessingJob.packet_id == existing_packet.packet_id
                        )
                    )
                    self._audit(
                        service_name="ground-api",
                        event_name="telemetry.delivery.duplicate",
                        actor_identity=actor_identity,
                        outcome="DUPLICATE",
                        receipt_id=receipt.receipt_id,
                        packet_id=existing_packet.packet_id,
                        job_id=existing_job.job_id if existing_job else None,
                        event_data={
                            "original_receipt_id": str(existing_packet.source_receipt_id)
                        },
                    )
                    return PacketAcceptanceResult(
                        receipt_id=receipt.receipt_id,
                        packet_id=existing_packet.packet_id,
                        job_id=existing_job.job_id if existing_job else None,
                        validation_status=DeliveryValidationStatus.DUPLICATE,
                        queue_status=(
                            job_state_to_queue_status(existing_job.state)
                            if existing_job
                            else DeliveryQueueStatus.NOT_APPLICABLE
                        ),
                        sequence_disposition=existing_packet.sequence_disposition,
                        missing_before=existing_packet.missing_before,
                        duplicate=True,
                        conflict=False,
                        duplicate_of_receipt_id=existing_packet.source_receipt_id,
                    )

                receipt.validation_status = DeliveryValidationStatus.CONFLICT
                receipt.queue_status = DeliveryQueueStatus.NOT_APPLICABLE
                receipt.rejection_reason = "SEQUENCE_CONFLICT"
                self._audit(
                    service_name="ground-api",
                    event_name="telemetry.delivery.sequence_conflict",
                    actor_identity=actor_identity,
                    outcome="CONFLICT",
                    reason="SEQUENCE_CONFLICT",
                    receipt_id=receipt.receipt_id,
                    packet_id=existing_packet.packet_id,
                )
                return PacketAcceptanceResult(
                    receipt_id=receipt.receipt_id,
                    packet_id=existing_packet.packet_id,
                    job_id=None,
                    validation_status=DeliveryValidationStatus.CONFLICT,
                    queue_status=DeliveryQueueStatus.NOT_APPLICABLE,
                    sequence_disposition=None,
                    missing_before=0,
                    duplicate=False,
                    conflict=True,
                    duplicate_of_receipt_id=existing_packet.source_receipt_id,
                )

            packet_id = values.packet_id
            sequence_state = await self._session.get(
                TelemetrySequenceState,
                (values.payload_id, values.boot_id),
                with_for_update=True,
            )
            if sequence_state is None:
                disposition = SequenceDisposition.FIRST
                missing_before = values.sequence_number
            else:
                forward_distance = (
                    values.sequence_number - sequence_state.high_water_sequence
                ) & MAX_UINT32
                if forward_distance == 0:
                    # A high-water value without its permanent packet row means
                    # database state is inconsistent; do not silently process it.
                    receipt.validation_status = DeliveryValidationStatus.CONFLICT
                    receipt.rejection_reason = "SEQUENCE_STATE_CONFLICT"
                    receipt.parsed_identifiers = values.identifiers()
                    receipt.updated_at = utc_now()
                    self._audit(
                        service_name="ground-api",
                        event_name="telemetry.sequence.state_conflict",
                        actor_identity=actor_identity,
                        outcome="CONFLICT",
                        reason=receipt.rejection_reason,
                        receipt_id=receipt.receipt_id,
                    )
                    return PacketAcceptanceResult(
                        receipt_id=receipt.receipt_id,
                        packet_id=None,
                        job_id=None,
                        validation_status=DeliveryValidationStatus.CONFLICT,
                        queue_status=DeliveryQueueStatus.NOT_APPLICABLE,
                        sequence_disposition=None,
                        missing_before=0,
                        duplicate=False,
                        conflict=True,
                        duplicate_of_receipt_id=None,
                    )
                if forward_distance == 1:
                    disposition = SequenceDisposition.IN_ORDER
                    missing_before = 0
                elif forward_distance < HALF_UINT32_RANGE:
                    disposition = SequenceDisposition.GAP
                    missing_before = forward_distance - 1
                else:
                    disposition = SequenceDisposition.LATE
                    missing_before = 0

            packet = LogicalTelemetryPacket(
                packet_id=packet_id,
                source_receipt_id=receipt.receipt_id,
                protocol_version=values.protocol_version,
                payload_id=values.payload_id,
                boot_id=values.boot_id,
                sequence_number=values.sequence_number,
                sample_timestamp_us=values.sample_timestamp_us,
                temperature_centi_c=values.temperature_centi_c,
                voltage_mv=values.voltage_mv,
                operating_mode=values.operating_mode,
                fault_flags=values.fault_flags,
                raw_sha256=receipt.body_sha256,
                sequence_disposition=disposition,
                missing_before=missing_before,
            )
            self._session.add(packet)
            await self._session.flush()

            if sequence_state is None:
                sequence_state = TelemetrySequenceState(
                    payload_id=values.payload_id,
                    boot_id=values.boot_id,
                    high_water_sequence=values.sequence_number,
                    accepted_packet_count=1,
                    missing_packet_count=missing_before,
                    last_high_water_packet_id=packet_id,
                )
                self._session.add(sequence_state)
            else:
                sequence_state.accepted_packet_count += 1
                if disposition in {
                    SequenceDisposition.IN_ORDER,
                    SequenceDisposition.GAP,
                }:
                    sequence_state.high_water_sequence = values.sequence_number
                    sequence_state.missing_packet_count += missing_before
                    sequence_state.last_high_water_packet_id = packet_id
                sequence_state.updated_at = utc_now()

            job = TelemetryProcessingJob(
                packet_id=packet_id,
                state=ProcessingJobState.PENDING_QUEUE,
                max_attempts=max_attempts,
            )
            self._session.add(job)
            await self._session.flush()

            receipt.validation_status = DeliveryValidationStatus.ACCEPTED
            receipt.rejection_reason = None
            receipt.parsed_identifiers = values.identifiers()
            receipt.logical_packet_id = packet_id
            receipt.queue_status = DeliveryQueueStatus.PENDING_QUEUE
            receipt.updated_at = utc_now()
            self._audit(
                service_name="ground-api",
                event_name="telemetry.packet.accepted",
                actor_identity=actor_identity,
                outcome="PENDING_QUEUE",
                receipt_id=receipt.receipt_id,
                packet_id=packet_id,
                job_id=job.job_id,
                event_data={
                    "sequence_disposition": disposition.value,
                    "missing_before": missing_before,
                },
            )
            return PacketAcceptanceResult(
                receipt_id=receipt.receipt_id,
                packet_id=packet_id,
                job_id=job.job_id,
                validation_status=DeliveryValidationStatus.ACCEPTED,
                queue_status=DeliveryQueueStatus.PENDING_QUEUE,
                sequence_disposition=disposition,
                missing_before=missing_before,
                duplicate=False,
                conflict=False,
                duplicate_of_receipt_id=None,
            )

    async def mark_job_queued(
        self,
        job_id: uuid.UUID,
        *,
        queue_message_id: str,
        actor_identity: str = "ground-api",
    ) -> bool:
        """Mark Redis acceptance; repeated calls with the same marker are safe."""

        async with self._session.begin():
            job = await self._session.get(
                TelemetryProcessingJob, job_id, with_for_update=True
            )
            if job is None:
                raise ProcessingJobNotFoundError(str(job_id))
            if job.state is ProcessingJobState.QUEUED:
                if job.queue_message_id != queue_message_id:
                    raise InvalidDeliveryStateError(
                        "job is already queued with a different queue message ID"
                    )
                return False
            if job.state not in {
                ProcessingJobState.PENDING_QUEUE,
                ProcessingJobState.RETRY_WAIT,
            }:
                raise InvalidDeliveryStateError(
                    f"cannot queue a {job.state.value} processing job"
                )

            job.state = ProcessingJobState.QUEUED
            job.queue_message_id = queue_message_id
            job.next_attempt_at = None
            job.updated_at = utc_now()
            receipts = (
                await self._session.scalars(
                    select(TelemetryDeliveryReceipt).where(
                        TelemetryDeliveryReceipt.logical_packet_id == job.packet_id,
                        TelemetryDeliveryReceipt.validation_status
                        == DeliveryValidationStatus.ACCEPTED,
                    )
                )
            ).all()
            for receipt in receipts:
                receipt.queue_status = DeliveryQueueStatus.QUEUED
                receipt.updated_at = utc_now()
            self._audit(
                service_name="ground-api",
                event_name="telemetry.processing.queued",
                actor_identity=actor_identity,
                outcome="QUEUED",
                packet_id=job.packet_id,
                job_id=job.job_id,
                event_data={"queue_message_id": queue_message_id},
            )
            return True

    async def pending_queue_jobs(self, *, limit: int = 100) -> list[uuid.UUID]:
        """Return durable jobs that a queue recovery loop should enqueue."""

        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        now = utc_now()
        async with self._session.begin():
            jobs = await self._session.scalars(
                select(TelemetryProcessingJob.job_id)
                .where(
                    or_(
                        TelemetryProcessingJob.state == ProcessingJobState.PENDING_QUEUE,
                        (
                            (TelemetryProcessingJob.state == ProcessingJobState.RETRY_WAIT)
                            & (TelemetryProcessingJob.next_attempt_at <= now)
                        ),
                    )
                )
                .order_by(TelemetryProcessingJob.created_at)
                .limit(limit)
            )
            return list(jobs)

    async def recoverable_queue_jobs(self, *, limit: int = 1_000) -> list[uuid.UUID]:
        """Return work PostgreSQL can use to reconstruct a lost Redis queue.

        QUEUED rows are included deliberately.  Redis's atomic marker makes an
        enqueue harmless when its reference still exists, while inclusion here
        restores a reference if Redis state was lost.
        """

        if not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        now = utc_now()
        async with self._session.begin():
            jobs = await self._session.scalars(
                select(TelemetryProcessingJob.job_id)
                .where(
                    or_(
                        TelemetryProcessingJob.state
                        == ProcessingJobState.PENDING_QUEUE,
                        TelemetryProcessingJob.state == ProcessingJobState.QUEUED,
                        (
                            (TelemetryProcessingJob.state == ProcessingJobState.RETRY_WAIT)
                            & (TelemetryProcessingJob.next_attempt_at <= now)
                        ),
                    )
                )
                .order_by(TelemetryProcessingJob.created_at)
                .limit(limit)
            )
            return list(jobs)

    async def claim_processing_job(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> TelemetryWorkItem | None:
        """Claim queued work once, recovering an expired worker lease safely."""

        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = utc_now()
        async with self._session.begin():
            job = await self._session.get(
                TelemetryProcessingJob, job_id, with_for_update=True
            )
            if job is None:
                raise ProcessingJobNotFoundError(str(job_id))
            processed = await self._session.get(ProcessedTelemetry, job.packet_id)
            if processed is not None:
                if job.state is not ProcessingJobState.COMPLETE:
                    job.state = ProcessingJobState.COMPLETE
                    job.completed_at = processed.processed_at
                    job.lease_expires_at = None
                    job.updated_at = now
                return None

            expired_lease = (
                job.state is ProcessingJobState.PROCESSING
                and job.lease_expires_at is not None
                and _as_utc(job.lease_expires_at) <= now
            )
            due_retry = (
                job.state is ProcessingJobState.RETRY_WAIT
                and job.next_attempt_at is not None
                and _as_utc(job.next_attempt_at) <= now
            )
            if job.state is not ProcessingJobState.QUEUED and not expired_lease and not due_retry:
                return None

            packet = await self._session.get(LogicalTelemetryPacket, job.packet_id)
            if packet is None:
                raise TelemetryStorageError(f"job {job_id} points to a missing packet")
            receipt = await self._session.get(
                TelemetryDeliveryReceipt, packet.source_receipt_id
            )
            if receipt is None:
                raise TelemetryStorageError(
                    f"packet {packet.packet_id} points to a missing raw receipt"
                )

            job.state = ProcessingJobState.PROCESSING
            job.attempt_count += 1
            job.claimed_by = worker_id
            job.claimed_at = now
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.next_attempt_at = None
            job.updated_at = now
            self._audit(
                service_name="payload-worker",
                event_name="telemetry.processing.claimed",
                actor_identity=worker_id,
                outcome="PROCESSING",
                receipt_id=receipt.receipt_id,
                packet_id=packet.packet_id,
                job_id=job.job_id,
                event_data={"attempt_count": job.attempt_count},
            )
            return TelemetryWorkItem(
                job_id=job.job_id,
                packet_id=packet.packet_id,
                raw_body=bytes(receipt.raw_body),
                received_at=_as_utc(receipt.received_at),
                values=DecodedTelemetryValues(
                    protocol_version=packet.protocol_version,
                    payload_id=packet.payload_id,
                    boot_id=packet.boot_id,
                    sequence_number=packet.sequence_number,
                    sample_timestamp_us=packet.sample_timestamp_us,
                    temperature_centi_c=packet.temperature_centi_c,
                    voltage_mv=packet.voltage_mv,
                    operating_mode=packet.operating_mode,
                    fault_flags=packet.fault_flags,
                ),
                attempt_count=job.attempt_count,
                max_attempts=job.max_attempts,
            )

    async def complete_processing_job(
        self,
        job_id: uuid.UUID,
        result: ProcessingResultValues,
        *,
        worker_id: str,
        processed_at: datetime | None = None,
    ) -> ProcessedTelemetry:
        """Store one result and complete its job in the same transaction."""

        completion_time = processed_at or utc_now()
        async with self._session.begin():
            job = await self._session.get(
                TelemetryProcessingJob, job_id, with_for_update=True
            )
            if job is None:
                raise ProcessingJobNotFoundError(str(job_id))
            existing = await self._session.get(ProcessedTelemetry, job.packet_id)
            if existing is not None:
                if not _same_processing_result(existing, result):
                    raise ProcessingResultConflictError(
                        f"packet {job.packet_id} already has a different processed result"
                    )
                job.state = ProcessingJobState.COMPLETE
                job.completed_at = existing.processed_at
                job.lease_expires_at = None
                job.updated_at = utc_now()
                return existing
            if job.state is not ProcessingJobState.PROCESSING:
                raise InvalidDeliveryStateError(
                    f"cannot complete a {job.state.value} processing job"
                )
            if job.claimed_by != worker_id:
                raise InvalidDeliveryStateError("processing job is owned by another worker")

            processed = ProcessedTelemetry(
                packet_id=job.packet_id,
                processing_job_id=job.job_id,
                temperature_c=result.temperature_c,
                voltage_v=result.voltage_v,
                operating_mode=result.operating_mode,
                fault_names=list(result.fault_names),
                classification=result.classification,
                is_stale=result.is_stale,
                processor_version=result.processor_version,
                processed_at=completion_time,
            )
            self._session.add(processed)
            job.state = ProcessingJobState.COMPLETE
            job.completed_at = completion_time
            job.lease_expires_at = None
            job.next_attempt_at = None
            job.last_error = None
            job.updated_at = completion_time
            self._audit(
                service_name="payload-worker",
                event_name="telemetry.processing.completed",
                actor_identity=worker_id,
                outcome="COMPLETE",
                packet_id=job.packet_id,
                job_id=job.job_id,
                event_data={
                    "classification": result.classification.value,
                    "is_stale": result.is_stale,
                },
            )
            await self._session.flush()
            return processed

    async def fail_processing_attempt(
        self,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        reason: str,
        temporary: bool,
        base_backoff_seconds: int = 1,
        now: datetime | None = None,
    ) -> ProcessingFailureResult:
        """Schedule bounded exponential retry or move work to ``FAILED``."""

        if base_backoff_seconds <= 0:
            raise ValueError("base_backoff_seconds must be positive")
        failure_time = now or utc_now()
        async with self._session.begin():
            job = await self._session.get(
                TelemetryProcessingJob, job_id, with_for_update=True
            )
            if job is None:
                raise ProcessingJobNotFoundError(str(job_id))
            if job.state is ProcessingJobState.FAILED:
                return ProcessingFailureResult(
                    job_id=job.job_id,
                    state=job.state,
                    attempt_count=job.attempt_count,
                    next_attempt_at=None,
                )
            if job.state is not ProcessingJobState.PROCESSING:
                raise InvalidDeliveryStateError(
                    f"cannot fail a {job.state.value} processing job"
                )
            if job.claimed_by != worker_id:
                raise InvalidDeliveryStateError("processing job is owned by another worker")

            exhausted = job.attempt_count >= job.max_attempts
            job.last_error = reason
            job.lease_expires_at = None
            job.updated_at = failure_time
            if not temporary or exhausted:
                job.state = ProcessingJobState.FAILED
                job.failed_at = failure_time
                job.next_attempt_at = None
                event_name = "telemetry.processing.failed"
            else:
                delay = min(
                    base_backoff_seconds * (2 ** max(job.attempt_count - 1, 0)), 300
                )
                job.state = ProcessingJobState.RETRY_WAIT
                job.next_attempt_at = failure_time + timedelta(seconds=delay)
                event_name = "telemetry.processing.retry_scheduled"

            self._audit(
                service_name="payload-worker",
                event_name=event_name,
                actor_identity=worker_id,
                outcome=job.state.value,
                reason=reason,
                packet_id=job.packet_id,
                job_id=job.job_id,
                event_data={
                    "attempt_count": job.attempt_count,
                    "max_attempts": job.max_attempts,
                    "next_attempt_at": (
                        job.next_attempt_at.isoformat() if job.next_attempt_at else None
                    ),
                },
            )
            return ProcessingFailureResult(
                job_id=job.job_id,
                state=job.state,
                attempt_count=job.attempt_count,
                next_attempt_at=job.next_attempt_at,
            )

    async def get_delivery(
        self, receipt_id: uuid.UUID
    ) -> TelemetryDeliveryReceipt | None:
        async with self._session.begin():
            return await self._session.get(TelemetryDeliveryReceipt, receipt_id)

    async def get_packet(self, packet_id: str) -> LogicalTelemetryPacket | None:
        async with self._session.begin():
            return await self._session.get(LogicalTelemetryPacket, packet_id)

    async def get_processed(self, packet_id: str) -> ProcessedTelemetry | None:
        async with self._session.begin():
            return await self._session.get(ProcessedTelemetry, packet_id)

    async def get_job(self, job_id: uuid.UUID) -> TelemetryProcessingJob | None:
        async with self._session.begin():
            return await self._session.get(TelemetryProcessingJob, job_id)

    async def get_job_for_packet(
        self, packet_id: str
    ) -> TelemetryProcessingJob | None:
        async with self._session.begin():
            return await self._session.scalar(
                select(TelemetryProcessingJob).where(
                    TelemetryProcessingJob.packet_id == packet_id
                )
            )

    async def list_audit_events(self, *, limit: int = 100) -> list[TelemetryAuditEvent]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        async with self._session.begin():
            events = await self._session.scalars(
                select(TelemetryAuditEvent)
                .order_by(TelemetryAuditEvent.occurred_at.desc())
                .limit(limit)
            )
            return list(events)

    async def processing_queue_depth(self) -> int:
        """Count durable work that has not reached a final state."""

        async with self._session.begin():
            value = await self._session.scalar(
                select(func.count(TelemetryProcessingJob.job_id)).where(
                    TelemetryProcessingJob.state.not_in(
                        [ProcessingJobState.COMPLETE, ProcessingJobState.FAILED]
                    )
                )
            )
            return int(value or 0)


def job_state_to_queue_status(state: ProcessingJobState) -> DeliveryQueueStatus:
    if state is ProcessingJobState.PENDING_QUEUE:
        return DeliveryQueueStatus.PENDING_QUEUE
    return DeliveryQueueStatus.QUEUED


def _as_utc(value: datetime) -> datetime:
    """SQLite may return a naive timestamp even for a timezone-aware column."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _same_processing_result(
    stored: ProcessedTelemetry, candidate: ProcessingResultValues
) -> bool:
    return (
        stored.temperature_c == candidate.temperature_c
        and stored.voltage_v == candidate.voltage_v
        and stored.operating_mode == candidate.operating_mode
        and stored.fault_names == candidate.fault_names
        and stored.classification is candidate.classification
        and stored.is_stale is candidate.is_stale
        and stored.processor_version == candidate.processor_version
    )
