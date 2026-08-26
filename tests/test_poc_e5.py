"""PoC-E5's harness on synthetic corpora with a known answer.

No database. Every fixture here is a hand-built bout list whose right answer is decided by
construction, so a failure means the harness is wrong — not that the corpus moved.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np
import pytest

from analysis.poc.e5_grapple_like import (
    MIN_HALF_EDGES,
    MIN_HALF_NODES,
    PRODUCTION_ARM,
    Cohort,
    EmbeddingSupply,
    Half,
    _to_athlete_graph,
    athlete_bouts,
    build_cohort,
    chance_mrr,
    distances,
    methods,
    run_pass,
    split_halves,
    verdicts,
    wins,
)
from analysis.poc.e9_markov import BoutRow

Style = list[tuple[str, str]]          # ordered (label, type) events, one athlete's game


def _bout(idx: int, a: str, b: str, a_events: Style, b_events: Style) -> BoutRow:
    """One synthetic bout: each athlete's own (label, type) events, actors alternating."""
    seq: list[dict[str, Any]] = []
    for i in range(max(len(a_events), len(b_events))):
        for who, evs in ((a, a_events), (b, b_events)):
            if i < len(evs):
                seq.append({"label": evs[i][0], "type": evs[i][1], "actor_id": who,
                            "successful": True, "ts": float(10 * len(seq))})
    return BoutRow(key=(2020, f"{idx:03d}", f"m{idx}"), sequence=seq,
                   athlete_a=a, athlete_b=b, event="Test Open", win_type="POINTS",
                   family="other", discipline="grappling",
                   elapsed=[float(10 * i) for i in range(len(seq))])


NEUTRAL: Style = [("neutral one", "transition"), ("neutral two", "transition")]


def _corpus(styles: dict[str, Style], n_bouts: int = 6) -> list[BoutRow]:
    """Every athlete fights a shared sparring partner ``n_bouts`` times, always playing
    their own fixed game. Self-recognition should be perfect for any method that can read
    the dimension the styles were built to differ in."""
    rows: list[BoutRow] = []
    idx = 0
    for athlete, events in styles.items():
        for _ in range(n_bouts):
            rows.append(_bout(idx, athlete, "sparring-partner", events, NEUTRAL))
            idx += 1
    return rows


#: Four games that differ in VOCABULARY **and** in type mix — the case every arm should
#: read. Each is a 4-cycle, so the structural arms have shape held constant on purpose.
DISTINCT_STYLES: dict[str, Style] = {
    "guard-player": [("closed guard", "guard"), ("armbar", "submission"),
                     ("triangle", "submission"), ("omoplata", "submission"),
                     ("closed guard", "guard")],
    "wrestler": [("single leg", "takedown"), ("double leg", "takedown"),
                 ("body lock", "control"), ("front headlock", "control"),
                 ("single leg", "takedown")],
    "leg-locker": [("ashi garami", "control"), ("heel hook", "submission"),
                   ("fifty fifty", "guard"), ("inside sankaku", "control"),
                   ("ashi garami", "control")],
    "passer": [("knee slice", "pass"), ("side control", "control"),
               ("mount", "control"), ("back control", "control"),
               ("knee slice", "pass")],
}

#: Same four vocabularies, all events typed ``control`` — vocabulary differs, TYPE MIX does
#: not. This is the blind spot the shipped method's 0.50·type-cosine + 0.20·hub-type has by
#: construction, and the fixture that pins it.
SAME_TYPE_STYLES: dict[str, Style] = {
    name: [(label, "control") for label, _ in events]
    for name, events in DISTINCT_STYLES.items()
}

#: One shared vocabulary, four different SHAPES: a star, a path, a triangle traversed
#: twice, and a densely re-entered clique. Nothing a label-reading method can see.
SHAPE_STYLES: dict[str, Style] = {
    "hub": [(x, "control") for x in
            ["a", "b", "a", "c", "a", "d", "a", "e", "a"]],
    "chain": [(x, "control") for x in ["a", "b", "c", "d", "e", "f", "g", "h", "i"]],
    "loop": [(x, "control") for x in ["a", "b", "c", "a", "b", "c", "a", "b", "c"]],
    "dense": [(x, "control") for x in ["a", "b", "c", "d", "b", "d", "a", "c", "a"]],
}


# ── cohort construction ─────────────────────────────────────────────────────────


def test_athlete_bouts_only_counts_bouts_the_athlete_files_events_in() -> None:
    guard: Style = [("closed guard", "guard"), ("armbar", "submission")]
    wrestle: Style = [("single leg", "takedown"), ("double leg", "takedown")]
    rows = [_bout(0, "x", "y", guard, []), _bout(1, "x", "y", [], wrestle)]
    per = athlete_bouts(rows)
    assert len(per["x"]) == 1        # bout 1 files nothing under x
    assert len(per["y"]) == 1        # bout 0 files nothing under y


