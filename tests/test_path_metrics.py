"""Fase 3 (``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`` §9) — ``analysis.path_metrics``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from analysis.chain_compiler import ChainAction, ChainEdge, compile_two_sided
from analysis.path_metrics import PathMetrics, metrics_for_paths, path_metrics


def _action(
    key: str, label: str, *, type_: str = "transition", actor: str | None = "a",
    inferred: bool = False, actor_is_opponent: bool = False,
) -> ChainAction:
    return ChainAction(key=key, label=label, type=type_, actor=actor, inferred=inferred,
                       source_event_index=None, actor_is_opponent=actor_is_opponent)


def _edge(source_key: str, target_key: str, actions: tuple[ChainAction, ...], *,
          terminal: bool = False) -> ChainEdge:
    return ChainEdge(source_key=source_key, target_key=target_key, actions=actions,
                     terminal=terminal)


def _no_rating(_key: str) -> float | None:
    return None


# ── length / observed / observed_ratio ───────────────────────────────────────────────────


def test_length_observed_and_ratio() -> None:
    actions = (
        _action("a1", "A1"),
        _action("a2", "A2", inferred=True),
        _action("a3", "A3"),
    )
    edge = _edge("mount", "side control", actions)
    m = path_metrics(edge, support=7, rating_of=_no_rating, block=None)
    assert m.length == 3
    assert m.observed == 2
    assert m.observed_ratio == pytest.approx(2 / 3)
    assert m.support == 7
    assert m.terminal is False


def test_observed_ratio_is_zero_on_an_empty_path() -> None:
    edge = _edge("mount", "side control", ())
    m = path_metrics(edge, support=1, rating_of=_no_rating, block=None)
    assert m.length == 0
    assert m.observed_ratio == 0.0


def test_terminal_passes_through_from_the_edge() -> None:
    edge = _edge("armlock", "finish", (_action("armlock", "Armlock"),), terminal=True)
    m = path_metrics(edge, support=1, rating_of=_no_rating, block=None)
    assert m.terminal is True


# ── strength ──────────────────────────────────────────────────────────────────────────────


def test_strength_is_the_markov_weighted_mean_of_rated_observed_actions() -> None:
    actions = (
        _action("k1", "L1"),
        _action("k2", "L2", inferred=True),   # inferred: rated, but must NOT count
        _action("k3", "L3"),
    )
    edge = _edge("s", "t", actions)
    ratings = {"k1": 1500.0, "k2": 9999.0, "k3": 1600.0}
    m = path_metrics(edge, support=1, rating_of=ratings.get, block=None)
    # block=None -> relative_shares is uniform -> equal weights renormalise to a plain mean of
    # the two OBSERVED, RATED actions; k2's rating never enters despite being present.
    assert m.strength == pytest.approx(1550.0)


def test_strength_is_none_when_nothing_is_rated() -> None:
    actions = (_action("k1", "L1"), _action("k2", "L2"))
    edge = _edge("s", "t", actions)
    m = path_metrics(edge, support=1, rating_of=_no_rating, block=None)
    assert m.strength is None


def test_strength_excludes_inferred_actions_even_when_all_actions_are_inferred() -> None:
    actions = (_action("k1", "L1", inferred=True),)
    edge = _edge("s", "t", actions)
    m = path_metrics(edge, support=1, rating_of=lambda _k: 1234.0, block=None)
    assert m.strength is None


# ── role_delta ────────────────────────────────────────────────────────────────────────────


def test_role_delta_none_when_both_ends_share_the_same_stance() -> None:
    edge = _edge("mount", "side control", (_action("control transition", "Control Transition"),))
    m = path_metrics(edge, support=1, rating_of=_no_rating, block=None)
    assert m.role_delta == "none"


def test_role_delta_same_actor_shift_on_a_topology_flip_with_no_opponent_action() -> None:
    edge = _edge("closed guard", "mount", (_action("sweep", "Sweep", type_="sweep"),))
    m = path_metrics(edge, support=1, rating_of=_no_rating, block=None)
    assert m.role_delta == "same-actor-shift"


def test_role_delta_inversion_on_a_topology_flip_attributed_to_the_opponent() -> None:
    edge = _edge("closed guard", "mount",
                 (_action("reversal", "Reversal", type_="transition", actor_is_opponent=True),))
    m = path_metrics(edge, support=1, rating_of=_no_rating, block=None)
    assert m.role_delta == "inversion"


def test_role_delta_unknown_when_an_endpoint_has_no_resolvable_stance() -> None:
    # "kimura grip" resolves through neither the declared table nor the technique library
    # (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md §8.2's _CONTROL_GRIP gap) — no type survives
    # onto a bare ChainEdge, so it stays unresolved here (module docstring, "ponytail").
    edge = _edge("kimura grip", "mount", (_action("control transition", "Control Transition"),))
    m = path_metrics(edge, support=1, rating_of=_no_rating, block=None)
    assert m.role_delta == "unknown"


# ── metrics_for_paths ────────────────────────────────────────────────────────────────────


def test_metrics_for_paths_pairs_each_edge_with_its_own_metrics() -> None:
    edges = [
        _edge("mount", "side control", (_action("control transition", "Control Transition"),)),
        _edge("closed guard", "mount", (_action("sweep", "Sweep", type_="sweep"),)),
    ]
    support = {id(edges[0]): 4, id(edges[1]): 9}
    pairs = metrics_for_paths(
        edges, support_of=lambda e: support[id(e)], rating_of=_no_rating, block=None,
    )
    assert [e for e, _ in pairs] == edges
    assert [m.support for _, m in pairs] == [4, 9]
    assert isinstance(pairs[0][1], PathMetrics)


# ── invariance over the owner's real corpus (Fase 2 §8.5) ───────────────────────────────


DEFAULT_EXPORT = Path("/home/vetor/GrapplingArc/_analytics_export.json")


def test_observed_total_matches_the_fase2_invariant_on_the_real_corpus() -> None:
    """Same invariant ``tests/test_actions_parity.py`` locks at the compiler level (1548
    observed action occurrences over the owner's 281-bout dump, 1390 before N0's authority swap
    — docs/taxonomy/04_ONTOLOGIA_CANONICA.md; N2's `guard recovery` fix moves it again, 1548 ->
    1387 after D7, was 1551; `tests/test_actions_parity.py` carries the exact accounting), reproduced through
    ``PathMetrics.observed`` — proves this module doesn't lose or invent an observation while
    reshaping ``ChainEdge`` into path statistics. Skips when the private corpus dump isn't
    present (never committed — LGPD, same convention as ``test_actions_parity.py``)."""
    from scripts.shadow_chain_compiler import _side_of

    if not DEFAULT_EXPORT.is_file():
        pytest.skip("corpus privado do dono não está presente (_analytics_export.json)")

    matches = json.loads(DEFAULT_EXPORT.read_text(encoding="utf-8"))
    total_observed = 0
    for match in matches:
        seq = match.get("sequence") or []
        if not seq:
            continue
        result = compile_two_sided(seq, _side_of(match))
        for side in ("a", "b"):
            pairs = metrics_for_paths(
                result[side].edges, support_of=lambda _e: 1, rating_of=_no_rating, block=None,
            )
            total_observed += sum(m.observed for _, m in pairs)

    assert total_observed == 1387
