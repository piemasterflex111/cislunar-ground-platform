"""Small, visible Redis queue for telemetry processing jobs.

Redis is the waiting line, not the source of mission evidence.  Each queue
entry therefore contains only identifiers for a durable database job and its
logical packet.  Processing attempts, errors, and results belong in the
database record loaded through ``job_id``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, Self, cast
from uuid import UUID

if TYPE_CHECKING:
    from redis.asyncio import Redis


ENQUEUE_SCRIPT = """-- telemetry_queue:enqueue
local existing = redis.call('GET', KEYS[1])
if not existing then
    redis.call('SET', KEYS[1], ARGV[1])
    redis.call('LPUSH', KEYS[2], ARGV[1])
    return 1
end
if existing == ARGV[1] then
    return 0
end
return -1
"""

ACK_SCRIPT = """-- telemetry_queue:ack
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
if removed == 1 then
    redis.call('DEL', KEYS[2])
end
return removed
"""

RETRY_SCRIPT = """-- telemetry_queue:retry
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
if removed == 1 then
    redis.call('LPUSH', KEYS[2], ARGV[1])
end
return removed
"""

DEAD_LETTER_SCRIPT = """-- telemetry_queue:dead_letter
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
if removed == 1 then
    redis.call('LPUSH', KEYS[2], ARGV[1])
end
return removed
"""

RECOVER_PROCESSING_SCRIPT = """-- telemetry_queue:recover_processing
local recovered = 0
while true do
    local item = redis.call('RPOPLPUSH', KEYS[1], KEYS[2])
    if not item then
        return recovered
    end
    recovered = recovered + 1
