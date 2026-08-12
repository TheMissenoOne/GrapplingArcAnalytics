"""Tests for live_state.smooth_timeline / build_report (transfer smoke)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "poc"))

from decision_vision.live_state import (
    assemble_report,
    build_report,
    smooth_timeline,
)


def _row(t, position="closed_guard", role="athlete1", state="on_top",
         pos_conf=0.9, role_conf=0.9, state_conf=0.9, pose_pair=True):
    return {
        "timestamp": t, "position": position, "position_conf": pos_conf,
        "role": role, "role_conf": role_conf, "state": state,
        "state_conf": state_conf, "pose_pair": pose_pair,
    }


def _rows(ts, **kw):
    def pick(key):
        val = kw.get(key)
        return val if not isinstance(val, list) else val[idx]

    out = []
    for idx, t in enumerate(ts):
        out.append(_row(t, **{k: pick(k) for k in kw}))
    return out


def test_identity_untouched():
    rows = _rows([0.0, 0.5, 1.0], role="athlete1")
    out, segs = smooth_timeline(rows)
    for r in out:
        assert "track_0" not in r
        assert "track_1" not in r
    assert len(segs) == 1
    assert segs[0]["role"] == "athlete1"


def test_role_carry_below_conf():
    rows = _rows([0.0, 0.5, 1.0], role="athlete1", role_conf=0.9) + \
        _rows([1.5, 2.0], role="unknown", role_conf=0.2)
    out, _ = smooth_timeline(rows)
    assert out[-1]["role"] == "athlete1"


def test_role_persistence_requires_persist_frames():
    rows = _rows([0.0, 0.5, 1.0, 1.5, 2.0],
                 role=["athlete1", "athlete1", "athlete2", "athlete1", "athlete1"])
    out, _ = smooth_timeline(rows, persist=3)
    roles = [r["role"] for r in out]
    assert roles == ["athlete1", "athlete1", "athlete1", "athlete1", "athlete1"]


def test_role_switch_commits():
    rows = _rows([0.0, 0.5, 1.0, 1.5, 2.0],
                 role=["athlete1", "athlete1", "athlete2", "athlete2", "athlete2"])
    out, _ = smooth_timeline(rows, min_duration_s=0.0)
    roles = [r["role"] for r in out]
    assert roles == ["athlete1", "athlete1", "athlete1", "athlete2", "athlete2"]


def test_min_duration_squash_short_last_segment():
    rows = _rows([0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
                 position=["closed_guard", "closed_guard", "mount", "mount",
                           "closed_guard", "closed_guard"])
    out, segs = smooth_timeline(rows, min_duration_s=2.0)
    assert segs == [
        {"start": 0.0, "end": 2.0, "seconds": 2.0, "position": "closed_guard",
         "role": "athlete1", "state": "on_top"},
        {"start": 2.0, "end": 5.0, "seconds": 3.0, "position": "mount",
         "role": "athlete1", "state": "on_top"},
    ]
    positions = [r["position"] for r in out]
    assert positions == ["closed_guard", "closed_guard", "mount", "mount", "mount", "mount"]


def test_min_duration_short_first_segment():
    rows = _rows([0.0, 0.5, 1.5, 3.5],
                 position=["mount", "mount", "closed_guard", "closed_guard"])
    out, segs = smooth_timeline(rows, min_duration_s=2.0)
    assert segs[0]["start"] == 0.0
    assert segs[0]["position"] == "closed_guard"
    assert all(r["position"] == "closed_guard" for r in out)


def test_void_does_not_swallow_real_spans():
    rows = _rows([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5],
                 position=["unknown", "unknown", "open_guard", "open_guard",
                           "open_guard", "open_guard", "open_guard", "unknown",
                           "unknown", "unknown", "unknown", "unknown"],
                 pose_pair=[False, False, True, True, True, True, True,
                            False, False, False, False, False])
    out, segs = smooth_timeline(rows, min_duration_s=2.0)
    assert segs[0]["position"] == "open_guard"
    assert segs[1]["position"] == "unknown"
    assert out[7]["position"] == "unknown"
    assert all(r["position"] == "open_guard" for r in out[2:7])


def test_short_void_absorbed_into_real_prev():
    rows = _rows([0.0, 0.5, 1.0, 1.5, 2.0, 2.5],
                 position=["open_guard", "open_guard", "open_guard",
                           "open_guard", "open_guard", "unknown"],
                 pose_pair=[True, True, True, True, True, False])
    out, segs = smooth_timeline(rows, min_duration_s=2.0)
    assert len(segs) == 1
    assert segs[0]["position"] == "open_guard"
    assert all(r["position"] == "open_guard" for r in out)


def test_real_burst_between_voids_keeps_real_labels():
    ts = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    positions = ["unknown", "takedown", "back", "turtle", "standing",
                 "takedown", "unknown", "unknown", "unknown"]
    pair = [False, True, True, True, True, True, False, False, False]
    rows = _rows(ts, position=positions, pose_pair=pair)
    out, segs = smooth_timeline(rows, min_duration_s=2.0)
    pair_out = [r for r in out if r["pose_pair"]]
    assert all(r["position"] != "unknown" for r in pair_out)
    assert len(segs) <= 3


def test_empty_input():
    out, segs = smooth_timeline([])
    assert out == [] and segs == []


def test_build_report_counts():
    rows = _rows([0.0, 0.5, 1.0, 1.5], role="athlete1")
    report = build_report(rows)
    assert report["frames_sampled"] == 4
    assert report["pose_pair_rate"] == 1.0
    assert report["position_coverage"] == 1.0
    assert report["duration_s"] == 1.5
    assert report["per_position"]["closed_guard"]["role_explicit_rate"] == 1.0
    assert report["flips_per_minute"]["role"] == 0.0


def test_build_report_flips():
    rows = _rows([0.0, 0.5, 1.0, 1.5],
                 role=["athlete1", "athlete1", "athlete2", "athlete2"])
    report = build_report(rows)
    assert report["flips_per_minute"]["role"] == pytest.approx(1.0 / (1.5 / 60.0))


def test_build_report_unknown_conf_excluded():
    rows = _rows([0.0, 0.5],
                 position=["closed_guard", "unknown"],
                 pos_conf=[0.9, 0.0], pose_pair=[True, True])
    report = build_report(rows)
    assert report["position_overall_conf"]["mean"] == pytest.approx(0.9)


def test_assemble_report_rates_and_switches():
    ts = [0.0, 0.5, 1.0, 1.5, 2.0, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5]
    roles = ["athlete1"] * 5 + ["athlete2"] * 6
    rows = _rows(ts, role=roles)
    final_rows, segments = smooth_timeline(rows)
    report = assemble_report(rows, final_rows, segments,
                             match_id="m1", start=0.0, end=6.5,
                             sample_every=0.5)
    assert report["raw"]["frames_sampled"] == 11
    assert report["role_observed_rate_raw"] > 0
    assert report["role_timeline_coverage"] > 0
    assert report["role_inferred_rate"] >= 0
    assert report["role_observed_rate_raw"] <= report["role_timeline_coverage"]
    assert report["role_switch_events_smoothed"] == 1
    assert report["segments"] == len(segments)
    assert report["range"] == [0.0, 6.5]
    assert report["sample_every"] == 0.5
