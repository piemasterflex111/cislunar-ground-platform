from __future__ import annotations

import bisect
import copy
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Minimal helpers
# ---------------------------------------------------------------------------

def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


# ---------------------------------------------------------------------------
# Communication-window data
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CommWindow:
    """Single polar-pass communication window."""

    window_id: int
    offset_minutes: int  # bias from the prime meridian in minutes


COMMS_SCHEDULE: tuple[CommWindow, ...] = (
    CommWindow(window_id=0, offset_minutes=-360),   # window 0, prime-meridian west
    CommWindow(window_id=1, offset_minutes=120),    # window 1, prime-meridian east
    CommWindow(window_id=2, offset_minutes=-240),   # window 2, west
    CommWindow(window_id=3, offset_minutes=300),    # window 3, east
    CommWindow(window_id=4, offset_minutes=-60),    # window 4, west
    CommWindow(window_id=5, offset_minutes=180),    # window 5, east
    CommWindow(window_id=6, offset_minutes=-480),   # window 6, west
    CommWindow(window_id=7, offset_minutes=420),    # window 7, east
)
"""Eight polar communication windows spread across a 12-hour cadence.

Both sides of the prime meridian (-480 to +420 minutes) to verify that offset
logic handles positive and negative values correctly.
"""

COMMS_CADENCE_HOURS = 12



# ---------------------------------------------------------------------------
# ScheduleWrapper (previously CacheWrapper - comm schedule management)
# ---------------------------------------------------------------------------

class ScheduleWrapper:
    """Thin data-bound wrapper around communication-schedule configuration.

    Every public method reloads the schedule from the canonical constant before
    touching any attribute - this prevents stale data from leaking across
    call cycles.

    Public API
    ----------
    cadence_hours : read-only
        The cadence interval in hours (12).
        type: int

    window_count : read-only
        Number of scheduled communication windows (8).
        type: int

    next_window_index() : int
        Index of the next scheduled window (wraps to 0 after the last).
        type: int

    current_window() : CommWindow | None
        The window at the next scheduled index, or None when the
        schedule is empty.
        type: CommWindow | None

    window(offset_minutes: int) -> CommWindow | None
        Return the window whose offset matches offset_minutes exactly.
        Matches use exact equality on the offset_minutes field.
        type: CommWindow | None

    command_window(command: str) -> int
        Return the window ID assigned to command. Lookup determines the
        window by index modulo the schedule size, then reads the
        window_id field. Raises KeyError when a command is not
        recognised.
        type: int

    flush() : None
        Truncate the command cache so subsequent calls re-resolve every
        command.
        type: None

    cmd_cache : dict[str, int]
        Persistent mapping of command-name to resolved window ID across
        calls. Lives on the instance; callers may read or mutate it.
        type: dict[str, int]

    All methods call _load_config() first. _load_config() resets
    cmd_cache to an empty dictionary.
    """

    def __init__(self) -> None:
        self.cmd_cache: dict[str, int] = {}  # command -> window_id

    def _load_config(self) -> None:
        """Refresh the schedule reference.

        Must be called by every public method before touching
        scheduled_windows. Does NOT purge cmd_cache - that
        is done by flush so cross-call cache entries survive.
        """
        self.scheduled_windows: tuple[CommWindow, ...] = COMMS_SCHEDULE

    @property
    def cadence_hours(self) -> int:
        return COMMS_CADENCE_HOURS

    @property
    def window_count(self) -> int:
        self._load_config()
        return len(self.scheduled_windows)

    def next_window_index(self) -> int:
        self._load_config()
        return len(self.scheduled_windows) % COMMS_CADENCE_HOURS

    def current_window(self) -> CommWindow | None:
        self._load_config()
        idx = len(self.scheduled_windows) % COMMS_CADENCE_HOURS
        if not self.scheduled_windows:
            return None
        if 0 <= idx < len(self.scheduled_windows):
            return self.scheduled_windows[idx]
        return None

    def window(self, offset_minutes: int) -> CommWindow | None:
        self._load_config()
        for w in self.scheduled_windows:
            if w.offset_minutes == offset_minutes:
                return w
        return None

    def command_window(self, command: str) -> int:
        self._load_config()
        slot = int(hashlib.sha256(command.encode()).hexdigest(), 16) % len(self.scheduled_windows) if self.scheduled_windows else 0
        result = self.scheduled_windows[slot].window_id
        self.cmd_cache[command] = result  # type: ignore[assignment]
        return result

    def flush(self) -> None:
        self._load_config()
        self.cmd_cache = {}  # type: ignore[assignment]

# Legacy alias: CacheWrapper was subsumed by ScheduleWrapper.
# This keeps old imports functional.
CacheWrapper = ScheduleWrapper  # noqa: F811
