"""Unit tests for the rank-aware graph-ELO growth engine (pure, no DB)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from analysis.athlete_elo import (
    BASE_BLACKBELT_ELO,
    base_elo_for_belt,
    k_factor,
    replay_matches,
    score_from_match,
)


def _match(
    won: bool = True,
    win_type: str | None = "SUBMISSION",
    sequence: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(won=won, win_type=win_type, sequence=sequence or [])


def _sub_win(label: str = "Rear Naked Choke") -> SimpleNamespace:
    """Submission win whose sequence touches a single (reused) node."""
    return _match(
        won=True,
        win_type="SUBMISSION",
        sequence=[{"label": label, "type": "submission", "actor": "you"}],
    )


# ── belt floor ──────────────────────────────────────────────────────────────
def test_belt_ladder_and_unknown_default() -> None:
    assert base_elo_for_belt("black") == BASE_BLACKBELT_ELO == 800.0
    assert base_elo_for_belt("white") == 200.0
    assert base_elo_for_belt("purple") == 500.0
    assert base_elo_for_belt(None) == 800.0
    assert base_elo_for_belt("ultra-instinct") == 800.0


def test_new_node_seeds_at_belt_floor() -> None:
    # Balanced points (3-3) ⇒ S == 0.5; opponent at the floor ⇒ expected == 0.5,
    # so delta == 0 and the freshly-seeded node sits exactly on the floor.
    even = [
        {"label": "Guard Pass", "type": "position", "actor": "you"},       # 3
        {"label": "Guard Pass", "type": "position", "actor": "opponent"},  # 3
    ]
    graph, _ = replay_matches(
        "X", [_match(won=True, win_type="POINTS", sequence=even)],
        rank_target=800.0, opp_elos=[800.0], belt="black",
    )
    assert next(iter(graph.nodes.values())).computed_elo == pytest.approx(800.0, abs=1e-6)

    # A purple belt seeds at 500 under the same neutral conditions.
    g_purple, _ = replay_matches(
        "X", [_match(won=True, win_type="POINTS", sequence=even)],
        rank_target=500.0, opp_elos=[500.0], belt="purple",
    )
    assert next(iter(g_purple.nodes.values())).computed_elo == pytest.approx(500.0, abs=1e-6)


# ── k_factor behavior ─────────────────────────────────────────────────────────
def test_k_factor_shrinks_as_gap_closes_and_floors() -> None:
    far = k_factor(1, 800.0, 1400.0)   # gap 600 ⇒ gap_factor clamped to 1.0
    near = k_factor(1, 1399.0, 1400.0)  # gap ~0 ⇒ gap_factor floored at 0.1
    assert far == pytest.approx(40.0)
    assert near == pytest.approx(4.0)
    assert near < far
    # At the target the floor still applies (never zero).
    assert k_factor(1, 1400.0, 1400.0) == pytest.approx(4.0)


# ── convergence ────────────────────────────────────────────────────────────────
def test_repeated_wins_converge_toward_target() -> None:
    target = 1400.0
    matches = [_sub_win() for _ in range(40)]
    opp = [target] * 40
    graph, snaps = replay_matches("X", matches, target, opp, belt="black")

    # Climbs from the floor strongly toward the target, asymptotically, without
    # ever overshooting it (the gap-factor floor slows growth near the target).
    assert snaps[0] > BASE_BLACKBELT_ELO
    assert graph.user_elo <= target + 1e-6              # no overshoot
    assert graph.user_elo > 1300.0                       # >80% of the 800→1400 gap closed
    assert (target - snaps[-1]) < (target - snaps[0])    # gap narrowed

    # Per-match deltas shrink (monotone non-increasing) as the gap closes.
    diffs = [snaps[i] - snaps[i - 1] for i in range(1, len(snaps))]
    assert diffs[0] > 0
    assert all(diffs[i] >= diffs[i + 1] - 1e-6 for i in range(len(diffs) - 1))


def test_loss_lowers_graph_elo() -> None:
    target = 1400.0
    matches = [_sub_win(), _match(won=False, win_type="SUBMISSION", sequence=[
        {"label": "Rear Naked Choke", "type": "submission", "actor": "you"},
    ])]
    graph, snaps = replay_matches("X", matches, target, [1000.0, 1000.0], belt="black")
    assert snaps[1] < snaps[0]


def test_opponent_elo_drives_expected_term() -> None:
    target = 1400.0
    g_low, _ = replay_matches("X", [_sub_win()], target, [1000.0], belt="black")
    g_high, _ = replay_matches("X", [_sub_win()], target, [1600.0], belt="black")
    # Beating a stronger opponent yields a bigger gain.
    assert g_high.user_elo > g_low.user_elo > BASE_BLACKBELT_ELO


# ── score_from_match ──────────────────────────────────────────────────────────
def test_score_submission_outcome_dominates() -> None:
    assert score_from_match(_match(won=True, win_type="SUBMISSION")) == 1.0
    assert score_from_match(_match(won=False, win_type="SUBMISSION")) == 0.0


def test_score_sequence_point_map() -> None:
    m = _match(won=True, win_type="POINTS", sequence=[
        {"label": "Guard Pass", "type": "position", "actor": "you"},      # pass → 3
        {"label": "Single Leg Takedown", "type": "takedown", "actor": "opponent"},  # 2
    ])
    assert score_from_match(m) == pytest.approx(3 / 5)


def test_score_outcome_fallback() -> None:
    assert score_from_match(_match(won=True, win_type="POINTS", sequence=[])) == 0.75
    assert score_from_match(_match(won=False, win_type="POINTS", sequence=[])) == 0.25
