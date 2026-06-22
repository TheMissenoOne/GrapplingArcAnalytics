"""DB layer tests — SQLite in-memory for model round-trips (no Postgres needed)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# Point at SQLite so these tests run without Postgres
_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created."""

    # SQLite compat: render JSONB/UUID as TEXT
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models with Base.metadata
    from db.base import Base
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]

    eng = create_engine(_SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, checkfirst=True)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


def _make_bundle(user_id: str | None = None) -> dict:
    uid = user_id or str(uuid.uuid4())
    return {
        "schemaVersion": 3,
        "user": {
            "auth": {
                "id": uid,
                "fullName": "Test User",
                "beltRank": "blue",
                "beltDegrees": 2,
                "isGuest": False,
            }
        },
        "graph": {
            "userElo": 850.0,
            "nodes": [
                {
                    "id": "n1",
                    "label": "Closed Guard",
                    "type": "position",
                    "data": {"type": "guard", "computedElo": 900.0, "usageCount": 5, "trend": "core"},  # noqa: E501
                },
                {
                    "id": "n2",
                    "label": "Armbar",
                    "type": "technique",
                    "data": {"type": "submission", "computedElo": 800.0, "usageCount": 3, "trend": "emerging"},  # noqa: E501
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "n2",
                    "data": {"elo": 820.0, "setup": "hip escape"},
                }
            ],
        },
    }


def test_upsert_graph_from_bundle_creates_rows(session):
    from db.repository import upsert_graph_from_bundle
    from schemas.app_types import UserBundle

    data = _make_bundle()
    bundle = UserBundle.from_json(data)
    graph_id = upsert_graph_from_bundle(bundle, session)
    session.commit()

    from db.models import Graph, GraphEdge, GraphNode

    graph = session.get(Graph, graph_id)
    assert graph is not None
    assert graph.owner_kind == "user"
    assert graph.user_elo == 850.0

    nodes = list(session.execute(select(GraphNode).where(GraphNode.graph_id == graph_id)).scalars())
    assert len(nodes) == 2
    node_keys = {n.node_key for n in nodes}
    assert "closed guard" in node_keys
    assert "armbar" in node_keys

    edges = list(session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars())
    assert len(edges) == 1
    assert edges[0].source_key == "closed guard"
    assert edges[0].target_key == "armbar"
    assert edges[0].elo == 820.0


def test_upsert_idempotent(session):
    from db.repository import upsert_graph_from_bundle
    from schemas.app_types import UserBundle

    user_id = str(uuid.uuid4())
    data = _make_bundle(user_id)
    bundle = UserBundle.from_json(data)
    id1 = upsert_graph_from_bundle(bundle, session)
    session.commit()
    id2 = upsert_graph_from_bundle(bundle, session)
    session.commit()

    from db.models import Graph

    graphs = list(session.execute(select(Graph).where(Graph.owner_id == user_id)).scalars())
    assert len(graphs) == 1
    assert id1 == id2


def test_node_key_is_normalized(session):
    from db.repository import upsert_graph_from_bundle
    from schemas.app_types import UserBundle

    data = _make_bundle()
    data["graph"]["nodes"][0]["label"] = "  Closed Guard!!  "
    bundle = UserBundle.from_json(data)
    upsert_graph_from_bundle(bundle, session)
    session.commit()

    from db.models import GraphNode

    nodes = list(session.execute(select(GraphNode)).scalars())
    keys = {n.node_key for n in nodes}
    assert "closed guard" in keys


def test_athlete_graph_upsert(session):
    from analysis.athlete_graph import build_athlete_graph
    from db.repository import upsert_graph_from_athlete_graph

    sessions_payload = [
        {
            "topics": [],
            "rounds": [
                {
                    "entries": [
                        {"label": "Back Take", "type": "position", "actor": "you"},
                        {"label": "Rear Naked Choke", "type": "submission", "actor": "you"},
                    ]
                }
            ],
        }
    ]
    athlete_id = str(uuid.uuid4())
    graph = build_athlete_graph("Gordon Ryan", sessions_payload)
    graph_id = upsert_graph_from_athlete_graph(graph, athlete_id, session)
    session.commit()

    from db.models import GraphNode

    nodes = list(session.execute(select(GraphNode).where(GraphNode.graph_id == graph_id)).scalars())
    keys = {n.node_key for n in nodes}
    assert "back take" in keys
    assert "rear naked choke" in keys


def test_bundle_import_provenance(session):
    from db.models import BundleImport
    from db.repository import upsert_graph_from_bundle
    from schemas.app_types import UserBundle

    data = _make_bundle()
    bundle = UserBundle.from_json(data)
    upsert_graph_from_bundle(bundle, session)
    session.commit()

    imports = list(session.execute(select(BundleImport)).scalars())
    assert len(imports) == 1
    assert imports[0].owner_id == bundle.user.id


def test_fixture_bundle_round_trip():
    """Parse the real mock bundle fixture."""
    fixture = (
        Path(__file__).parent.parent
        / "GrapplingArcApp" / "src" / "data" / "mockData" / "mock_user_bundle.json"
    )
    if not fixture.exists():
        pytest.skip("mock_user_bundle.json not found")
    from schemas.app_types import UserBundle

    with open(fixture) as f:
        data = json.load(f)
    bundle = UserBundle.from_json(data)
    assert bundle.user is not None
    assert bundle.graph is not None
