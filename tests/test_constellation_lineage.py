"""Lineage between two constellation snapshots — pure, in-memory (doc 04)."""

from __future__ import annotations

import networkx as nx

from analysis.constellations.detect import detect
from analysis.constellations.lineage import match_lineage


def _clique(members: list[str], weight: float = 5) -> nx.DiGraph:
    g = nx.DiGraph()
    for i in range(len(members)):
        a, b = members[i], members[(i + 1) % len(members)]
        g.add_edge(a, b, weight=weight)
    return g


def test_lineage_matches_identical_snapshot_perfectly() -> None:
    g = _clique(["A1", "A2", "A3"])
    old = detect(g).constellations
    new = detect(g).constellations
    result = match_lineage(old, new)
    assert len(result.matches) == 1
    assert result.matches[0].jaccard == 1.0
    assert result.matches[0].new_fingerprint == result.matches[0].old_fingerprint
    assert not result.births
    assert not result.deaths
    assert not result.merges
    assert not result.splits


def test_lineage_birth_and_death() -> None:
    old = detect(_clique(["A1", "A2", "A3"])).constellations
    new = detect(_clique(["Z1", "Z2", "Z3"])).constellations  # unrelated members
    result = match_lineage(old, new)
    assert result.deaths == [old[0].fingerprint]
    assert result.births == [new[0].fingerprint]
    assert not result.merges
    assert not result.splits


def _k4(weight: float = 5) -> nx.DiGraph:
    g = nx.DiGraph()
    members = ["A1", "A2", "B1", "B2"]
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            g.add_edge(a, b, weight=weight)
    return g


def test_lineage_detects_merge() -> None:
    # Two small old constellations that both become subsets of one bigger new one.
    old_g = nx.DiGraph()
    old_g.add_edge("A1", "A2", weight=5)
    old_g.add_edge("B1", "B2", weight=5)
    old = detect(old_g).constellations  # two 2-member communities: {A1,A2}, {B1,B2}

    new = detect(_k4()).constellations  # fully-connected 4-clique -> one community
    assert len(new) == 1

    result = match_lineage(old, new, min_jaccard=0.1)
    assert result.merges == {new[0].fingerprint: sorted(o.fingerprint for o in old)}
    assert not result.splits


def test_lineage_detects_split() -> None:
    # Mirror of the merge case: one old 4-member community becomes two new 2-member ones.
    old = detect(_k4()).constellations  # fully-connected 4-clique -> one community
    assert len(old) == 1

    new_g = nx.DiGraph()
    new_g.add_edge("A1", "A2", weight=5)
    new_g.add_edge("B1", "B2", weight=5)
    new = detect(new_g).constellations  # two 2-member communities

    result = match_lineage(old, new, min_jaccard=0.1)
    assert result.splits == {old[0].fingerprint: sorted(n.fingerprint for n in new)}
    assert not result.merges


def test_lineage_empty_old_is_all_births() -> None:
    new = detect(_clique(["A1", "A2", "A3"])).constellations
    result = match_lineage([], new)
    assert result.births == [new[0].fingerprint]
    assert not result.matches
    assert not result.deaths
