"""Phase 2 payload and third-party vendor telemetry simulator.

The payload-side encoder and the vendor forwarding boundary live in one service
but remain separate classes.  The vendor forwards the exact binary frame; it
does not decode and rebuild it before calling ground.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mission_ground.telemetry.codec import (
    CRC32_BIG_ENDIAN,
    TELEMETRY_V1_BODY_SIZE,
    UINT32_MAX,
    FaultFlag,
    OperatingMode,
    TelemetryFrame,
    crc32_iso_hdlc,
    encode_telemetry,
)
from mission_ground.telemetry.config import VendorSettings
from mission_ground.telemetry.structured_logging import configure_logging

LOG = configure_logging("vendor-simulator")


class FailureMode(str, Enum):
    NONE = "NONE"
    CORRUPT_CRC = "CORRUPT_CRC"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    SKIP_SEQUENCE = "SKIP_SEQUENCE"
    DUPLICATE_LAST = "DUPLICATE_LAST"


class OperatingModeName(str, Enum):
    SAFE = "SAFE"
    STANDBY = "STANDBY"
    SCIENCE = "SCIENCE"
    CALIBRATION = "CALIBRATION"


class TelemetryRequest(BaseModel):
    """Human-readable control values converted into one binary payload frame."""

    model_config = ConfigDict(extra="forbid")

    temperature_c: float = Field(ge=-100.0, le=150.0)
    voltage_v: float = Field(ge=0.0, le=50.0)
    operating_mode: OperatingModeName
    fault_flags: int = Field(default=0, ge=0, le=0x3F)
    failure_mode: FailureMode = FailureMode.NONE


def _timestamp_us(now: datetime) -> int:
    return int(now.timestamp() * 1_000_000)


def _scaled(value: float, factor: int) -> int:
    """Convert a display decimal to an exact integer wire unit."""

    return int(
        (Decimal(str(value)) * factor).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


class PayloadSimulator:
    """State belonging to the logical onboard payload source."""

    def __init__(self, *, boot_id: int, persist_boot_id: Callable[[int], None]) -> None:
        self.boot_id = boot_id
        self.next_sequence = 0
        self._persist_boot_id = persist_boot_id

    def _roll_session_if_needed(self) -> None:
        if self.next_sequence <= UINT32_MAX:
            return
        if self.boot_id == UINT32_MAX:
            raise RuntimeError("payload boot identifier space is exhausted")
        self.boot_id += 1
        self._persist_boot_id(self.boot_id)
        self.next_sequence = 0

    def make_frame(self, request: TelemetryRequest) -> tuple[bytes, str]:
        if request.failure_mode is FailureMode.SKIP_SEQUENCE:
            self.next_sequence += 1
        self._roll_session_if_needed()
        sequence = self.next_sequence
        self.next_sequence += 1
        frame = TelemetryFrame(
            payload_id=1,
            boot_id=self.boot_id,
            sequence_number=sequence,
            timestamp_us=_timestamp_us(datetime.now(UTC)),
            temperature_centi_c=_scaled(request.temperature_c, 100),
            voltage_mv=_scaled(request.voltage_v, 1_000),
            operating_mode=OperatingMode[request.operating_mode.value],
            fault_flags=FaultFlag(request.fault_flags),
        )
        return encode_telemetry(frame), frame.packet_id


class VendorForwarder:
    """The third-party boundary that forwards exact payload bytes to ground."""

    def __init__(self, settings: VendorSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    async def forward(self, raw: bytes, *, request_id: uuid.UUID) -> httpx.Response:
        """Retry a temporary handoff with the same identity and exact bytes."""

        for attempt in range(1, 4):
            retry_reason: str
            try:
                response = await self.client.post(
                    f"{self.settings.ground_api_url}/vendor/payload-data",
                    content=raw,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Request-ID": str(request_id),
                        # Phase 2 teaching credential only.  Phase 5 replaces it
                        # with mutual Transport Layer Security certificate identity.
                        "X-Vendor-Token": self.settings.vendor_token,
                    },
                )
            except httpx.HTTPError as exc:
                retry_reason = type(exc).__name__
                if attempt == 3:
                    raise
            else:
                if response.status_code < 500 or attempt == 3:
                    return response
                retry_reason = f"HTTP_{response.status_code}"
            LOG.warning(
                "temporary ground handoff failure; retrying exact delivery",
                extra={
                    "event_name": "telemetry.forward.retry",
                    "request_id": str(request_id),
                    "attempt": attempt,
                    "error_reason": retry_reason,
                },
            )
            await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
        raise AssertionError("unreachable vendor retry state")


def _load_next_boot_id(path_text: str) -> tuple[int, Callable[[int], None]]:
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = 0
    if path.exists():
        text = path.read_text(encoding="ascii").strip()
        if text:
            previous = int(text)
    if not 0 <= previous < UINT32_MAX:
        raise RuntimeError("persisted boot identifier cannot be incremented safely")
    current = previous + 1

    def persist(value: int) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(f"{value}\n", encoding="ascii")
        os.replace(temporary, path)

    persist(current)
    return current, persist


def _apply_wire_failure(raw: bytes, failure_mode: FailureMode) -> bytes:
    changed = bytearray(raw)
    if failure_mode is FailureMode.CORRUPT_CRC:
        changed[-1] ^= 0x01
    elif failure_mode is FailureMode.UNSUPPORTED_VERSION:
        changed[0] = 2
        checksum = crc32_iso_hdlc(bytes(changed[:TELEMETRY_V1_BODY_SIZE]))
        changed[TELEMETRY_V1_BODY_SIZE:] = CRC32_BIG_ENDIAN.pack(checksum)
    return bytes(changed)


def create_app(settings: VendorSettings | None = None) -> FastAPI:
    configured = settings or VendorSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        boot_id, persist = _load_next_boot_id(configured.boot_id_file)
        app.state.lock = asyncio.Lock()
        app.state.payload = PayloadSimulator(boot_id=boot_id, persist_boot_id=persist)
        app.state.client = httpx.AsyncClient(timeout=10.0)
        app.state.forwarder = VendorForwarder(configured, app.state.client)
        app.state.last_raw = None
        app.state.last_message_id = None
        app.state.auto_task = None
        if configured.auto_send_interval_seconds > 0:
            app.state.auto_task = asyncio.create_task(_automatic_loop(app))
        LOG.info(
            "vendor simulator ready",
            extra={"event_name": "simulator.started", "boot_id": boot_id},
        )
        try:
            yield
        finally:
            if app.state.auto_task is not None:
                app.state.auto_task.cancel()
                with suppress(asyncio.CancelledError):
                    await app.state.auto_task
            await app.state.client.aclose()

    app = FastAPI(title="Phase 2 Vendor Simulator", version="0.2.0", lifespan=lifespan)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "vendor-simulator"}

    @app.post("/simulator/telemetry")
    async def submit_telemetry(request: TelemetryRequest) -> dict[str, object]:
        async with app.state.lock:
            if request.failure_mode is FailureMode.DUPLICATE_LAST:
                if app.state.last_raw is None:
                    raise HTTPException(
                        status_code=409,
                        detail="DUPLICATE_LAST requires one earlier telemetry frame",
                    )
                raw = app.state.last_raw
                message_id = app.state.last_message_id
            else:
                raw, message_id = app.state.payload.make_frame(request)
                raw = _apply_wire_failure(raw, request.failure_mode)
                if request.failure_mode in {FailureMode.NONE, FailureMode.SKIP_SEQUENCE}:
                    app.state.last_raw = raw
                    app.state.last_message_id = message_id

        request_id = uuid.uuid4()
        try:
            ground_response = await app.state.forwarder.forward(raw, request_id=request_id)
        except httpx.HTTPError as exc:
            LOG.warning(
                "ground telemetry handoff failed",
                extra={
                    "event_name": "telemetry.forward.failed",
                    "request_id": str(request_id),
                    "message_id": message_id,
                    "error_reason": type(exc).__name__,
                },
            )
            raise HTTPException(status_code=502, detail="ground-api could not be reached") from exc

        try:
            response_body: object = ground_response.json()
        except ValueError:
            response_body = {"detail": ground_response.text[:500]}
        LOG.info(
            "telemetry frame forwarded",
            extra={
                "event_name": "telemetry.forward.completed",
                "request_id": str(request_id),
                "message_id": message_id,
                "ground_status": ground_response.status_code,
                "failure_mode": request.failure_mode.value,
            },
        )
        return {
            "message_id": message_id,
            "request_id": str(request_id),
            "raw_hex": raw.hex(),
            "ground_status": ground_response.status_code,
            "ground_response": response_body,
        }

    async def _automatic_loop(app: FastAPI) -> None:
        request = TelemetryRequest(
            temperature_c=28.4,
            voltage_v=28.1,
            operating_mode=OperatingModeName.SCIENCE,
        )
        while True:
            await asyncio.sleep(configured.auto_send_interval_seconds)
            try:
                await submit_telemetry(request)
            except Exception:
                LOG.exception(
                    "automatic telemetry send failed",
                    extra={"event_name": "telemetry.auto_send.failed"},
                )

    return app


app = create_app()
