"""Tests for the frame-stream segmenter (smoothing + run encoding)."""

from __future__ import annotations

import pytest

from cv.segmenter import segment, smooth_labels


class TestSmoothLabels:
    def test_empty(self) -> None:
        assert smooth_labels([], window=5) == []

    def test_single_label(self) -> None:
        labels = ["a"] * 10
        assert smooth_labels(labels) == labels

    def test_even_window_clamped_odd(self) -> None:
        labels = ["a", "b", "a", "b", "a"]
        result = smooth_labels(labels, window=4)
        assert len(result) == 5

    def test_noise_spike_removed(self) -> None:
        labels = (
            ["mount_top"] * 5
            + ["guard_bottom"]
            + ["mount_top"] * 5
        )
        smoothed = smooth_labels(labels, window=5)
        assert all(lab == "mount_top" for lab in smoothed)

    def test_tie_keeps_current(self) -> None:
        labels = ["a", "a", "b", "b", "a"]
        result = smooth_labels(labels, window=3)
        assert result[2] == "b"


class TestSegment:
    def test_empty(self) -> None:
        assert segment([], window=5, min_frames=1) == []

    def test_single_run(self) -> None:
        frames = [(0, "mount_top", 0.9), (1, "mount_top", 0.85), (2, "mount_top", 0.95)]
        events = segment(frames, window=5, min_frames=1)
        assert len(events) == 1
        ev = events[0]
        assert ev.label == "mount_top"
        assert ev.start == 0
        assert ev.end == 2
        assert ev.n_frames == 3
        assert ev.mean_conf == pytest.approx(0.9)

    def test_noise_spike_removed_by_smoothing(self) -> None:
        frames = (
            [(i, "mount_top", 0.9) for i in range(5)]
            + [(5, "guard_bottom", 0.9)]
            + [(i, "mount_top", 0.9) for i in range(6, 11)]
        )
        events = segment(frames, window=5, min_frames=1)
        assert len(events) == 1
        assert events[0].label == "mount_top"
        assert events[0].start == 0
        assert events[0].end == 10

    def test_two_distinct_runs(self) -> None:
        frames = (
            [(i, "mount_top", 0.85) for i in range(4)]
            + [(i, "guard_bottom", 0.75) for i in range(4, 8)]
        )
        events = segment(frames, window=1, min_frames=1)
        assert len(events) == 2
        assert events[0].label == "mount_top"
        assert events[0].start == 0
        assert events[0].end == 3
        assert events[1].label == "guard_bottom"
        assert events[1].start == 4
        assert events[1].end == 7

    def test_min_frames_drops_short_events(self) -> None:
        frames = (
            [(i, "mount_top", 0.9) for i in range(3)]
            + [(3, "guard_bottom", 0.9)]
            + [(i, "mount_top", 0.9) for i in range(4, 7)]
        )
        events = segment(frames, window=1, min_frames=3)
        assert len(events) == 2
        assert events[0].label == "mount_top"
        assert events[1].label == "mount_top"

    def test_events_order_preserved(self) -> None:
        frames = [(0, "a", 0.5), (1, "b", 0.5), (2, "c", 0.5)]
        events = segment(frames, window=1, min_frames=1)
        assert [e.label for e in events] == ["a", "b", "c"]
