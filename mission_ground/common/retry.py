from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 30,
    initial_delay: float = 0.5,
    max_delay: float = 5.0,
) -> T:
    delay = initial_delay
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return await operation()
        except Exception as exc:  # startup dependency retry boundary
            last_error = exc
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, max_delay)
    assert last_error is not None
    raise last_error
