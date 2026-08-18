"""Sparse-node taxonomy — every node gets exactly one named state (doc 04)."""

from __future__ import annotations

import networkx as nx

from analysis.constellations.detect import detect
from analysis.constellations.taxonomy import NodeState, TaxonomyThresholds, classify_nodes


def test_taxonomy_covers_every_node_with_no_overlap() -> None:
    g = nx.DiGraph()
    for a, b in [("A1", "A2"), ("A2", "A3"), ("A3", "A1")]:  # 3-clique -> detected
        g.add_edge(a, b, weight=5)
    g.add_node("Lonely")  # isolated -> its own singleton community
    result = detect(g)
    all_nodes = {"A1", "A2", "A3", "Lonely", "NeverSeen"}  # NeverSeen not even in g

    states = classify_nodes(result, all_nodes=all_nodes)

    assert set(states.keys()) == all_nodes
    assert states["A1"] == states["A2"] == states["A3"] == NodeState.DETECTED
    assert states["Lonely"] == NodeState.SINGLETON
    assert states["NeverSeen"] == NodeState.UNASSIGNED


def test_taxonomy_low_support_below_threshold() -> None:
    g = nx.DiGraph()
    g.add_edge("X1", "X2", weight=1)  # 2-member community, weight 1
    result = detect(g)
    thresholds = TaxonomyThresholds(min_community_size=2, min_internal_support=5.0)
    states = classify_nodes(result, thresholds=thresholds)
    assert states["X1"] == states["X2"] == NodeState.LOW_SUPPORT


def test_taxonomy_without_all_nodes_never_produces_unassigned() -> None:
    g = nx.DiGraph()
    g.add_node("Solo")
    result = detect(g)
    states = classify_nodes(result)
    assert states == {"Solo": NodeState.SINGLETON}
    assert NodeState.UNASSIGNED not in states.values()
