"""scripts.prune_orphan_graph_nodes — athlete-only orphan node cleanup.

SQLite in-memory, same shape as ``test_db.py`` — model round-trips, no Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.models import Graph, GraphEdge, GraphNode

_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def engine() -> Iterator[Engine]:
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models with Base.metadata
    from db.base import Base

    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = (  # type: ignore[method-assign]
        lambda self, type_, **kw: "VARCHAR(36)"
    )
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]

    eng = create_engine(_SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, checkfirst=True)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


def test_prune_deletes_orphan_athlete_node_but_spares_user_and_connected(session: Session) -> None:
    from scripts.prune_orphan_graph_nodes import orphan_athlete_nodes, prune

    athlete_graph_id = str(uuid.uuid4())
    user_graph_id = str(uuid.uuid4())
    session.add_all(
        [
            Graph(id=athlete_graph_id, owner_kind="athlete", owner_id=str(uuid.uuid4())),
            Graph(id=user_graph_id, owner_kind="user", owner_id=str(uuid.uuid4())),
        ]
    )
    session.add_all(
        [
            # athlete: A has an edge to B, C is orphaned
            GraphNode(graph_id=athlete_graph_id, node_key="a", label="A"),
            GraphNode(graph_id=athlete_graph_id, node_key="b", label="B"),
            GraphNode(graph_id=athlete_graph_id, node_key="c", label="C"),
            # user: edge-less node, legitimate, must survive
            GraphNode(graph_id=user_graph_id, node_key="my drill", label="My Drill"),
        ]
    )
    session.add(
        GraphEdge(
            graph_id=athlete_graph_id, edge_key="a→b",
            source_key="a", target_key="b", elo=1000.0,
        )
    )
    session.commit()

    orphans_before = orphan_athlete_nodes(session)
    assert {n.node_key for n in orphans_before} == {"c"}

    counts = prune(session, apply=True)
    assert counts == {"c": 1}

    remaining_keys = {n.node_key for n in session.query(GraphNode).all()}
    assert remaining_keys == {"a", "b", "my drill"}
    assert orphan_athlete_nodes(session) == []


def test_prune_dry_run_deletes_nothing(session: Session) -> None:
    from scripts.prune_orphan_graph_nodes import prune

    graph_id = str(uuid.uuid4())
    session.add(Graph(id=graph_id, owner_kind="athlete", owner_id=str(uuid.uuid4())))
    session.add(GraphNode(graph_id=graph_id, node_key="c", label="C"))
    session.commit()

    counts = prune(session, apply=False)
    assert counts == {"c": 1}
    assert session.query(GraphNode).count() == 1