def test_odd_even_split_balances_and_chronological_does_not_share_bouts() -> None:
    rows = _corpus({"a": DISTINCT_STYLES["guard-player"]}, n_bouts=6)
    per = athlete_bouts(rows)
    for scheme in ("odd_even", "chronological"):
        ha, hb = split_halves(per["a"], "a", scheme)
        assert ha.n_bouts + hb.n_bouts == 6
        assert abs(ha.n_bouts - hb.n_bouts) <= 1


def test_split_halves_rejects_an_unknown_scheme() -> None:
    rows = _corpus({"a": DISTINCT_STYLES["wrestler"]}, n_bouts=4)
    with pytest.raises(ValueError, match="unknown split scheme"):
        split_halves(athlete_bouts(rows)["a"], "a", "random")


def test_cohort_drops_and_counts_an_athlete_whose_half_is_too_thin() -> None:
    """One athlete with a single-label game cannot make a 3-node, 2-edge half."""
    one_trick: Style = [("closed guard", "guard")]
    rows = _corpus({**DISTINCT_STYLES, "one-trick": one_trick}, n_bouts=6)
    c = build_cohort(athlete_bouts(rows), floor=4, scheme="odd_even")
    assert c.eligible == 6                       # 4 styles + one-trick + sparring-partner
    assert c.dropped >= 1
    assert c.n == c.eligible - c.dropped
    assert all(h.ok for h in [*c.a, *c.b])


def test_half_gate_thresholds_are_what_the_prereg_says() -> None:
    g = nx.DiGraph()
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    assert g.number_of_nodes() == MIN_HALF_NODES and g.number_of_edges() == MIN_HALF_EDGES
    assert Half("x", "A", 2, g).ok
    thin = nx.DiGraph()
    thin.add_edge("a", "b")
    assert not Half("x", "A", 2, thin).ok


def test_cohort_median_shape_is_reported_over_both_halves() -> None:
    c = build_cohort(athlete_bouts(_corpus(DISTINCT_STYLES)), floor=4, scheme="odd_even")
    n, e = c.median_shape
    assert n >= MIN_HALF_NODES and e >= MIN_HALF_EDGES


# ── the production arm's reproduction ───────────────────────────────────────────


def test_production_arm_truncates_to_the_twelve_busiest_nodes() -> None:
    g = nx.DiGraph()
    for i in range(20):
        g.add_node(f"n{i}", occ=20 - i, type="control")
    for i in range(19):
        g.add_edge(f"n{i}", f"n{i + 1}", weight=1)
    h = Half("x", "A", 5, g)
    assert len(_to_athlete_graph(h, 12).nodes) == 12
    assert len(_to_athlete_graph(h, None).nodes) == 20
    # the kept twelve are the busiest, not the first twelve by name
    assert set(_to_athlete_graph(h, 12).nodes) == {f"n{i}" for i in range(12)}


def test_production_arm_keeps_only_edges_between_surviving_nodes() -> None:
    g = nx.DiGraph()
    g.add_node("keep1", occ=10, type="control")
    g.add_node("keep2", occ=9, type="control")
    g.add_node("drop", occ=1, type="control")
    g.add_edge("keep1", "keep2", weight=3)
    g.add_edge("keep2", "drop", weight=2)
    ag = _to_athlete_graph(Half("x", "A", 1, g), 2)
    assert set(ag.edges) == {("keep1", "keep2")}


# ── the criterion ───────────────────────────────────────────────────────────────


def test_chance_mrr_is_the_harmonic_mean_rank() -> None:
    assert chance_mrr(1) == pytest.approx(1.0)
    assert chance_mrr(2) == pytest.approx((1 + 0.5) / 2)
    assert chance_mrr(4) == pytest.approx((1 + 1 / 2 + 1 / 3 + 1 / 4) / 4)


def _arms(styles: dict[str, Style]) -> dict[str, float]:
    c = build_cohort(athlete_bouts(_corpus(styles)), floor=4, scheme="odd_even")
    assert c.n >= 4
    return {a.name: a.mrr for a in run_pass(c, EmbeddingSupply(), n_boot=200).arms}


def test_the_shipped_method_recognises_a_game_when_the_type_mix_differs() -> None:
    """Positive control for the label/type-reading family. Four games with different
    vocabularies AND different type mixes — the shipped method must score perfectly."""
    mrr = _arms(DISTINCT_STYLES)
    assert mrr[PRODUCTION_ARM] == pytest.approx(1.0)
    assert mrr["athlete_systems, untruncated"] == pytest.approx(1.0)


