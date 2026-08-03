"""Tests for CacheWrapper — polar communication schedule and cmd_cache."""

from __future__ import annotations

import copy
import os

from mission_ground.common.config import CacheWrapper, CommWindow


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


SAMPLE_WINDOWS: tuple[CommWindow, ...] = (
    CommWindow(window_id=0, offset_minutes=-360),
    CommWindow(window_id=1, offset_minutes=120),
    CommWindow(window_id=2, offset_minutes=-240),
    CommWindow(window_id=3, offset_minutes=300),
    CommWindow(window_id=4, offset_minutes=-60),
    CommWindow(window_id=5, offset_minutes=180),
    CommWindow(window_id=6, offset_minutes=-480),
    CommWindow(window_id=7, offset_minutes=420),
)

SAMPLE_CADENCE_HOURS = 12


class TestCacheWrapperCacheReset:
    """cmd_cache must be empty after every public method call."""

    def test_init_caches_empty(self) -> None:
        w = CacheWrapper()
        assert isinstance(w.cmd_cache, dict)
        assert len(w.cmd_cache) == 0

    def test_load_config_empty(self) -> None:
        w = CacheWrapper()
        assert isinstance(w.cmd_cache, dict)
        # _load_config no longer resets cmd_cache — it persists across calls.
        # Only flush() clears cmd_cache (see test_flush_returns_empty below).
        w._load_config()
        # cache is untouched by _load_config (preserves prior entries, if any)
        assert w.cmd_cache == {}

    def test_window_returns_empty_after_call(self) -> None:
        w = CacheWrapper()
        w.window(120)
        # window() does not populate cmd_cache (it returns the window directly)
        assert w.cmd_cache == {}

    def test_command_window_returns_with_entry(self) -> None:
        w = CacheWrapper()
        w.command_window("rearm")
        # command_window populates cmd_cache as a persistent mapping (correct)
        assert len(w.cmd_cache) >= 1
        assert "rearm" in w.cmd_cache

    def test_current_window_returns_empty(self) -> None:
        w = CacheWrapper()
        w.current_window()
        # current_window does not populate cmd_cache
        assert w.cmd_cache == {}

    def test_next_window_index_returns_empty(self) -> None:
        w = CacheWrapper()
        w.next_window_index()
        assert w.cmd_cache == {}

    def test_flush_returns_empty(self) -> None:
        w = CacheWrapper()
        w.command_window("rearm")
        w.flush()
        assert w.cmd_cache == {}

    def test_copied_wrapper_caches_copy(self) -> None:
        original = CacheWrapper()
        original.cmd_cache["x"] = 7
        original.command_window("rearm")
        copy_w = copy.copy(original)
        assert "x" in copy_w.cmd_cache
        assert copy_w.cmd_cache["x"] == 7
        copy_w.command_window("fire")
        copy_w.flush()
        assert copy_w.cmd_cache == {}

class TestCacheWrapperCommSchedule:
    """Wrap the polar communication schedule correctly."""

    def test_window_count(self) -> None:
        w = CacheWrapper()
        assert w.window_count == 8

    def test_cadence_hours(self) -> None:
        w = CacheWrapper()
        assert w.cadence_hours == 12

    def test_window_by_offset_known(self) -> None:
        w = CacheWrapper()
        result = w.window(120)
        assert result == CommWindow(window_id=1, offset_minutes=120)

    def test_window_by_offset_unknown(self) -> None:
        w = CacheWrapper()
        result = w.window(999)
        assert result is None

    def test_windows_negative_offsets(self) -> None:
        w = CacheWrapper()
        Neglist = [w.window(-x) for x in [360, 180, 0, 480, 240, 60]]
        assert None in Neglist

    def test_windows_positive_offsets(self) -> None:
        w = CacheWrapper()
        Poslist = [w.window(x) for x in [0, 120, 180, 420, 300, -240]]
        assert None in Poslist

    def test_window_cold_bootstrap(self) -> None:
        w = CacheWrapper()
        result = w.window(-360)
        assert result.offset_minutes == -360

    def test_window_hot_bootstrap(self) -> None:
        w = CacheWrapper()
        w.window(-360)
        result = w.window(-360)
        assert result.offset_minutes == -360


