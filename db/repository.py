"""DB repository — upsert helpers for graphs, athletes, archetypes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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
    Profile,
    TechniqueNode,
)
from schemas.app_types import UserBundle


@dataclass
class DerivedNode:
    """A graph node reconstructed from edges + the shared technique library.

    Per-user node stats are no longer persisted (graph_nodes is dropped), so
    ``computed_elo`` is derived as the strongest incident edge ELO. Exposes the
    attributes ``analysis.archetype.graph_feature_vector`` reads."""

    node_key: str
    node_type: str
    computed_elo: float | None


def _register_techniques(techs: dict[str, dict[str, str]], session: Session) -> None:
    """Batch insert-if-absent into the shared technique library (one statement).

    Empty node_keys are skipped (a punctuation/whitespace-only label normalizes to
    '' and must not become a junk library row / FK target). ``source='user'``;
    never clobbers a curated 'library' row (do-nothing on conflict)."""
    rows = [t for key, t in techs.items() if key]
    if not rows:
        return
    session.execute(
        pg_insert(TechniqueNode).values(rows).on_conflict_do_nothing(index_elements=["node_key"])
    )


def incident_edge_elos(edges: Iterable[GraphEdge]) -> dict[str, list[float]]:
    """Map each node_key to the ELOs of its incident edges (graph_nodes is gone, so
    the node set + per-node stats are reconstructed from edges). Shared by clustering
    and the athlete export so the derivation has a single definition."""
    incident: dict[str, list[float]] = {}
    for e in edges:
        incident.setdefault(e.source_key, []).append(e.elo)
        incident.setdefault(e.target_key, []).append(e.elo)
    return incident


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

    # Collect techniques (nodes + edge endpoints) and register them in one batch
    # BEFORE edges — the edge FK requires the endpoints to already exist. Per-user
    # stats are not persisted (identity only; derived from edges at read time).
    techs: dict[str, dict[str, str]] = {}
    for node in bundle.graph.nodes:
        key = _normalize_name(node.label)
        if key:
            techs.setdefault(
                key,
                {"node_key": key, "label": node.label, "type": node.type,
                 "node_type": node.node_type, "source": "user"},
            )
    resolved_edges = []
    for edge in bundle.graph.edges:
        source_label = _label_for_id(edge.source, bundle) or edge.source
        target_label = _label_for_id(edge.target, bundle) or edge.target
        source_key = _normalize_name(source_label)
        target_key = _normalize_name(target_label)
        for key, label in ((source_key, source_label), (target_key, target_label)):
            if key:
                techs.setdefault(
                    key,
                    {"node_key": key, "label": label, "type": "technique",
                     "node_type": "", "source": "user"},
                )
        resolved_edges.append((edge, source_key, target_key))

    _register_techniques(techs, session)

    # Upsert edges (skip any with an empty endpoint — no valid FK target).
    for edge, source_key, target_key in resolved_edges:
        if not source_key or not target_key:
            continue
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
    user_elo = getattr(athlete_graph, "user_elo", None)
    stmt = (
        pg_insert(Graph)
        .values(
            owner_kind="athlete",
            owner_id=athlete_id,
            user_elo=user_elo,
            synced_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["owner_kind", "owner_id"],
            set_={"user_elo": user_elo, "synced_at": datetime.now(UTC)},
        )
        .returning(Graph.id)
    )
    graph_id: str = session.execute(stmt).scalar_one()

    # Register techniques (nodes + edge endpoints) in one batch before edges (FK).
    techs: dict[str, dict[str, str]] = {}
    for node_key, node in athlete_graph.nodes.items():
        if node_key:
            techs.setdefault(
                node_key,
                {"node_key": node_key, "label": node.label, "type": "technique",
                 "node_type": node.type, "source": "user"},
            )
    for (src, tgt), _edge in athlete_graph.edges.items():
        for key in (src, tgt):
            if key:
                techs.setdefault(
                    key,
                    {"node_key": key, "label": key, "type": "technique",
                     "node_type": "", "source": "user"},
                )
    _register_techniques(techs, session)

    for (src, tgt), edge in athlete_graph.edges.items():
        if not src or not tgt:
            continue
        edge_key = f"{src}→{tgt}"
        # Prefer the grown edge ELO; fall back to the raw count for count-only callers.
        edge_elo = edge.elo if edge.elo is not None else float(edge.count)
        edge_stmt = (
            pg_insert(GraphEdge)
            .values(
                graph_id=graph_id,
                edge_key=edge_key,
                source_key=src,
                target_key=tgt,
                elo=edge_elo,
            )
            .on_conflict_do_update(
                index_elements=["graph_id", "edge_key"],
                set_={"elo": edge_elo},
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
    opponent_athlete_id: str | None = None,
    opponent_elo: float | None = None,
) -> str:
    match = AthleteMatch(
        athlete_id=athlete_id,
        opponent_name=opponent_name,
        opponent_athlete_id=opponent_athlete_id,
        opponent_elo=opponent_elo,
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


def graphs_for_clustering(session: Session) -> list[tuple[str, list[DerivedNode]]]:
    """Return [(graph_id, [DerivedNode, ...])] for all graphs.

    Nodes are reconstructed from each graph's edges joined to the shared
    ``technique_nodes`` library (graph_nodes is dropped): the node set is the
    edge endpoints, ``node_type`` comes from the library, and ``computed_elo``
    is derived as the strongest incident edge ELO."""
    node_types: dict[str, str] = {
        row[0]: row[1]
        for row in session.execute(
            select(TechniqueNode.node_key, TechniqueNode.node_type)
        ).all()
    }
    graphs = list(session.execute(select(Graph)).scalars())
    result = []
    for g in graphs:
        edges = session.execute(select(GraphEdge).where(GraphEdge.graph_id == g.id)).scalars()
        incident = incident_edge_elos(edges)
        nodes = [
            DerivedNode(
                node_key=key,
                node_type=node_types.get(key, ""),
                computed_elo=max(elos) if elos else None,
            )
            for key, elos in incident.items()
        ]
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


def _load_leaderboard() -> list[dict[str, Any]]:
    """Load the ADCC ELO leaderboard, regenerating the JSON if it's missing."""
    import json

    from pipelines.etl import PROCESSED_DIR

    path = PROCESSED_DIR / "adcc_elo_table.json"
    if not path.exists():
        from export.adcc_elo_table import export_adcc_elo_table

        export_adcc_elo_table()
    with open(path) as f:
        data: list[dict[str, Any]] = json.load(f)
    return data


def rank_elo_for_athlete(name: str) -> float | None:
    """Look up an athlete's ADCC rank ELO by normalized name, or None."""
    target = _normalize_name(name)
    for rec in _load_leaderboard():
        if _normalize_name(str(rec.get("fighter", ""))) == target:
            return float(rec["elo"])
    return None


def seed_athletes_from_leaderboard(session: Session) -> int:
    """Create Athlete rows from the leaderboard with ``rank_elo`` set.

    Skips fighters whose (normalized) name already exists. Returns count created.
    """
    existing = {
        _normalize_name(a.name)
        for a in session.execute(select(Athlete)).scalars()
    }
    created = 0
    for rec in _load_leaderboard():
        name = str(rec.get("fighter", "")).strip()
        if not name or _normalize_name(name) in existing:
            continue
        session.add(
            Athlete(
                name=name,
                belt="black",
                weight_class=str(rec.get("weight_class") or "") or None,
                source="leaderboard",
                rank_elo=float(rec["elo"]),
            )
        )
        existing.add(_normalize_name(name))
        created += 1
    return created
