"""Private (app-fed) data must never reach a competitive or public artefact.

Ethics + LGPD rule, root ``CLAUDE.md`` → Public vs Private Data. These lock the guards
that keep ``owner_kind='user'`` graphs out of the shared corpus. They are cheap on
purpose: the failure they prevent is silent, and only shows up as a centroid that quietly
drifted toward a paying user's private game.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from analysis.embeddings import backfill_archetype_embeddings, nearest_graphs


@pytest.fixture()
def session():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers the ORM models
    from db.base import Base
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_VECTOR = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, checkfirst=True)
    with Session(eng) as s:
        yield s
    Base.metadata.drop_all(eng)


DIM = 768


def _vec(*head: float) -> list[float]:
    """pgvector pins the column at 768 dims; only the leading values matter here."""
    return [*head, *([0.0] * (DIM - len(head)))]


def _graph(session, owner_kind: str, vector: list[float], archetype_id: int | None = 1):
    from db.models import Graph

    g = Graph(id=str(uuid.uuid4()), owner_kind=owner_kind, owner_id=str(uuid.uuid4()),
              archetype_id=archetype_id, embedding=vector)
    session.add(g)
    session.flush()
    return g


def test_centroide_ignora_grafo_privado_de_usuario(session) -> None:
    """A user graph carrying an archetype_id must not move the centroid.

    ``scripts.assign_user_archetypes`` stamps ``archetype_id`` on user graphs, so this is
    the exact path by which private data would otherwise enter the shared archetypes.
    """
    from db.models import Archetype

    session.add(Archetype(id=1, name="pressure passer"))
    _graph(session, "athlete", _vec(1.0, 0.0))
    _graph(session, "user", _vec(0.0, 1.0))   # privado — não pode contar
    session.flush()

    assert backfill_archetype_embeddings(session) == 1
    centroide = list(session.get(Archetype, 1).embedding)
    assert centroide == pytest.approx(_vec(1.0, 0.0)), (
        "o grafo privado entrou na média do centroide"
    )


def test_centroide_sem_grafo_de_atleta_nao_usa_o_privado(session) -> None:
    from db.models import Archetype

    session.add(Archetype(id=1, name="guard player"))
    _graph(session, "user", _vec(0.0, 1.0))
    session.flush()

    assert backfill_archetype_embeddings(session) == 0
    assert session.get(Archetype, 1).embedding is None


def test_busca_de_similares_e_athlete_only_por_padrao() -> None:
    """A private graph must never surface as somebody else's "similar athlete".

    pgvector's ``<=>`` has no SQLite equivalent, so this inspects the statement the
    function actually builds instead of running it.
    """
    capturado: list[object] = []

    class _Resultado:
        def scalar_one_or_none(self):
            return _vec(1.0, 0.0)

        def __iter__(self):
            return iter(())

    class _Sessao:
        def execute(self, statement):
            capturado.append(statement)
            return _Resultado()

    sessao = _Sessao()
    assert nearest_graphs(sessao, "algum-id") == []            # type: ignore[arg-type]
    sql = str(capturado[-1].compile(compile_kwargs={"literal_binds": False}))
    assert "owner_kind" in sql, "a busca de semelhantes não filtra owner_kind"

    # e o opt-out continua disponível para mostrar ao próprio dono
    nearest_graphs(sessao, "algum-id", owner_kind=None)        # type: ignore[arg-type]
    assert "owner_kind" not in str(capturado[-1].compile(compile_kwargs={"literal_binds": False}))