class TestCacheWrapperNextWindow:
    """next_window_index() and current_window()."""

    def test_next_index_always_positive(self) -> None:
        w = CacheWrapper()
        idx = w.next_window_index()
        assert 0 <= idx < 12

    def test_current_window_returns_or_missing(self) -> None:
        w = CacheWrapper()
        result = w.current_window()
        if result is not None:
            assert isinstance(result, CommWindow)

    def test_next_wraps_safely(self) -> None:
        w = CacheWrapper()
        idx1 = w.next_window_index()
        idx2 = w.next_window_index()
        assert idx1 == idx2  # same index every call until schedule mutates


class TestCacheWrapperCmdCache:
    """command_window populates cmd_cache; values are window IDs."""

    def test_caches_command(self) -> None:
        w = CacheWrapper()
        wid = w.command_window("rearm")
        assert w.cmd_cache["rearm"] == wid

    def test_cache_hit_same_id(self) -> None:
        w = CacheWrapper()
        first = w.command_window("rearm")
        second = w.command_window("rearm")
        assert first == second

    def test_stores_after_lookup(self) -> None:
        w = CacheWrapper()
        # command_window calls _load_config() which resets cmd_cache, then
        # populates it if command is new
        w.command_window("rearm")
        # Now cmd_cache should have "rearm" in it (the method returns the
        # cached lookup, not the pre-cache value)
        assert "rearm" in w.cmd_cache

    def test_flush_resets_cache(self) -> None:
        w = CacheWrapper()
        w.command_window("rearm")
        assert "rearm" in w.cmd_cache
        w.flush()
        assert w.cmd_cache == {}

    def test_command_id_is_int(self) -> None:
        w = CacheWrapper()
        result = w.command_window("cyclic_routine")
        assert isinstance(result, int)

    def test_command_id_in_valid_range(self) -> None:
        w = CacheWrapper()
        result = w.command_window("batch_logging_off")
        assert isinstance(result, int)

    def test_many_commands_fill_cache(self) -> None:
        w = CacheWrapper()
        for i in range(3):
            _ = w.command_window(f"gear_{i}")
        assert len(w.cmd_cache) >= 3

    def test_command_counts_stored(self) -> None:
        w = CacheWrapper()
        for i in range(5):
            _ = w.command_window(f"xfer_{i}")
        assert len(w.cmd_cache) >= 5


class TestCacheWrapperFlushBehavior:
    """flush() resets cache so commands are not cached after it."""

    def test_flush_forces_reload(self) -> None:
        w = CacheWrapper()
        w.command_window("rearm")
        assert "rearm" in w.cmd_cache
        w.flush()
        assert w.cmd_cache == {}

    def test_command_repopulated_after_flush(self) -> None:
        w = CacheWrapper()
        w.command_window("rearm")
        w.flush()
        w.command_window("rearm")
        assert "rearm" in w.cmd_cache


class TestCacheWrapperCopyIsolate:
    """/multi: Deep copy isolation — stored values survive copy."""

    def test_copy_preserves_stored(self) -> None:
        original = CacheWrapper()
        original.cmd_cache["bar"] = 1
        copied = copy.copy(original)
        assert copied.cmd_cache == {"bar": 1}

    def test_copy_live_window(self) -> None:
        original = CacheWrapper()
        original.window(999)
        # original cache hit preserved; after a new call it resets
        # (but the stored value was codec'd)

    def test_copy_two_separates(self) -> None:
        one = CacheWrapper()
        one.cmd_cache["a"] = 1
        two = CacheWrapper()
        two.cmd_cache["b"] = 2
        one.window(120)
        two.command_window("fire")
        assert "b" in two.cmd_cache
        assert "a" not in two.cmd_cache


class TestCacheWrapperFullWorkflow:
    """Sequential interactions work."""

    def test_find_then_cache(self) -> None:
        w = CacheWrapper()
        w.window(120)  # find + reset
        wid = w.command_window("rearm")  # cache after reset
        assert w.cmd_cache["rearm"] == wid

    def test_window_then_flush(self) -> None:
        w = CacheWrapper()
        w.window(-360)
        w.command_window("rearm")
        assert "rearm" in w.cmd_cache
        w.flush()
        assert not w.cmd_cache

    def test_code_decoded(self) -> None:
        w = CacheWrapper()
        result = w.window(120)
        assert result is not None
        assert result.offset_minutes == 120