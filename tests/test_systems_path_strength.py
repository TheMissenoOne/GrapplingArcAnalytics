"""Absorbing-Markov path strength inside a system — hand-computable synthetic graphs.

Every expected number below is derivable on paper from the sequences that build the graph,
so a change in ``transitions/build_graph`` semantics (occ / denom / risk / within-actor
edges) fails here loudly instead of silently moving a published strength.

Labels are deliberately unrecognisable to ``technique_match.clean_label`` ("Pos Alpha", not
"Closed Guard") so canonicalisation cannot rename a node mid-test.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from analysis.systems_path_strength import (
    MIN_DESIRED_OCC,
    absorption,
    desired_node,
    direction_factor,
    expected_steps,
    rank_systems,
    system_path_strength,
    to_dict,
    transition_rows,
)
from analysis.transitions import network_from_sequences

A, B, C, X = "Pos Alpha", "Pos Beta", "Fin Gamma", "Out Delta"
_PRIZE_ONE: dict[str, float] = {C: 1.0}  # PtV(desired)=1 → prize=1, so composites are exact


def _ev(label: str, typ: str, actor: str = "a", ok: bool = False) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor_id": actor, "successful": ok}


def _chain_graph(n: int = 10) -> nx.DiGraph:
    """``A → B → C`` (C a landed submission), ``n`` identical bouts."""
    bout = [_ev(A, "guard"), _ev(B, "transition"), _ev(C, "submission", ok=True)]
    return network_from_sequences([list(bout) for _ in range(n)])


def _leak_graph(n: int = 10) -> nx.DiGraph:
    """Half the bouts run ``A → B → C``, half run ``A → X`` out of the system."""
    inside = [_ev(A, "guard"), _ev(B, "transition"), _ev(C, "submission", ok=True)]
    outside = [_ev(A, "guard"), _ev(X, "escape")]
    return network_from_sequences(
        [list(inside) for _ in range(n)] + [list(outside) for _ in range(n)]
    )


def _cycle_graph(n: int = 10) -> nx.DiGraph:
    """``A ⇄ B`` forever, plus an isolated (unreachable) submission ``C``."""
    loop = [_ev(A, "guard"), _ev(B, "transition"), _ev(A, "guard"), _ev(B, "transition")]
    lone = [_ev(C, "submission", ok=True)]
    return network_from_sequences(
        [list(loop) for _ in range(n)] + [list(lone) for _ in range(n)]
    )


def _risk_graph(n: int = 10) -> nx.DiGraph:
    """Half the bouts finish through ``A → B → C``; in the other half the OPPONENT finishes
    directly out of ``A`` — a leak the within-actor kernel cannot see on its own."""
    inside = [_ev(A, "guard"), _ev(B, "transition"), _ev(C, "submission", ok=True)]
    died = [_ev(A, "guard"), _ev("Opp Sub", "submission", actor="b", ok=True)]
    return network_from_sequences(
        [list(inside) for _ in range(n)] + [list(died) for _ in range(n)]
    )


# ── the chain ────────────────────────────────────────────────────────────────

def test_pure_chain_absorbs_with_certainty() -> None:
    g = _chain_graph()
    rows = transition_rows(g, [A, B, C], C)
    assert rows == {A: {B: 1.0}, B: {C: 1.0}}
    assert absorption(rows, C) == {A: 1.0, B: 1.0}
    # B is one step from the finish, A is two — absorption counts steps, not edges.
    assert expected_steps(rows, C) == {A: 2.0, B: 1.0}


def test_leak_out_of_the_system_halves_absorption() -> None:
    g = _leak_graph()
    rows = transition_rows(g, [A, B, C], C)
    # A splits 10/10 between B (inside) and X (outside); the outside half is EXIT mass and
    # is NOT renormalised away — that is what the second absorbing state buys.
    assert rows[A] == {B: 0.5}
    assert absorption(rows, C) == {A: 0.5, B: 1.0}
    assert expected_steps(rows, C) == {A: 1.5, B: 1.0}


def test_opponent_risk_is_injected_into_the_leak() -> None:
    g = _risk_graph()
    # 20 appearances of A, 10 of them followed by the opponent's finish → p_risk = 0.5.
    assert g.nodes[A]["risk"] / g.nodes[A]["denom"] == 0.5
    rows = transition_rows(g, [A, B, C], C)
    assert rows[A] == {B: 0.5}  # survive(0.5) × the only out-edge (share 1.0)
    assert absorption(rows, C) == {A: 0.5, B: 1.0}


def test_closed_cycle_absorbs_nowhere_and_reports_no_expected_steps() -> None:
    g = _cycle_graph()
    rows = transition_rows(g, [A, B, C], C)
    assert rows == {A: {B: 1.0}, B: {A: 1.0}}
    assert absorption(rows, C) == {A: 0.0, B: 0.0}
    # (I − Q) is singular here; the iteration says "not established" instead of raising.
    assert expected_steps(rows, C) == {A: None, B: None}


# ── directionality (the App-shared contract, applied only in the composite) ───

def test_direction_factor_reads_the_edge_arrow_contract() -> None:
    g = nx.DiGraph()
    g.add_edge("f", "t", weight=1)
    g.add_edge("t", "f", weight=10)
    # edge_arrow says "arrow, pointing t→f": f→t is the minority direction = counterflow.
    assert direction_factor(g, "f", "t") == 0.5
    assert direction_factor(g, "t", "f") == 1.0

    sparse = nx.DiGraph()
    sparse.add_edge("a", "b", weight=1)  # max weight below MIN_EDGE_ARROW → undirected
    assert direction_factor(sparse, "a", "b") == 1.0

    two_way = nx.DiGraph()
    two_way.add_edge("a", "b", weight=20)
    two_way.add_edge("b", "a", weight=10)  # 0.5 minority share > TWO_WAY_RATIO → undirected
    assert direction_factor(two_way, "a", "b") == 1.0
    assert direction_factor(two_way, "b", "a") == 1.0


def _counterflow_graph() -> nx.DiGraph:
    """``A → C`` seen twice against ``C → A`` seen ten times: the route to the goal is the
    minority direction of an edge the data draws the other way."""
    to_goal = [_ev(A, "guard"), _ev(C, "submission", ok=True)]
    from_goal = [_ev(C, "submission"), _ev(A, "guard")]
    return network_from_sequences(
        [list(to_goal) for _ in range(2)] + [list(from_goal) for _ in range(10)]
    )


def test_counterflow_discounts_strength_but_never_the_probability() -> None:
    g = _counterflow_graph()
    assert direction_factor(g, A, C) == 0.5
    r = system_path_strength(g, [A, C], C, _PRIZE_ONE)
    neutral = system_path_strength(g, [A, C], C, _PRIZE_ONE, counterflow=1.0)
    assert r is not None and neutral is not None
    # Same graph, same absorption — direction is a valuation knob, not a measurement.
    assert [n.p_desired for n in r.nodes] == [n.p_desired for n in neutral.nodes] == [1.0]
    assert neutral.strength == 1.0
    assert r.strength == 0.5
    assert r.paths[0].direction == 0.5 and r.paths[0].p_chain == 1.0


# ── the composite ────────────────────────────────────────────────────────────

def test_system_strength_is_the_sum_of_its_node_strengths() -> None:
    r = system_path_strength(_chain_graph(), [A, B, C], C, _PRIZE_ONE)
    assert r is not None
    assert r.desired == C
    assert not r.gated
    # occ(A) = occ(B) = 10 → usage 0.5 each; both absorb with certainty; prize 1.
    assert {n.node: (n.usage, n.p_desired, n.strength) for n in r.nodes} == {
        A: (0.5, 1.0, 0.5), B: (0.5, 1.0, 0.5),
    }
    assert r.strength == round(sum(n.strength for n in r.nodes), 6) == 1.0


def test_leak_lowers_system_strength_and_usage_follows_volume() -> None:
    r = system_path_strength(_leak_graph(), [A, B, C], C, _PRIZE_ONE)
    assert r is not None
    by_node = {n.node: n for n in r.nodes}
    # occ(A)=20, occ(B)=10 → usage 2/3 and 1/3.
    assert round(by_node[A].usage, 4) == 0.6667
    assert by_node[A].p_desired == 0.5
    assert round(r.strength, 4) == round(0.5 * 2 / 3 + 1.0 * 1 / 3, 4)


def test_prize_scales_the_whole_system() -> None:
    g = _chain_graph()
    full = system_path_strength(g, [A, B, C], C, {C: 1.0})
    neutral = system_path_strength(g, [A, B, C], C, {C: 0.0})
    assert full is not None and neutral is not None
    assert neutral.prize == 0.5
    assert neutral.strength == full.strength * 0.5


# ── concrete routes ──────────────────────────────────────────────────────────

def test_top_paths_are_real_sequences_bounded_by_their_own_absorption() -> None:
    r = system_path_strength(_leak_graph(), [A, B, C], C, _PRIZE_ONE)
    assert r is not None
    assert [p.path for p in r.paths] == [[B, C], [A, B, C]]
    assert dict(zip([p.label for p in r.paths], [p.p_chain for p in r.paths], strict=True)) == {
        f"{B} → {C}": 1.0, f"{A} → {B} → {C}": 0.5,
    }
    # A simple route can never be worth more than every route from the same start.
    p_desired = {n.node: n.p_desired for n in r.nodes}
    assert all(p.p_chain <= p_desired[p.path[0]] + 1e-9 for p in r.paths)


# ── target selection, gates, determinism ─────────────────────────────────────

def test_desired_node_prefers_the_finish_then_falls_back_to_the_hub() -> None:
    assert desired_node(_chain_graph(), [A, B, C]) == C
    # No submission in the member set → the weighted-degree hub. In the cycle graph A→B
    # carries 20 and B→A carries 10, so A holds the larger total degree.
    assert desired_node(_cycle_graph(), [A, B]) == A
    assert desired_node(_chain_graph(), ["nobody"]) is None


def test_gates_refuse_interpretation_without_hiding_the_numbers() -> None:
    thin = system_path_strength(_chain_graph(n=MIN_DESIRED_OCC - 1), [A, B, C], C, _PRIZE_ONE)
    assert thin is not None
    assert thin.gated and thin.gate_reason == "desired_below_occ_floor"
    assert thin.strength > 0  # measured anyway — a gate is a refusal to narrate

    lone = system_path_strength(_chain_graph(), [C], C, _PRIZE_ONE)
    assert lone is not None
    assert lone.gated and lone.gate_reason == "system_too_small"


def test_output_is_deterministic_and_member_order_independent() -> None:
    g = _leak_graph()
    runs = [
        system_path_strength(g, members, C, _PRIZE_ONE)
        for members in ([A, B, C], [C, B, A], [A, B, C])
    ]
    assert all(r is not None for r in runs)
    dicts = [to_dict(r) for r in runs if r is not None]
    assert dicts[0] == dicts[1] == dicts[2]


def test_rank_systems_orders_by_strength_and_skips_absent_members() -> None:
    g = _leak_graph()
    ranked = rank_systems(g, [[A, B, C], [X], ["ghost"]], v=_PRIZE_ONE)
    assert [r.desired for r in ranked] == [C, X]  # "ghost" is in no graph → dropped
    assert ranked[0].strength >= ranked[1].strength
    assert rank_systems(g, [], v=_PRIZE_ONE) == []
