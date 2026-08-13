"""Pure logic of the frame pre-labelling job."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "poc"))

from decision_vision.prelabel_frames import merge_windows, nearest_prediction  # noqa: E402


def test_merge_windows_collapses_a_cluster_into_one_pass() -> None:
    # Events a second apart must become ONE stream pass. Without merging, a bout
    # with 20 clustered events costs 20 passes over overlapping video — an order
    # of magnitude more work for the same 20 predictions.
    assert merge_windows([100.0, 101.0, 102.0], lead_in=4.0, trail=0.5) == [(96.0, 102.5)]


def test_merge_windows_keeps_distant_events_apart() -> None:
    spans = merge_windows([100.0, 400.0], lead_in=4.0, trail=0.5)
    assert spans == [(96.0, 100.5), (396.0, 400.5)]


def test_merge_windows_never_seeks_before_zero() -> None:
    assert merge_windows([1.0], lead_in=4.0, trail=0.5) == [(0.0, 1.5)]


def test_merge_windows_handles_nothing() -> None:
    assert merge_windows([]) == []


def test_nearest_prediction_refuses_a_distant_frame() -> None:
    # A prediction from two seconds away is not a prediction about this frame.
    # Presenting it for review would launder a guess as a proposal.
    preds = {100.0: {"role": "athlete1"}, 105.0: {"role": "athlete2"}}
    assert nearest_prediction(preds, 100.1) == {"role": "athlete1"}
    assert nearest_prediction(preds, 102.5) is None


def test_nearest_prediction_with_nothing_sampled() -> None:
    assert nearest_prediction({}, 100.0) is None
