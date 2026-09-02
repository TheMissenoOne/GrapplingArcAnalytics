"""scripts/group_admin.py — SQLite in-memory round-trips (mirrors tests/test_db.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_SQLITE_URL = "sqlite:///:memory:"
PROF_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def engine():
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


def test_mint_invite_is_readable_and_expires(session):
    from db.models import Group, Profile
    from scripts.group_admin import mint_invite

    session.add(Profile(id=PROF_ID, full_name="Professor"))
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=PROF_ID, name="GB")
    session.add(group)
    session.commit()

    invite = mint_invite(session, group.id, PROF_ID, days=7)

    assert invite.code.startswith("GA-")
    assert len(invite.code) == 9  # "GA-" + 6
    assert set(invite.code[3:]) <= set("ACDEFGHJKMNPQRTUVWXY34679")  # no 0/O, 1/I/L, S/5, 8/B
    delta = invite.expires_at.replace(tzinfo=UTC) - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)


def test_create_group_makes_owner_the_first_member(session):
    from db.models import GroupMember, Profile
    from scripts.group_admin import create_group

    session.add(Profile(id=PROF_ID, full_name="Professor"))
    session.commit()

    group = create_group(session, PROF_ID, "Gracie Barra")

    member = session.query(GroupMember).filter_by(group_id=group.id, profile_id=PROF_ID).one()
    assert member.role == "owner"


def test_mint_invite_defaults_to_student_role(session):
    from db.models import Group, Profile
    from scripts.group_admin import mint_invite

    session.add(Profile(id=PROF_ID, full_name="Professor"))
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=PROF_ID, name="GB")
    session.add(group)
    session.commit()

    invite = mint_invite(session, group.id, PROF_ID)

    assert invite.role == "student"


def test_mint_invite_can_grant_professor(session):
    from db.models import Group, Profile
    from scripts.group_admin import mint_invite

    session.add(Profile(id=PROF_ID, full_name="Professor"))
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=PROF_ID, name="GB")
    session.add(group)
    session.commit()

    invite = mint_invite(session, group.id, PROF_ID, role="professor")

    assert invite.role == "professor"


def test_mint_invite_rejects_unknown_role(session):
    from db.models import Group, Profile
    from scripts.group_admin import mint_invite

    session.add(Profile(id=PROF_ID, full_name="Professor"))
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=PROF_ID, name="GB")
    session.add(group)
    session.commit()

    with pytest.raises(ValueError):
        mint_invite(session, group.id, PROF_ID, role="owner")


def test_roster_lists_every_member_and_role(session):
    from db.models import Group, GroupMember, Profile
    from scripts.group_admin import roster

    student_id = "22222222-2222-2222-2222-222222222222"
    session.add_all([
        Profile(id=PROF_ID, full_name="Professor"),
        Profile(id=student_id, full_name="Aluno"),
    ])
    group = Group(id="33333333-3333-3333-3333-333333333333", owner_id=PROF_ID, name="GB")
    session.add(group)
    session.add_all([
        GroupMember(group_id=group.id, profile_id=PROF_ID, role="owner"),
        GroupMember(group_id=group.id, profile_id=student_id, role="student"),
    ])
    session.commit()

    assert set(roster(session, group.id)) == {(PROF_ID, "owner"), (student_id, "student")}
