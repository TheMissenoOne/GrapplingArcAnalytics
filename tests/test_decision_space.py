"""Decision Space model tests (DS-01..05) — no DB required."""

from __future__ import annotations

from analysis.decision_space import position_decision_space, sequence_decision_space


def test_default_scores_compress_for_control():
    sub = position_decision_space("submission")
    guard = position_decision_space("guard")
    # A submission leaves the defender almost no options; guard leaves plenty.
    assert sub["defender_score"] < guard["defender_score"]
    assert sub["attacker_score"] > sub["defender_score"]


def test_curated_overrides_default():
    curated = {"attacker_score": 0.9, "defender_score": 0.05}
    ds = position_decision_space("guard", curated)
    assert ds == {"attacker_score": 0.9, "defender_score": 0.05}


def test_sequence_timeline_shape_and_reduction():
    sequence = [
        {"label": "Takedown", "type": "takedown", "side": "a"},
        {"label": "Guard Pass", "type": "pass", "side": "a"},
        {"label": "Back Control", "type": "control", "side": "a"},
        {"label": "Rear Naked Choke", "type": "submission", "side": "a"},
    ]
    out = sequence_decision_space(sequence)
    assert out["mode"] == "expert"
    assert len(out["timeline"]) == 4
    # 'a' progressively compresses 'b' → at least one major reduction, no recovery.
    assert out["reductions"]
    assert out["recoveries"] == []
    # Final defender (b) space should be the lowest along the run.
    last = out["timeline"][-1]
    assert last["ds_after"]["b"] <= last["ds_before"]["b"]


def test_sequence_turning_point_on_lead_flip():
    sequence = [
        {"label": "Mount", "type": "control", "side": "a"},
        {"label": "Armbar", "type": "submission", "side": "b"},
    ]
    out = sequence_decision_space(sequence)
    # The lead flips from a to b when b hits the submission (DS-12 turning point).
    assert out["turning_points"]


def test_non_ab_side_events_skipped():
    out = sequence_decision_space([{"label": "x", "type": "control", "side": "x"}])
    assert out["timeline"] == []
