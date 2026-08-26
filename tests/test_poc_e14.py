"""PoC-E14's harness on synthetic bouts with a known answer. No database."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pytest

from analysis.poc.e9_markov import BoutRow
from analysis.poc.e14_temporal_motifs import (
    ARM_DELTA,
    ARM_INDEX,
    ARM_STATE,
    MIN_EVAL_ROWS_WITH_MOTIF,
    Pass,
    Run,
    _window,
    build_rows,
    mean_window_edges,
    motifs_ending_at,
    own_temporal_edges,
    ts_slice,
    verdicts,
    wins,
)
from analysis.poc.signatures import TEdge


def _bout_row(key: int, labels: list[str], times: list[float] | None) -> BoutRow:
    """One bout, all events the same actor's, with or without timestamps."""
    seq: list[dict[str, Any]] = []
    for i, lb in enumerate(labels):
        e: dict[str, Any] = {"label": lb, "type": "control", "actor_id": "you",
                             "successful": False}
        if times is not None:
            e["ts"] = times[i]
        seq.append(e)
    return BoutRow(key=(2020, f"{key:03d}", f"m{key}"), sequence=seq,
                   athlete_a="you", athlete_b="opp", event="Test", win_type="POINTS",
                   family="other", discipline="grappling",
                   elapsed=None if times is None else [t - min(times) for t in times])


# ── AA-010 ──────────────────────────────────────────────────────────────────────


def test_a_bout_missing_one_timestamp_is_dropped_and_counted() -> None:
    """AA-010: never defaulted, never repaired, always counted."""
    good = _bout_row(0, ["a", "b", "c"], [0.0, 10.0, 20.0])
    bad = _bout_row(1, ["a", "b", "c"], None)
    usable, note = ts_slice([good, bad])
    assert {b.key for b, _ in usable} == {good.key}
    assert "1 of 2" in note and "never defaulted" in note


def test_elapsed_is_within_bout_so_an_unknown_origin_cannot_shift_it() -> None:
    """`ts_origin` is NULL on most matches; a within-bout difference is immune to it."""
    early = _bout_row(0, ["a", "b", "c"], [0.0, 10.0, 25.0])
    late = _bout_row(1, ["a", "b", "c"], [9000.0, 9010.0, 9025.0])
    usable, _ = ts_slice([early, late])
    # `ts_slice` mirrors: two perspectives per bout, athlete_a first.
    (be, ee), (bl, el) = usable[0], usable[2]
    assert [e.t for e in own_temporal_edges(be, ee)] == \
           [e.t for e in own_temporal_edges(bl, el)]


# ── temporal edges ──────────────────────────────────────────────────────────────


def test_own_temporal_edges_refuses_self_loops_like_the_graph_builder() -> None:
    row = _bout_row(0, ["a", "a", "b", "b", "c"], [0.0, 5.0, 10.0, 15.0, 20.0])
    bout, elapsed = ts_slice([row])[0][0]      # the athlete_a ("you") perspective
    edges = own_temporal_edges(bout, elapsed)
    assert [(e.u, e.v) for e in edges] == [("a", "b"), ("b", "c")]
    assert [e.t for e in edges] == [10.0, 20.0]     # the TARGET event's time


def test_window_stops_at_the_delta_boundary() -> None:
    edges = [TEdge("a", "b", 0.0), TEdge("b", "c", 50.0), TEdge("c", "d", 100.0)]
    assert len(_window(edges, 2, 60.0)) == 2        # 100 − 50 ≤ 60, 100 − 0 > 60
    assert len(_window(edges, 2, 200.0)) == 3
    assert len(_window(edges, 0, 200.0)) == 1


def test_mean_window_edges_never_falls_below_the_motif_size() -> None:
    assert mean_window_edges([], 60.0) == 3


# ── motifs ending here ──────────────────────────────────────────────────────────


def test_motifs_ending_at_counts_only_motifs_using_the_last_edge() -> None:
    """Four edges on ≤3 nodes, all in window. The motifs ending at edge 3 are the ordered
    pairs from {0,1,2} completed by edge 3 — C(3,2) = 3 of them."""
    edges = [TEdge("a", "b", 0.0), TEdge("b", "c", 1.0),
             TEdge("c", "a", 2.0), TEdge("a", "b", 3.0)]
    got = motifs_ending_at(edges, 3, delta=100.0, by_index=False)
    assert sum(got.values()) == 3
    assert all(v > 0 for v in got.values())


def test_no_motif_before_there_are_enough_edges() -> None:
    edges = [TEdge("a", "b", 0.0), TEdge("b", "c", 1.0)]
    assert motifs_ending_at(edges, 1, delta=100.0, by_index=False) == Counter()


def test_the_delta_window_excludes_what_the_index_window_keeps() -> None:
    """The whole cell in one assertion: three edges in the same ORDER, one stream tight and
    one stretched. The index counter cannot tell them apart; the δ counter must."""
    tight = [TEdge("a", "b", 0.0), TEdge("b", "c", 1.0), TEdge("c", "a", 2.0)]
    loose = [TEdge("a", "b", 0.0), TEdge("b", "c", 500.0), TEdge("c", "a", 1000.0)]
    assert sum(motifs_ending_at(tight, 2, 60.0, by_index=False).values()) == 1
    assert sum(motifs_ending_at(loose, 2, 60.0, by_index=False).values()) == 0
    assert (motifs_ending_at(tight, 2, 3.0, by_index=True)
            == motifs_ending_at(loose, 2, 3.0, by_index=True))


