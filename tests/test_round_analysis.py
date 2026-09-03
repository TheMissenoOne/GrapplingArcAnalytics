"""analysis/round_analysis.py -- pure, no I/O, synthetic events only (no real user data)."""

from __future__ import annotations

from analysis.round_analysis import build_highlights, build_sequences, derive_difficulty


def _ev(ts, type_, label, actor, successful=None, confidence=None):
    e = {"ts": ts, "type": type_, "label": label, "actor": actor}
    if successful is not None:
        e["successful"] = successful
    if confidence is not None:
        e["confidence"] = confidence
    return e


# ── build_sequences ──────────────────────────────────────────────────────────────────────────
def test_three_resets_produce_four_sequences():
    events = [
        _ev(5, "control", "Top Control", "you"),
        _ev(120, "control", "Top Control", "partner"),
        _ev(250, "control", "Top Control", "you"),
        _ev(400, "control", "Top Control", "partner"),
    ]
    resets = [100, 200, 300]
    seqs = build_sequences(events, resets)
    assert len(seqs) == 4
    assert [ev["ts"] for ev in seqs[0]] == [5]
    assert [ev["ts"] for ev in seqs[1]] == [120]
    assert [ev["ts"] for ev in seqs[2]] == [250]
    assert [ev["ts"] for ev in seqs[3]] == [400]


def test_no_reset_and_no_gap_is_one_sequence():
    events = [_ev(t, "control", "Top Control", "you") for t in (0, 5, 10, 15)]
    seqs = build_sequences(events, [])
    assert len(seqs) == 1
    assert len(seqs[0]) == 4


def test_no_reset_but_wide_gap_falls_back_to_gap_split():
    events = [_ev(0, "control", "Top Control", "you"), _ev(10, "control", "Top Control", "you"),
              _ev(200, "control", "Top Control", "partner")]
    seqs = build_sequences(events, [])
    assert len(seqs) == 2
    assert [ev["ts"] for ev in seqs[0]] == [0, 10]
    assert [ev["ts"] for ev in seqs[1]] == [200]


# ── derive_difficulty ────────────────────────────────────────────────────────────────────────
def test_difficulty_symmetric_is_five():
    events = [
        _ev(0, "control", "Top Control", "you"),
        _ev(10, "control", "Top Control", "partner"),
    ]
    motion = {"records": [{"t": 20.0, "diff_raw": 1.0}]}
    assert derive_difficulty(events, motion) == 5.0


def test_difficulty_you_dominate_is_low():
    events = [
        _ev(0, "control", "Top Control", "you"),
        _ev(5, "takedown", "Single Leg Takedown", "you", successful=True),
        _ev(8, "pass", "Knee Slice Pass", "you", successful=True),
    ]
    motion = {"records": [{"t": 20.0, "diff_raw": 1.0}]}
    value = derive_difficulty(events, motion)
    assert value < 2.0


def test_difficulty_partner_dominates_and_finishes_is_ten():
    events = [
        _ev(0, "control", "Top Control", "partner"),
        _ev(5, "submission", "Rear Naked Choke", "partner", successful=True),
        _ev(10, "submission", "Armbar", "partner", successful=True),
    ]
    motion = {"records": [{"t": 20.0, "diff_raw": 1.0}]}
    assert derive_difficulty(events, motion) == 10.0


# ── build_highlights ─────────────────────────────────────────────────────────────────────────
def test_highlights_ranks_successful_high_confidence_above_weak_event():
    events = [
        _ev(10, "submission", "Rear Naked Choke", "you", successful=True, confidence="high"),
        _ev(50, "guard", "Closed Guard", "partner", successful=False, confidence="low"),
    ]
    motion = {"records": [{"t": 10.0, "diff_raw": 5.0}, {"t": 50.0, "diff_raw": 0.1}]}
    highlights = build_highlights(events, motion, k=5)
    assert len(highlights) == 2
    assert highlights[0]["label"] == "Rear Naked Choke"
    assert highlights[0]["score"] > highlights[1]["score"]


def test_highlights_respects_k_and_clamps_window_start():
    events = [_ev(t, "takedown", "Double Leg Takedown", "you", successful=True, confidence="high")
              for t in (0, 30, 60)]
    highlights = build_highlights(events, motion=None, k=1)
    assert len(highlights) == 1
    assert highlights[0]["start"] >= 0.0


def test_highlights_handle_missing_motion_without_crashing():
    events = [_ev(5, "sweep", "Scissor Sweep", "you", successful=True)]
    highlights = build_highlights(events, motion={}, k=5)
    assert highlights[0]["label"] == "Scissor Sweep"
