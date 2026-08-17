"""Rating V2 tests — all in-memory, no DB. See docs/rating_v2/."""

from __future__ import annotations

import random

import pytest

from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.glicko2 import expected_score, update_period
from analysis.rating_v2.models import Observation, RatingState
from analysis.rating_v2.node_periods import NodeBout, NodeConfig, run_node_periods
from analysis.rating_v2.node_replay import build_node_bouts, node_key_of
from analysis.rating_v2.periods import Bout, run_periods
from analysis.rating_v2.replay import (
    MatchRow,
    _score_a,
    bouts_hash,
    build_bouts,
    build_seeds,
)

# ── Wave 2: pure Glicko-2 core ───────────────────────────────────────────────


def test_official_glicko2_example() -> None:
    """The published worked example — the gate the whole engine is measured against."""
    start = RatingState(1500.0, 200.0, 0.06)
    games = [
        Observation(1400.0, 30.0, 1.0),
        Observation(1550.0, 100.0, 0.0),
        Observation(1700.0, 300.0, 0.0),
    ]
    result = update_period(start, games)
    assert result.rating == pytest.approx(1464.06, abs=0.1)
    assert result.deviation == pytest.approx(151.52, abs=0.1)
    assert result.volatility == pytest.approx(0.059996, abs=1e-5)


def test_determinism_same_input_same_output() -> None:
    start = RatingState(1500.0, 200.0, 0.06)
    games = [Observation(1400.0, 30.0, 1.0), Observation(1550.0, 100.0, 0.0)]
    r1 = update_period(start, games)
    r2 = update_period(start, games)
    assert r1 == r2


def test_no_observations_widens_rd_only() -> None:
    start = RatingState(1500.0, 200.0, 0.06)
    result = update_period(start, [])
    assert result.rating == start.rating
    assert result.deviation > start.deviation
    assert result.volatility == start.volatility


# ── Wave 3: periods (order independence, inactivity) ────────────────────────


def test_shuffling_bouts_within_a_period_does_not_change_output() -> None:
    seeds = {a: RatingState(1750.0, 250.0, 0.06) for a in ("A", "B", "C", "D")}
    bouts = [
        Bout(period=2024, athlete_a="A", athlete_b="B", score_a=1.0),
        Bout(period=2024, athlete_a="C", athlete_b="D", score_a=0.0),
        Bout(period=2024, athlete_a="A", athlete_b="C", score_a=1.0),
        Bout(period=2024, athlete_a="B", athlete_b="D", score_a=0.5),
    ]
    config = EngineConfig()

    baseline = run_periods(seeds, bouts, config)

    rng = random.Random(42)
    for _ in range(20):
        shuffled = bouts.copy()
        rng.shuffle(shuffled)
        result = run_periods(seeds, shuffled, config)
        for athlete_id, state in result.items():
            base = baseline[athlete_id]
            assert state.rating == pytest.approx(base.rating, abs=1e-9)
            assert state.deviation == pytest.approx(base.deviation, abs=1e-9)
            assert state.volatility == pytest.approx(base.volatility, abs=1e-9)


def test_inactive_athlete_rd_grows_across_periods() -> None:
    """A played only in 2020. A later period (2024, C vs D) still exists in the replay —
    A must widen for that gap even without a bout of their own."""
    seeds = {a: RatingState(1750.0, 250.0, 0.06) for a in ("A", "B", "C", "D")}
    config = EngineConfig()
    bouts_2020_only = [Bout(period=2020, athlete_a="A", athlete_b="B", score_a=1.0)]

    rd_after_2020 = run_periods(seeds, bouts_2020_only, config)["A"].deviation

    bouts_with_later_period = bouts_2020_only + [
        Bout(period=2024, athlete_a="C", athlete_b="D", score_a=1.0)
    ]
    rd_after_2024 = run_periods(seeds, bouts_with_later_period, config)["A"].deviation

    assert rd_after_2024 > rd_after_2020


