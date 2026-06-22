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

from db.models import Graph, GraphEdge, GraphNode


def athlete_graph_to_app_json(graph_id: str, session: Session) -> dict[str, Any]:
    """Reconstruct app-shaped graph JSON from DB rows for a given graph_id."""
    graph = session.get(Graph, graph_id)
    if graph is None:
        raise ValueError(f"Graph {graph_id} not found")

    nodes_rows = list(
        session.execute(select(GraphNode).where(GraphNode.graph_id == graph_id)).scalars()
    )
    edges_rows = list(
        session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars()
    )

    nodes = [
        {
            "id": n.node_key,
            "label": n.label,
            "type": n.type,
            "data": {
                "label": n.label,
                "type": n.node_type,
                "computedElo": n.computed_elo,
                "usageCount": n.usage_count,
                "trend": n.trend,
            },
        }
        for n in nodes_rows
    ]

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