def test_the_shipped_method_is_blind_to_four_disjoint_vocabularies_of_one_type() -> None:
    """A structural property of the shipped similarity, pinned so it cannot be forgotten.

    ``system_similarity`` is 0.50·(8-dim type-share cosine) + 0.20·(same hub type) +
    0.15·(size) + 0.15·(ELO). Give four athletes four completely disjoint technique
    vocabularies that happen to share a type mix and it scores every pair identically —
    the percentage on the dossier is reading the TYPE PROFILE, not the techniques. This is
    the mechanism behind `e5.md`'s corpus finding that the shipped arm is not separable
    from a size descriptor.
    """
    mrr = _arms(SAME_TYPE_STYLES)
    assert mrr[PRODUCTION_ARM] == pytest.approx(0.25)          # 1/4 — the pessimistic floor
    assert mrr["athlete_systems, untruncated"] == pytest.approx(0.25)


def test_the_structural_arms_recognise_a_game_by_shape_alone() -> None:
    """Positive control for the signature family. One shared vocabulary, four different
    graph shapes — nothing a label reader can see, and exactly what NetLSD and the network
    portrait exist for."""
    mrr = _arms(SHAPE_STYLES)
    assert mrr["NetLSD (heat trace)"] == pytest.approx(1.0)
    assert mrr["portrait divergence"] == pytest.approx(1.0)


def test_the_structural_arms_are_blind_to_vocabulary_when_the_shape_is_held_fixed() -> None:
    """The mirror image, and the reason both families are in the same table: four disjoint
    vocabularies laid out in the same 4-cycle are indistinguishable to a shape descriptor."""
    mrr = _arms(DISTINCT_STYLES)
    assert mrr["NetLSD (heat trace)"] == pytest.approx(0.25)
    assert mrr["portrait divergence"] == pytest.approx(0.25)


def test_arms_collapse_to_chance_when_every_athlete_plays_the_same_game() -> None:
    """The negative control. Identical vocabularies → nothing to recognise. Ranks are
    ties, and ties are pessimistic, so every arm must sit at the floor."""
    same: Style = DISTINCT_STYLES["guard-player"]
    c = build_cohort(athlete_bouts(_corpus({k: list(same) for k in DISTINCT_STYLES})),
                     floor=4, scheme="odd_even")
    p = run_pass(c, EmbeddingSupply(), n_boot=200)
    for arm in p.arms:
        assert arm.mrr == pytest.approx(1.0 / c.n), f"{arm.name} scored above chance on noise"


def test_distance_matrix_is_query_by_candidate_not_symmetric_by_assumption() -> None:
    c = build_cohort(athlete_bouts(_corpus(DISTINCT_STYLES)), floor=4, scheme="odd_even")
    m = methods(EmbeddingSupply())[0]
    d = distances(m, c)
    assert d.shape == (c.n, c.n)
    assert np.all(np.isfinite(d))
    # the diagonal (own half A → own half B) must be the row minimum for a working method
    assert all(d[i, i] == d[i].min() for i in range(c.n))


def test_mpnet_arms_are_skipped_when_embedding_coverage_is_short() -> None:
    thin = EmbeddingSupply(vectors={"a": np.ones(4)}, covered=1, total=100)
    assert not thin.usable
    assert not any("mpnet" in m.name for m in methods(thin))
    rich = EmbeddingSupply(vectors={"a": np.ones(4)}, covered=90, total=100)
    assert rich.usable
    assert sum("mpnet" in m.name for m in methods(rich)) == 2


def test_a_degenerate_interval_is_never_a_win() -> None:
    """PoC-E9's amendment, adopted verbatim."""
    assert wins((0.5, 0.1, 0.9))
    assert not wins((0.5, -0.1, 0.9))            # crosses zero
    assert not wins((0.5, 0.5, 0.5))             # no width — nothing was established
    assert not wins((float("nan"),) * 3)


def test_verdict_refuses_a_challenger_that_only_beats_production() -> None:
    """The pre-registered AND: production AND both nulls. A challenger that clears
    production while tying the degree histogram is a size descriptor, and must be refused
    however good its MRR looks."""
    p = run_pass(Cohort(floor=4, scheme="odd_even"), EmbeddingSupply())
    assert verdicts(p) == {"cell": "NOT RUN — no cohort"}

    c = build_cohort(athlete_bouts(_corpus(DISTINCT_STYLES)), floor=4, scheme="odd_even")
    real = run_pass(c, EmbeddingSupply(), n_boot=200)
    real.vs_production["NetLSD (heat trace)"] = (0.4, 0.2, 0.6)   # clears production
    real.vs_size["NetLSD (heat trace)"] = (0.4, 0.2, 0.6)         # clears the size null
    real.vs_degree["NetLSD (heat trace)"] = (0.4, -0.2, 0.6)      # ties the degree null
    assert verdicts(real)["replacement"].startswith("REJECT")

    real.vs_degree["NetLSD (heat trace)"] = (0.4, 0.2, 0.6)
    assert verdicts(real)["replacement"].startswith("ACCEPT")


def test_production_arm_name_matches_the_method_list() -> None:
    """A rename that silently detaches the verdict from the arm it grades is a live risk;
    this pins the two together."""
    assert methods(EmbeddingSupply())[0].name == PRODUCTION_ARM
