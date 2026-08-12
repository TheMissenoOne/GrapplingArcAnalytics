from pathlib import Path

import numpy as np
import pandas as pd

from decision_vision.audit_overlay import (
    frame_timestamp,
    label_detections,
    lookup_recorded,
)


def _pose(seed: float) -> np.ndarray:
    return np.full((17, 3), seed, dtype=np.float64)


def test_frame_timestamp_parses_padded_filename() -> None:
    assert frame_timestamp(Path("frame_05313.50s.png")) == 5313.5
    assert frame_timestamp(Path("not_a_frame.png")) is None


def test_label_detections_marks_selected_pair_by_identity_not_value() -> None:
    a, b, c = _pose(1.0), _pose(2.0), _pose(1.0)  # c has same values as a, different object
    labels = label_detections([a, b, c], pair=(b, a))
    assert labels == ["sel1", "sel0", "unselected"]


def test_label_detections_all_unselected_when_no_pair_found() -> None:
    poses = [_pose(1.0), _pose(2.0)]
    assert label_detections(poses, pair=None) == ["unselected", "unselected"]


def test_lookup_recorded_matches_within_tolerance() -> None:
    df = pd.DataFrame(
        {"timestamp": [5313.0, 5313.5, 5314.0], "role": ["a2", "a1", "unknown"]}
    )
    row = lookup_recorded(df, 5313.5)
    assert row is not None
    assert row["role"] == "a1"
    assert lookup_recorded(df, 5320.0) is None
