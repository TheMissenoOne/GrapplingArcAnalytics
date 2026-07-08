"""analysis/merge_attempt_nodes tests — SQLite in-memory, same pattern as tests/test_db.py."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created."""
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


def _seed(session: Session) -> str:
    """One 'user' graph with a canonical "Heel Hook" node + an "Heel Hook Attempt"
    duplicate, plus "Closed Guard"/"Armbar". Edges:
      - closed guard -> heel hook          (canonical already exists, elo=700)
      - closed guard -> heel hook attempt  (elo=650 — remaps onto the row above: collision)
      - heel hook attempt -> armbar        (elo=800 — remaps cleanly, no collision)
    """
    from db.models import Graph, GraphEdge, TechniqueNode

    for key, label, node_type, source in [
        ("closed guard", "Closed Guard", "guard", "library"),
        ("heel hook", "Heel Hook", "submission", "library"),
        ("heel hook attempt", "Heel Hook Attempt", "submission", "user"),
        ("armbar", "Armbar", "submission", "library"),
    ]:
        session.add(TechniqueNode(node_key=key, label=label, type="technique",
                                   node_type=node_type, source=source))

    graph = Graph(owner_kind="user", owner_id="00000000-0000-0000-0000-000000000001")
    session.add(graph)
    session.flush()

    session.add_all([
        GraphEdge(graph_id=graph.id, edge_key="closed guard→heel hook",
                  source_key="closed guard", target_key="heel hook", elo=700.0),
        GraphEdge(graph_id=graph.id, edge_key="closed guard→heel hook attempt",
                  source_key="closed guard", target_key="heel hook attempt", elo=650.0),
        GraphEdge(graph_id=graph.id, edge_key="heel hook attempt→armbar",
                  source_key="heel hook attempt", target_key="armbar", elo=800.0),
    ])
    session.commit()
    return graph.id


def test_merge_resolves_remaps_and_dedupes(session):
    from analysis.merge_attempt_nodes import check, find_attempt_nodes, run
    from db.models import GraphEdge, TechniqueNode

    graph_id = _seed(session)

    # dry-run: no writes, but the plan resolves + previews the collision dedupe
    dry = run(session, apply=False)
    assert [p.attempt_key for p in dry.resolved] == ["heel hook attempt"]
    assert dry.unresolved == []
    assert dry.graph_edges_touched == 2  # both edges through the attempt node
    assert dry.graph_edges_deleted == 1  # the collision loses one row
    session.commit()
    assert find_attempt_nodes(session)[0].node_key == "heel hook attempt"  # untouched

    # real run
    report = run(session, apply=True)
    session.commit()
    assert report.graph_edges_deleted == 1

    # attempt node gone
    assert session.execute(
        select(TechniqueNode).where(TechniqueNode.node_key == "heel hook attempt")
    ).first() is None

    edges = list(session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars())
    assert len(edges) == 2  # collision deduped away
    by_key = {e.edge_key: e for e in edges}
    assert set(by_key) == {"closed guard→heel hook", "heel hook→armbar"}
    assert by_key["closed guard→heel hook"].elo == 700.0  # higher-elo row survives
    assert by_key["heel hook→armbar"].source_key == "heel hook"  # remapped, not orphaned

    # no orphan: nothing still points at the deleted key
    assert not any(e.source_key == "heel hook attempt" or e.target_key == "heel hook attempt"
                   for e in edges)

    # --check is green + idempotent (second run is a no-op)
    assert check(session) == 0
    again = run(session, apply=True)
    assert again.plans == []
