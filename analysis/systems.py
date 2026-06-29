"""System / Dilemma proposal — AI-assisted authoring scaffold (RF04/RF05).

Proposes candidate Systems (and their entry positions + dilemma forks) from the match
corpus so the admin can curate them into the ``systems``/``dilemmas`` tables rather than
authoring from a blank page. Pure heuristics over the existing transition network — no
write; the human reviews + edits in the ``web/`` admin (per the agreed authoring flow).

Reuses ``analysis.network_metrics``:
- ``detect_communities``  → game families → candidate Systems (DS-10 progression seed).
- ``pagerank_ranking``    → a community's central nodes → entry positions.
- ``reward_risk_ranking`` → high-variance nodes → candidate Dilemma forks.

``propose_systems(session)`` is DB-backed; ``propose_from_network(g)`` is pure so it can be
unit-tested on a hand-built graph without a DB.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from analysis.names import _normalize_name
from analysis.network_metrics import (
    build_transition_network,
    detect_communities,
    pagerank_ranking,
    reward_risk_ranking,
)


def propose_from_network(
    g: nx.DiGraph, min_occ: int = 2, max_systems: int = 12
) -> list[dict[str, Any]]:
    """Candidate systems from a prebuilt transition network. Pure (no DB)."""
    communities = detect_communities(g, min_occ=min_occ)
    pagerank = dict(pagerank_ranking(g, limit=10_000))
    reward_risk = {
        label: rr
        for label, rr, _ in reward_risk_ranking(g, min_occ=min_occ, limit=10_000)
    }

    proposals: list[dict[str, Any]] = []
    for members in communities[:max_systems]:
        if len(members) < 2:
            continue  # a lone node is not a system
        # Entry positions = the community's most central nodes (by PageRank).
        ranked = sorted(members, key=lambda n: pagerank.get(n, 0.0), reverse=True)
        entry_positions = [_normalize_name(str(n)) for n in ranked[:3]]
        # Candidate dilemmas = the highest reward-risk nodes in the community (decision forks).
        dilemma_nodes = sorted(
            (n for n in members if n in reward_risk),
            key=lambda n: reward_risk[n],
            reverse=True,
        )[:3]
        proposals.append(
            {
                "name": f"{str(ranked[0]).title()} System",
                "key": _normalize_name(str(ranked[0])) + "-system",
                "entry_positions": entry_positions,
                "member_positions": [_normalize_name(str(n)) for n in ranked],
                "candidate_dilemmas": [
                    {"key": _normalize_name(str(n)) + "-fork", "around": _normalize_name(str(n))}
                    for n in dilemma_nodes
                ],
                "ds_progression": [],  # admin fills the DS arc per milestone (DS-10)
            }
        )
    return proposals


def propose_systems(session: Any, min_occ: int = 2, max_systems: int = 12) -> list[dict[str, Any]]:
    """DB-backed: build the corpus transition network, then propose candidate systems."""
    g = build_transition_network(session)
    return propose_from_network(g, min_occ=min_occ, max_systems=max_systems)