def test_widening_runs_to_end_of_replay_not_to_athletes_own_last_bout() -> None:
    """Pinned contract (2026-08-17): an athlete's RD keeps widening through every later
    period the REPLAY covers, not just up to their own last fight. A's only bout is in
    2010; the dataset goes to 2026 via unrelated B/C bouts. A's RD "today" (2026) must be
    strictly bigger than A's RD right after the 2010 bout — that gap in confidence is the
    whole point of a "how much do we know NOW" rating."""
    seeds = {a: RatingState(1750.0, 250.0, 0.06) for a in ("A", "X", "B", "C")}
    config = EngineConfig()

    bout_2010 = [Bout(period=2010, athlete_a="A", athlete_b="X", score_a=1.0)]
    rd_right_after_2010 = run_periods(seeds, bout_2010, config)["A"].deviation

    unrelated_bouts_through_2026 = bout_2010 + [
        Bout(period=year, athlete_a="B", athlete_b="C", score_a=1.0)
        for year in (2012, 2016, 2020, 2026)
    ]
    rd_at_end_of_replay = run_periods(seeds, unrelated_bouts_through_2026, config)["A"].deviation

    assert rd_at_end_of_replay > rd_right_after_2010


# ── ADR-06: winner resolution ────────────────────────────────────────────────


def test_decision_without_winner_is_excluded_not_a_draw() -> None:
    assert _score_a(winner_id=None, athlete_a_id="A", win_type="DECISION") is None


def test_draw_scores_half_both_sides() -> None:
    assert _score_a(winner_id=None, athlete_a_id="A", win_type="DRAW") == 0.5


def test_known_winner_scores_one_zero() -> None:
    assert _score_a(winner_id="A", athlete_a_id="A", win_type="DECISION") == 1.0
    assert _score_a(winner_id="B", athlete_a_id="A", win_type="DECISION") == 0.0


def test_build_bouts_excludes_unknown_winner_decisions() -> None:
    config = EngineConfig()
    matches = [
        MatchRow("A", "B", None, "ADCC 2024", 2024, "DECISION"),  # excluded: unknown
        MatchRow("A", "B", "A", "ADCC 2024", 2024, "SUB"),  # included
        MatchRow("A", "B", None, "ADCC 2024", 2024, "DRAW"),  # included, 0.5
    ]
    events_map = {"ADCC 2024": "submission_grappling"}
    bouts, coverage = build_bouts(matches, config, events_map, "unknown")
    assert coverage["unknown_winner"] == 1
    assert coverage["eligible"] == 2
    assert len(bouts) == 2
    assert any(b.score_a == 0.5 for b in bouts)


# ── discipline filter ────────────────────────────────────────────────────────


def test_out_of_discipline_matches_are_excluded() -> None:
    config = EngineConfig(disciplines=("submission_grappling",))
    matches = [
        MatchRow("A", "B", "A", "UFC 300", 2024, "KO"),
        MatchRow("C", "D", "C", "NCAA 2024", 2024, "DEC"),
        MatchRow("E", "F", "E", "ADCC 2024", 2024, "SUB"),
    ]
    events_map = {"UFC 300": "mma", "NCAA 2024": "wrestling", "ADCC 2024": "submission_grappling"}
    bouts, coverage = build_bouts(matches, config, events_map, "unknown")
    assert coverage["out_of_discipline"] == 2
    assert coverage["eligible"] == 1
    assert bouts[0].athlete_a == "E"


def test_self_match_guard() -> None:
    config = EngineConfig()
    matches = [MatchRow("A", "A", "A", "ADCC 2024", 2024, "SUB")]
    events_map = {"ADCC 2024": "submission_grappling"}
    bouts, coverage = build_bouts(matches, config, events_map, "unknown")
    assert coverage["self_match"] == 1
    assert bouts == []


# ── ADR-01: rank_elo prior ───────────────────────────────────────────────────


def test_seed_from_rank_elo_flag() -> None:
    config = EngineConfig(seed_from_rank_elo=True, athlete_seed=1750.0, initial_rd=250.0)
    bouts = [Bout(period=2024, athlete_a="A", athlete_b="B", score_a=1.0)]
    seeds = build_seeds(bouts, config, rank_elo_by_athlete={"A": 2000.0})
    assert seeds["A"].rating == 2000.0
    assert seeds["A"].deviation == 250.0  # prior, not certainty — RD unchanged
    assert seeds["B"].rating == 1750.0  # no rank_elo -> flat seed


