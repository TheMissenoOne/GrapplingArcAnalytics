"""Tirar um atleta do banco tem duas formas, e elas discordam sobre o grafo de propósito.

O que estes testes travam não é a exclusão — é a **invariante**: todo grafo com
``owner_kind='athlete'`` tem linha em ``athletes``, sem exceção. Enquanto isso valer, um órfão é
sempre defeito, e ninguém precisa perguntar se aquele em particular era intencional.

Foi essa pergunta que não teve resposta por dois meses: em 2026-08-19 havia **sete** grafos órfãos
em produção, quatro do dedupe de 29/06 e três do reparo AA-011, e nada distinguia "sobrou por
descuido" de "foi deixado de propósito" porque a segunda coisa nem existia.

Por isso o caminho de pedido de titular **não apaga a linha**. Apagar e marcar o grafo criaria um
segundo tipo legítimo de órfão, e a guarda enfraqueceria para "órfão SEM marca" — exatamente a
ambiguidade que deixou os sete passarem.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.models import Athlete, Graph
from db.repository import (
    ANONYMIZED_NAME,
    AthleteRemovalReason,
    remove_athlete,
)

# SQLite in-memory, same shape as `test_db.py` — these are model round-trips and need no
# Postgres. The fixtures are duplicated rather than shared because this repo has no conftest,
# and inventing one to hold two fixtures would move them further from the tests that use them.
_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def engine() -> Iterator[Engine]:
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models with Base.metadata
    from db.base import Base

    # SQLite has neither type; rendering them as its own is what lets these model round-trips
    # run without Postgres. Both ignores are the assignment itself, not the value.
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


@pytest.fixture()
def athlete_with_graph(session: Session) -> Athlete:
    """Um atleta publicado, com nome, apelido, equipe — e um grafo próprio."""
    athlete = Athlete(
        name="Rafael Lovato Jr",
        nickname="Lovato",
        team="Lovato Jiu-Jitsu",
        weight_class="99kg",
        belt="black",
        elo=1420.0,
        is_published=True,
    )
    session.add(athlete)
    session.flush()
    session.add(Graph(owner_kind="athlete", owner_id=athlete.id, user_elo=1420.0))
    session.flush()
    return athlete


def _graph_of(session: Session, athlete_id: str) -> Graph | None:
    found: Graph | None = session.execute(
        select(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == athlete_id)
    ).scalar_one_or_none()
    return found


def test_dado_invalido_leva_o_grafo_junto(session: Session, athlete_with_graph: Athlete) -> None:
    """O atleta nunca foi real, então o grafo derivado das lutas dele também não é."""
    athlete_id = athlete_with_graph.id

    remove_athlete(athlete_with_graph, session, reason=AthleteRemovalReason.INVALID_DATA)

    assert session.get(Athlete, athlete_id) is None
    assert _graph_of(session, athlete_id) is None


def test_pedido_de_titular_mantem_o_grafo(session: Session, athlete_with_graph: Athlete) -> None:
    """A pessoa é real e as lutas também; o que precisa parar é o dado identificar ela.

    Este é o caso que um ``ON DELETE CASCADE`` destruiria — e o motivo de a decisão morar em
    código, não em constraint: a constraint dispara igual nos dois casos, porque ela não sabe
    por quê.
    """
    athlete_id = athlete_with_graph.id

    remove_athlete(athlete_with_graph, session, reason=AthleteRemovalReason.RIGHTS_REQUEST)

    graph = _graph_of(session, athlete_id)
    assert graph is not None
    # O grafo segue intacto, inclusive o ELO — derivado de lutas publicadas, não identifica
    # ninguém depois que o nome sai, e é o que o mantém útil para trabalho agregado.
    assert graph.user_elo == 1420.0


def test_pedido_de_titular_apaga_o_que_identifica_e_preserva_o_que_nao_identifica(
    session: Session, athlete_with_graph: Athlete,
) -> None:
    remove_athlete(athlete_with_graph, session, reason=AthleteRemovalReason.RIGHTS_REQUEST)
    athlete = session.get(Athlete, athlete_with_graph.id)

    assert athlete is not None
    assert athlete.name == ANONYMIZED_NAME
    assert athlete.nickname is None
    assert athlete.team is None
    assert athlete.weight_class is None
    # Sem página individual para quem pediu para não ser identificado.
    assert athlete.is_published is False
    assert athlete.anonymized_at is not None

    # O `id` FICA. É UUID pseudonímico, não carrega identidade própria, e mantê-lo é o que faz o
    # grafo continuar com dono válido — que é a invariante inteira.
    assert athlete.id == athlete_with_graph.id
    # Derivados de luta pública continuam: não identificam, e são o valor que sobra.
    assert athlete.elo == 1420.0


def test_a_invariante_vale_depois_dos_dois_caminhos(session: Session) -> None:
    """A afirmação que interessa, feita sobre o banco inteiro e não sobre uma linha.

    Se algum dia um caminho de remoção deixar um grafo para trás, é aqui que aparece — e é o
    teste que não existia quando os sete se acumularam.
    """
    invalido = Athlete(name="Phantom Duplicate", elo=1000.0)
    titular = Athlete(name="Real Person", elo=1500.0)
    session.add_all([invalido, titular])
    session.flush()
    for a in (invalido, titular):
        session.add(Graph(owner_kind="athlete", owner_id=a.id, user_elo=a.elo))
    session.flush()

    remove_athlete(invalido, session, reason=AthleteRemovalReason.INVALID_DATA)
    remove_athlete(titular, session, reason=AthleteRemovalReason.RIGHTS_REQUEST)

    orphans = session.execute(
        select(Graph).where(
            Graph.owner_kind == "athlete",
            Graph.owner_id.notin_(select(Athlete.id)),
        )
    ).scalars().all()

    assert orphans == []
