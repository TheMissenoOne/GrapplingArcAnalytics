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

from db.models import Graph, GraphEdge, GraphNode, TechniqueNode


def athlete_graph_to_app_json(graph_id: str, session: Session) -> dict[str, Any]:
    """Reconstruct app-shaped graph JSON from DB rows for a given graph_id.

    Node identity/label/type comes from the shared ``technique_nodes`` library
    (one canonical row per ``node_key``); the node set is the union of every
    endpoint referenced by this graph's edges plus any legacy per-user
    ``graph_nodes`` rows (dual-read during the migration — once ``graph_nodes``
    is dropped, the node set is the edge endpoints alone). Per-user stats
    (``computedElo``/``usageCount``/``trend``) are no longer persisted shared,
    so they are read from the legacy ``graph_nodes`` row when present, else
    derived from incident edges.
    """
    graph = session.get(Graph, graph_id)
    if graph is None:
        raise ValueError(f"Graph {graph_id} not found")

    edges_rows = list(
        session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars()
    )
    # Legacy per-user node rows (kept for stats during dual-read; may be empty).
    legacy_nodes = {
        n.node_key: n
        for n in session.execute(
            select(GraphNode).where(GraphNode.graph_id == graph_id)
        ).scalars()
    }

    # Node set = every edge endpoint ∪ legacy node rows.
    node_keys: set[str] = set(legacy_nodes)
    for e in edges_rows:
        node_keys.add(e.source_key)
        node_keys.add(e.target_key)

    library = {
        t.node_key: t
        for t in session.execute(
            select(TechniqueNode).where(TechniqueNode.node_key.in_(node_keys))
        ).scalars()
    } if node_keys else {}

    # Incident-edge stats for keys without a legacy node row.
    incident: dict[str, list[float]] = {}
    for e in edges_rows:
        incident.setdefault(e.source_key, []).append(e.elo)
        incident.setdefault(e.target_key, []).append(e.elo)

    nodes = []
    for key in sorted(node_keys):
        legacy = legacy_nodes.get(key)
        lib = library.get(key)
        label = (lib.label if lib else None) or (legacy.label if legacy else key)
        node_type = (lib.node_type if lib else None) or (legacy.node_type if legacy else "")
        ntype = (lib.type if lib else None) or (legacy.type if legacy else "technique")
        if legacy is not None:
            computed_elo, usage_count, trend = (
                legacy.computed_elo,
                legacy.usage_count,
                legacy.trend,
            )
        else:
            elos = incident.get(key, [])
            computed_elo = max(elos) if elos else None
            usage_count = len(elos)
            trend = ""
        nodes.append(
            {
                "id": key,
                "label": label,
                "type": ntype,
                "data": {
                    "label": label,
                    "type": node_type,
                    "computedElo": computed_elo,
                    "usageCount": usage_count,
                    "trend": trend,
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
