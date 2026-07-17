from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")
pytest.importorskip("pydantic")

from fastapi import HTTPException  # noqa: E402

from mission_ground.services.vendor_simulator import (  # noqa: E402
    FailureMode,
    OperatingModeName,
    PayloadSimulator,
    TelemetryRequest,
    VendorForwarder,
    _apply_wire_failure,
    _load_next_boot_id,
    _scaled,
    create_app,
)
from mission_ground.telemetry.codec import (  # noqa: E402
    TELEMETRY_V1_BODY_SIZE,
    FaultFlag,
    OperatingMode,
    TelemetryFrame,
    TelemetryRejectionReason,
    TelemetryValidationError,
    crc32_iso_hdlc,
    decode_telemetry,
    encode_telemetry,
)
from mission_ground.telemetry.config import VendorSettings  # noqa: E402


def run[T](operation: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(operation)


def nominal_request(*, failure_mode: FailureMode = FailureMode.NONE) -> TelemetryRequest:
    return TelemetryRequest(
        temperature_c=28.4,
        voltage_v=28.1,
        operating_mode=OperatingModeName.SCIENCE,
        fault_flags=0,
        failure_mode=failure_mode,
    )


def known_raw() -> bytes:
    return encode_telemetry(
        TelemetryFrame(
            payload_id=1,
            boot_id=42,
            sequence_number=1,
            timestamp_us=1_784_203_200_000_000,
            temperature_centi_c=2_840,
            voltage_mv=28_100,
            operating_mode=OperatingMode.SCIENCE,
            fault_flags=FaultFlag.NONE,
        )
    )


def route_endpoint(app: Any, path: str, method: str) -> Any:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route {method} {path} was not registered")


@pytest.mark.parametrize(
    ("value", "factor", "expected"),
    [
        (28.405, 100, 2_841),
        (-1.005, 100, -101),
        (28.1005, 1_000, 28_101),
    ],
)
def test_scaled_uses_decimal_half_away_from_zero(
    value: float,
    factor: int,
    expected: int,
) -> None:
    assert _scaled(value, factor) == expected


def test_wire_corruption_changes_crc_without_changing_body() -> None:
    original = known_raw()
    corrupted = _apply_wire_failure(original, FailureMode.CORRUPT_CRC)

    assert corrupted[:-1] == original[:-1]
    assert corrupted[-1] == original[-1] ^ 1
    with pytest.raises(TelemetryValidationError) as raised:
        decode_telemetry(corrupted, ground_received_at=datetime(2026, 7, 16, 12, 1, tzinfo=UTC))
    assert raised.value.reason is TelemetryRejectionReason.CRC_MISMATCH


def test_unsupported_version_failure_recomputes_crc_to_isolate_version_rule() -> None:
    changed = _apply_wire_failure(known_raw(), FailureMode.UNSUPPORTED_VERSION)

    assert changed[0] == 2
    stored_crc = int.from_bytes(changed[TELEMETRY_V1_BODY_SIZE:], "big")
    assert stored_crc == crc32_iso_hdlc(changed[:TELEMETRY_V1_BODY_SIZE])
    with pytest.raises(TelemetryValidationError) as raised:
        decode_telemetry(changed, ground_received_at=datetime(2026, 7, 16, 12, 1, tzinfo=UTC))
    assert raised.value.reason is TelemetryRejectionReason.UNSUPPORTED_PROTOCOL_VERSION


def test_payload_simulator_emits_sequence_gap_without_reusing_an_identifier() -> None:
    persisted: list[int] = []
    simulator = PayloadSimulator(boot_id=9, persist_boot_id=persisted.append)

    first_raw, first_id = simulator.make_frame(nominal_request())
    gap_raw, gap_id = simulator.make_frame(
        nominal_request(failure_mode=FailureMode.SKIP_SEQUENCE)
    )
    receipt_time = datetime.now(UTC)
    first = decode_telemetry(first_raw, ground_received_at=receipt_time)
    gap = decode_telemetry(gap_raw, ground_received_at=receipt_time)

    assert (first.sequence_number, gap.sequence_number) == (0, 2)
    assert first_id == "TLM-P01-B00000009-S00000000"
    assert gap_id == "TLM-P01-B00000009-S00000002"
    assert persisted == []


def test_payload_simulator_rolls_boot_before_sequence_number_would_wrap() -> None:
    persisted: list[int] = []
    simulator = PayloadSimulator(boot_id=9, persist_boot_id=persisted.append)
    simulator.next_sequence = 1 << 32

    raw, message_id = simulator.make_frame(nominal_request())
    decoded = decode_telemetry(raw, ground_received_at=datetime.now(UTC))

    assert decoded.boot_id == 10
    assert decoded.sequence_number == 0
    assert message_id == "TLM-P01-B0000000A-S00000000"
    assert persisted == [10]


def test_load_next_boot_id_persists_increment_and_callback_updates_file(tmp_path: Any) -> None:
    boot_file = tmp_path / "state" / "boot_id"
    boot_file.parent.mkdir()
    boot_file.write_text("41\n", encoding="ascii")

    boot_id, persist = _load_next_boot_id(str(boot_file))

    assert boot_id == 42
    assert boot_file.read_text(encoding="ascii") == "42\n"
    persist(43)
    assert boot_file.read_text(encoding="ascii") == "43\n"


class RecordingClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> Any:
        self.calls.append((url, content, headers))
        return self.response


def test_vendor_forwarder_sends_exact_bytes_and_required_identity_headers() -> None:
    response = httpx.Response(202, json={"status": "QUEUED"})
    client = RecordingClient(response)
    settings = VendorSettings(
        ground_api_url="http://ground-api:8080",
        vendor_token="vendor-secret",
        boot_id_file="/tmp/vendor-boot-id",
        auto_send_interval_seconds=0,
    )
    request_id = uuid.UUID("5b0ef52e-1517-4b70-8e9d-f49ef2f72d8d")

    returned = run(VendorForwarder(settings, client).forward(known_raw(), request_id=request_id))

    assert returned is response
    assert client.calls == [
        (
            "http://ground-api:8080/vendor/payload-data",
            known_raw(),
            {
                "Content-Type": "application/octet-stream",
                "X-Request-ID": str(request_id),
                "X-Vendor-Token": "vendor-secret",
            },
        )
    ]


class RetryClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    async def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> Any:
        self.calls.append((url, content, headers))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize(
    "first_outcome",
    [
        httpx.Response(503, json={"reason": "REDIS_UNAVAILABLE"}),
        httpx.ReadTimeout("response was lost"),
    ],
)
def test_vendor_temporary_retry_reuses_exact_request_identity_and_bytes(
    first_outcome: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_wait(delay: float) -> None:
        del delay

    monkeypatch.setattr("mission_ground.services.vendor_simulator.asyncio.sleep", no_wait)
    client = RetryClient(
        [first_outcome, httpx.Response(200, json={"status": "QUEUED"})]
    )
    settings = VendorSettings(
        ground_api_url="http://ground-api:8080",
        vendor_token="vendor-secret",
        boot_id_file="/tmp/vendor-boot-id",
        auto_send_interval_seconds=0,
    )
    request_id = uuid.UUID("5b0ef52e-1517-4b70-8e9d-f49ef2f72d8d")

    response = run(
        VendorForwarder(settings, client).forward(known_raw(), request_id=request_id)
    )

    assert response.status_code == 200
    assert len(client.calls) == 2
    assert client.calls[0] == client.calls[1]
    assert client.calls[0][1] == known_raw()
    assert client.calls[0][2]["X-Request-ID"] == str(request_id)


class RecordingForwarder:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, uuid.UUID]] = []

    async def forward(self, raw: bytes, *, request_id: uuid.UUID) -> Any:
        self.calls.append((raw, request_id))
        return httpx.Response(202, json={"status": "QUEUED"})


