from __future__ import annotations

from dataclasses import dataclass, field

from .packet import MAX_SEQUENCE


@dataclass(slots=True)
class SequenceTracker:
    _last: dict[int, int] = field(default_factory=dict)

    def observe(self, apid: int, current: int) -> int:
        """Return the number of missing packets before current, accounting for wraparound."""
        previous = self._last.get(apid)
        self._last[apid] = current
        if previous is None:
            return 0
        expected = (previous + 1) & MAX_SEQUENCE
        if current == expected:
            return 0
        return (current - expected) & MAX_SEQUENCE
