"""DB layer tests — SQLite in-memory for model round-trips (no Postgres needed)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# Point at SQLite so these tests run without Postgres
_SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture()
def engine():
    """In-memory SQLite engine with all tables created."""

    # SQLite compat: render JSONB/UUID as TEXT
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


def _make_bundle(user_id: str | None = None) -> dict:
    uid = user_id or str(uuid.uuid4())
    return {
        "schemaVersion": 3,
        "user": {
            "auth": {
                "id": uid,
                "fullName": "Test User",
                "beltRank": "blue",
                "beltDegrees": 2,
                "isGuest": False,
            }
        },
        "graph": {
            "userElo": 850.0,
            "nodes": [
                {
                    "id": "n1",
                    "label": "Closed Guard",
                    "type": "position",
                    "data": {"type": "guard", "computedElo": 900.0, "usageCount": 5, "trend": "core"},  # noqa: E501
                },
                {
                    "id": "n2",
                    "label": "Armbar",
                    "type": "technique",
                    "data": {"type": "submission", "computedElo": 800.0, "usageCount": 3, "trend": "emerging"},  # noqa: E501
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "n1",
                    "target": "n2",
                    "data": {"elo": 820.0, "setup": "hip escape"},
                }
            ],
        },
    }


def test_node_key_is_normalized() -> None:
    """The node-key contract, asserted where it lives.

    This used to go through ``upsert_graph_from_bundle`` — the offline file-import path,
    removed with ``db.ingest``. The contract it was really checking is
    ``_normalize_name``'s, which the App mirrors char-for-char in ``normalizeLabel``, so
    it is checked directly instead of through a writer that happened to call it.
    """
    from analysis.names import _normalize_name

    assert _normalize_name("  Closed Guard!!  ") == "closed guard"


def test_athlete_graph_upsert(session):
    from analysis.athlete_graph import build_athlete_graph
    from db.repository import upsert_graph_from_athlete_graph

    sessions_payload = [
        {
            "topics": [],
            "rounds": [
                {
                    "entries": [
                        {"label": "Back Take", "type": "position", "actor": "you"},
                        {"label": "Rear Naked Choke", "type": "submission", "actor": "you"},
                    ]
                }
            ],
        }
    ]
    athlete_id = str(uuid.uuid4())
    graph = build_athlete_graph("Gordon Ryan", sessions_payload)
    upsert_graph_from_athlete_graph(graph, athlete_id, session)
    session.commit()

    from db.models import TechniqueNode

    nodes = list(session.execute(select(TechniqueNode)).scalars())
    keys = {n.node_key for n in nodes}
    assert "back take" in keys
    assert "rear naked choke" in keys


def test_athlete_graph_upsert_prunes_stale_edges(session):
    """A re-derivation that drops a technique must delete its edge, not just stop
    updating it — root cause of Gordon's 172-persisted-vs-119-current drift."""
    from analysis.athlete_graph import build_athlete_graph
    from db.repository import upsert_graph_from_athlete_graph

    athlete_id = str(uuid.uuid4())
    full_sessions = [
        {
            "topics": [],
            "rounds": [
                {
                    "entries": [
                        {"label": "Back Take", "type": "position", "actor": "you"},
                        {"label": "Rear Naked Choke", "type": "submission", "actor": "you"},
                        {"label": "Armbar", "type": "submission", "actor": "you"},
                    ]
                }
            ],
        }
    ]
    graph = build_athlete_graph("Gordon Ryan", full_sessions)
    graph_id = upsert_graph_from_athlete_graph(graph, athlete_id, session)
    session.commit()

    from db.models import GraphEdge

    edges = list(session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars())
    keys_before = {e.edge_key for e in edges}
    assert len(edges) == 2  # back take→RNC, RNC→armbar

    # Re-derive with the armbar chain dropped (e.g. it no longer shows up in replay).
    shrunk_sessions = [
        {
            "topics": [],
            "rounds": [
                {
                    "entries": [
                        {"label": "Back Take", "type": "position", "actor": "you"},
                        {"label": "Rear Naked Choke", "type": "submission", "actor": "you"},
                    ]
                }
            ],
        }
    ]
    graph2 = build_athlete_graph("Gordon Ryan", shrunk_sessions)
    upsert_graph_from_athlete_graph(graph2, athlete_id, session)
    session.commit()

    edges_after = list(
        session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars()
    )
    keys_after = {e.edge_key for e in edges_after}
    assert len(edges_after) == 1
    assert keys_after < keys_before  # the armbar edge is gone, not just unchanged
    assert all("armbar" not in k for k in keys_after)


