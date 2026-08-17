"""Per-athlete normalization for transition graphs — the athlete-balanced aggregate.

Two consumers of ``build_graph.network_from_sequences``:
  - **athlete**: one athlete's own sequences, weight = raw occurrence — just call
    ``network_from_sequences`` directly, no normalization needed.
  - **category**: sequences from many athletes. Pooling them raw lets whoever fought
    the most matches define the division's constellations (in +65 kg of the ADCC 2026
    roster, one athlete is ~86% of the events). ``athlete_balanced_category_graph``
    rescales each athlete's edge weights to sum to 1 *before* summing across athletes,
    so every athlete contributes an equal share regardless of volume.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from analysis.transitions.build_graph import network_from_sequences


def normalize_athlete_graph(g: nx.DiGraph) -> nx.DiGraph:
    """Rescale edge weights to sum to 1 — this athlete's own game as a share of itself.

    Node attrs (``occ``, ``type``, ...) pass through unchanged; only edge ``weight`` is
    rescaled (``ok`` and ``dist`` are dropped — meaningless once weight is a share).
    """
    out = nx.DiGraph()
    out.add_nodes_from(g.nodes(data=True))
    total = sum(d.get("weight", 0.0) for _, _, d in g.edges(data=True))
    for u, v, d in g.edges(data=True):
        w = d.get("weight", 0.0) / total if total else 0.0
        out.add_edge(u, v, weight=w)
    return out


def category_graph_raw(athlete_sequences: dict[str, list[list[dict[str, Any]]]]) -> nx.DiGraph:
    """Crude aggregation: pool every athlete's sequences and build one graph.

    Volume-dominant. Exists so callers can measure the divergence against
    ``athlete_balanced_category_graph`` on the same input — never use this for a
    published category constellation.
    """
    pooled = [seq for seqs in athlete_sequences.values() for seq in seqs]
    return network_from_sequences(pooled)


def athlete_balanced_category_graph(
    athlete_sequences: dict[str, list[list[dict[str, Any]]]],
) -> nx.DiGraph:
    """Aggregate per-athlete transition graphs so each athlete contributes an equal
    total edge-weight share, regardless of how many matches they have.

    Iterates athletes in sorted key order — determinism, not a data claim.
    """
    combined = nx.DiGraph()
    node_meta: dict[str, dict[str, Any]] = {}
    for athlete_id in sorted(athlete_sequences):
        norm = normalize_athlete_graph(network_from_sequences(athlete_sequences[athlete_id]))
        for n, d in norm.nodes(data=True):
            node_meta.setdefault(n, {"type": d.get("type", "")})
        for u, v, d in norm.edges(data=True):
            if combined.has_edge(u, v):
                combined[u][v]["weight"] += d["weight"]
            else:
                combined.add_edge(u, v, weight=d["weight"])
    for n, meta in node_meta.items():
        combined.add_node(n, **meta)
    return combined
