"""Per-discipline boards — event classifier + per-board leaderboard build (SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from analysis.discipline import athlete_disciplines, match_discipline
from analysis.rating_v2.config import SITE_RATING_RUN_ID
from db.models import Athlete, AthleteRatingStateV2, Match, RatingEngineRun


@pytest.fixture()
def session():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models
    from db.base import Base
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]

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


def _v2_rating(session: Session, athlete: Athlete, rating: float, rd: float = 50.0,
               run_id: str = SITE_RATING_RUN_ID) -> None:
    """Seed one athlete_rating_states_v2 row (+ its parent run, once) for the pinned run
    ``ranked_pools``/``build_elo`` read by default (ADR-02)."""
    if session.get(RatingEngineRun, run_id) is None:
        session.add(RatingEngineRun(id=run_id, engine_version="glicko2-v1-shadow",
                                     status="complete"))
        session.flush()
    session.add(AthleteRatingStateV2(run_id=run_id, athlete_id=athlete.id, rating=rating,
                                      rating_deviation=rd, volatility=0.06))
    session.flush()


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
    # Grappling now reads rating_v2 (wave 9) -- rank_elo above is unused for this pool,
    # kept only for realism.
    _v2_rating(session, g1, 1900.0)
    _v2_rating(session, g2, 1700.0)
    # MMA rates from the UFC Elo CSV (real names required); unmatched → unranked.
    m1 = _athlete(session, "Khabib Nurmagomedov")
    m2 = _athlete(session, "Dan Hooker")
    m3 = _athlete(session, "Kiyoshi Tamura")  # PRIDE-only, not in the UFC CSV
    _match(session, g1, g2, "ADCC 2024")
    _match(session, m1, m2, "UFC 325")
    _match(session, m3, m2, None)

    boards = build_elo(session, limit=8, min_bouts=0)
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
    for i, g in enumerate(gs):
        _v2_rating(session, g, 1000.0 + i)  # grappling pool reads V2, same order as rank_elo
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
    for i, g in enumerate(gs):
        _v2_rating(session, g, 1000.0 + i)  # grappling pool reads V2, same order as rank_elo
    for i in range(0, 6, 2):
        m = _match(session, gs[i], gs[i + 1], "ADCC 2024")
        m.sequence = [{"label": "Guard Pass", "type": "pass", "actor_id": gs[i].id,
                       "successful": True}]
    session.flush()

    profile = build_style_profile(gs[0], session)
    assert profile["fighter"]["elo_percentile"] is not None  # pool of 6 >= floor


# ── Wave 9: grappling pool migrated to rating_v2 (analysis/discipline.py:ranked_pools) ──

def test_ranked_pools_grappling_reads_v2_not_rank_elo(session: Session) -> None:
    """rank_elo says Galvao > Gordon; the pinned V2 run says the opposite. The pool must
    follow V2 -- proves the seam actually switched source, not just plumbing."""
    from analysis.discipline import ranked_pools

    gordon = _athlete(session, "Gordon", rank_elo=1000.0)
    galvao = _athlete(session, "Galvao", rank_elo=2000.0)
    _match(session, gordon, galvao, "ADCC 2024")
    _v2_rating(session, gordon, 1900.0)
    _v2_rating(session, galvao, 1700.0)

    rows = ranked_pools(session)["grappling"]
    assert [name for _, name, _ in rows] == ["Gordon", "Galvao"]


def test_ranked_pools_mma_and_wrestling_unaffected_by_v2(session: Session) -> None:
    """V2 only rated the grappling corpus -- mma/wrestling sources are untouched even
    when a pinned run exists and has rows for those athletes' ids (it never does in
    practice, but the pool must not accidentally consult V2 for them)."""
    from analysis.discipline import ranked_pools

    m1 = _athlete(session, "Khabib Nurmagomedov")
    m2 = _athlete(session, "Dan Hooker")
    _match(session, m1, m2, "UFC 325")
    w1 = _athlete(session, "W1", elo=1100.0)
    w2 = _athlete(session, "W2", elo=1050.0)
    _match(session, w1, w2, "NCAA 2026")
    # Seed unrelated V2 rows -- if the mma/wrestling branch ever started reading V2 by
    # accident this would silently pass, so also seed a decoy for m1 to catch it.
    _v2_rating(session, m1, 1234.0)

    pools = ranked_pools(session)
    assert [n for _, n, _ in pools["mma"]] == ["Khabib Nurmagomedov", "Dan Hooker"]
    assert [n for _, n, _ in pools["wrestling"]] == ["W1", "W2"]


def test_ranked_pools_athlete_absent_from_run_not_in_pool(session: Session) -> None:
    """No V2 row for an athlete in the pinned run -> absent from the pool, same as an
    athlete with no rank_elo today. Not seeded with a fallback rating."""
    from analysis.discipline import ranked_pools

    scored = _athlete(session, "Scored", rank_elo=1000.0)
    unscored = _athlete(session, "Unscored", rank_elo=1500.0)  # has rank_elo, no V2 row
    _match(session, scored, unscored, "ADCC 2024")
    _v2_rating(session, scored, 1800.0)

    ids = {aid for aid, _, _ in ranked_pools(session)["grappling"]}
    assert scored.id in ids
    assert unscored.id not in ids


def test_ranked_pools_no_run_pinned_falls_back_to_v1(session: Session) -> None:
    """run_id=None -- ranked_pools must still work, sourcing grappling from rank_elo."""
    from analysis.discipline import ranked_pools

    a = _athlete(session, "A", rank_elo=1900.0)
    b = _athlete(session, "B", rank_elo=1700.0)
    _match(session, a, b, "ADCC 2024")
    # no _v2_rating() calls at all -- no run exists in this session

    rows = ranked_pools(session, run_id=None)["grappling"]
    assert [(name, rating) for _, name, rating in rows] == [("A", 1900.0), ("B", 1700.0)]


def test_build_elo_excludes_high_rd_athlete_kept_in_pool(session: Session) -> None:
    """build_elo's published top-N drops an RD>200 athlete that ranked_pools still
    contains (Wave 9 asymmetry: confidence filters at publication, not in the pool)."""
    from analysis.discipline import ranked_pools
    from export.site_data import SITE_MIN_CONFIDENCE_RD, build_elo

    confident = _athlete(session, "Confident", rank_elo=1000.0)
    uncertain = _athlete(session, "Uncertain", rank_elo=900.0)
    _match(session, confident, uncertain, "ADCC 2024")
    _v2_rating(session, confident, 1900.0, rd=SITE_MIN_CONFIDENCE_RD)  # on the cut -> kept
    _v2_rating(session, uncertain, 1850.0, rd=SITE_MIN_CONFIDENCE_RD + 1)  # over -> dropped

    pool_names = {name for _, name, _ in ranked_pools(session)["grappling"]}
    assert pool_names == {"Confident", "Uncertain"}  # the denominator keeps both

    board_names = [row[1] for row in build_elo(session, min_bouts=0)["grappling"]]
    assert board_names == ["Confident"]  # the published board drops the uncertain one


def test_build_elo_bout_floor_drops_a_thin_record(session: Session) -> None:
    """The board's second cut: enough RECORD to be ranked, which RD cannot express.

    RD conflates "few bouts" with "many bouts, inactive lately" — measured on the pinned
    prod run, RD<=200 alone seated 3- and 4-bout athletes at #5-#8 while #1 had 114. Here
    `thin` is the HIGHER-rated athlete with an impeccable RD, and is still kept out of the
    published board purely for lack of record, while staying in the pool that feeds
    percentiles.
    """
    from analysis.discipline import ranked_pools
    from export.site_data import build_elo

    deep = _athlete(session, "Deep Record", rank_elo=1000.0)
    thin = _athlete(session, "Thin Record", rank_elo=900.0)
    filler = _athlete(session, "Filler", rank_elo=800.0)
    for _ in range(3):
        _match(session, deep, filler, "ADCC 2024")
    _match(session, thin, filler, "ADCC 2024")
    _v2_rating(session, deep, 1800.0, rd=50.0)
    _v2_rating(session, thin, 1900.0, rd=50.0)   # rated HIGHER, and perfectly confident
    _v2_rating(session, filler, 1000.0, rd=50.0)

    pool_names = {name for _, name, _ in ranked_pools(session)["grappling"]}
    assert "Thin Record" in pool_names, "the percentile denominator keeps everyone"

    board = [row[1] for row in build_elo(session, min_bouts=3)["grappling"]]
    assert board[0] == "Deep Record", "the thin record does not outrank a real one"
    assert "Thin Record" not in board
    # and with the floor off, the higher rating wins — proving the floor is what excluded it
    assert build_elo(session, min_bouts=0)["grappling"][0][1] == "Thin Record"


def test_v2_run_overrides_the_untagged_event_guess(session: Session) -> None:
    """An untagged (`event=None`) career dump reads as mma, but the tag carries no
    discipline information — in prod those 124 bouts are Khabib's MMA career AND Leandro
    Lo's grappling career. Presence in the pinned V2 run is what breaks the tie, because
    the V2 corpus is grappling-only by construction.
    """
    from analysis.discipline import athlete_disciplines, ranked_pools

    grappler = _athlete(session, "Untagged Grappler", rank_elo=1000.0)
    fighter = _athlete(session, "Untagged Fighter", rank_elo=900.0)
    _match(session, grappler, fighter, None)  # untagged -> both guessed mma
    assert athlete_disciplines(session, run_id=None)[grappler.id] == "mma"

    _v2_rating(session, grappler, 1900.0, rd=50.0)  # only the grappler was rated by V2

    disc = athlete_disciplines(session)
    assert disc[grappler.id] == "grappling", "a V2 rating is grappling evidence"
    assert disc[fighter.id] == "mma", "absence from the run leaves the guess alone"

    pools = ranked_pools(session)
    assert [n for _, n, _ in pools["grappling"]] == ["Untagged Grappler"]


def test_definite_tag_beats_the_v2_override(session: Session) -> None:
    """A real UFC bout is positive evidence; a V2 row is an inference from absence. The
    tag wins, so a UFC fighter can never be pulled onto the grappling board by a stray
    rating row."""
    from analysis.discipline import athlete_disciplines

    fighter = _athlete(session, "Tagged Fighter", rank_elo=900.0)
    other = _athlete(session, "Opponent", rank_elo=800.0)
    _match(session, fighter, other, "UFC 325")
    _match(session, fighter, other, None)  # plus an untagged career-dump bout
    _v2_rating(session, fighter, 1900.0, rd=50.0)

    assert athlete_disciplines(session)[fighter.id] == "mma"
