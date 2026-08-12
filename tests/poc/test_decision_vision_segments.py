"""`segments.csv` must not be able to hide a role switch.

The real case, from the 2026-08-12 visual audit: `segments.csv` reported
`standing 5414.5->5424.5 athlete2` while `state_samples.csv` carried `athlete1`
from 5415.0. Reading segments alone, a committed role switch was invisible.

Mechanism (it was NOT "the segment carries the role from its start"): spans were
keyed on (position, role, state), so the boundary WAS created — and the
min-duration squash then regrouped runs by (position, state) only, role-blind,
merging straight across it. Segments are now rebuilt from the final rows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "poc"))

from decision_vision.live_state import smooth_timeline  # noqa: E402


def _row(ts: float, position: str, role: str, state: str) -> dict[str, object]:
    """A frame the smoother will accept as explicit, high-confidence evidence."""
    return {
        "timestamp": ts,
        "position": position,
        "position_conf": 0.99,
        "role": role,
        "role_conf": 0.99,
        "state": state,
        "state_conf": 0.99,
        "pose_pair": True,
    }


def test_role_switch_inside_one_position_run_is_not_swallowed() -> None:
    # Same position and state throughout; only the ROLE changes half way. The
    # old segment builder merged this into a single span carrying athlete2.
    rows = [_row(t, "standing", "athlete2", "standing") for t in (0.0, 0.5, 1.0, 1.5, 2.0)]
    rows += [_row(t, "standing", "athlete1", "standing") for t in (2.5, 3.0, 3.5, 4.0, 4.5, 5.0)]

    _, segments = smooth_timeline(rows, role_conf_min=0.85, persist=2, min_duration_s=1.0)

    roles = [s["role"] for s in segments]
    assert "athlete1" in roles, "the committed role switch vanished from segments"
    assert "athlete2" in roles

    # And a boundary exists exactly where the role changed, not somewhere else.
    switch = next(s for s in segments if s["role"] == "athlete1")
    assert switch["start"] >= 2.5


def test_segments_agree_with_the_rows_they_summarise() -> None:
    """The invariant that would have caught the original defect: for every row,
    the segment covering its timestamp must report the row's own role."""
    rows = [_row(t, "standing", "athlete2", "standing") for t in (0.0, 0.5, 1.0, 1.5, 2.0)]
    rows += [_row(t, "standing", "athlete1", "standing") for t in (2.5, 3.0, 3.5, 4.0, 4.5, 5.0)]

    smoothed, segments = smooth_timeline(rows, role_conf_min=0.85, persist=2, min_duration_s=1.0)

    for row in smoothed:
        ts = row["timestamp"]
        covering = [s for s in segments if s["start"] <= ts <= s["end"]]
        assert covering, f"no segment covers {ts}"
        assert any(s["role"] == row["role"] for s in covering), (
            f"at {ts} rows say {row['role']} but segments say "
            f"{[s['role'] for s in covering]}"
        )


def test_position_change_still_starts_a_segment() -> None:
    """The fix must not cost the boundaries that already worked."""
    rows = [_row(t, "standing", "athlete1", "standing") for t in (0.0, 0.5, 1.0, 1.5, 2.0)]
    rows += [_row(t, "mount", "athlete1", "mount1") for t in (2.5, 3.0, 3.5, 4.0, 4.5, 5.0)]

    _, segments = smooth_timeline(rows, role_conf_min=0.85, persist=2, min_duration_s=1.0)

    positions = [s["position"] for s in segments]
    assert "standing" in positions
    assert "mount" in positions
