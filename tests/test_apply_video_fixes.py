"""scripts.apply_video_fixes — slug->match resolution + video_url set (SQLite, no prod)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def session():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models with Base.metadata
    from db.base import Base

    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]

    engine = create_engine(_SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, checkfirst=True)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(engine)


def test_apply_video_fixes_resolves_and_sets_video_url(session, monkeypatch):
    import scripts.apply_video_fixes as avf
    from db.models import Athlete, Match

    a1, a2 = Athlete(id=str(uuid.uuid4()), name="Aden Valencia"), Athlete(
        id=str(uuid.uuid4()), name="Shayne Van Ness"
    )
    b1, b2 = Athlete(id=str(uuid.uuid4()), name="Andrew Tackett"), Athlete(
        id=str(uuid.uuid4()), name="P. Barch"
    )
    session.add_all([a1, a2, b1, b2])
    session.flush()

    # NCAA-2026 match: swap-fix case.
    m_swap = Match(
        id=str(uuid.uuid4()), athlete_a_id=a1.id, athlete_b_id=a2.id, year=2026,
        video_url="https://www.youtube.com/watch?v=MlancZWswSk&t=1278s",
    )
    # WNO-2025 match: strip-fix case (opponent stored in reversed a/b order — pair match
    # must be order-independent).
    m_strip = Match(
        id=str(uuid.uuid4()), athlete_a_id=b2.id, athlete_b_id=b1.id, year=2025,
        video_url="https://www.youtube.com/watch?v=7AyoRIBXvUc&t=3406s",
    )
    session.add_all([m_swap, m_strip])
    session.commit()

    @contextmanager
    def fake_db_session():
        yield session
        session.commit()

    monkeypatch.setattr("db.base.db_session", fake_db_session)
    monkeypatch.setattr(
        avf,
        "FIXES",
        {
            "aden-valencia-vs-shayne-van-ness-2026": avf.FIXES[
                "aden-valencia-vs-shayne-van-ness-2026"
            ],
            "andrew-tackett-vs-p-barch-2025": avf.FIXES["andrew-tackett-vs-p-barch-2025"],
        },
    )

    assert avf.run(dry_run=False) == 0

    session.refresh(m_swap)
    session.refresh(m_strip)
    assert m_swap.video_url == "https://www.youtube.com/watch?v=jT5wAzLN014&t=1278s"
    assert m_strip.video_url is None


def test_apply_gaudio_ts_shifts_by_offset():
    import scripts.apply_video_fixes as avf
    from db.models import Match

    match = Match(
        id=str(uuid.uuid4()), athlete_a_id="a", athlete_b_id="b", year=2025,
        sequence=[
            {"label": "Sweep", "type": "sweep", "actor_id": "a", "ts": 8139},
            {"label": "Armbar", "type": "submission", "actor_id": "a", "ts": 8890},
            {"label": "Reset", "type": "reset", "actor_id": "a"},  # no ts — left alone
        ],
    )

    applied = avf._apply_gaudio_ts(match, dry_run=False)

    assert applied is True
    assert [e.get("ts") for e in match.sequence] == [54, 805, None]


def test_apply_gaudio_ts_bails_out_on_negative_offset():
    import scripts.apply_video_fixes as avf
    from db.models import Match

    match = Match(
        id=str(uuid.uuid4()), athlete_a_id="a", athlete_b_id="b", year=2025,
        sequence=[{"label": "Sweep", "type": "sweep", "actor_id": "a", "ts": 100}],
    )

    applied = avf._apply_gaudio_ts(match, dry_run=False)

    assert applied is False
    assert match.sequence[0]["ts"] == 100  # untouched
