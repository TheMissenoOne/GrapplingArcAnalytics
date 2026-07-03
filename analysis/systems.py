"""System / Dilemma proposal — AI-assisted authoring scaffold (RF04/RF05).

Proposes candidate Systems (and their entry positions + dilemma forks) from the match
corpus so the admin can curate them into the ``systems``/``dilemmas`` tables rather than
authoring from a blank page. Pure heuristics over the existing transition network — no
write; the human reviews + edits in the ``web/`` admin (per the agreed authoring flow).

Reuses ``analysis.network_metrics`` + ``analysis.path_to_victory``:
- ``detect_communities``       → game families → candidate Systems (DS-10 progression seed).
- ``pagerank_ranking``         → a community's central nodes → entry positions.
- ``path_to_victory.dilemmas`` → nodes with ≥2 high-PtV out-edges → candidate Dilemma
  forks (real either/or branches, replacing the old "high reward-risk node" proxy).

``propose_systems(session)`` is DB-backed; ``propose_from_network(g)`` is pure so it can be
unit-tested on a hand-built graph without a DB.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
from sqlalchemy.orm import Session

from analysis.names import _normalize_name
from analysis.network_metrics import (
    build_transition_network,
    detect_communities,
    pagerank_ranking,
)
from analysis.path_to_victory import dilemmas as ptv_dilemmas
from analysis.path_to_victory import path_to_victory
from db.ontology_repository import upsert_dilemma, upsert_system


def propose_from_network(
    g: nx.DiGraph, min_occ: int = 2, max_systems: int = 12
) -> list[dict[str, Any]]:
    """Candidate systems from a prebuilt transition network. Pure (no DB)."""
    communities = detect_communities(g, min_occ=min_occ)
    pagerank = dict(pagerank_ranking(g, limit=10_000))
    # Dilemma = a real either/or fork: ≥2 out-edges with high path-to-victory.
    forks = {d["node"]: d for d in ptv_dilemmas(g, path_to_victory(g))}

    proposals: list[dict[str, Any]] = []
    for members in communities[:max_systems]:
        if len(members) < 2:
            continue  # a lone node is not a system
        # Entry positions = the community's most central nodes (by PageRank).
        ranked = sorted(members, key=lambda n: pagerank.get(n, 0.0), reverse=True)
        entry_positions = [_normalize_name(str(n)) for n in ranked[:3]]
        dilemma_nodes = [n for n in members if n in forks]
        dilemma_nodes.sort(key=lambda n: forks[n]["ptv"], reverse=True)
        proposals.append(
            {
                "name": f"{str(ranked[0]).title()} System",
                "key": _normalize_name(str(ranked[0])) + "-system",
                "entry_positions": entry_positions,
                "member_positions": [_normalize_name(str(n)) for n in ranked],
                "candidate_dilemmas": [
                    {
                        "key": _normalize_name(str(n)) + "-fork",
                        "around": _normalize_name(str(n)),
                        "branches": [
                            _normalize_name(str(b)) for b, _ in forks[n]["branches"][:2]
                        ],
                        "subtree": [_normalize_name(str(s)) for s in forks[n]["subtree"]],
                    }
                    for n in dilemma_nodes[:3]
                ],
                "ds_progression": [],  # admin fills the DS arc per milestone (DS-10)
            }
        )
    return proposals


def propose_systems(session: Any, min_occ: int = 2, max_systems: int = 12) -> list[dict[str, Any]]:
    """DB-backed: build the corpus transition network, then propose candidate systems."""
    g = build_transition_network(session)
    return propose_from_network(g, min_occ=min_occ, max_systems=max_systems)


def persist_proposals(proposals: list[dict[str, Any]], session: Session) -> list[str]:
    """Upsert proposed systems and their dilemmas into the DB. Returns system IDs."""
    system_ids: list[str] = []
    for proposal in proposals:
        system_id = upsert_system(
            key=proposal["key"],
            name=proposal["name"],
            objective=None,
            entry_positions=proposal.get("entry_positions", []),
            activation_conditions=[],
            expected_opponent_responses=[],
            alternative_paths=[],
            mastery_criteria=[],
            ds_mode="",
            session=session,
        )
        system_ids.append(system_id)
        # Upsert candidate dilemmas linked to this system; the PtV fork's two
        # branches ARE the dilemma's options.
        for dilemma in proposal.get("candidate_dilemmas", []):
            branches = dilemma.get("branches", [])
            upsert_dilemma(
                key=dilemma["key"],
                name=dilemma.get("name", dilemma["key"]),
                situation=dilemma.get("around"),
                option_a=branches[0] if branches else None,
                option_b=branches[1] if len(branches) > 1 else None,
                principle_keys=[],
                session=session,
            )
    return system_ids
