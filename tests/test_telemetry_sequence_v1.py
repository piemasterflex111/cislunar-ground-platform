from __future__ import annotations

import pytest

from mission_ground.telemetry.sequence import (
    HALF_RANGE,
    SequenceDisposition,
    SequenceStateError,
    decide_sequence,
)

RAW = b"one validated telemetry frame"


@pytest.mark.parametrize(
    ("current", "disposition", "missing"),
    [
        (0, SequenceDisposition.FIRST_IN_ORDER, 0),
        (13, SequenceDisposition.FIRST_WITH_GAP, 13),
    ],
)
def test_first_packet_establishes_high_water_and_records_initial_gap(
    current: int,
    disposition: SequenceDisposition,
    missing: int,
) -> None:
    result = decide_sequence(
        last_high_water=None,
        current_sequence=current,
        incoming_bytes=RAW,
    )

    assert result.disposition is disposition
    assert result.accepted
    assert result.should_queue
    assert result.should_advance_high_water
    assert result.next_high_water == current
    assert result.missing_count == missing


def test_in_order_packet_advances_without_a_gap() -> None:
    result = decide_sequence(
        last_high_water=10,
        current_sequence=11,
        incoming_bytes=RAW,
    )

    assert result.disposition is SequenceDisposition.IN_ORDER
    assert result.should_queue
    assert result.next_high_water == 11
    assert result.missing_count == 0


def test_forward_gap_records_only_values_between_last_and_current() -> None:
    result = decide_sequence(
        last_high_water=10,
        current_sequence=13,
        incoming_bytes=RAW,
    )

    assert result.disposition is SequenceDisposition.GAP
    assert result.missing_count == 2
    assert result.next_high_water == 13


def test_exact_duplicate_is_idempotent_and_not_queued_again() -> None:
    result = decide_sequence(
        last_high_water=7,
        current_sequence=7,
        incoming_bytes=RAW,
        existing_packet_bytes=RAW,
    )

    assert result.disposition is SequenceDisposition.DUPLICATE
    assert result.accepted
    assert not result.should_queue
    assert not result.should_advance_high_water
    assert result.next_high_water == 7
    assert not result.is_unique


def test_same_packet_key_with_different_bytes_is_a_rejected_conflict() -> None:
    result = decide_sequence(
        last_high_water=7,
        current_sequence=7,
        incoming_bytes=RAW,
        existing_packet_bytes=b"different valid frame",
    )

    assert result.disposition is SequenceDisposition.CONFLICT
    assert not result.accepted
    assert not result.should_queue
    assert result.next_high_water == 7
    assert not result.is_unique


@pytest.mark.parametrize("distance", [HALF_RANGE, HALF_RANGE + 1])
def test_half_range_or_more_is_late_and_does_not_move_high_water(distance: int) -> None:
    last = 100
    current = (last + distance) % (1 << 32)

    result = decide_sequence(
        last_high_water=last,
        current_sequence=current,
        incoming_bytes=RAW,
    )

    assert result.disposition is SequenceDisposition.LATE_OR_OUT_OF_ORDER
    assert result.accepted
    assert result.should_queue
    assert not result.should_advance_high_water
    assert result.next_high_water == last


def test_late_previously_unseen_packet_is_processed_once() -> None:
    result = decide_sequence(
        last_high_water=20,
        current_sequence=18,
        incoming_bytes=RAW,
    )

    assert result.disposition is SequenceDisposition.LATE_OR_OUT_OF_ORDER
    assert result.is_unique
    assert result.should_queue


def test_equal_high_water_without_permanent_packet_record_exposes_state_error() -> None:
    with pytest.raises(SequenceStateError, match="permanent packet record"):
        decide_sequence(
            last_high_water=7,
            current_sequence=7,
            incoming_bytes=RAW,
        )


@pytest.mark.parametrize("invalid", [-1, 1 << 32, True])
def test_sequence_values_must_be_unsigned_32_bit_integers(invalid: int) -> None:
    with pytest.raises(ValueError, match="unsigned 32-bit"):
        decide_sequence(
            last_high_water=None,
            current_sequence=invalid,
            incoming_bytes=RAW,
        )
