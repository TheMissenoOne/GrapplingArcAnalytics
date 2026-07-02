"""Per-discipline boards — event classifier + per-board leaderboard build (SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from analysis.discipline import athlete_disciplines, match_discipline
from db.models import Athlete, Match


@pytest.fixture()
def session():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models
    from db.base import Base
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng, checkfirst=True)
    with Session(eng) as s:
        yield s
    Base.metadata.drop_all(eng)


def _athlete(session: Session, name: str, elo: float = 1000.0,
             rank_elo: float | None = None) -> Athlete:
    a = Athlete(name=name, elo=elo, rank_elo=rank_elo, belt="black")
    session.add(a)
    session.flush()
    return a


def _match(session: Session, a: Athlete, b: Athlete, event: str | None,
           status: str = "final") -> Match:
    m = Match(athlete_a_id=a.id, athlete_b_id=b.id, winner_id=a.id,
              event=event, win_type="DECISION", status=status, sequence=[])
    session.add(m)
    session.flush()
    return m


def test_match_discipline() -> None:
    assert match_discipline(None) == "mma"
    assert match_discipline("UFC 294") == "mma"
    assert match_discipline("UFC Fight Night") == "mma"
    assert match_discipline("NCAA 2026") == "wrestling"
    assert match_discipline("ADCC 2024") == "grappling"
    assert match_discipline("WNO 31") == "grappling"


def test_athlete_disciplines_majority_tie_and_drafts(session: Session) -> None:
    x = _athlete(session, "Crossover")
    opp = _athlete(session, "Opp")
    _match(session, x, opp, "UFC 300")
    _match(session, x, opp, "UFC 301")
    _match(session, x, opp, "ADCC 2024")
    # draft must not count (would flip the majority to grappling)
    _match(session, x, opp, "ADCC 2025", status="draft")
    _match(session, x, opp, "WNO 31", status="draft")

    tie = _athlete(session, "Tied")
    _match(session, tie, opp, "UFC 299")
    _match(session, tie, opp, "CJI")

    wrestler = _athlete(session, "Wrestler")
    _match(session, wrestler, opp, None)  # untagged (defaults to mma)
    _match(session, wrestler, opp, "NCAA 2026")

    d = athlete_disciplines(session)
    assert d[x.id] == "mma"
    assert d[tie.id] == "grappling"      # tie → grappling
    assert d[wrestler.id] == "wrestling"  # definite tag beats the None default


def test_build_elo_per_board(session: Session) -> None:
    from analysis.discipline import ufc_elo_by_key
    from analysis.names import athlete_key
    from export.site_data import build_elo

    g1 = _athlete(session, "Gordon", rank_elo=1343.0)
    g2 = _athlete(session, "Galvao", rank_elo=1200.0)
    # MMA rates from the UFC Elo CSV (real names required); unmatched → unranked.
    m1 = _athlete(session, "Khabib Nurmagomedov")
    m2 = _athlete(session, "Dan Hooker")
    m3 = _athlete(session, "Kiyoshi Tamura")  # PRIDE-only, not in the UFC CSV
    _match(session, g1, g2, "ADCC 2024")
    _match(session, m1, m2, "UFC 325")
    _match(session, m3, m2, None)

    boards = build_elo(session, limit=8)
    assert set(boards) == {"grappling", "mma", "wrestling"}
    assert boards["wrestling"] == []
    assert boards["grappling"][0][:3] == ["1", "Gordon", "100%"]
    assert boards["grappling"][1][1] == "Galvao"
    ufc = ufc_elo_by_key()
    assert ufc[athlete_key("Khabib Nurmagomedov")] > ufc[athlete_key("Dan Hooker")]
    assert [r[1] for r in boards["mma"]] == ["Khabib Nurmagomedov", "Dan Hooker"]
    assert boards["mma"][0][2] == "100%"
    for rows in boards.values():
        for r in rows:
            assert r[2].endswith("%")  # never a raw rating


def test_standings_small_pool_and_default_elo_unranked(session: Session) -> None:
    from export.site_data import _elo_standings

    # 6 grapplers (>= floor), 2 grown wrestlers (< floor of 5), 1 never-replayed wrestler
    gs = [_athlete(session, f"G{i}", rank_elo=1000.0 + i) for i in range(6)]
    for i in range(0, 6, 2):
        _match(session, gs[i], gs[i + 1], "ADCC 2024")
    w1 = _athlete(session, "W1", elo=1100.0)
    w2 = _athlete(session, "W2", elo=1050.0)
    w3 = _athlete(session, "W3")  # elo stays at the 1000.0 column default
    _match(session, w1, w2, "NCAA 2026")
    _match(session, w3, w2, "NCAA 2025")

    from analysis.discipline import ranked_pools
    assert w3.id not in {aid for aid, _, _ in ranked_pools(session)["wrestling"]}

    pct = _elo_standings(session)
    assert all(g.id in pct for g in gs)
    assert w1.id not in pct and w2.id not in pct  # tiny pool → unranked
    assert pct[gs[5].id] == min(pct.values())  # highest rank_elo → best percentile


def test_build_style_profile_pool_scoped(session: Session) -> None:
    """End-to-end smoke: the dossier profile builds against a discipline pool
    (regression: dict(session.execute(...)) treated the Result as a mapping)."""
    from analysis.style_profile import build_style_profile

    gs = [_athlete(session, f"P{i}", rank_elo=1000.0 + i) for i in range(6)]
    for i in range(0, 6, 2):
        m = _match(session, gs[i], gs[i + 1], "ADCC 2024")
        m.sequence = [{"label": "Guard Pass", "type": "pass", "actor_id": gs[i].id,
                       "successful": True}]
    session.flush()

    profile = build_style_profile(gs[0], session)
    assert profile["fighter"]["elo_percentile"] is not None  # pool of 6 >= floor
