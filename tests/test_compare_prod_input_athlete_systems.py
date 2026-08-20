"""Locks the prod-input comparison harness itself
(``scripts/compare_prod_input_athlete_systems.py``) — not the DB, not a specific
athlete's numbers. Pure ``AthleteGraph`` fixtures only, same style as
``tests/test_compare_athlete_system_detectors.py``."""

from __future__ import annotations

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from scripts.compare_prod_input_athlete_systems import (
    _graph_from_bouts,
    _rebuild,
    bootstrap_bout_stability,
    bootstrap_prod_stability,
    cross_input_jaccard,
    measure_prod_athlete,
)


def _by_bout() -> dict[str, list[tuple[str, str]]]:
    """Bout provenance whose union is exactly ``_prod_graph()``'s edge set — the real
    invariant of ``graph_edge_bouts``: every persisted edge came from some bout."""
    return {
        "m1": [("closed guard", "armbar"), ("closed guard", "triangle")],
        "m2": [("armbar", "triangle")],
        "m3": [("guard pass", "mount")],
        "m4": [("mount", "side control")],
        "m5": [("closed guard", "armbar")],
    }


def _prod_graph() -> AthleteGraph:
    """Two connected clusters + one isolated node — uniform edge weight 1, exactly the
    shape ``export.ontology._athlete_systems_by_graph`` produces in production."""
    ag = AthleteGraph(athlete="graph-1")
    labels = {
        "closed guard": "guard", "armbar": "submission", "triangle": "submission",
        "guard pass": "pass", "mount": "control", "side control": "control",
        "lonely node": "control",
    }
    for key, typ in labels.items():
        ag.nodes[key] = AthleteNode(label=key, type=typ, count=1)
    for s, t in [
        ("closed guard", "armbar"), ("closed guard", "triangle"), ("armbar", "triangle"),
        ("guard pass", "mount"), ("mount", "side control"),
    ]:
        ag.edges[(s, t)] = AthleteEdge(source=s, target=t, count=1)
    return ag


def test_measure_prod_athlete_no_edges_notes_it() -> None:
    row = measure_prod_athlete(AthleteGraph(athlete="empty"), "Nobody")
    assert row.n_edges == 0
    assert "sem arestas" in row.note


def test_measure_prod_athlete_old_coverage_drops_isolated_node() -> None:
    """min_system_size=2 (prod default): the isolated node never forms a system —
    mirrors the exact coverage gap wave 6 measured, on this input too."""
    row = measure_prod_athlete(_prod_graph(), "x")
    assert row.old_coverage < 1.0
    assert row.new_singleton_share >= 0.0  # new detector never drops a node either way


def test_measure_prod_athlete_below_min_edges_skips_bootstrap() -> None:
    ag = AthleteGraph(athlete="tiny")
    ag.nodes["a"] = AthleteNode(label="a", type="", count=1)
    ag.nodes["b"] = AthleteNode(label="b", type="", count=1)
    ag.edges[("a", "b")] = AthleteEdge(source="a", target="b", count=1)
    row = measure_prod_athlete(ag, "x")
    assert row.new_stability is None
    assert row.old_stability is None
    assert "bootstrap por aresta não roda" in row.note


def test_bootstrap_prod_stability_empty_is_zero() -> None:
    assert bootstrap_prod_stability(AthleteGraph(athlete="empty")) == (0.0, 0.0)


def test_bootstrap_prod_stability_returns_bounded_scores() -> None:
    new_j, old_j = bootstrap_prod_stability(_prod_graph(), n_resamples=10, seed=1)
    assert 0.0 <= new_j <= 1.0
    assert 0.0 <= old_j <= 1.0


def test_rebuild_repeats_edge_key_as_weight() -> None:
    ag = _prod_graph()
    sample = [("closed guard", "armbar"), ("closed guard", "armbar"), ("mount", "side control")]
    out = _rebuild(ag, sample)
    assert out.edges[("closed guard", "armbar")].count == 2
    assert out.edges[("mount", "side control")].count == 1
    assert set(out.nodes) == {"closed guard", "armbar", "mount", "side control"}


