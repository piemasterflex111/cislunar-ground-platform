from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from mission_ground.telemetry.queue import (
    ACK_SCRIPT,
    DEAD_LETTER_SCRIPT,
    ENQUEUE_SCRIPT,
    RECOVER_PROCESSING_SCRIPT,
    RETRY_SCRIPT,
    QueueConflictError,
    QueueMessage,
    QueueMessageError,
    QueueStateError,
    TelemetryQueue,
)

T = TypeVar("T")


def run(operation: Coroutine[Any, Any]) -> Any:
    return asyncio.run(operation)


class FakeRedis:
    """Small Redis list/key model implementing only the commands under test."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.lists: defaultdict[str, list[bytes]] = defaultdict(list)
        self.ping_result = True

    @staticmethod
    def _key(value: object) -> str:
        assert isinstance(value, str)
        return value

    @staticmethod
    def _raw(value: object) -> bytes:
        assert isinstance(value, bytes)
        return value

    def _remove_once(self, key: str, raw: bytes) -> int:
        values = self.lists[key]
        try:
            values.remove(raw)
        except ValueError:
            return 0
        return 1

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> int:
        keys = [self._key(item) for item in keys_and_args[:numkeys]]
        arguments = keys_and_args[numkeys:]

        if script == ENQUEUE_SCRIPT:
            marker, pending = keys
            raw = self._raw(arguments[0])
            existing = self.values.get(marker)
            if existing is None:
                self.values[marker] = raw
                self.lists[pending].insert(0, raw)
                return 1
            return 0 if existing == raw else -1

        if script == ACK_SCRIPT:
            processing, marker = keys
            raw = self._raw(arguments[0])
            removed = self._remove_once(processing, raw)
            if removed:
                self.values.pop(marker, None)
            return removed

        if script in {RETRY_SCRIPT, DEAD_LETTER_SCRIPT}:
            processing, destination = keys
            raw = self._raw(arguments[0])
            removed = self._remove_once(processing, raw)
            if removed:
                self.lists[destination].insert(0, raw)
            return removed

        if script == RECOVER_PROCESSING_SCRIPT:
            processing, pending = keys
            recovered = 0
            while self.lists[processing]:
                raw = self.lists[processing].pop()
                self.lists[pending].insert(0, raw)
                recovered += 1
            return recovered

        raise AssertionError("unexpected Lua script")

    async def brpoplpush(
        self,
        source: str,
        destination: str,
        timeout: int,
    ) -> bytes | None:
        del timeout
        if not self.lists[source]:
            return None
        raw = self.lists[source].pop()
        self.lists[destination].insert(0, raw)
        return raw

    async def llen(self, name: str) -> int:
        return len(self.lists[name])

    async def ping(self) -> bool:
        return self.ping_result


def make_queue() -> tuple[TelemetryQueue, FakeRedis]:
    redis = FakeRedis()
    return TelemetryQueue(redis, namespace="test:telemetry"), redis


def message(number: int = 1) -> QueueMessage:
    return QueueMessage(
        job_id=f"00000000-0000-0000-0000-{number:012d}",
        packet_id=f"TLM-P01-B0000002A-S{number:08X}",
    )


def test_queue_message_json_is_deterministic_and_has_only_identifiers() -> None:
    item = message()

    assert item.to_json_bytes() == (
        b'{"job_id":"00000000-0000-0000-0000-000000000001",'
        b'"packet_id":"TLM-P01-B0000002A-S00000001"}'
    )
    assert QueueMessage.from_json_bytes(item.to_json_bytes()) == item


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"[]",
        b'{"job_id":"job-1"}',
        b'{"job_id":"job-1","packet_id":"packet-1","attempt":1}',
        b'{"job_id":1,"packet_id":"packet-1"}',
    ],
)
def test_queue_message_rejects_invalid_or_expanded_contract(raw: bytes) -> None:
    with pytest.raises(QueueMessageError):
        QueueMessage.from_json_bytes(raw)


@pytest.mark.parametrize(
    ("job_id", "packet_id"),
    [
        ("not-a-uuid", "TLM-P01-B0000002A-S00000001"),
        ("00000000-0000-0000-0000-00000000000A", "TLM-P01-B0000002A-S00000001"),
        ("00000000-0000-0000-0000-000000000001", "bad-packet-id"),
    ],
)
def test_queue_message_requires_canonical_persistent_identifiers(
    job_id: str,
    packet_id: str,
) -> None:
    with pytest.raises(QueueMessageError):
        QueueMessage(job_id=job_id, packet_id=packet_id)


def test_enqueue_is_atomic_and_idempotent_for_exact_same_job() -> None:
    queue, redis = make_queue()
    item = message()

    assert run(queue.enqueue(item)) is True
    assert run(queue.enqueue(item)) is False
    assert redis.lists[queue.keys.pending] == [item.to_json_bytes()]
    assert redis.values[queue.keys.marker(item.job_id)] == item.to_json_bytes()


def test_enqueue_rejects_same_job_id_with_different_packet_reference() -> None:
    queue, redis = make_queue()
    original = message()
    conflict = QueueMessage(job_id=original.job_id, packet_id="TLM-P01-B0000002A-S00000002")
    run(queue.enqueue(original))

    with pytest.raises(QueueConflictError, match="different queue bytes"):
        run(queue.enqueue(conflict))

    assert redis.lists[queue.keys.pending] == [original.to_json_bytes()]


def test_claim_is_fifo_and_atomically_moves_pending_to_processing() -> None:
    queue, redis = make_queue()
    first = message(1)
    second = message(2)
    run(queue.enqueue(first))
    run(queue.enqueue(second))

    claimed = run(queue.claim(timeout_seconds=1))

    assert claimed == first
    assert redis.lists[queue.keys.pending] == [second.to_json_bytes()]
    assert redis.lists[queue.keys.processing] == [first.to_json_bytes()]


def test_claim_returns_none_when_no_work_is_available() -> None:
    queue, _ = make_queue()
    assert run(queue.claim(timeout_seconds=1)) is None


def test_claim_quarantines_malformed_work_instead_of_crash_looping() -> None:
    queue, redis = make_queue()
    malformed = b"not-json"
    redis.lists[queue.keys.pending].append(malformed)

    with pytest.raises(QueueMessageError):
        run(queue.claim(timeout_seconds=1))

    assert redis.lists[queue.keys.pending] == []
    assert redis.lists[queue.keys.processing] == []
    assert redis.lists[queue.keys.failed] == [malformed]


def test_ack_removes_processing_and_marker_after_completion() -> None:
    queue, redis = make_queue()
    item = message()
    run(queue.enqueue(item))
    assert run(queue.claim(timeout_seconds=1)) == item

    run(queue.ack(item))

    assert redis.lists[queue.keys.processing] == []
    assert queue.keys.marker(item.job_id) not in redis.values


def test_acknowledge_does_not_remove_marker_when_job_is_not_processing() -> None:
    queue, redis = make_queue()
    item = message()
    run(queue.enqueue(item))

    with pytest.raises(QueueStateError, match="not in the processing list"):
        run(queue.acknowledge(item))

    assert queue.keys.marker(item.job_id) in redis.values


def test_retry_returns_job_to_pending_and_retains_marker() -> None:
    queue, redis = make_queue()
    item = message()
    run(queue.enqueue(item))
    run(queue.claim(timeout_seconds=1))

    run(queue.retry(item))

    assert redis.lists[queue.keys.processing] == []
    assert redis.lists[queue.keys.pending] == [item.to_json_bytes()]
    assert redis.values[queue.keys.marker(item.job_id)] == item.to_json_bytes()


def test_dead_letter_retains_failed_job_and_marker_as_evidence() -> None:
    queue, redis = make_queue()
    item = message()
    run(queue.enqueue(item))
    run(queue.claim(timeout_seconds=1))

    run(queue.dead_letter(item))

    assert redis.lists[queue.keys.processing] == []
    assert redis.lists[queue.keys.failed] == [item.to_json_bytes()]
    assert redis.values[queue.keys.marker(item.job_id)] == item.to_json_bytes()


def test_recover_processing_returns_stranded_jobs_to_pending_in_fifo_order() -> None:
    queue, redis = make_queue()
    first = message(1)
    second = message(2)
    run(queue.enqueue(first))
    run(queue.enqueue(second))
    run(queue.claim(timeout_seconds=1))
    run(queue.claim(timeout_seconds=1))

    assert run(queue.recover_processing()) == 2
    assert run(queue.claim(timeout_seconds=1)) == first
    assert run(queue.claim(timeout_seconds=1)) == second
    assert redis.values[queue.keys.marker(first.job_id)] == first.to_json_bytes()
    assert redis.values[queue.keys.marker(second.job_id)] == second.to_json_bytes()


def test_depths_and_ping_report_each_queue_state() -> None:
    queue, redis = make_queue()
    first = message(1)
    second = message(2)
    run(queue.enqueue(first))
    run(queue.enqueue(second))
    run(queue.claim(timeout_seconds=1))
    run(queue.dead_letter(first))

    assert run(queue.queue_depth()) == 1
    assert run(queue.processing_depth()) == 0
    assert run(queue.failed_depth()) == 1
    assert run(queue.ping()) is True
    redis.ping_result = False
    assert run(queue.ping()) is False


# ── failure-mode regression tests ──────────────────────────────────


def test_ack_on_already_acked_job_does_not_compress_background_queue() -> None:
    """A double-ack must not remove an unrelated pending job that sits deepest
    in the pending list (FIFO invariant)."""
    queue, redis = make_queue()
    first = message(1)
    second = message(2)
    run(queue.enqueue(first))
    run(queue.enqueue(second))

    claimed = run(queue.claim(timeout_seconds=1))
    assert claimed == first

    run(queue.ack(first))

    with pytest.raises(QueueStateError, match="not in the processing list"):
        run(queue.ack(first))

    # pending queue must still hold 'second'
    assert run(queue.queue_depth()) == 1
    claimed2 = run(queue.claim(timeout_seconds=1))
    assert claimed2 == second
    assert redis.lists[queue.keys.failed] == []
    # processing holds the just-claimed second item — ack it before asserting
    run(queue.ack(second))
    assert redis.lists[queue.keys.processing] == []


def test_invalid_timeout_parameters_are_rejected_by_type_and_value_checks() -> None:
    """Claim validates timeout_seconds: type must be int, value must be
    non-negative.  bool is rejected because Python is True/False subclasses
    of int, and the code explicitly guards against that."""
    queue, _ = make_queue()

    for bad_val in [True, False, 3.14, "two"]:
        with pytest.raises((TypeError, ValueError)):
            run(queue.claim(timeout_seconds=bad_val))

    with pytest.raises(ValueError, match="negative"):
        run(queue.claim(timeout_seconds=-1))


def test_claim_leaves_pending_intact_when_malformed_entry_is_quarantined() -> None:
    """A malformed entry in pending must move to failed, but any healthy entries
    sitting behind the bad one must remain untouched in pending."""
    queue, redis = make_queue()

    healthy = message(2)
    malformed = b"corrupt-payload"

    run(queue.enqueue(healthy))
    # Append malformed to the right end so brpoplpush claims it first
    # (FIFO: claim pops from right, so rightmost item is reclaimed oldest).
    redis.lists[queue.keys.pending].append(malformed)

    # claim() catches QueueMessageError, raises after quarantining
    with pytest.raises(QueueMessageError):
        run(queue.claim(timeout_seconds=1))

    # pending queue must only contain healthy, not malformed
    assert malformed not in redis.lists[queue.keys.pending]
    assert redis.lists[queue.keys.processing] == []
    assert redis.lists[queue.keys.failed] == [malformed]
    # healthy is still claimable
    assert run(queue.claim(timeout_seconds=1)) == healthy


def test_dead_letter_on_no_longer_processing_job_raises_state_error() -> None:
    """dead_letter after retry must raise QueueStateError because the job was
    already moved back to pending."""
    queue, _ = make_queue()
    item = message()
    run(queue.enqueue(item))
    run(queue.claim(timeout_seconds=1))
    run(queue.retry(item))

    with pytest.raises(QueueStateError, match="not in the processing list"):
        run(queue.dead_letter(item))

    # pending must still hold the retried job
    assert run(queue.queue_depth()) == 1


def test_ack_on_retried_job_raises_state_error() -> None:
    """After a retry the job lives in pending again — ack must fail because it
    is no longer in processing."""
    queue, _ = make_queue()
    item = message()
    run(queue.enqueue(item))
    run(queue.claim(timeout_seconds=1))
    run(queue.retry(item))

    with pytest.raises(QueueStateError, match="not in the processing list"):
        run(queue.ack(item))

    # pending must still contain the item
    assert run(queue.queue_depth()) == 1


def test_recover_processing_moves_stranded_items_but_not_already_resolved() -> None:
    """RecoverProcessing should only report what was moved.  A second call
    when processing is empty must return 0 without error."""
    queue, _ = make_queue()
    run(queue.enqueue(message()))
    run(queue.claim(timeout_seconds=1))

    assert run(queue.recover_processing()) == 1
    assert run(queue.queue_depth()) == 1

    # Second call: nothing left in processing → 0, no error.
    assert run(queue.recover_processing()) == 0


def test_retry_on_non_processing_job_under_recover_processing_saves_recovery() -> None:
    """retry must raise QueueStateError when the item is not in processing, and
    robustly keep the processing list intact for subsequent recovery operations."""
    queue, _ = make_queue()

    with pytest.raises(QueueStateError):
        run(queue.retry(message()))


def test_dead_letter_on_empty_processing_raises_state_error() -> None:
    """dead_letter called when processing is empty must not corrupt the failed
    FIFO list or raise silently."""
    queue, _ = make_queue()

    with pytest.raises(QueueStateError, match="not in the processing list"):
        run(queue.dead_letter(message()))

    assert run(queue.failed_depth()) == 0  # failed stays empty


def test_recovery_order_preserves_fifo_on_the_largest_useful_batch() -> None:
    """place six messages in processing, recover, then drain and assert the
    FIFO order of backfill is identical to the original enqueue order."""
    queue, _ = make_queue()
    items = [message(i) for i in range(1, 7)]

    for item in items:
        run(queue.enqueue(item))

    for _ in items:
        run(queue.claim(timeout_seconds=1))

    run(queue.recover_processing())

    assert run(queue.queue_depth()) == 6
    assert run(queue.processing_depth()) == 0

    for expected in items:
        claimed = run(queue.claim(timeout_seconds=1))
        assert claimed == expected, (
            f"FIFO broken at index {items.index(expected)}: "
            f"expected {expected.job_id}, got {claimed.job_id}"
        )
