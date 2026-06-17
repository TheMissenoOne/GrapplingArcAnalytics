"""Build per-athlete edge-centric graphs from exported SessionPayload dicts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from analysis.names import _normalize_name

logger = logging.getLogger(__name__)


@dataclass
class AthleteNode:
    label: str
    type: str
    count: int


@dataclass
class AthleteEdge:
    source: str
    target: str
    count: int


@dataclass
class AthleteGraph:
    athlete: str
    nodes: dict[str, AthleteNode] = field(default_factory=dict)
    edges: dict[tuple[str, str], AthleteEdge] = field(default_factory=dict)


def build_athlete_graph(athlete: str, sessions: list[dict[str, Any]]) -> AthleteGraph:
    """Build an edge-centric graph from one athlete's training sessions.

    Considers only entries with ``actor == "you"`` (the athlete's own moves).
    Nodes are distinct technique labels (normalised for dedup).  Edges are
    consecutive same-round pairs of your-moves; self-loops (same label twice
    in a row) are skipped.

    Parameters
    ----------
    athlete : str
        Athlete identifier (name or UUID).
    sessions : list[dict]
        SessionPayload dicts shaped like
        ``{"topics": [...], "rounds": [{"entries": [{"label", "type", "actor"}]}]}``.

    Returns
    -------
    AthleteGraph
    """
    graph = AthleteGraph(athlete=athlete)

    for session in sessions:
        rounds = session.get("rounds") or []
        for rnd in rounds:
            entries = rnd.get("entries") or []
            your_entries: list[dict[str, Any]] = []
            for entry in entries:
                actor = entry.get("actor", "")
                label_raw = entry.get("label", "")
                if actor != "you" or not label_raw:
                    continue
                your_entries.append(entry)

            for entry in your_entries:
                label = entry.get("label", "")
                typ = entry.get("type", "")
                norm = _normalize_name(label)

                if norm in graph.nodes:
                    graph.nodes[norm].count += 1
                else:
                    graph.nodes[norm] = AthleteNode(label=label, type=typ, count=1)

            for i in range(1, len(your_entries)):
                prev_label = _normalize_name(your_entries[i - 1].get("label", ""))
                curr_label = _normalize_name(your_entries[i].get("label", ""))
                if prev_label == curr_label:
                    continue
                key = (prev_label, curr_label)
                if key in graph.edges:
                    graph.edges[key].count += 1
                else:
                    graph.edges[key] = AthleteEdge(
                        source=prev_label, target=curr_label, count=1,
                    )

    return graph


def out_distribution(graph: AthleteGraph, label: str) -> dict[str, float]:
    """Normalised out-edge probability distribution from *label*.

    Parameters
    ----------
    graph : AthleteGraph
    label : str
        Source label (normalised form).

    Returns
    -------
    dict[str, float]
        ``{target_label: probability}`` — sums to 1.0, or ``{}`` if *label*
        has no outgoing edges / is unknown.
    """
    out_edges = {
        key: edge
        for key, edge in graph.edges.items()
        if key[0] == label
    }
    if not out_edges:
        return {}
    total = sum(edge.count for edge in out_edges.values())
    return {key[1]: edge.count / total for key, edge in out_edges.items()}