def test_cross_input_jaccard_identical_graphs_is_one() -> None:
    import networkx as nx

    ag = _prod_graph()
    seq_g = nx.DiGraph()
    for k, n in ag.nodes.items():
        seq_g.add_node(k, type=n.type, occ=1)
    for (s, t), e in ag.edges.items():
        seq_g.add_edge(s, t, weight=e.count)
    assert cross_input_jaccard(seq_g, ag, "x") == 1.0


def test_cross_input_jaccard_normalizes_display_label_casing() -> None:
    """Regression: ``network_from_sequences`` nodes are the display label ("Closed
    Guard"); prod ``node_key``s are normalized+lowercased ("closed guard"). Without
    normalizing the sequence side first, this pair reads as a totally disjoint node set
    (Jaccard 0) even though it is the same structure — that was the bug found while
    building this script against prod data."""
    import networkx as nx

    ag = _prod_graph()
    seq_g = nx.DiGraph()
    for k, n in ag.nodes.items():
        seq_g.add_node(k.title(), type=n.type, occ=1)
    for (s, t), e in ag.edges.items():
        seq_g.add_edge(s.title(), t.title(), weight=e.count)
    assert cross_input_jaccard(seq_g, ag, "x") == 1.0


def test_cross_input_jaccard_no_edges_is_none() -> None:
    import networkx as nx

    assert cross_input_jaccard(nx.DiGraph(), AthleteGraph(athlete="empty"), "x") is None


def test_full_bout_draw_reproduces_the_production_graph() -> None:
    """The load-bearing property of the unweighted bout unit: drawing every bout once
    yields the production input itself (uniform weight 1), so the bootstrap baseline is
    the graph under test and not a third construction."""
    ag = _prod_graph()
    by_bout = _by_bout()
    rebuilt = _graph_from_bouts(ag, by_bout, sorted(by_bout), weighted=False)
    assert set(rebuilt.edges) == set(ag.edges)
    assert {e.count for e in rebuilt.edges.values()} == {1}


def test_weighted_bout_draw_counts_bouts_not_edges() -> None:
    ag = _prod_graph()
    by_bout = _by_bout()
    rebuilt = _graph_from_bouts(ag, by_bout, ["m1", "m5", "m5"], weighted=True)
    # ("closed guard","armbar") is in m1 and in both draws of m5 -> 3
    assert rebuilt.edges[("closed guard", "armbar")].count == 3
    assert rebuilt.edges[("closed guard", "triangle")].count == 1


def test_dropping_a_bout_drops_the_edges_only_it_carried() -> None:
    """Why the bout unit is stronger than the edge unit: an unsampled bout takes its
    edges with it (structural variation), where an edge resample only reweights."""
    ag = _prod_graph()
    rebuilt = _graph_from_bouts(ag, _by_bout(), ["m1", "m2", "m4"], weighted=False)
    assert ("guard pass", "mount") not in rebuilt.edges


def test_bootstrap_bout_stability_no_provenance_is_none() -> None:
    assert bootstrap_bout_stability(_prod_graph(), {}) is None


def test_bootstrap_bout_stability_returns_bounded_scores() -> None:
    result = bootstrap_bout_stability(_prod_graph(), _by_bout(), n_resamples=10, seed=1)
    assert result is not None
    new_j, old_j = result
    assert 0.0 <= new_j <= 1.0
    assert 0.0 <= old_j <= 1.0


def test_measure_prod_athlete_below_bout_floor_notes_it() -> None:
    row = measure_prod_athlete(_prod_graph(), "x", {"m1": [("closed guard", "armbar")]})
    assert row.n_bouts == 1
    assert row.new_stability_bout is None
    assert "bootstrap por luta não roda" in row.note
