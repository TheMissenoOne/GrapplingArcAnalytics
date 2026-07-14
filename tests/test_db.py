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

    from db.models import Graph, GraphEdge, TechniqueNode

    graph = session.get(Graph, graph_id)
    assert graph is not None
    assert graph.owner_kind == "user"
    assert graph.user_elo == 850.0

    # Node identity lives in the shared technique library now (not per-graph rows).
    nodes = list(session.execute(select(TechniqueNode)).scalars())
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

    from db.models import TechniqueNode

    nodes = list(session.execute(select(TechniqueNode)).scalars())
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
    upsert_graph_from_athlete_graph(graph, athlete_id, session)
    session.commit()

    from db.models import TechniqueNode

    nodes = list(session.execute(select(TechniqueNode)).scalars())
    keys = {n.node_key for n in nodes}
    assert "back take" in keys
    assert "rear naked choke" in keys


def test_athlete_graph_upsert_prunes_stale_edges(session):
    """A re-derivation that drops a technique must delete its edge, not just stop
    updating it — root cause of Gordon's 172-persisted-vs-119-current drift."""
    from analysis.athlete_graph import build_athlete_graph
    from db.repository import upsert_graph_from_athlete_graph

    athlete_id = str(uuid.uuid4())
    full_sessions = [
        {
            "topics": [],
            "rounds": [
                {
                    "entries": [
                        {"label": "Back Take", "type": "position", "actor": "you"},
                        {"label": "Rear Naked Choke", "type": "submission", "actor": "you"},
                        {"label": "Armbar", "type": "submission", "actor": "you"},
                    ]
                }
            ],
        }
    ]
    graph = build_athlete_graph("Gordon Ryan", full_sessions)
    graph_id = upsert_graph_from_athlete_graph(graph, athlete_id, session)
    session.commit()

    from db.models import GraphEdge

    edges = list(session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars())
    keys_before = {e.edge_key for e in edges}
    assert len(edges) == 2  # back take→RNC, RNC→armbar

    # Re-derive with the armbar chain dropped (e.g. it no longer shows up in replay).
    shrunk_sessions = [
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
    graph2 = build_athlete_graph("Gordon Ryan", shrunk_sessions)
    upsert_graph_from_athlete_graph(graph2, athlete_id, session)
    session.commit()

    edges_after = list(
        session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars()
    )
    keys_after = {e.edge_key for e in edges_after}
    assert len(edges_after) == 1
    assert keys_after < keys_before  # the armbar edge is gone, not just unchanged
    assert all("armbar" not in k for k in keys_after)


def test_register_matches_bulk_inserts_and_registers_techniques(session):
    """The dump importer's batched insert path: one bulk statement for all bouts'
    Match rows + one merged technique-registration call, instead of per-bout."""
    from db.models import Match, TechniqueNode
    from db.repository import register_matches_bulk

    rows = [
        dict(
            athlete_a_id=str(uuid.uuid4()), athlete_b_id=str(uuid.uuid4()),
            winner_id=None, win_type="SUBMISSION", submission="Armbar",
            event="Test Event", year=2024, weight_class=None, stage=None,
            sequence=[{"label": "Armbar", "type": "submission", "actor_id": "x"}],
            created_by=None, video_url=None, timeline=None,
        ),
        dict(
            athlete_a_id=str(uuid.uuid4()), athlete_b_id=str(uuid.uuid4()),
            winner_id=None, win_type="SUBMISSION", submission="Heel Hook",
            event="Test Event", year=2025, weight_class=None, stage=None,
            sequence=[{"label": "Heel Hook", "type": "submission", "actor_id": "y"}],
            created_by=None, video_url=None, timeline=None,
        ),
    ]
    register_matches_bulk(rows, session)
    session.commit()

    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 2
    assert all(m.status == "final" for m in matches)

    techs = {t.node_key for t in session.execute(select(TechniqueNode)).scalars()}
    assert "armbar" in techs
    assert "heel hook" in techs


def test_run_dump_defers_replay_until_after_all_inserts(session, monkeypatch):
    """Root fix for redundant replays: with ``replay=False`` + a shared
    ``out_participants`` set, an athlete appearing in two separate dumps accumulates
    once, and a single later replay pass touches them exactly once (not once per dump)."""
    import contextlib

    import db.base as db_base
    import db.repository as repo
    from db.models import Athlete, Match
    from scripts import dump_import

    @contextlib.contextmanager
    def _fake_db_session():
        yield session

    monkeypatch.setattr(db_base, "db_session", _fake_db_session)

    replay_calls: list[str] = []
    monkeypatch.setattr(
        repo, "replay_and_persist_athlete",
        lambda athlete, sess: replay_calls.append(athlete.id),
    )

    def _raw_dump(a_name: str, b_name: str, year: int) -> list[dict]:
        events = [
            {"label": "Guard Pull", "type": "guard", "actor": a_name},
            {"label": "Armbar", "type": "submission", "actor": a_name, "successful": True},
        ]
        return [{(a_name, year): {"winner": a_name, "method": "Submission (Armbar)",
                                   "opponent": b_name, "events": events}}]

    all_participants: set[str] = set()
    # "Craig Jones" is shared across both dumps — the redundant-replay case.
    dump_import.run_dump(
        _raw_dump("Craig Jones", "Kyle Boehm", 2024), event=None, label="A",
        replay=False, out_participants=all_participants,
    )
    dump_import.run_dump(
        _raw_dump("Craig Jones", "Nicky Rod", 2025), event=None, label="B",
        replay=False, out_participants=all_participants,
    )

    assert replay_calls == []  # deferred: no replay fired inside either run_dump call
    assert len(all_participants) == 3  # Craig + Kyle + Nicky, Craig counted once

    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 2

    # Simulate reprocess_all's single post-loop replay pass.
    for aid in all_participants:
        athlete = session.get(Athlete, aid)
        if athlete is not None:
            repo.replay_and_persist_athlete(athlete, session)

    assert len(replay_calls) == 3  # each unique athlete replayed exactly once total


def test_run_dump_batched_delete_insert_is_idempotent(session, monkeypatch):
    """Batching the per-bout delete+insert into one delete + one bulk insert must stay
    behaviorally identical: re-running the same dump replaces, never duplicates, a bout."""
    import contextlib

    import db.base as db_base
    from db.models import Match
    from scripts import dump_import

    @contextlib.contextmanager
    def _fake_db_session():
        yield session

    monkeypatch.setattr(db_base, "db_session", _fake_db_session)

    raw = [{("Craig Jones", 2024): {
        "winner": "Craig Jones", "method": "Submission (Armbar)", "opponent": "Kyle Boehm",
        "events": [
            {"label": "Guard Pull", "type": "guard", "actor": "Craig Jones"},
            {"label": "Armbar", "type": "submission", "actor": "Craig Jones", "successful": True},
        ],
    }}]

    dump_import.run_dump(raw, event=None, label="A", replay=False)
    dump_import.run_dump(raw, event=None, label="A", replay=False)  # re-run same dump

    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 1


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
        Path(__file__).parent.parent.parent
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