end
"""


class AsyncRedis(Protocol):
    """The small ``redis.asyncio.Redis`` surface used by this queue."""

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> Any: ...

    async def brpoplpush(
        self,
        source: str,
        destination: str,
        timeout: int,
    ) -> bytes | str | None: ...

    async def llen(self, name: str) -> int: ...

    async def ping(self) -> bool: ...


class QueueMessageError(ValueError):
    """A queue entry does not contain the exact two-field JSON contract."""


class QueueConflictError(RuntimeError):
    """A job identifier was reused for a different packet reference."""


class QueueStateError(RuntimeError):
    """A requested transition did not find the job in the processing list."""


@dataclass(frozen=True, slots=True)
class QueueMessage:
    """The complete and deliberately small telemetry queue contract."""

    job_id: str
    packet_id: str

    def __post_init__(self) -> None:
        for name, value in (("job_id", self.job_id), ("packet_id", self.packet_id)):
            if not isinstance(value, str) or not value or value.strip() != value:
                raise QueueMessageError(f"{name} must be a non-empty, trimmed string")
            if len(value) > 128:
                raise QueueMessageError(f"{name} must not exceed 128 characters")
        try:
            parsed_job_id = UUID(self.job_id)
        except ValueError as exc:
            raise QueueMessageError("job_id must be a canonical UUID string") from exc
        if str(parsed_job_id) != self.job_id:
            raise QueueMessageError("job_id must be a canonical lowercase UUID string")
        if re.fullmatch(r"TLM-P\d{2}-B[0-9A-F]{8}-S[0-9A-F]{8}", self.packet_id) is None:
            raise QueueMessageError("packet_id must use the version 1 telemetry identifier format")

    def to_json_bytes(self) -> bytes:
        """Serialize with stable key order and no insignificant whitespace."""

        return json.dumps(
            {"job_id": self.job_id, "packet_id": self.packet_id},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")

    @classmethod
    def from_json_bytes(cls, raw: bytes | str) -> Self:
        """Parse one queue entry and reject missing or additional fields."""

        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise QueueMessageError("queue entry is not valid JSON") from exc
        if not isinstance(decoded, dict) or set(decoded) != {"job_id", "packet_id"}:
            raise QueueMessageError("queue entry must contain only job_id and packet_id")
        job_id = decoded["job_id"]
        packet_id = decoded["packet_id"]
        if not isinstance(job_id, str) or not isinstance(packet_id, str):
            raise QueueMessageError("job_id and packet_id must both be strings")
        return cls(job_id=job_id, packet_id=packet_id)


@dataclass(frozen=True, slots=True)
class QueueKeys:
    """Redis keys used for the telemetry job lifecycle."""

    pending: str
    processing: str
    failed: str
    marker_prefix: str

    @classmethod
    def for_namespace(cls, namespace: str) -> Self:
        cleaned = namespace.strip(":")
        if not cleaned:
            raise ValueError("queue namespace must not be empty")
        return cls(
            pending=f"{cleaned}:pending",
            processing=f"{cleaned}:processing",
            failed=f"{cleaned}:failed",
            marker_prefix=f"{cleaned}:marker",
        )

    def marker(self, job_id: str) -> str:
        return f"{self.marker_prefix}:{job_id}"


class TelemetryQueue:
    """Redis-backed pending, processing, and failed telemetry work lists."""

    def __init__(
        self,
        redis: AsyncRedis,
        *,
        namespace: str = "ground:telemetry:jobs",
    ) -> None:
        self._redis = redis
        self.keys = QueueKeys.for_namespace(namespace)

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        namespace: str = "ground:telemetry:jobs",
    ) -> Self:
        """Create the production adapter using a ``redis.asyncio`` client."""

        # The import stays here so pure unit tests can exercise the queue model
        # before optional runtime dependencies are installed on the host.
        from redis.asyncio import Redis

        client: Redis = Redis.from_url(url, decode_responses=False)
        # redis-py's overloads are wider than this deliberately tiny protocol;
        # the adapter uses only the compatible runtime subset declared above.
        return cls(cast(AsyncRedis, client), namespace=namespace)

    async def enqueue(self, message: QueueMessage) -> bool:
        """Atomically enqueue once; return ``False`` for an exact duplicate."""

        raw = message.to_json_bytes()
        result = int(
            await self._redis.eval(
                ENQUEUE_SCRIPT,
                2,
                self.keys.marker(message.job_id),
                self.keys.pending,
                raw,
            )
        )
        if result == -1:
            raise QueueConflictError(
                f"job_id {message.job_id!r} already identifies different queue bytes"
            )
        return result == 1

    async def claim(self, *, timeout_seconds: int = 0) -> QueueMessage | None:
        """Block for the oldest pending job and atomically mark it processing."""

        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            raise TypeError("timeout_seconds must be an integer")
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        raw = await self._redis.brpoplpush(
            self.keys.pending,
            self.keys.processing,
            timeout_seconds,
        )
        if raw is None:
            return None
        try:
            return QueueMessage.from_json_bytes(raw)
        except QueueMessageError as exc:
            # A malformed entry must not crash-loop forever.  Preserve its raw
            # bytes in failed work so an operator can investigate the queue.
            removed = int(
                await self._redis.eval(
                    DEAD_LETTER_SCRIPT,
                    2,
                    self.keys.processing,
                    self.keys.failed,
                    raw,
                )
            )
            if removed != 1:
                raise QueueStateError(
                    "malformed queue entry could not be quarantined"
                ) from exc
            raise

    async def ack(self, message: QueueMessage) -> None:
        """Remove completed work and its marker after the database commit.

        The worker must commit the processed result and completed job state to
        the database before calling this method.  A crash before this call
        leaves the item recoverable in the processing list.
        """

        removed = int(
            await self._redis.eval(
                ACK_SCRIPT,
                2,
                self.keys.processing,
                self.keys.marker(message.job_id),
                message.to_json_bytes(),
            )
        )
        if removed != 1:
            raise QueueStateError(f"job {message.job_id!r} is not in the processing list")

    async def acknowledge(self, message: QueueMessage) -> None:
        """Use the more explicit name for :meth:`ack`."""

        await self.ack(message)

    async def retry(self, message: QueueMessage) -> None:
        """Atomically return processing work to pending while retaining its marker."""

        removed = int(
            await self._redis.eval(
                RETRY_SCRIPT,
                2,
                self.keys.processing,
                self.keys.pending,
                message.to_json_bytes(),
            )
        )
        if removed != 1:
            raise QueueStateError(f"job {message.job_id!r} is not in the processing list")

    async def dead_letter(self, message: QueueMessage) -> None:
        """Move repeatedly failing work to failed while retaining deduplication evidence."""

        removed = int(
            await self._redis.eval(
                DEAD_LETTER_SCRIPT,
                2,
                self.keys.processing,
                self.keys.failed,
                message.to_json_bytes(),
            )
        )
        if removed != 1:
            raise QueueStateError(f"job {message.job_id!r} is not in the processing list")

    async def recover_processing(self) -> int:
        """Return all work stranded by a worker exit to pending in FIFO order."""

        return int(
            await self._redis.eval(
                RECOVER_PROCESSING_SCRIPT,
                2,
                self.keys.processing,
                self.keys.pending,
            )
        )

    async def queue_depth(self) -> int:
        """Return the number of jobs currently waiting in pending."""

        return int(await self._redis.llen(self.keys.pending))

    async def processing_depth(self) -> int:
        """Return the number of jobs currently held by the worker."""

        return int(await self._redis.llen(self.keys.processing))

    async def failed_depth(self) -> int:
        """Return the number of jobs retained for manual investigation."""

        return int(await self._redis.llen(self.keys.failed))

    async def ping(self) -> bool:
        """Return whether Redis answered its lightweight dependency probe."""

        return bool(await self._redis.ping())