# ── rows ────────────────────────────────────────────────────────────────────────


def test_build_rows_aligns_one_row_per_own_event_and_carries_the_bout_key() -> None:
    row = _bout_row(0, ["a", "b", "c", "d"], [0.0, 10.0, 20.0, 30.0])
    pairs = ts_slice([row])[0]
    assert len(pairs) == 2                     # mirrored: one perspective per athlete
    rows = build_rows(pairs, delta=120.0, span=4)
    # Only the perspective that owns the events produces rows; the mirror contributes none.
    assert len(rows.events) == 4
    assert len(rows.delta_motifs) == len(rows.index_motifs) == len(rows.events)
    assert set(rows.groups) == {row.key}


def test_early_rows_carry_no_motif_because_no_edge_exists_yet() -> None:
    row = _bout_row(0, ["a", "b", "c", "d", "e"], [0.0, 5.0, 10.0, 15.0, 20.0])
    rows = build_rows(ts_slice([row])[0][:1], delta=120.0, span=4)
    assert rows.delta_motifs[0] == Counter()      # no edge closed yet
    assert rows.delta_motifs[1] == Counter()      # one edge — a 3-edge motif needs three


# ── criterion ───────────────────────────────────────────────────────────────────


def test_wins_is_e9s_rule() -> None:
    assert wins((0.02, 0.01, 0.03))
    assert not wins((0.02, -0.01, 0.05))
    assert not wins((0.02, 0.02, 0.02))          # degenerate width
    assert not wins((float("nan"),) * 3)


def _pass(**kw: Any) -> Pass:
    base: dict[str, Any] = dict(delta=120.0, span=4, n_train_rows=500, n_eval_rows=200,
                                n_pos=40, n_motifs=25,
                                cover_delta=MIN_EVAL_ROWS_WITH_MOTIF, cover_index=210)
    base.update(kw)
    return Pass(**base)


def test_the_power_gate_refuses_a_verdict_when_too_few_rows_carry_a_motif() -> None:
    p = _pass(cover_delta=5)
    p.underpowered = "too few"
    v = verdicts(Run("g", "t", 10, 5, [p]))
    assert v["cell"].startswith("NO VERDICT")
    assert "UNDERPOWERED" in v["power gate"]


def test_the_verdict_accepts_only_when_delta_beats_the_index_control() -> None:
    p = _pass()
    p.delta_vs_index = (0.03, 0.01, 0.05)
    p.delta_vs_state = (0.03, 0.01, 0.05)
    assert verdicts(Run("g", "t", 10, 5, [p]))["cell"] == "ACCEPT"

    p.delta_vs_index = (0.03, -0.01, 0.07)        # beats the state, ties the control
    assert verdicts(Run("g", "t", 10, 5, [p]))["cell"].startswith("REJECT")


def test_the_three_arm_names_are_distinct_and_stable() -> None:
    """The verdict text keys off these; a silent rename would detach it from its arm."""
    assert len({ARM_STATE, ARM_DELTA, ARM_INDEX}) == 3
    assert "δ" in ARM_DELTA and "order only" in ARM_INDEX


def test_a_pass_that_never_ran_reports_that_instead_of_a_number() -> None:
    p = _pass()
    p.error = "one class empty on the held-out rows — nothing to separate"
    assert verdicts(Run("g", "t", 0, 0, [p]))["cell"].startswith("NOT RUN")
    assert np.isnan(p.delta_vs_index[0])


def test_verdicts_needs_the_primary_delta_not_a_sensitivity_one() -> None:
    only_sensitivity = _pass(delta=240.0)
    only_sensitivity.delta_vs_index = (0.5, 0.4, 0.6)     # a huge win, at the wrong δ
    assert verdicts(Run("g", "t", 10, 5, [only_sensitivity]))["cell"].startswith("NOT RUN")


def test_index_and_delta_arms_share_one_feature_width() -> None:
    """Both arms must span the same feature space or the Δ is comparing model sizes."""
    p = _pass()
    from analysis.poc.e14_temporal_motifs import Model
    p.models = [Model(ARM_DELTA, 0.6, 0.5, 0.7, 243),
                Model(ARM_INDEX, 0.6, 0.5, 0.7, 243)]
    a, b = p.model(ARM_DELTA), p.model(ARM_INDEX)
    assert a is not None and b is not None and a.n_features == b.n_features


def test_pytest_approx_guard_on_the_motif_subtraction() -> None:
    """`count(window) − count(window[:-1])` must never leave a negative count behind."""
    edges = [TEdge("a", "b", float(i)) for i in range(6)]
    got = motifs_ending_at(edges, 5, delta=100.0, by_index=False)
    assert all(v > 0 for v in got.values())
    assert sum(got.values()) == pytest.approx(10)     # C(5,2) pairs completed by edge 5
