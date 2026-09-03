"""Manual, verified athlete merge — the src row + its FK refs + its sequence actor_ids
all fold into dst, and the src row (and its graph) disappear.

SQLite in-memory, same shape as ``tests/test_db.py`` / ``tests/test_athlete_removal.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.models import Athlete, Graph, Match
from scripts.dedupe_athletes import merge_into
from scripts.merge_athlete import run as merge_athlete_run

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


@pytest.fixture()
def src_dst(session: Session) -> tuple[Athlete, Athlete]:
    """``src`` is the duplicate (one bout, as A and as winner); ``dst`` is the real row
    (one other bout, as opponent) that already fought a third athlete."""
    src = Athlete(name="Bianca Basilio Bronze", elo=1000.0)
    dst = Athlete(name="Bianca Basilio", elo=1200.0)
    opp = Athlete(name="Opponent One", elo=1100.0)
    session.add_all([src, dst, opp])
    session.flush()
    session.add(Graph(owner_kind="athlete", owner_id=src.id, user_elo=1000.0))
    session.add(Graph(owner_kind="athlete", owner_id=dst.id, user_elo=1200.0))
    session.flush()

    # src beat opp, and the sequence tags src as the actor.
    session.add(Match(
        athlete_a_id=src.id, athlete_b_id=opp.id, winner_id=src.id, year=2024,
        status="final",
        sequence=[
            {"label": "armbar", "type": "submission", "actor_id": src.id, "successful": True},
        ],
    ))
    # dst has its own separate bout vs opp, unrelated to src.
    session.add(Match(
        athlete_a_id=dst.id, athlete_b_id=opp.id, winner_id=dst.id, year=2023,
        status="final",
        sequence=[{"label": "guard pass", "type": "pass", "actor_id": dst.id, "successful": True}],
    ))
    session.flush()
    return src, dst


def _graph_of(session: Session, owner_id: str) -> Graph | None:
    return session.execute(
        select(Graph).where(Graph.owner_kind == "athlete", Graph.owner_id == owner_id)
    ).scalar_one_or_none()


def test_merge_into_repoints_matches_and_actor_ids(
    session: Session, src_dst: tuple[Athlete, Athlete],
) -> None:
    src, dst = src_dst
    src_id, dst_id = src.id, dst.id

    stats = merge_into(session, src, dst, dry_run=False)

    assert stats.matches_repointed == 1  # only src's own bout touches src
    assert stats.seq_entries_fixed == 1
    assert stats.self_matches_deleted == 0

    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 2
    for m in matches:
        assert src_id not in (m.athlete_a_id, m.athlete_b_id, m.winner_id)
        for e in m.sequence or []:
            assert e.get("actor_id") != src_id

    the_repointed = next(m for m in matches if m.year == 2024)
    assert the_repointed.athlete_a_id == dst_id
    assert the_repointed.winner_id == dst_id
    assert the_repointed.sequence[0]["actor_id"] == dst_id

    # src row + its graph are gone; dst's graph survives.
    assert session.get(Athlete, src_id) is None
    assert _graph_of(session, src_id) is None
    assert _graph_of(session, dst_id) is not None


def test_merge_into_drops_resulting_self_match(session: Session) -> None:
    src = Athlete(name="Dup", elo=1000.0)
    dst = Athlete(name="Real", elo=1200.0)
    session.add_all([src, dst])
    session.flush()
    session.add(Graph(owner_kind="athlete", owner_id=src.id, user_elo=1000.0))
    session.flush()
    # src already fought dst directly — repointing src -> dst makes this a self-match.
    session.add(Match(
        athlete_a_id=src.id, athlete_b_id=dst.id, winner_id=dst.id, year=2024, status="final",
    ))
    session.flush()

    stats = merge_into(session, src, dst, dry_run=False)

    assert stats.self_matches_deleted == 1
    assert list(session.execute(select(Match)).scalars()) == []


def test_merge_into_dry_run_does_not_mutate(
    session: Session, src_dst: tuple[Athlete, Athlete],
) -> None:
    src, dst = src_dst
    src_id = src.id

    stats = merge_into(session, src, dst, dry_run=True)

    assert stats.matches_repointed == 1
    assert stats.seq_entries_fixed == 1
    assert session.get(Athlete, src_id) is not None
    matches = list(session.execute(select(Match)).scalars())
    assert any(src_id in (m.athlete_a_id, m.athlete_b_id, m.winner_id) for m in matches)


def test_merge_athlete_cli_guards_self_merge() -> None:
    # Checked before any session opens — no DB fixture needed.
    same_id = "11111111-1111-1111-1111-111111111111"
    assert merge_athlete_run(same_id, same_id, dry_run=True, rename=None) == 1


def test_merge_athlete_cli_refuses_anonymized_src(engine: Engine, session: Session) -> None:
    from datetime import UTC, datetime

    from sqlalchemy.orm import sessionmaker

    src = Athlete(name="[anonymized]", elo=1000.0)
    src.anonymized_at = datetime.now(UTC)
    dst = Athlete(name="Real", elo=1000.0)
    session.add_all([src, dst])
    session.commit()  # run() opens its OWN session — must be visible there

    # run() gets its session from `db.base.get_session_factory()`; point that at THIS
    # test's in-memory engine so the guard runs against real rows without touching Postgres.
    import db.base

    orig = db.base._SessionLocal
    db.base._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        assert merge_athlete_run(src.id, dst.id, dry_run=True, rename=None) == 1
    finally:
        db.base._SessionLocal = orig
