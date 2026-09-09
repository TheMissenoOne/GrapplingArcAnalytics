"""``db.repository.edges_by_graph`` — the N+1 both graph consumers used to carry.

``graphs_for_clustering`` and ``analysis.embeddings.backfill_graph_embeddings`` each ran
``select(GraphEdge).where(graph_id == gid)`` inside a loop: one round trip per graph against a
REMOTE Postgres, ~200 for the athlete corpus and every graph in the DB for the backfill.

The regression this file guards is not "is it fast" but "is the statement COUNT independent of
the graph count" — that is the property a loop silently loses again. SQLite in-memory, same
fixture pattern as ``tests/test_merge_technique_dups.py``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from db.repository import edges_by_graph, graphs_for_clustering

_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def engine():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models with Base.metadata
    from db.base import Base
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]

    eng = create_engine(_SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, checkfirst=True)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


class _Counter:
    """Counts SELECTs actually sent to the driver, so a hidden per-row query cannot pass."""

    def __init__(self, engine):
        self.engine = engine
        self.selects = 0

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on)

    def _on(self, conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            self.selects += 1


def _seed(session: Session, n_graphs: int) -> list[str]:
    """``n_graphs`` athlete graphs with 2 edges each, plus one EMPTY athlete graph and one
    USER graph the clustering call must not see."""
    from db.models import Graph, GraphEdge, TechniqueNode

    for key, node_type in [("closed guard", "guard"), ("armbar", "submission"),
                           ("triangle", "submission")]:
        session.add(TechniqueNode(node_key=key, label=key.title(), type="technique",
                                  node_type=node_type, source="library"))

    ids: list[str] = []
    for i in range(n_graphs):
        g = Graph(owner_kind="athlete", owner_id=f"00000000-0000-0000-0000-00000000000{i}")
        session.add(g)
        session.flush()
        ids.append(g.id)
        session.add_all([
            GraphEdge(graph_id=g.id, edge_key="closed guard→armbar",
                      source_key="closed guard", target_key="armbar", elo=700.0 + i),
            GraphEdge(graph_id=g.id, edge_key="closed guard→triangle",
                      source_key="closed guard", target_key="triangle", elo=800.0 + i),
        ])

    empty = Graph(owner_kind="athlete", owner_id="00000000-0000-0000-0000-0000000000ff")
    session.add(empty)
    private = Graph(owner_kind="user", owner_id="00000000-0000-0000-0000-0000000000ee")
    session.add(private)
    session.flush()
    ids.append(empty.id)
    session.add(GraphEdge(graph_id=private.id, edge_key="closed guard→armbar",
                          source_key="closed guard", target_key="armbar", elo=999.0))
    session.commit()
    return ids


def test_edges_by_graph_buckets_every_graph_in_one_query(session, engine):
    ids = _seed(session, n_graphs=4)
    session.expunge_all()

    with _Counter(engine) as counter:
        by_graph = edges_by_graph(session, ids)

    assert counter.selects == 1, "one statement for the whole set, not one per graph"
    assert [len(by_graph[g]) for g in ids[:4]] == [2, 2, 2, 2]
    assert ids[4] not in by_graph, "a graph with no edges is absent, not an empty list"
    assert {e.graph_id for g in ids[:4] for e in by_graph[g]} == set(ids[:4])


def test_edges_by_graph_does_not_query_for_an_empty_id_list(session, engine):
    with _Counter(engine) as counter:
        assert edges_by_graph(session, []) == {}
    assert counter.selects == 0


def test_graphs_for_clustering_statement_count_is_independent_of_graph_count(session, engine):
    """The N+1 guard. Two graphs and eight must cost the same number of statements."""
    _seed(session, n_graphs=2)
    session.expunge_all()
    with _Counter(engine) as small:
        few = graphs_for_clustering(session, owner_kind="athlete")

    session.rollback()
    for i in range(2, 8):
        from db.models import Graph, GraphEdge
        g = Graph(owner_kind="athlete", owner_id=f"00000000-0000-0000-0000-0000000001{i:02d}")
        session.add(g)
        session.flush()
        session.add(GraphEdge(graph_id=g.id, edge_key="closed guard→armbar",
                              source_key="closed guard", target_key="armbar", elo=700.0))
    session.commit()
    session.expunge_all()
    with _Counter(engine) as big:
        many = graphs_for_clustering(session, owner_kind="athlete")

    assert len(many) == len(few) + 6
    assert small.selects == big.selects, (
        f"{small.selects} statements for 3 graphs vs {big.selects} for 9 — the loop is back"
    )


def test_graphs_for_clustering_shape_is_unchanged(session, engine):
    ids = _seed(session, n_graphs=2)
    session.expunge_all()

    result = dict(graphs_for_clustering(session, owner_kind="athlete"))

    assert set(result) == set(ids[:3]), "athlete graphs only — the user graph must not appear"
    assert result[ids[2]] == [], "an edgeless graph still yields its (id, []) pair"
    nodes = {n.node_key: n for n in result[ids[0]]}
    assert set(nodes) == {"closed guard", "armbar", "triangle"}
    assert nodes["closed guard"].node_type == "guard"
    assert nodes["closed guard"].computed_elo == 800.0, "strongest incident edge ELO"
    assert nodes["armbar"].computed_elo == 700.0


def test_graphs_for_clustering_requires_owner_kind(session):
    """Root CLAUDE.md: an unfiltered `select(Graph)` is a defect. The default is gone."""
    with pytest.raises(TypeError):
        graphs_for_clustering(session)  # type: ignore[call-arg]