def test_register_matches_bulk_inserts_and_registers_techniques(session):
    """The dump importer's batched insert path: one bulk statement for all bouts'
    Match rows + one merged technique-registration call, instead of per-bout."""
    from db.models import Match, TechniqueNode
    from db.repository import register_matches_bulk

    rows = [
        dict(
            athlete_a_id=str(uuid.uuid4()), athlete_b_id=str(uuid.uuid4()),
            winner_id=None, win_type="SUBMISSION", submission="Armbar",
            event="Test Event", year=2024, weight_class=None, stage=None,
            sequence=[{"label": "Armbar", "type": "submission", "actor_id": "x"}],
            created_by=None, video_url=None, timeline=None,
        ),
        dict(
            athlete_a_id=str(uuid.uuid4()), athlete_b_id=str(uuid.uuid4()),
            winner_id=None, win_type="SUBMISSION", submission="Heel Hook",
            event="Test Event", year=2025, weight_class=None, stage=None,
            sequence=[{"label": "Heel Hook", "type": "submission", "actor_id": "y"}],
            created_by=None, video_url=None, timeline=None,
        ),
    ]
    register_matches_bulk(rows, session)
    session.commit()

    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 2
    assert all(m.status == "final" for m in matches)

    techs = {t.node_key for t in session.execute(select(TechniqueNode)).scalars()}
    assert "armbar" in techs
    assert "heel hook" in techs


def test_run_dump_defers_replay_until_after_all_inserts(session, monkeypatch):
    """Root fix for redundant replays: with ``replay=False`` + a shared
    ``out_participants`` set, an athlete appearing in two separate dumps accumulates
    once, and a single later replay pass touches them exactly once (not once per dump)."""
    import contextlib

    import db.base as db_base
    import db.repository as repo
    from db.models import Athlete, Match
    from scripts import dump_import

    @contextlib.contextmanager
    def _fake_db_session():
        yield session

    monkeypatch.setattr(db_base, "db_session", _fake_db_session)

    replay_calls: list[str] = []
    monkeypatch.setattr(
        repo, "replay_and_persist_athlete",
        lambda athlete, sess: replay_calls.append(athlete.id),
    )

    def _raw_dump(a_name: str, b_name: str, year: int) -> list[dict]:
        events = [
            {"label": "Guard Pull", "type": "guard", "actor": a_name},
            {"label": "Armbar", "type": "submission", "actor": a_name, "successful": True},
        ]
        return [{(a_name, year): {"winner": a_name, "method": "Submission (Armbar)",
                                   "opponent": b_name, "events": events}}]

    all_participants: set[str] = set()
    # "Craig Jones" is shared across both dumps — the redundant-replay case.
    dump_import.run_dump(
        _raw_dump("Craig Jones", "Kyle Boehm", 2024), event=None, label="A",
        replay=False, out_participants=all_participants,
    )
    dump_import.run_dump(
        _raw_dump("Craig Jones", "Nicky Rod", 2025), event=None, label="B",
        replay=False, out_participants=all_participants,
    )

    assert replay_calls == []  # deferred: no replay fired inside either run_dump call
    assert len(all_participants) == 3  # Craig + Kyle + Nicky, Craig counted once

    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 2

    # Simulate reprocess_all's single post-loop replay pass.
    for aid in all_participants:
        athlete = session.get(Athlete, aid)
        if athlete is not None:
            repo.replay_and_persist_athlete(athlete, session)

    assert len(replay_calls) == 3  # each unique athlete replayed exactly once total


def test_run_dump_batched_delete_insert_is_idempotent(session, monkeypatch):
    """Batching the per-bout delete+insert into one delete + one bulk insert must stay
    behaviorally identical: re-running the same dump replaces, never duplicates, a bout."""
    import contextlib

    import db.base as db_base
    from db.models import Match
    from scripts import dump_import

    @contextlib.contextmanager
    def _fake_db_session():
        yield session

    monkeypatch.setattr(db_base, "db_session", _fake_db_session)

    raw = [{("Craig Jones", 2024): {
        "winner": "Craig Jones", "method": "Submission (Armbar)", "opponent": "Kyle Boehm",
        "events": [
            {"label": "Guard Pull", "type": "guard", "actor": "Craig Jones"},
            {"label": "Armbar", "type": "submission", "actor": "Craig Jones", "successful": True},
        ],
    }}]

    dump_import.run_dump(raw, event=None, label="A", replay=False)
    dump_import.run_dump(raw, event=None, label="A", replay=False)  # re-run same dump

    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 1


