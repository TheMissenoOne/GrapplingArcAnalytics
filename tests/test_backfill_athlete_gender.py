"""scripts/backfill_athlete_gender — evidence-driven, dry-run by default, never guesses 'm'."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.models import Athlete
from scripts.backfill_athlete_gender import backfill, women_evidence_names

# SQLite in-memory, same shape as test_athlete_removal.py — model round-trips need no Postgres.
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


def test_women_evidence_names_pulls_from_all_four_sources() -> None:
    names = women_evidence_names()
    # ADCC 2026 roster
    assert "Livia Barasine" in names
    # Named women's-event dumps
    assert "Bia Mesquita" in names  # ADCC 2022 - Women
    assert "Kendall Reusing" in names  # Polaris 36 (women's superfights)


def test_dry_run_reports_without_writing(session: Session) -> None:
    a = Athlete(name="Bia Mesquita")
    session.add(a)
    session.flush()

    stats = backfill(session, {"Bia Mesquita"}, apply=False)
    assert stats["marked"] == 1
    session.refresh(a)
    assert a.gender is None  # dry run never writes


def test_apply_marks_the_matching_athlete_f(session: Session) -> None:
    a = Athlete(name="Bia Mesquita")
    session.add(a)
    session.flush()

    stats = backfill(session, {"Bia Mesquita"}, apply=True)
    assert stats["marked"] == 1
    session.refresh(a)
    assert a.gender == "f"


def test_never_marks_m_and_flags_existing_m_as_conflict(session: Session) -> None:
    a = Athlete(name="Bia Mesquita", gender="m")
    session.add(a)
    session.flush()

    stats = backfill(session, {"Bia Mesquita"}, apply=True)
    assert stats["conflict_m"] == 1
    assert stats["marked"] == 0
    session.refresh(a)
    assert a.gender == "m"  # never silently overwritten


def test_unmatched_name_counts_not_found(session: Session) -> None:
    stats = backfill(session, {"Nobody In The DB"}, apply=True)
    assert stats["not_found"] == 1
    assert stats["marked"] == 0
