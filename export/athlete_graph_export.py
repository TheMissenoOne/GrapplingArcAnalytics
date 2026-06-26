"""Export DB athlete graph rows into the app-shaped JSON format.

Output shape mirrors GrapplingArc graphRepository.ts:Graph:
  {
    "nodes": [{"id": node_key, "label": ..., "type": ..., "data": {...}}],
    "edges": [{"id": edge_key, "source": source_key, "target": target_key, "data": {...}}],
    "userElo": ...
  }

This is what the app reads from published_athlete_graphs + graph_nodes/edges.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Graph, GraphEdge, TechniqueNode
from db.repository import incident_edge_elos


def athlete_graph_to_app_json(graph_id: str, session: Session) -> dict[str, Any]:
    """Reconstruct app-shaped graph JSON from DB rows for a given graph_id.

    The node set is the graph's edge endpoints (graph_nodes is dropped); node
    identity/label/type comes from the shared ``technique_nodes`` library (one
    canonical row per ``node_key``). Per-user stats are no longer persisted, so
    ``computedElo``/``usageCount`` are derived from incident edges (strongest
    incident ELO / incident-edge count); ``trend`` is left empty.
    """
    graph = session.get(Graph, graph_id)
    if graph is None:
        raise ValueError(f"Graph {graph_id} not found")

    edges_rows = list(
        session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars()
    )

    # Node set + incident-edge stats from the edges (shared derivation with clustering).
    incident = incident_edge_elos(edges_rows)
    node_keys = set(incident)

    library = {
        t.node_key: t
        for t in session.execute(
            select(TechniqueNode).where(TechniqueNode.node_key.in_(node_keys))
        ).scalars()
    }

    nodes = []
    for key in sorted(node_keys):
        lib = library.get(key)
        label = (lib.label if lib else None) or key
        elos = incident.get(key, [])
        nodes.append(
            {
                "id": key,
                "label": label,
                "type": lib.type if lib else "technique",
                "data": {
                    "label": label,
                    "type": lib.node_type if lib else "",
                    "computedElo": max(elos) if elos else None,
                    "usageCount": len(elos),
                    "trend": "",
                },
            }
        )

    edges = [
        {
            "id": e.edge_key,
            "source": e.source_key,
            "target": e.target_key,
            "data": {
                "elo": e.elo,
                "setup": e.setup,
            },
        }
        for e in edges_rows
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "userElo": graph.user_elo,
    }


def export_published_athletes(session: Session) -> list[dict[str, Any]]:
    """Return app JSON for all published athlete graphs."""
    from db.models import Athlete

    athletes = list(
        session.execute(select(Athlete).where(Athlete.is_published == True)).scalars()  # noqa: E712
    )
    results = []
    for athlete in athletes:
        graph = session.execute(
            select(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == athlete.id)
        ).scalar_one_or_none()
        if graph is None:
            continue
        app_json = athlete_graph_to_app_json(graph.id, session)
        results.append(
            {
                "athlete": {
                    "id": athlete.id,
                    "name": athlete.name,
                    "nickname": athlete.nickname,
                    "team": athlete.team,
                    "weight_class": athlete.weight_class,
                    "belt": athlete.belt,
                    "elo": athlete.elo,
                },
                "graph": app_json,
            }
        )
    return results