def test_run_dump_different_concrete_events_coexist(session, monkeypatch):
    """Same pair, same year, two DIFFERENT concrete events = two physical bouts. Importing
    the second must not delete the first (measured 2026-08-25: World No-Gi 2024 clobbered
    the pair's ADCC 2024 match, 36 events lost). A None-tagged career dump still replaces
    (wildcard, the historical behavior) -- covered by the idempotency test above."""
    import contextlib

    import db.base as db_base
    from db.models import Match
    from scripts import dump_import

    @contextlib.contextmanager
    def _fake_db_session():
        yield session

    monkeypatch.setattr(db_base, "db_session", _fake_db_session)

    def raw(label):
        return [{("Morgan Black", 2024): {
            "winner": "Morgan Black", "method": "Decision", "opponent": "Brianna Ste-Marie",
            "events": [{"label": "Guard Pull", "type": "guard", "actor": "Morgan Black"}],
        }}]

    dump_import.run_dump(raw("x"), event="ADCC 2024", label="A", replay=False)
    dump_import.run_dump(raw("x"), event="World No-Gi 2024", label="B", replay=False)
    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 2, "different concrete events must coexist"
    # and re-running one of them still replaces itself, not the sibling
    dump_import.run_dump(raw("x"), event="World No-Gi 2024", label="B", replay=False)
    matches = list(session.execute(select(Match)).scalars())
    assert len(matches) == 2


def test_run_dump_fills_video_start_seconds_from_dump_offset(session, monkeypatch):
    """Q5: a dump-declared ref-block ``start`` (video-absolute) fills a NULL
    ``video_start_seconds`` at import time when nothing else has resolved one."""
    import contextlib

    import db.base as db_base
    from db.models import Match
    from scripts import dump_import

    @contextlib.contextmanager
    def _fake_db_session():
        yield session

    monkeypatch.setattr(db_base, "db_session", _fake_db_session)
    monkeypatch.setattr(dump_import, "_load_url_mapping", lambda: {})  # no URL, offset only

    raw = [{("Craig Jones", 2024): {
        "winner": "Craig Jones", "method": "Submission (Armbar)", "opponent": "Kyle Boehm",
        "start": "1:30",
        "events": [
            {"label": "Guard Pull", "type": "guard", "actor": "Craig Jones"},
            {"label": "Armbar", "type": "submission", "actor": "Craig Jones", "successful": True},
        ],
    }}]

    dump_import.run_dump(raw, event=None, label="A", replay=False)

    match = session.execute(select(Match)).scalar_one()
    assert match.video_url is None  # no URL source available — offset alone still lands
    assert match.video_start_seconds == 90
    assert match.ts_origin == "video_absolute"


def test_run_dump_preserves_hand_fixed_video_url_on_reimport(session, monkeypatch):
    """Q5: a reimport must not silently wipe a video_url applied out-of-band (the
    scripts/apply_video_fixes.py channel) even though run_dump deletes + re-inserts the row."""
    import contextlib

    import db.base as db_base
    from db.models import Match
    from scripts import dump_import

    @contextlib.contextmanager
    def _fake_db_session():
        yield session

    monkeypatch.setattr(db_base, "db_session", _fake_db_session)
    mapping = {"EVT": {"video_url": "https://youtu.be/WRONGVIDEO",
                        "matches": [{"athlete": "Craig Jones", "opponent": "Kyle Boehm",
                                     "year": 2024, "winner": "Craig Jones", "seconds": 10}]}}
    monkeypatch.setattr(dump_import, "_load_url_mapping", lambda: mapping)

    raw = [{("Craig Jones", 2024): {
        "winner": "Craig Jones", "method": "Submission (Armbar)", "opponent": "Kyle Boehm",
        "start": "1:30",
        "events": [
            {"label": "Guard Pull", "type": "guard", "actor": "Craig Jones"},
            {"label": "Armbar", "type": "submission", "actor": "Craig Jones", "successful": True},
        ],
    }}]

    dump_import.run_dump(raw, event=None, label="A", replay=False)
    match = session.execute(select(Match)).scalar_one()
    assert match.video_url == "https://youtu.be/WRONGVIDEO?t=90s"  # filled from mapping+dump

    # Hand fix applied straight to the DB, as scripts/apply_video_fixes.py would.
    match.video_url = "https://youtu.be/HANDFIXED"
    session.flush()

    dump_import.run_dump(raw, event=None, label="A", replay=False)  # reimport same dump

    match = session.execute(select(Match)).scalar_one()
    assert match.video_url == "https://youtu.be/HANDFIXED"  # survived the delete+reinsert