def test_seed_flat_when_flag_off() -> None:
    config = EngineConfig(seed_from_rank_elo=False)
    bouts = [Bout(period=2024, athlete_a="A", athlete_b="B", score_a=1.0)]
    seeds = build_seeds(bouts, config, rank_elo_by_athlete={"A": 2000.0})
    assert seeds["A"].rating == config.athlete_seed


# ── determinism of the replay hash ───────────────────────────────────────────


def test_bouts_hash_is_order_independent() -> None:
    b1 = [
        Bout(period=2024, athlete_a="A", athlete_b="B", score_a=1.0),
        Bout(period=2023, athlete_a="C", athlete_b="D", score_a=0.5),
    ]
    b2 = list(reversed(b1))
    assert bouts_hash(b1) == bouts_hash(b2)


# ── glicko2.expected_score ───────────────────────────────────────────────────


def test_expected_score_equal_ratings_is_half() -> None:
    assert expected_score(1750.0, 200.0, 1750.0, 200.0) == pytest.approx(0.5)


def test_expected_score_complements_across_sides() -> None:
    p_a = expected_score(1900.0, 150.0, 1700.0, 150.0)
    p_b = expected_score(1700.0, 150.0, 1900.0, 150.0)
    assert p_a + p_b == pytest.approx(1.0)
    assert p_a > 0.5


# ── Wave 5: node evidence layer (ADR-03) ─────────────────────────────────────
#
# "Node observation" invariant: a unique node in one athlete/bout contributes AT MOST ONE
# Glicko observation — repeating a label inside the same bout bumps occurrence count, never
# game count. Missing sequence is unknown, never negative evidence. A node seen for the
# first time inherits the athlete's CURRENT global rating with high RD. ``bouts_observed``
# is tracked separately from raw ``occurrences``.


def _node_seeds(*athletes: str) -> dict[str, RatingState]:
    return {a: RatingState(1750.0, 250.0, 0.06) for a in athletes}


def test_repeated_node_label_in_one_bout_is_one_observation() -> None:
    node_bouts = [
        NodeBout(
            bout=Bout(period=2024, athlete_a="A", athlete_b="B", score_a=1.0),
            nodes_a=frozenset({"guard"}),
            occurrences_a={"guard": 3},
        )
    ]
    _, node_states, node_meta = run_node_periods(
        _node_seeds("A", "B"), node_bouts, EngineConfig(), NodeConfig()
    )
    meta = node_meta[("A", "guard")]
    assert meta.bouts_observed == 1
    assert meta.occurrences == 3
    assert ("A", "guard") in node_states


def test_bouts_observed_counts_distinct_bouts_not_occurrences() -> None:
    node_bouts = [
        NodeBout(
            bout=Bout(period=2024, athlete_a="A", athlete_b="B", score_a=1.0),
            nodes_a=frozenset({"guard"}),
            occurrences_a={"guard": 3},
        ),
        NodeBout(
            bout=Bout(period=2024, athlete_a="A", athlete_b="C", score_a=0.0),
            nodes_a=frozenset({"guard"}),
            occurrences_a={"guard": 1},
        ),
    ]
    _, _, node_meta = run_node_periods(
        _node_seeds("A", "B", "C"), node_bouts, EngineConfig(), NodeConfig()
    )
    meta = node_meta[("A", "guard")]
    assert meta.bouts_observed == 2  # two bouts
    assert meta.occurrences == 4  # 3 + 1 raw repeats
    assert meta.bouts_observed != meta.occurrences


def test_missing_sequence_widens_without_negative_evidence() -> None:
    node_bouts = [
        NodeBout(
            bout=Bout(period=2020, athlete_a="A", athlete_b="X", score_a=1.0),
            nodes_a=frozenset({"armbar"}),
            occurrences_a={"armbar": 1},
        ),
        # 2024: A fights again but this bout's sequence has nothing for A (missing sequence,
        # or the node simply doesn't recur) — must never read as a loss for "armbar".
        NodeBout(bout=Bout(period=2024, athlete_a="A", athlete_b="Y", score_a=1.0)),
    ]
    _, node_states, node_meta = run_node_periods(
        _node_seeds("A", "X", "Y"), node_bouts, EngineConfig(), NodeConfig()
    )
    armbar = node_states[("A", "armbar")]
    # one real observation only — never a second one from the empty-sequence bout.
    assert node_meta[("A", "armbar")].bouts_observed == 1
    # RD still widened for the 2020->2024 gap (same widening semantics as periods.py),
    # never sharpened by a fabricated negative result.
    assert armbar.deviation > 0


