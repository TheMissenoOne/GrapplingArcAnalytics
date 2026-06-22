"""DB repository — upsert helpers for graphs, athletes, archetypes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from analysis.names import _normalize_name
from db.models import (
    Archetype,
    Athlete,
    AthleteMatch,
    BundleImport,
    Graph,
    GraphEdge,
    GraphNode,
    Profile,
)
from schemas.app_types import UserBundle


def upsert_graph_from_bundle(bundle: UserBundle, session: Session) -> str:
    """Persist a UserBundle's graph into the DB. Returns graph id."""
    if bundle.user is None:
        raise ValueError("Bundle has no user")
    owner_id = bundle.user.id

    # Upsert profile
    profile = session.get(Profile, owner_id)
    if profile is None:
        profile = Profile(
            id=owner_id,
            full_name=bundle.user.full_name,
            belt_rank=bundle.user.belt_rank,
            belt_degrees=bundle.user.belt_degrees,
            is_guest=bundle.user.is_guest,
        )
        session.add(profile)
    else:
        profile.full_name = bundle.user.full_name
        profile.belt_rank = bundle.user.belt_rank
        profile.belt_degrees = bundle.user.belt_degrees

    # Upsert graph row
    stmt = (
        pg_insert(Graph)
        .values(
            owner_kind="user",
            owner_id=owner_id,
            user_elo=bundle.graph.user_elo if bundle.graph else None,
            schema_version=bundle.schema_version,
            synced_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["owner_kind", "owner_id"],
            set_={
                "user_elo": bundle.graph.user_elo if bundle.graph else None,
                "schema_version": bundle.schema_version,
                "synced_at": datetime.now(UTC),
            },
        )
        .returning(Graph.id)
    )
    graph_id: str = session.execute(stmt).scalar_one()

    if bundle.graph is None:
        return graph_id

    # Upsert nodes
    for node in bundle.graph.nodes:
        node_key = _normalize_name(node.label)
        node_stmt = (
            pg_insert(GraphNode)
            .values(
                graph_id=graph_id,
                node_key=node_key,
                label=node.label,
                type=node.type,
                node_type=node.node_type,
                computed_elo=node.computed_elo,
                usage_count=node.usage_count,
                trend=node.trend,
            )
            .on_conflict_do_update(
                index_elements=["graph_id", "node_key"],
                set_={
                    "label": node.label,
                    "type": node.type,
                    "node_type": node.node_type,
                    "computed_elo": node.computed_elo,
                    "usage_count": node.usage_count,
                    "trend": node.trend,
                },
            )
        )
        session.execute(node_stmt)

    # Upsert edges
    for edge in bundle.graph.edges:
        source_key = _normalize_name(
            _label_for_id(edge.source, bundle) or edge.source
        )
        target_key = _normalize_name(
            _label_for_id(edge.target, bundle) or edge.target
        )
        edge_key = f"{source_key}→{target_key}"
        edge_stmt = (
            pg_insert(GraphEdge)
            .values(
                graph_id=graph_id,
                edge_key=edge_key,
                source_key=source_key,
                target_key=target_key,
                elo=edge.elo,
                setup=edge.setup or "",
            )
            .on_conflict_do_update(
                index_elements=["graph_id", "edge_key"],
                set_={"elo": edge.elo, "setup": edge.setup or ""},
            )
        )
        session.execute(edge_stmt)

    # Audit log
    session.add(BundleImport(owner_id=owner_id))

    return graph_id


def _label_for_id(node_id: str, bundle: UserBundle) -> str | None:
    """Resolve app-local node id → label using bundle's node list."""
    if bundle.graph is None:
        return None
    for n in bundle.graph.nodes:
        if n.id == node_id:
            return n.label
    return None