def test_fixture_bundle_round_trip():
    """Parse the real mock bundle fixture."""
    fixture = (
        Path(__file__).parent.parent.parent
        / "GrapplingArcApp" / "src" / "data" / "mockData" / "mock_user_bundle.json"
    )
    if not fixture.exists():
        pytest.skip("mock_user_bundle.json not found")
    from schemas.app_types import UserBundle

    with open(fixture) as f:
        data = json.load(f)
    bundle = UserBundle.from_json(data)
    assert bundle.user is not None
    assert bundle.graph is not None


# ── user_sessions / user_sync_meta (alembic 0017/0018) ──────────────────────────


def test_user_session_upsert_insert_and_update(session):
    from datetime import UTC, datetime

    from db.models import Profile, UserSession
    from db.repository import upsert_user_session

    owner_id = str(uuid.uuid4())
    session.add(Profile(id=owner_id))
    session.flush()

    t1 = datetime(2026, 7, 1, tzinfo=UTC)
    upsert_user_session(owner_id, "s-1-abc", {"exercises": []}, t1, session)
    session.commit()

    row = session.get(UserSession, "s-1-abc")
    assert row is not None
    assert row.owner_id == owner_id
    assert row.updated_at.replace(tzinfo=UTC) == t1

    # Update-on-conflict: same id, new data/updated_at overwrites in place.
    t2 = datetime(2026, 7, 2, tzinfo=UTC)
    upsert_user_session(owner_id, "s-1-abc", {"exercises": ["squat"]}, t2, session)
    session.commit()

    row = session.get(UserSession, "s-1-abc")
    assert row.data == {"exercises": ["squat"]}
    assert row.updated_at.replace(tzinfo=UTC) == t2
    assert len(list(session.execute(select(UserSession)).scalars())) == 1


def test_get_user_sessions_since_filters_by_updated_at(session):
    from datetime import UTC, datetime

    from db.models import Profile
    from db.repository import get_user_sessions_since, upsert_user_session

    owner_id = str(uuid.uuid4())
    session.add(Profile(id=owner_id))
    session.flush()

    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = datetime(2026, 7, 1, tzinfo=UTC)
    upsert_user_session(owner_id, "s-old", {}, old, session)
    upsert_user_session(owner_id, "s-new", {}, new, session)
    session.commit()

    all_sessions = get_user_sessions_since(owner_id, None, session)
    assert {s.id for s in all_sessions} == {"s-old", "s-new"}

    recent = get_user_sessions_since(owner_id, datetime(2026, 6, 1, tzinfo=UTC), session)
    assert [s.id for s in recent] == ["s-new"]


def test_sync_meta_create_and_update(session):
    from datetime import UTC, datetime

    from db.models import Profile
    from db.repository import get_sync_meta, upsert_sync_meta

    owner_id = str(uuid.uuid4())
    session.add(Profile(id=owner_id))
    session.flush()

    assert get_sync_meta(owner_id, session) is None

    upsert_sync_meta(owner_id, session, session_count=3)
    session.commit()
    meta = get_sync_meta(owner_id, session)
    assert meta is not None
    assert meta.session_count == 3
    assert meta.big_sync_completed_at is None

    done_at = datetime(2026, 7, 16, tzinfo=UTC)
    upsert_sync_meta(owner_id, session, big_sync_completed_at=done_at, session_count=10)
    session.commit()
    meta = get_sync_meta(owner_id, session)
    assert meta.big_sync_completed_at.replace(tzinfo=UTC) == done_at
    assert meta.session_count == 10