def test_new_node_inherits_current_global_rating_not_the_original_seed() -> None:
    """A wins a bout in 2020 (global rating moves off 1750); a NEW node "leg drag" is first
    used in 2021. The node must seed at A's 2021 pre-period global rating, not 1750."""
    node_bouts = [
        NodeBout(bout=Bout(period=2020, athlete_a="A", athlete_b="X", score_a=1.0)),
        NodeBout(
            bout=Bout(period=2021, athlete_a="A", athlete_b="Y", score_a=0.0),
            nodes_a=frozenset({"leg drag"}),
            occurrences_a={"leg drag": 1},
        ),
    ]
    node_config = NodeConfig(node_initial_rd=280.0)
    global_states, node_states, _ = run_node_periods(
        _node_seeds("A", "X", "Y"), node_bouts[:1], EngineConfig(), node_config
    )
    a_global_after_2020 = global_states["A"].rating
    assert a_global_after_2020 != pytest.approx(1750.0)  # evidence moved it

    _, node_states_full, _ = run_node_periods(
        _node_seeds("A", "X", "Y"), node_bouts, EngineConfig(), node_config
    )
    leg_drag = node_states_full[("A", "leg drag")]
    # seeded at A's rating AS OF the period it first appears (2021 pre-period == end of 2020).
    assert leg_drag.rating != pytest.approx(1750.0)
    assert leg_drag.deviation <= 280.0  # started at 280, one weighted obs can only shrink it


def test_build_node_bouts_eligibility_matches_build_bouts() -> None:
    """Lockstep gate: node_replay's own filter must accept/reject the same rows as
    replay.build_bouts on the same input — the two are not allowed to drift apart."""
    config = EngineConfig()
    events_map = {"ADCC 2024": "submission_grappling", "UFC 300": "mma"}
    matches = [
        MatchRow("A", "B", "A", "ADCC 2024", 2024, "SUB"),
        MatchRow("A", "B", None, "ADCC 2024", 2024, "DECISION"),  # unknown winner
        MatchRow("C", "D", "C", "UFC 300", 2024, "KO"),  # out of discipline
        MatchRow("E", "E", "E", "ADCC 2024", 2024, "SUB"),  # self match
        MatchRow("F", "G", None, "ADCC 2024", 2024, "DRAW"),  # draw, included
    ]
    bouts, coverage = build_bouts(matches, config, events_map, "unknown")
    node_bouts, node_coverage = build_node_bouts(matches, config, events_map, "unknown")
    assert coverage["eligible"] == node_coverage["eligible"] == len(bouts) == len(node_bouts)
    assert coverage == node_coverage


def test_node_global_track_matches_periods_run_periods() -> None:
    """The global track computed inside run_node_periods must be bit-identical to
    periods.run_periods on the same bouts — node evidence never feeds back into it."""
    seeds = _node_seeds("A", "B", "C", "D")
    bouts = [
        Bout(period=2024, athlete_a="A", athlete_b="B", score_a=1.0),
        Bout(period=2024, athlete_a="C", athlete_b="D", score_a=0.0),
        Bout(period=2025, athlete_a="A", athlete_b="C", score_a=0.5),
    ]
    config = EngineConfig()

    expected = run_periods(seeds, bouts, config)
    node_bouts = [NodeBout(bout=b) for b in bouts]
    got, _, _ = run_node_periods(seeds, node_bouts, config, NodeConfig())

    for athlete_id, state in expected.items():
        assert got[athlete_id] == state


def test_node_key_of_reuses_repo_normalization() -> None:
    """node_key derivation must go through analysis.names — never invent normalization
    (root CLAUDE.md contract with the App's normalizeLabel())."""
    from analysis.names import _normalize_name, canonicalize

    label = "Ankle Pick Takedown"
    assert node_key_of(label) == canonicalize(_normalize_name(label))
