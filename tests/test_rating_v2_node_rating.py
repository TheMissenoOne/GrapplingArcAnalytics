"""ADR-16 — the production per-node Glicko-2 track for athlete graphs.

Distinct from ``test_rating_v2_node_layer_shadow.py``, which guards the wave-5 SHADOW study
(``node_periods``/``node_replay``, bout-scored, feeding the 0036 tables). This file covers the
track that actually produces ``computed_elo``.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.models import RatingState
from analysis.rating_v2.node_rating import (
    NODE_EVENT_WEIGHT,
    NODE_INITIAL_RD,
    NodeEvidenceBout,
    NodeRating,
    node_key_of,
    observations_for_side,
    project_onto_graph,
    run_node_ratings,
)
from analysis.rating_v2.periods import Bout, run_periods, run_periods_with_snapshots
from analysis.rating_v2.replay import MatchRow, build_bouts

A = "athlete-a"
B = "athlete-b"


# ── observation extraction ──────────────────────────────────────────────────────────────


def _event(label: str, typ: str, actor: str, successful: bool | None) -> dict[str, Any]:
    ev: dict[str, Any] = {"label": label, "type": typ, "actor_id": actor}
    if successful is not None:
        ev["successful"] = successful
    return ev


def test_null_successful_produces_no_observation() -> None:
    """ADR-06 one level down: a missing outcome is lost coverage, never a fabricated one.

    Measured on the 2026-08-26 corpus, 67.7% of own-actor labelled events carry no flag —
    so this is the rule that decides two thirds of the input, not an edge case.
    """
    seq = [
        _event("Armbar", "submission", A, None),
        _event("Heel Hook", "submission", A, True),
        _event("Back Take", "control", A, False),
    ]
    obs = observations_for_side(seq, A, None)
    assert [o.node_key for o in obs] == [node_key_of("Heel Hook"), node_key_of("Back Take")]
    assert [o.score for o in obs] == [1.0, 0.0]


def test_only_the_named_actor_is_read() -> None:
    seq = [_event("Armbar", "submission", A, True), _event("Armbar", "submission", B, True)]
    assert len(observations_for_side(seq, A, None)) == 1
    assert len(observations_for_side(seq, B, None)) == 1


def test_weights_are_mean_one_so_total_evidence_does_not_move() -> None:
    """The invariant the root CLAUDE.md Markov row states: only the SPLIT moves.

    Sum-1 would make a six-event bout worth as much as a one-event bout; raw block weights
    would let total information wander with which actions happened to appear.
    """
    block = {"SUBA": 0.9, "SUB": 0.2, "GPS": 0.5}
    seq = [
        _event("Armbar", "submission", A, False),  # SUBA
        _event("Heel Hook", "submission", A, True),  # SUB
        _event("Knee Cut", "pass", A, True),  # GPS
        _event("Closed Guard", "guard", A, True),  # no code -> block mean
    ]
    obs = observations_for_side(seq, A, block)
    assert len(obs) == 4
    total = sum(o.weight for o in obs)
    assert total == pytest.approx(NODE_EVENT_WEIGHT * len(obs))
    # And the split is not uniform, or the weighting would be doing nothing.
    assert len({round(o.weight, 6) for o in obs}) > 1


def test_equal_weights_and_no_block_are_both_the_identity() -> None:
    seq = [
        _event("Armbar", "submission", A, True),
        _event("Knee Cut", "pass", A, False),
    ]
    flat = observations_for_side(seq, A, {"SUB": 0.4, "GPSA": 0.4})
    none = observations_for_side(seq, A, None)
    assert [o.weight for o in flat] == [pytest.approx(NODE_EVENT_WEIGHT)] * 2
    assert [o.weight for o in none] == [pytest.approx(NODE_EVENT_WEIGHT)] * 2


# ── the node replay ─────────────────────────────────────────────────────────────────────


def _snapshots(
    seeds: dict[str, RatingState], bouts: list[Bout]
) -> dict[int, dict[str, RatingState]]:
    return run_periods_with_snapshots(seeds, bouts, EngineConfig())[1]


def test_run_periods_with_snapshots_agrees_with_run_periods() -> None:
    """One implementation of the global track, two return shapes — never two answers."""
    seeds = {A: RatingState(1750, 250, 0.06), B: RatingState(1600, 200, 0.06)}
    bouts = [Bout(2024, A, B, 1.0), Bout(2025, A, B, 0.0)]
    config = EngineConfig()
    assert run_periods(seeds, bouts, config) == run_periods_with_snapshots(seeds, bouts, config)[0]


def test_new_node_is_seeded_at_its_athletes_pre_period_global() -> None:
    seeds = {A: RatingState(1750, 250, 0.06), B: RatingState(1600, 200, 0.06)}
    bouts = [Bout(2024, A, B, 1.0)]
    snaps = _snapshots(seeds, bouts)
    evidence = [
        NodeEvidenceBout(
            bout=bouts[0],
            observations_a=observations_for_side(
                [_event("Armbar", "submission", A, True)], A, None
            ),
        )
    ]
    ratings = run_node_ratings(snaps, evidence)
    node = ratings[(A, node_key_of("Armbar"))]
    assert node.seed_rating == pytest.approx(1750.0)
    # One landed observation moves it up, but only a little: the weight is a fraction of a
    # game and the RD stays wide.
    assert node.rating > 1750.0
    assert node.deviation < NODE_INITIAL_RD
    assert node.deviation > 300.0
    assert node.observations == 1 and node.bouts == 1


def test_a_missed_technique_moves_the_node_down() -> None:
    seeds = {A: RatingState(1750, 250, 0.06), B: RatingState(1750, 250, 0.06)}
    bouts = [Bout(2024, A, B, 1.0)]
    snaps = _snapshots(seeds, bouts)
    def rate(successful: bool) -> float:
        obs = observations_for_side([_event("X", "sweep", A, successful)], A, None)
        return run_node_ratings(snaps, [NodeEvidenceBout(bouts[0], obs)])[
            (A, node_key_of("X"))
        ].rating

    landed, missed = rate(True), rate(False)
    assert missed < 1750.0 < landed


def test_the_node_is_anchored_on_its_own_athlete_not_the_opponent() -> None:
    """The anchor decision, asserted so it cannot silently flip back.

    Anchoring on the OPPONENT was tried first and measured `corr(global, node offset) =
    −0.790`: "the technique landed" and "the bout was won" are different variables, so a
    dominant athlete's every technique read as below their own level and every evidenced node
    fell below every seeded one. Own-anchor decouples the node from athlete strength — the
    same reading the App's `ratingV2Evidence.ts` already used.

    Concretely: the same landed technique moves a node by the SAME amount whoever it was
    landed on, because opponent strength is what the GLOBAL track measures and the node is
    expressed relative to that.
    """
    bouts = [Bout(2024, A, B, 1.0)]
    seq = [_event("Armbar", "submission", A, True)]

    def offset(opponent: RatingState) -> float:
        node = run_node_ratings(
            _snapshots({A: RatingState(1750, 250, 0.06), B: opponent}, bouts),
            [NodeEvidenceBout(bouts[0], observations_for_side(seq, A, None))],
        )[(A, node_key_of("Armbar"))]
        return node.rating - node.seed_rating

    assert offset(RatingState(2100, 100, 0.06)) == pytest.approx(
        offset(RatingState(1400, 100, 0.06))
    )


def test_re_anchoring_keeps_the_evidence_and_drops_the_stale_seed() -> None:
    """The seed anchor goes stale as the athlete's own rating moves, and that staleness — NOT
    the evidence — was the whole of the measured strength bias (corr −0.855 for the anchor
    against +0.109 for the evidence term). Re-anchoring keeps what the technique earned and
    re-expresses it against the athlete's current level, which is also the level
    ``project_onto_graph`` gives a never-seen node.
    """
    seeds = {A: RatingState(1750, 250, 0.06), B: RatingState(1400, 100, 0.06)}
    bouts = [Bout(2024, A, B, 1.0), Bout(2025, A, B, 1.0)]
    snaps = _snapshots(seeds, bouts)
    obs = observations_for_side([_event("X", "sweep", A, True)], A, None)
    evidence = [NodeEvidenceBout(bouts[0], obs), NodeEvidenceBout(bouts[1])]

    raw = run_node_ratings(snaps, evidence)[(A, node_key_of("X"))]
    final = run_periods(seeds, bouts, EngineConfig())
    anchored = run_node_ratings(snaps, evidence, final)[(A, node_key_of("X"))]

    # The athlete climbed over those two periods, so the seed is now below their real level.
    assert final[A].rating > raw.seed_rating
    assert anchored.offset == pytest.approx(raw.offset)
    assert anchored.rating == pytest.approx(final[A].rating + raw.offset)
    assert anchored.deviation == pytest.approx(raw.deviation)  # anchor, not information
    # A landed technique must not read below the athlete's own current level.
    assert anchored.rating > final[A].rating


def test_a_first_observation_is_scored_against_a_coin_flip() -> None:
    """A node is seeded AT its athlete's global, so E is exactly 0.5 on its first sighting —
    landing and missing move it by the same distance in opposite directions. That symmetry is
    what makes the node rating a statement about the TECHNIQUE rather than about the athlete.
    """
    seeds = {A: RatingState(1750, 250, 0.06), B: RatingState(1400, 100, 0.06)}
    bouts = [Bout(2024, A, B, 1.0)]
    snaps = _snapshots(seeds, bouts)

    def offset(successful: bool) -> float:
        obs = observations_for_side([_event("X", "sweep", A, successful)], A, None)
        node = run_node_ratings(snaps, [NodeEvidenceBout(bouts[0], obs)])[(A, node_key_of("X"))]
        return node.rating - node.seed_rating

    assert offset(True) == pytest.approx(-offset(False))


def test_repeats_in_one_bout_count_as_one_bout_but_several_observations() -> None:
    seeds = {A: RatingState(1750, 250, 0.06), B: RatingState(1750, 250, 0.06)}
    bouts = [Bout(2024, A, B, 1.0)]
    seq = [_event("Armbar", "submission", A, True)] * 3
    ratings = run_node_ratings(
        _snapshots(seeds, bouts),
        [NodeEvidenceBout(bouts[0], observations_for_side(seq, A, None))],
    )
    node = ratings[(A, node_key_of("Armbar"))]
    assert (node.observations, node.bouts) == (3, 1)


def test_an_unused_node_widens_until_the_end_of_the_replay() -> None:
    """ADR-09's widening rule, one level down: RD answers "what do we know TODAY".

    The widening is driven by the PERIODS THE REPLAY COVERS, not by whether this node saw a
    bout — which is the whole point. A technique last landed in 2024 must read as less
    certain in 2026 than it did the day it landed.
    """
    seeds = {A: RatingState(1750, 250, 0.06), B: RatingState(1750, 250, 0.06)}
    first = Bout(2024, A, B, 1.0)
    evidence = [
        NodeEvidenceBout(first, observations_for_side([_event("X", "sweep", A, True)], A, None))
    ]
    stops_in_2024 = run_node_ratings(_snapshots(seeds, [first]), evidence)[(A, node_key_of("X"))]
    runs_to_2026 = run_node_ratings(
        _snapshots(seeds, [first, Bout(2025, A, B, 1.0), Bout(2026, A, B, 1.0)]), evidence
    )[(A, node_key_of("X"))]
    assert runs_to_2026.deviation > stops_in_2024.deviation
    assert runs_to_2026.rating == pytest.approx(stops_in_2024.rating)


def test_weight_is_repeat_count_expansion_for_an_integer_weight() -> None:
    """The justification for calling a weighted observation Glicko-2 at all.

    ``update_period`` multiplies the information term and the score residual by the SAME
    weight, so an integer weight is arithmetically identical to repeating the observation.
    The fractional case is that identity's continuous extension — which is what licenses a
    Markov share as an observation weight instead of a second engine.
    """
    from analysis.rating_v2.glicko2 import update_period
    from analysis.rating_v2.models import Observation

    state = RatingState(1750, 350, 0.06)
    one = Observation(1600, 200, 1.0, weight=3.0)
    three = [Observation(1600, 200, 1.0)] * 3
    weighted = update_period(state, [one], tau=0.5)
    repeated = update_period(state, three, tau=0.5)
    assert weighted.rating == pytest.approx(repeated.rating, abs=1e-9)
    assert weighted.deviation == pytest.approx(repeated.deviation, abs=1e-9)


# ── the projection ──────────────────────────────────────────────────────────────────────


def _graph() -> AthleteGraph:
    g = AthleteGraph(athlete="A")
    for key in ("armbar", "closed guard", "knee cut"):
        g.nodes[key] = AthleteNode(label=key, type="", count=1, computed_elo=812.0)
    g.edges[("closed guard", "armbar")] = AthleteEdge("closed guard", "armbar", 2, elo=805.0)
    g.edges[("armbar", "knee cut")] = AthleteEdge("armbar", "knee cut", 1, elo=804.0)
    g.user_elo = 810.0
    return g


def _ratings(**by_key: float) -> dict[tuple[str, str], NodeRating]:
    return {
        (A, k): NodeRating(v, 320.0, 0.06, observations=1, bouts=1, seed_rating=v)
        for k, v in by_key.items()
    }


def test_projection_seeds_every_unseen_node_at_the_global() -> None:
    """The App measured this the hard way: a raw projection left σ 354 and zero signatures,
    seeding took it to σ 31.9. Partial coverage on a rating field is two units, not less data.
    """
    g = _graph()
    result = project_onto_graph(g, A, _ratings(armbar=1900.0), global_rating=1780.0)
    assert (result.evidenced, result.seeded) == (1, 2)
    assert g.nodes["armbar"].computed_elo == 1900.0
    assert g.nodes["closed guard"].computed_elo == 1780.0
    assert g.nodes["knee cut"].computed_elo == 1780.0
    assert all(n.computed_elo is not None and n.computed_elo > 1000 for n in g.nodes.values())


def test_projection_re_derives_edge_elo_and_user_elo() -> None:
    g = _graph()
    project_onto_graph(g, A, _ratings(armbar=1900.0), global_rating=1780.0)
    assert g.edges[("closed guard", "armbar")].elo == pytest.approx((1780.0 + 1900.0) / 2)
    assert g.edges[("armbar", "knee cut")].elo == pytest.approx((1900.0 + 1780.0) / 2)
    # `user_elo` is the engine's own answer, not a mean over mostly-seeded nodes.
    assert g.user_elo == 1780.0


def test_projection_leaves_v1_alone_when_the_athlete_has_no_v2_state() -> None:
    """ADR-05 keeps MMA and wrestling out of the grappling run; those athletes have no global
    state, and half a projection is the one thing that must never reach the DB."""
    g = _graph()
    result = project_onto_graph(g, A, _ratings(armbar=1900.0), global_rating=None)
    assert (result.evidenced, result.seeded) == (0, 0)
    assert [n.computed_elo for n in g.nodes.values()] == [812.0, 812.0, 812.0]
    assert g.user_elo == 810.0


# ── lockstep with the global replay's eligibility ───────────────────────────────────────


def test_node_evidence_eligibility_matches_build_bouts() -> None:
    """``build_corpus_node_ratings`` re-implements ``build_bouts``' filter because it needs the
    ``Match`` row that function throws away. This is the gate that keeps the two in step."""
    from analysis.rating_v2.replay import _discipline_of, _score_a

    config = EngineConfig()
    events_map = {"ADCC 2024": "submission_grappling", "UFC 300": "mma"}
    rows = [
        MatchRow(A, B, A, "ADCC 2024", 2024, "SUBMISSION", "final", []),
        MatchRow(A, B, A, "UFC 300", 2024, "SUBMISSION", "final", []),  # out of discipline
        MatchRow(A, B, A, "ADCC 2024", None, "SUBMISSION", "final", []),  # no year
        MatchRow(A, B, None, "ADCC 2024", 2024, "DECISION", "final", []),  # ADR-06 unknown
        MatchRow(A, B, A, "ADCC 2024", 2024, "SUBMISSION", "draft", []),  # not final
        MatchRow(A, A, A, "ADCC 2024", 2024, "SUBMISSION", "final", []),  # self-match
        MatchRow(A, B, None, "ADCC 2024", 2024, "DRAW", "final", []),  # draw is eligible
    ]
    expected, _ = build_bouts(rows, config, events_map, "unknown")

    mirrored = [
        m
        for m in rows
        if m.status == "final"
        and m.athlete_a_id != m.athlete_b_id
        and _discipline_of(m.event, events_map, "unknown") in config.disciplines
        and m.year is not None
        and _score_a(m.winner_id, m.athlete_a_id, m.win_type) is not None
    ]
    assert len(mirrored) == len(expected) == 2


def test_node_key_uses_the_repo_wide_derivation() -> None:
    """Same key space as ``athlete_elo``/``athlete_graph``, therefore the same one the
    persisted edges live in — and the char-for-char twin of the App's ``normalizeLabel``."""
    from analysis.names import _normalize_name, canonicalize

    for label in ("Closed Guard", "Berimbolo", "De La Riva Guard"):
        assert node_key_of(label) == canonicalize(_normalize_name(label))


def test_series_stays_on_one_scale() -> None:
    from analysis.rating_v2.node_rating import CorpusNodeRatings

    corpus = CorpusNodeRatings(
        node_ratings={},
        global_final={A: RatingState(1800, 200, 0.06)},
        global_after={
            2024: {A: RatingState(1700, 250, 0.06)},
            2025: {A: RatingState(1800, 200, 0.06)},
        },
        coverage={},
    )
    assert corpus.series_for(A, [2024, 2025, 2025]) == [1700.0, 1800.0, 1800.0]
    # A bout before the athlete's first period falls back to the final rating, never to a
    # V1 number — the row must not carry two scales (ADR-02).
    assert corpus.series_for(A, [2010]) == [1800.0]
    assert corpus.series_for("nobody", [2024]) == []
    assert all(math.isfinite(x) for x in corpus.series_for(A, [2024, None]))