# ── delete tombstones (alembic 0019) ────────────────────────────────────────────
# NB: the concurrent-push stale-write guard is a Postgres BEFORE UPDATE trigger, not
# exercised here (SQLite in-memory) — see 0019's docstring. These validate the model
# shape + that the incremental read does not filter tombstones out.


def test_user_session_deleted_at_roundtrips(session):
    from datetime import UTC, datetime

    from db.models import Profile, UserSession
    from db.repository import upsert_user_session

    owner_id = str(uuid.uuid4())
    session.add(Profile(id=owner_id))
    session.flush()

    t = datetime(2026, 7, 3, tzinfo=UTC)
    upsert_user_session(owner_id, "s-del", {}, t, session, deleted_at=t)
    session.commit()

    row = session.get(UserSession, "s-del")
    assert row.deleted_at is not None
    assert row.deleted_at.replace(tzinfo=UTC) == t


# ── groups / group_members / group_invites (alembic 0024) ───────────────────────


def test_group_membership_round_trips(session):
    from db.models import Group, GroupMember, Profile

    prof = Profile(id="11111111-1111-1111-1111-111111111111", full_name="Professor")
    student = Profile(id="22222222-2222-2222-2222-222222222222", full_name="Aluno")
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=prof.id, name="Gracie Barra")
    session.add_all([prof, student, group])
    session.add_all([
        GroupMember(group_id=group.id, profile_id=prof.id, role="professor"),
        GroupMember(group_id=group.id, profile_id=student.id, role="student"),
    ])
    session.commit()

    roles = {m.profile_id: m.role for m in session.query(GroupMember).filter_by(group_id=group.id)}
    assert roles == {prof.id: "professor", student.id: "student"}


def test_group_member_trains_here_round_trips_and_defaults_false(session):
    """Alembic 0055 — separate from ``role``: an owner/professor opts in explicitly, a plain
    row otherwise defaults to not training there (join_group() sets it per-invite server-side,
    outside this ORM-level default)."""

    from db.models import Group, GroupMember, Profile

    owner = Profile(id=str(uuid.uuid4()), full_name="Dono")
    student = Profile(id=str(uuid.uuid4()), full_name="Aluno")
    group = Group(id=str(uuid.uuid4()), owner_id=owner.id, name="SP Grappling")
    session.add_all([owner, student, group])
    session.add_all([
        GroupMember(group_id=group.id, profile_id=owner.id, role="owner"),
        GroupMember(group_id=group.id, profile_id=student.id, role="student", trains_here=True),
    ])
    session.commit()

    owner_row = session.get(GroupMember, (group.id, owner.id))
    student_row = session.get(GroupMember, (group.id, student.id))
    assert owner_row.trains_here is False
    assert student_row.trains_here is True

    owner_row.trains_here = True
    session.commit()
    assert session.get(GroupMember, (group.id, owner.id)).trains_here is True


def test_group_member_consent_at_round_trips(session):
    """``join_group()`` (alembic 0054) stamps this the moment the Web confirmation screen lets
    the join through — NULL means "joined before 0054" or "never confirmed", never "declined"."""

    from datetime import UTC, datetime

    from db.models import Group, GroupMember, Profile

    prof = Profile(id=str(uuid.uuid4()), full_name="Professor")
    student = Profile(id=str(uuid.uuid4()), full_name="Aluno")
    group = Group(id=str(uuid.uuid4()), owner_id=prof.id, name="Gracie Barra")
    session.add_all([prof, student, group])
    session.flush()

    t = datetime(2026, 9, 3, tzinfo=UTC)
    session.add(GroupMember(group_id=group.id, profile_id=student.id, role="student", consent_at=t))
    session.commit()

    row = session.get(GroupMember, (group.id, student.id))
    assert row.consent_at is not None
    assert row.consent_at.replace(tzinfo=UTC) == t


# ── professor_evaluations (alembic 0054) ─────────────────────────────────────────