def test_simulator_endpoint_forwards_valid_frame_then_exact_duplicate() -> None:
    settings = VendorSettings(
        ground_api_url="http://ground-api:8080",
        vendor_token="vendor-secret",
        boot_id_file="/tmp/vendor-boot-id",
        auto_send_interval_seconds=0,
    )
    app = create_app(settings)
    app.state.lock = asyncio.Lock()
    app.state.payload = PayloadSimulator(boot_id=42, persist_boot_id=lambda value: None)
    app.state.last_raw = None
    app.state.last_message_id = None
    app.state.forwarder = RecordingForwarder()
    submit = route_endpoint(app, "/simulator/telemetry", "POST")

    first = run(submit(nominal_request()))
    duplicate = run(submit(nominal_request(failure_mode=FailureMode.DUPLICATE_LAST)))

    assert first["ground_status"] == 202
    assert duplicate["ground_status"] == 202
    assert duplicate["message_id"] == first["message_id"]
    assert duplicate["raw_hex"] == first["raw_hex"]
    assert duplicate["request_id"] != first["request_id"]
    assert len(app.state.forwarder.calls) == 2
    assert app.state.forwarder.calls[0][0] == app.state.forwarder.calls[1][0]


def test_simulator_endpoint_rejects_duplicate_before_first_valid_frame() -> None:
    settings = VendorSettings(
        ground_api_url="http://ground-api:8080",
        vendor_token="vendor-secret",
        boot_id_file="/tmp/vendor-boot-id",
        auto_send_interval_seconds=0,
    )
    app = create_app(settings)
    app.state.lock = asyncio.Lock()
    app.state.payload = PayloadSimulator(boot_id=42, persist_boot_id=lambda value: None)
    app.state.last_raw = None
    app.state.last_message_id = None
    app.state.forwarder = RecordingForwarder()
    submit = route_endpoint(app, "/simulator/telemetry", "POST")

    with pytest.raises(HTTPException) as raised:
        run(submit(nominal_request(failure_mode=FailureMode.DUPLICATE_LAST)))
    assert raised.value.status_code == 409
