"""scripts.import_user_bundle — deterministic match->session conversion, actor mapping,
outcome, dump dedupe, and full SessionState key coverage (SQLite, no prod)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scripts.import_user_bundle import (
    convert_athlete_matches,
    dedupe_sessions_by_id,
    match_to_session,
)

_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def session():
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    import db.models  # noqa: F401 — registers all ORM models with Base.metadata
    from db.base import Base

    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]

    engine = create_engine(_SQLITE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, checkfirst=True)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(engine)


# ── Every non-optional key of the App's SessionState (src/types/session.ts,
# GrapplingArcApp, read directly for this list — id/createdAt/updatedAt/showModal/
# duration/topicType/topicInput/topicAssocPos/topics/goal/round/rounds/reflection/
# media/videos/projectId/pendingGraphUpdates). `title`/`classSessionId` are `?` on
# the type and deliberately excluded here. ─────────────────────────────────────
SESSION_STATE_REQUIRED_KEYS = frozenset({
    "id", "createdAt", "updatedAt", "showModal", "duration", "topicType", "topicInput",
    "topicAssocPos", "topics", "goal", "round", "rounds", "reflection", "media", "videos",
    "projectId", "pendingGraphUpdates",
})


class _FakeMatch:
    """Duck-types what ``db.repository._perspective_view``/``bout_flags`` need."""

    def __init__(self, id, athlete_a_id, athlete_b_id, sequence, winner_id=None, year=2020,
                 win_type=None, created_at=None):
        self.id = id
        self.athlete_a_id = athlete_a_id
        self.athlete_b_id = athlete_b_id
        self.sequence = sequence
        self.winner_id = winner_id
        self.year = year
        self.win_type = win_type
        self.created_at = created_at


def _two_sided_sequence(a_id: str, b_id: str) -> list[dict]:
    return [
        {"label": "Closed Guard", "type": "guard", "actor_id": a_id, "successful": True},
        {"label": "Guard Pass", "type": "pass", "actor_id": b_id},
        {"label": "Armbar", "type": "submission", "actor_id": a_id, "successful": False},
    ]


def test_match_to_session_deterministic():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    match = _FakeMatch(str(uuid.uuid4()), a, b, _two_sided_sequence(a, b), winner_id=a)

    s1 = match_to_session(match, a, "Opponent Name")
    s2 = match_to_session(match, a, "Opponent Name")

    assert s1["id"] == s2["id"]
    assert s1["id"] == f"s-match-{match.id[:12]}"
    assert s1["rounds"][0]["entries"] == s2["rounds"][0]["entries"]
    entry_ids = [e["id"] for e in s1["rounds"][0]["entries"]]
    assert entry_ids == sorted(set(entry_ids), key=entry_ids.index)  # unique + stable order


def test_match_to_session_actor_mapping_you_partner():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    match = _FakeMatch(str(uuid.uuid4()), a, b, _two_sided_sequence(a, b))

    s = match_to_session(match, a, "Opponent Name")
    actors = {e["actor"] for e in s["rounds"][0]["entries"]}
    assert actors <= {"you", "partner"}  # never the raw perspective value 'opponent'
    assert "you" in actors and "partner" in actors

    entries = s["rounds"][0]["entries"]
    assert entries[0]["actor"] == "you"     # a's guard
    assert entries[1]["actor"] == "partner"  # b's pass
    assert entries[2]["successful"] is False  # tri-state preserved when present


@pytest.mark.parametrize(
    "winner_id, expected",
    [("A", "succeeded"), ("B", "failed"), (None, None)],
)
def test_match_to_session_outcome(winner_id, expected):
    a, b = "A", "B"
    winner = a if winner_id == "A" else b if winner_id == "B" else None
    match = _FakeMatch(str(uuid.uuid4()), a, b, _two_sided_sequence(a, b), winner_id=winner)

    s = match_to_session(match, a, "Opponent Name")
    assert s["rounds"][0]["outcome"] == expected


def test_match_to_session_has_every_required_session_state_key():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    match = _FakeMatch(str(uuid.uuid4()), a, b, _two_sided_sequence(a, b), winner_id=a)

    s = match_to_session(match, a, "Opponent Name")
    missing = SESSION_STATE_REQUIRED_KEYS - s.keys()
    assert not missing, f"synthetic session missing required SessionState keys: {missing}"


def test_dedupe_sessions_by_id_keeps_last_occurrence():
    sessions = [
        {"id": "s-1", "title": "first"},
        {"id": "s-2", "title": "only"},
        {"id": "s-1", "title": "second"},
        {"id": "s-1", "title": "third"},
    ]
    deduped, dup_ids = dedupe_sessions_by_id(sessions)

    assert dup_ids == ["s-1"]
    assert [s["id"] for s in deduped] == ["s-1", "s-2"]  # original first-seen ORDER kept
    assert next(s for s in deduped if s["id"] == "s-1")["title"] == "third"  # LAST wins


def test_convert_athlete_matches_skips_unreliable_bouts(session):
    from db.models import Athlete, Match

    athlete = Athlete(id=str(uuid.uuid4()), name="Bruno")
    opponent_reliable = Athlete(id=str(uuid.uuid4()), name="Reliable Opponent")
    opponent_one_sided = Athlete(id=str(uuid.uuid4()), name="One Sided Opponent")
    session.add_all([athlete, opponent_reliable, opponent_one_sided])
    session.flush()

    reliable = Match(
        id=str(uuid.uuid4()), athlete_a_id=athlete.id, athlete_b_id=opponent_reliable.id,
        winner_id=athlete.id, year=2020,
        sequence=_two_sided_sequence(athlete.id, opponent_reliable.id),
    )
    # One-sided: >= MIN_EVENTS_FOR_ONE_SIDED (6) events, all filed under the SAME athlete —
    # the opponent's side was never recorded, so role cannot be trusted.
    one_sided_seq = [
        {"label": "Takedown", "type": "takedown", "actor_id": athlete.id},
        {"label": "Pass", "type": "pass", "actor_id": athlete.id},
        {"label": "Mount", "type": "control", "actor_id": athlete.id},
        {"label": "Armbar", "type": "submission", "actor_id": athlete.id, "successful": False},
        {"label": "Sweep", "type": "sweep", "actor_id": athlete.id},
        {"label": "Choke", "type": "submission", "actor_id": athlete.id, "successful": True},
    ]
    one_sided = Match(
        id=str(uuid.uuid4()), athlete_a_id=athlete.id, athlete_b_id=opponent_one_sided.id,
        winner_id=athlete.id, year=2020, sequence=one_sided_seq,
    )
    session.add_all([reliable, one_sided])
    session.commit()

    converted, skipped = convert_athlete_matches(athlete.id, session)

    assert [s["id"] for s in converted] == [f"s-match-{reliable.id[:12]}"]
    assert [m.id for m, _ in skipped] == [one_sided.id]
