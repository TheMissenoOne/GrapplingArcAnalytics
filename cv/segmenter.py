"""Collapse a per-frame classification stream into discrete position events.

Majority-vote smoothing followed by run-length encoding to produce clean
events from noisy frame-by-frame predictions.
"""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PositionEvent:
    """A discrete position event derived from a run of smoothed labels."""

    label: str
    start: int
    end: int
    n_frames: int
    mean_conf: float


def smooth_labels(labels: list[str], window: int = 5) -> list[str]:
    """Sliding-window majority-vote label smoother.

    Each position is replaced by the majority label in the window centered on
    it.  Ties keep the current label.

    Parameters
    ----------
    labels : list[str]
        Raw label sequence, one per frame.
    window : int
        Window size (clamped to odd if even).

    Returns
    -------
    list[str]
        Smoothed sequence (same length as input).
    """
    if window % 2 == 0:
        window += 1
    half = window // 2
    n = len(labels)
    if n == 0:
        return []

    smoothed: list[str] = []
    for i in range(n):
        left = max(0, i - half)
        right = min(n, i + half + 1)
        counts: collections.Counter[str] = collections.Counter()
        for j in range(left, right):
            counts[labels[j]] += 1
        sorted_counts = counts.most_common()
        if len(sorted_counts) > 1 and sorted_counts[0][1] == sorted_counts[1][1]:
            smoothed.append(labels[i])
        else:
            smoothed.append(sorted_counts[0][0])
    return smoothed


def segment(
    frames: list[tuple[int, str, float]],
    window: int = 5,
    min_frames: int = 1,
) -> list[PositionEvent]:
    """Segment a per-frame classification stream into discrete position events.

    Parameters
    ----------
    frames : list[tuple[int, str, float]]
        Time-ordered list of ``(frame_index, label, confidence)``.
    window : int
        Smoothing window size (passed to :func:`smooth_labels`).
    min_frames : int
        Minimum ``n_frames`` for an event to be kept.

    Returns
    -------
    list[PositionEvent]
    """
    if not frames:
        return []

    raw_labels = [label for _, label, _ in frames]
    smoothed = smooth_labels(raw_labels, window)

    events: list[PositionEvent] = []
    i = 0
    n = len(frames)
    while i < n:
        current_label = smoothed[i]
        run_indices: list[int] = []
        j = i
        while j < n and smoothed[j] == current_label:
            run_indices.append(j)
            j += 1

        start_frame = frames[run_indices[0]][0]
        end_frame = frames[run_indices[-1]][0]
        n_run = end_frame - start_frame + 1
        confs = [frames[k][2] for k in run_indices]
        mean_conf = sum(confs) / len(confs)

        if n_run >= min_frames:
            events.append(
                PositionEvent(
                    label=current_label,
                    start=start_frame,
                    end=end_frame,
                    n_frames=n_run,
                    mean_conf=mean_conf,
                )
            )
        i = j

    return events
