from mission_ground.common.packet import MAX_SEQUENCE
from mission_ground.common.sequence import SequenceTracker


def test_sequence_gap_and_wraparound() -> None:
    tracker = SequenceTracker()
    assert tracker.observe(100, MAX_SEQUENCE - 1) == 0
    assert tracker.observe(100, MAX_SEQUENCE) == 0
    assert tracker.observe(100, 0) == 0
    assert tracker.observe(100, 3) == 2
