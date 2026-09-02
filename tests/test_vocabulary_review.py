"""Vocabulary review queue (Onda A1) — query shape, dismissal filter, append guard."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import scripts.extend_technique_library as etl
from db.models import Graph, GraphNode
from scripts.vocabulary_review import load_dismissals, pending_labels

# SQLite in-memory, same shape as test_db.py — no Postgres needed for a model round-trip.
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


def _user_graph(session: Session, owner_id: str) -> Graph:
    g = Graph(owner_kind="user", owner_id=owner_id)
    session.add(g)
    session.flush()
    return g


# ── pending_labels query shape ──────────────────────────────────────────────


def test_pending_labels_only_unresolved_user_nodes(session: Session) -> None:
    """owner_kind='athlete' and already-linked nodes never show up in the queue."""
    user_graph = _user_graph(session, "11111111-1111-1111-1111-111111111111")
    athlete_graph = Graph(owner_kind="athlete", owner_id="22222222-2222-2222-2222-222222222222")
    session.add(athlete_graph)
    session.flush()

    session.add_all([
        # unresolved user label — SHOULD show up
        GraphNode(graph_id=user_graph.id, node_key="meu chokezinho",
                  label="Meu Chokezinho", type="submission", node_type="submission"),
        # already linked to the curated library — should NOT show up
        GraphNode(graph_id=user_graph.id, node_key="armbar", label="Armbar",
                  type="submission", node_type="submission", canonical_node_key="armbar"),
        # athlete (public) graph — not this user's private queue
        GraphNode(graph_id=athlete_graph.id, node_key="something",
                  label="Something", type="control", node_type="control"),
    ])
    session.commit()

    rows = pending_labels(session)
    keys = {r["node_key"] for r in rows}
    assert keys == {"meu chokezinho"}


def test_pending_labels_counts_distinct_graphs_and_orders_desc(session: Session) -> None:
    g1 = _user_graph(session, "11111111-1111-1111-1111-111111111111")
    g2 = _user_graph(session, "33333333-3333-3333-3333-333333333333")
    session.add_all([
        GraphNode(graph_id=g1.id, node_key="berimbolinho", label="Berimbolinho",
                  type="control", node_type="guard"),
        GraphNode(graph_id=g2.id, node_key="berimbolinho", label="Berimbolinho",
                  type="control", node_type="guard"),
        GraphNode(graph_id=g1.id, node_key="rara", label="Rara",
                  type="control", node_type="guard"),
    ])
    session.commit()

    rows = pending_labels(session)
    assert rows[0]["node_key"] == "berimbolinho"
    assert rows[0]["count"] == 2
    assert rows[1]["node_key"] == "rara"
    assert rows[1]["count"] == 1


def test_pending_labels_never_selects_owner_or_graph_columns(session: Session) -> None:
    """The privacy contract: only label/type/node_type/node_key/count leave the query."""
    g = _user_graph(session, "11111111-1111-1111-1111-111111111111")
    session.add(GraphNode(graph_id=g.id, node_key="x", label="X",
                          type="control", node_type="control"))
    session.commit()

    row = pending_labels(session)[0]
    assert set(row.keys()) == {"label", "type", "node_type", "node_key", "count"}


# ── dismissals ───────────────────────────────────────────────────────────────


def test_load_dismissals_missing_file_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.vocabulary_review as vr

    monkeypatch.setattr(vr, "DISMISS_PATH", tmp_path / "vocabulary_review.json")
    assert load_dismissals() == {}


def test_dismissed_key_filters_out_of_list(
    session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.vocabulary_review as vr

    dismiss_path = tmp_path / "vocabulary_review.json"
    dismiss_path.write_text(json.dumps(
        {"dismissed": {"meu chokezinho": {"reason": "one-off typo", "date": "2026-09-01"}}}
    ))
    monkeypatch.setattr(vr, "DISMISS_PATH", dismiss_path)

    g = _user_graph(session, "11111111-1111-1111-1111-111111111111")
    session.add(GraphNode(graph_id=g.id, node_key="meu chokezinho", label="Meu Chokezinho",
                          type="submission", node_type="submission"))
    session.commit()

    dismissed = vr.load_dismissals()
    rows = [r for r in pending_labels(session) if r["node_key"] not in dismissed]
    assert rows == []


# ── append_entries guard (approve) ──────────────────────────────────────────


@pytest.fixture()
def tmp_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lib = tmp_path / "technique_library.json"
    lib.write_text(json.dumps([
        {"en": "Armbar", "pt": "Chave de Braço", "type": "submission", "variants": ["armlock"]},
    ]))
    monkeypatch.setattr(etl, "LIB", lib)
    return lib


def test_approve_writes_expected_entry(tmp_library: Path) -> None:
    added = etl.append_entries([
        ("Foot Lock", "Chave de Pé", "submission", ["footlock", "chave no pe"]),
    ])
    assert added == [{
        "en": "Foot Lock", "pt": "Chave de Pé", "type": "submission",
        "variants": ["footlock", "chave no pe"],
    }]
    on_disk = json.loads(tmp_library.read_text())
    assert {"en": "Foot Lock", "pt": "Chave de Pé", "type": "submission",
            "variants": ["footlock", "chave no pe"]} in on_disk
    assert len(on_disk) == 2


def test_approve_refuses_duplicate_en(tmp_library: Path) -> None:
    """en already in the library → no-op, not an error, nothing written."""
    before = tmp_library.read_text()
    added = etl.append_entries([("Armbar", "Chave de Braço 2", "submission", [])])
    assert added == []
    assert tmp_library.read_text() == before


def test_approve_refuses_resolution_change(tmp_library: Path) -> None:
    """A new entry sorted ahead of 'Armbar' claiming its 'armlock' variant is refused."""
    before = tmp_library.read_text()
    with pytest.raises(etl.ResolutionConflictError):
        etl.append_entries([("Ab Lock", "Trava Abdominal", "submission", ["armlock"])])
    # refusal writes nothing
    assert tmp_library.read_text() == before
