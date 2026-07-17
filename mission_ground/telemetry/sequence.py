"""Pure decisions for persistent telemetry sequence tracking.

The caller loads the high-water mark and any existing packet bytes from
PostgreSQL, calls :func:`decide_sequence`, and persists the returned decision
in one transaction.  This module intentionally owns no process-local state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

UINT32_MODULUS = 1 << 32
UINT32_MAX = UINT32_MODULUS - 1
HALF_RANGE = 1 << 31


class SequenceDisposition(str, Enum):
    """Named outcomes from the Interface Control Document sequence table."""

    FIRST_IN_ORDER = "FIRST_IN_ORDER"
    FIRST_WITH_GAP = "FIRST_WITH_GAP"
    IN_ORDER = "IN_ORDER"
    GAP = "GAP"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    LATE_OR_OUT_OF_ORDER = "LATE_OR_OUT_OF_ORDER"


class SequenceStateError(ValueError):
    """Persistent sequence state contradicts its permanent packet records."""


@dataclass(frozen=True, slots=True)
class SequenceDecision:
    """A persistence-neutral decision for one already validated frame."""

    disposition: SequenceDisposition
    accepted: bool
    should_queue: bool
    should_advance_high_water: bool
    next_high_water: int | None
    missing_count: int = 0

    @property
    def is_unique(self) -> bool:
        """Return whether this delivery represents new logical packet evidence."""

        return self.disposition not in {
            SequenceDisposition.DUPLICATE,
            SequenceDisposition.CONFLICT,
        }


def _validate_uint32(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= UINT32_MAX:
        raise ValueError(f"{name} must be an unsigned 32-bit integer")


def decide_sequence(
    *,
    last_high_water: int | None,
    current_sequence: int,
    incoming_bytes: bytes,
    existing_packet_bytes: bytes | None = None,
) -> SequenceDecision:
    """Apply the version 1 sequence algorithm without storing any state.

    ``existing_packet_bytes`` is the permanent record found for the same
    ``(payload_id, boot_id, sequence_number)`` key.  An exact match is an
    idempotent duplicate; different bytes under the same key are a conflict.
    """

    _validate_uint32("current_sequence", current_sequence)
    if last_high_water is not None:
        _validate_uint32("last_high_water", last_high_water)
    if not isinstance(incoming_bytes, bytes) or not incoming_bytes:
        raise ValueError("incoming_bytes must be non-empty bytes")
    if existing_packet_bytes is not None and not isinstance(existing_packet_bytes, bytes):
        raise TypeError("existing_packet_bytes must be bytes or None")

    if existing_packet_bytes is not None:
        if existing_packet_bytes == incoming_bytes:
            return SequenceDecision(
                disposition=SequenceDisposition.DUPLICATE,
                accepted=True,
                should_queue=False,
                should_advance_high_water=False,
                next_high_water=last_high_water,
            )
        return SequenceDecision(
            disposition=SequenceDisposition.CONFLICT,
            accepted=False,
            should_queue=False,
            should_advance_high_water=False,
            next_high_water=last_high_water,
        )

    if last_high_water is None:
        disposition = (
            SequenceDisposition.FIRST_IN_ORDER
            if current_sequence == 0
            else SequenceDisposition.FIRST_WITH_GAP
        )
        return SequenceDecision(
            disposition=disposition,
            accepted=True,
            should_queue=True,
            should_advance_high_water=True,
            next_high_water=current_sequence,
            missing_count=current_sequence,
        )

    forward_distance = (current_sequence - last_high_water) % UINT32_MODULUS
    if forward_distance == 0:
        raise SequenceStateError(
            "the current sequence equals the high-water mark, but its permanent "
            "packet record was not supplied"
        )
    if forward_distance == 1:
        return SequenceDecision(
            disposition=SequenceDisposition.IN_ORDER,
            accepted=True,
            should_queue=True,
            should_advance_high_water=True,
            next_high_water=current_sequence,
        )
    if 2 <= forward_distance < HALF_RANGE:
        return SequenceDecision(
            disposition=SequenceDisposition.GAP,
            accepted=True,
            should_queue=True,
            should_advance_high_water=True,
            next_high_water=current_sequence,
            missing_count=forward_distance - 1,
        )
    return SequenceDecision(
        disposition=SequenceDisposition.LATE_OR_OUT_OF_ORDER,
        accepted=True,
        should_queue=True,
        should_advance_high_water=False,
        next_high_water=last_high_water,
    )
