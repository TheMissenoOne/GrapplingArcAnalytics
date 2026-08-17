"""Locks the wave-6 comparison harness itself (``scripts/compare_athlete_system_detectors.py``)
— not the DB, not a specific athlete's numbers. Pure graph fixtures only."""

from __future__ import annotations

import networkx as nx

from scripts.compare_athlete_system_detectors import (
    bootstrap_stability,
    measure_athlete,
    new_partition,
    old_partition,
)


def _graph() -> nx.DiGraph:
    """Two connected clusters (guard family, pass family) + one isolated node."""
    g = nx.DiGraph()
    g.add_node("lonely node", type="control", occ=3)
    edges = [
        ("closed guard", "armbar", 5), ("closed guard", "triangle", 4),
        ("armbar", "triangle", 2),
        ("guard pass", "mount", 6), ("mount", "side control", 3),
    ]
    for u, v, w in edges:
        g.add_edge(u, v, weight=w, type="")
    for n in g.nodes:
        g.nodes[n].setdefault("occ", sum(d["weight"] for _, _, d in g.edges(n, data=True)) or 1)
        g.nodes[n].setdefault("type", "")
    return g


def _sequences() -> list[list[dict]]:
    """Per-bout own-events sequences that build (roughly) the same shape as ``_graph()``."""
    bouts = [
        [{"label": "closed guard", "type": "guard", "actor_id": "x", "successful": False},
         {"label": "armbar", "type": "submission", "actor_id": "x", "successful": True}],
        [{"label": "closed guard", "type": "guard", "actor_id": "x", "successful": False},
         {"label": "triangle", "type": "submission", "actor_id": "x", "successful": True}],
        [{"label": "guard pass", "type": "pass", "actor_id": "x", "successful": True},
         {"label": "mount", "type": "control", "actor_id": "x", "successful": False}],
        [{"label": "mount", "type": "control", "actor_id": "x", "successful": False},
         {"label": "side control", "type": "control", "actor_id": "x", "successful": False}],
        [{"label": "lonely node", "type": "control", "actor_id": "x", "successful": False}],
    ]
    return bouts * 2  # enough units for bootstrap to be non-degenerate


def test_old_partition_min_size_1_covers_every_node() -> None:
    """The adapter's job: no node vanishes at min_system_size=1, matching what the new
    detector already guarantees — required for a valid Jaccard comparison."""
    g = _graph()
    partition = old_partition(g, "x", min_system_size=1)
    covered = {n for members in partition for n in members}
    assert covered == set(g.nodes)


def test_old_partition_min_size_2_can_drop_isolated_node() -> None:
    """Prod's own default (min_system_size=2): a node with no edges never forms a system —
    this IS the coverage gap wave 6 measures, not an adapter bug."""
    g = _graph()
    partition = old_partition(g, "x", min_system_size=2)
    covered = {n for members in partition for n in members}
    assert "lonely node" not in covered


def test_new_partition_never_drops_a_node() -> None:
    g = _graph()
    members, _ = new_partition(g)
    covered = {n for c in members for n in c}
    assert covered == set(g.nodes)


def test_bootstrap_stability_empty_input_is_zero() -> None:
    assert bootstrap_stability([], "x") == (0.0, 0.0)


def test_bootstrap_stability_returns_bounded_scores() -> None:
    new_j, old_j = bootstrap_stability(_sequences(), "x", n_resamples=10, seed=1)
    assert 0.0 <= new_j <= 1.0
    assert 0.0 <= old_j <= 1.0


def test_measure_athlete_no_events_notes_it() -> None:
    row = measure_athlete("Nobody", bouts=[])
    assert row.n_nodes == 0
    assert "sem eventos" in row.note


def test_measure_athlete_below_min_bouts_skips_bootstrap() -> None:
    bouts = [
        {"events": [{"label": "closed guard", "type": "guard", "actor": "X", "successful": False}]},
    ]
    # sequences_by_athlete filters events by `e.get("actor") == athlete`
    row = measure_athlete("X", bouts=bouts)
    assert row.n_bouts == 1
    assert row.new_stability is None
    assert row.old_stability is None
    assert "bootstrap não roda" in row.note