def upsert_graph_from_athlete_graph(
    athlete_graph: Any, athlete_id: str, session: Session
) -> str:
    """Persist an AthleteGraph (from analysis/athlete_graph.py) into the DB."""
    stmt = (
        pg_insert(Graph)
        .values(
            owner_kind="athlete",
            owner_id=athlete_id,
            synced_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["owner_kind", "owner_id"],
            set_={"synced_at": datetime.now(UTC)},
        )
        .returning(Graph.id)
    )
    graph_id: str = session.execute(stmt).scalar_one()

    for node_key, node in athlete_graph.nodes.items():
        node_stmt = (
            pg_insert(GraphNode)
            .values(
                graph_id=graph_id,
                node_key=node_key,
                label=node.label,
                type="technique",
                node_type=node.type,
                usage_count=node.count,
            )
            .on_conflict_do_update(
                index_elements=["graph_id", "node_key"],
                set_={"label": node.label, "usage_count": node.count},
            )
        )
        session.execute(node_stmt)

    for (src, tgt), edge in athlete_graph.edges.items():
        edge_key = f"{src}→{tgt}"
        edge_stmt = (
            pg_insert(GraphEdge)
            .values(
                graph_id=graph_id,
                edge_key=edge_key,
                source_key=src,
                target_key=tgt,
                elo=float(edge.count),
            )
            .on_conflict_do_update(
                index_elements=["graph_id", "edge_key"],
                set_={"elo": float(edge.count)},
            )
        )
        session.execute(edge_stmt)

    return graph_id


def register_match(
    athlete_id: str,
    opponent_name: str | None,
    event: str | None,
    year: int | None,
    weight_class: str | None,
    win_type: str | None,
    stage: str | None,
    submission: str | None,
    won: bool,
    sequence: list[dict[str, Any]],
    created_by: str | None,
    session: Session,
) -> str:
    match = AthleteMatch(
        athlete_id=athlete_id,
        opponent_name=opponent_name,
        event=event,
        year=year,
        weight_class=weight_class,
        win_type=win_type,
        stage=stage,
        submission=submission,
        won=won,
        sequence=sequence,
        created_by=created_by,
    )
    session.add(match)
    session.flush()
    return match.id


def upsert_athlete(
    name: str,
    nickname: str | None = None,
    team: str | None = None,
    weight_class: str | None = None,
    belt: str | None = None,
    source: str = "manual",
    session: Session = None,  # type: ignore[assignment]
) -> str:
    athlete = Athlete(
        name=name,
        nickname=nickname,
        team=team,
        weight_class=weight_class,
        belt=belt,
        source=source,
    )
    session.add(athlete)
    session.flush()
    return athlete.id


def get_athlete_matches(athlete_id: str, session: Session) -> list[AthleteMatch]:
    return list(
        session.execute(
            select(AthleteMatch).where(AthleteMatch.athlete_id == athlete_id)
        ).scalars()
    )


def graphs_for_clustering(session: Session) -> list[tuple[str, list[GraphNode]]]:
    """Return [(graph_id, [GraphNode, ...])] for all graphs."""
    graphs = list(session.execute(select(Graph)).scalars())
    result = []
    for g in graphs:
        nodes = list(session.execute(select(GraphNode).where(GraphNode.graph_id == g.id)).scalars())
        result.append((g.id, nodes))
    return result


def save_archetypes(
    centroids: list[list[float]],
    names: list[str],
    feature_version: str,
    session: Session,
) -> list[int]:
    ids = []
    for name, centroid in zip(names, centroids):
        a = Archetype(name=name, centroid={"vector": centroid}, feature_version=feature_version)
        session.add(a)
        session.flush()
        ids.append(a.id)
    return ids


def assign_archetype_to_graph(graph_id: str, archetype_id: int, session: Session) -> None:
    graph = session.get(Graph, graph_id)
    if graph:
        graph.archetype_id = archetype_id


def publish_athlete(athlete_id: str, session: Session) -> None:
    athlete = session.get(Athlete, athlete_id)
    if athlete:
        athlete.is_published = True