def test_professor_evaluation_round_trips(session):
    from db.models import Group, GroupMember, ProfessorEvaluation, Profile

    prof = Profile(id=str(uuid.uuid4()), full_name="Professor")
    student = Profile(id=str(uuid.uuid4()), full_name="Aluno")
    group = Group(id=str(uuid.uuid4()), owner_id=prof.id, name="Gracie Barra")
    session.add_all([prof, student, group])
    session.add_all([
        GroupMember(group_id=group.id, profile_id=prof.id, role="professor"),
        GroupMember(group_id=group.id, profile_id=student.id, role="student"),
    ])
    session.flush()

    evaluation = ProfessorEvaluation(
        group_id=group.id,
        student_id=student.id,
        professor_id=prof.id,
        rating_note="Guard retention improving, work on far-side underhook.",
        score=4,
    )
    session.add(evaluation)
    session.commit()

    fetched = session.query(ProfessorEvaluation).filter_by(student_id=student.id).one()
    assert fetched.group_id == group.id
    assert fetched.professor_id == prof.id
    assert fetched.score == 4

    # Never a write path to the Elo tables — a coach note is not a rating.
    assert not hasattr(fetched, "elo")
    assert not hasattr(fetched, "user_elo")


# ── class_sessions (alembic 0026) ────────────────────────────────────────────────


def test_class_session_round_trips_and_links_to_user_session(session):
    from datetime import UTC, datetime

    from db.models import ClassSession, Group, Profile, UserSession
    from db.repository import upsert_user_session

    prof = Profile(id=str(uuid.uuid4()), full_name="Professor")
    student = Profile(id=str(uuid.uuid4()), full_name="Aluno")
    group = Group(id=str(uuid.uuid4()), owner_id=prof.id, name="Gracie Barra")
    session.add_all([prof, student, group])
    session.flush()

    klass = ClassSession(
        group_id=group.id,
        created_by=prof.id,
        title="Segunda de guarda",
        join_token="tok-123",
        token_expires_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    session.add(klass)
    session.commit()

    fetched = session.get(ClassSession, klass.id)
    assert fetched is not None
    assert fetched.group_id == group.id
    assert fetched.join_token == "tok-123"

    t = datetime(2026, 8, 11, tzinfo=UTC)
    upsert_user_session(student.id, "s-class", {}, t, session)
    session.commit()
    row = session.get(UserSession, "s-class")
    row.class_session_id = klass.id
    session.commit()

    row = session.get(UserSession, "s-class")
    assert row.class_session_id == klass.id


def test_get_user_sessions_since_includes_tombstones(session):
    from datetime import UTC, datetime

    from db.models import Profile
    from db.repository import get_user_sessions_since, upsert_user_session

    owner_id = str(uuid.uuid4())
    session.add(Profile(id=owner_id))
    session.flush()

    t = datetime(2026, 7, 1, tzinfo=UTC)
    upsert_user_session(owner_id, "s-live", {}, t, session)
    upsert_user_session(owner_id, "s-tomb", {}, t, session, deleted_at=t)
    session.commit()

    rows = get_user_sessions_since(owner_id, None, session)
    assert {r.id for r in rows} == {"s-live", "s-tomb"}  # tombstone not filtered out
    tomb = next(r for r in rows if r.id == "s-tomb")
    assert tomb.deleted_at is not None


def test_profile_athlete_link_column_and_partial_unique(engine, session):
    """Alembic 0051: ``profiles.athlete_id`` column exists and round-trips, and the
    partial unique index (raw SQL in the migration, not represented in db/models.py —
    same convention as 0048's ``ix_athletes_anonymized``) is re-created here directly
    against SQLite (same partial-index syntax) so the CONSTRAINT is proven, not just
    the column."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from db.models import Athlete, Profile

    with engine.connect() as conn:
        conn.execute(
            text(
                "create unique index ux_profiles_athlete_id "
                "on profiles (athlete_id) where athlete_id is not null"
            )
        )
        conn.commit()

    athlete = Athlete(name="Test Athlete")
    session.add(athlete)
    session.flush()

    p1 = Profile(id=str(uuid.uuid4()), athlete_id=athlete.id)
    session.add(p1)
    session.commit()
    assert session.get(Profile, p1.id).athlete_id == athlete.id

    # A second profile claiming the SAME athlete violates the partial unique index.
    p2 = Profile(id=str(uuid.uuid4()), athlete_id=athlete.id)
    session.add(p2)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    # Two unlinked profiles (NULL athlete_id) never collide — the index is partial.
    p3 = Profile(id=str(uuid.uuid4()))
    p4 = Profile(id=str(uuid.uuid4()))
    session.add_all([p3, p4])
    session.commit()  # no error
