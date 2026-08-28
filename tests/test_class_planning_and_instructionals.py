"""alembic 0050 — create_group/set_member_role + class_plan_templates/instructionals RLS.

Source-scan, same reason as ``tests/test_group_member_names.py``: the suite runs on SQLite
in-memory and never executes a Postgres migration (0019's scope note), so a policy/function
body can only be checked by reading the tracked text of the revision that defines it. Fragile
on purpose — if the SQL shape moves, this fails and the reviewer has to look again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0050_class_planning_and_instructionals.py"
)


@pytest.fixture()
def source() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


# ── create_group ──────────────────────────────────────────────────────────


def test_create_group_is_definer_and_writes_both_rows(source: str) -> None:
    assert "create or replace function public.create_group(p_name text)" in source
    body = source.split("create or replace function public.create_group")[1].split("$$;")[0]

    assert "security definer" in body
    assert "set search_path = public" in body
    assert "insert into public.groups" in body
    assert "insert into public.group_members" in body
    assert "'owner'" in body
    # owner_id/profile_id come from auth.uid(), never a caller-supplied argument — nobody can
    # create a group on someone else's behalf.
    assert body.count("auth.uid()") >= 2


def test_create_group_and_set_member_role_grants(source: str) -> None:
    # Both functions are revoked/granted through the same ``_FUNCTIONS`` loop (mirrors 0028's
    # pattern) — the literal SQL carries an f-string placeholder, so this checks the tuple
    # membership and the loop's revoke-from-public/anon, grant-to-authenticated template.
    assert '"public.create_group(text)"' in source
    assert '"public.set_member_role(uuid, uuid, text)"' in source
    assert 'revoke all on function {fn} from public;' in source
    assert 'revoke all on function {fn} from anon;' in source
    assert 'grant execute on function {fn} to authenticated;' in source


# ── set_member_role ───────────────────────────────────────────────────────


def test_set_member_role_is_owner_gated_and_definer(source: str) -> None:
    assert "create or replace function public.set_member_role(" in source
    assert "p_group_id uuid, p_profile_id uuid, p_role text" in source
    body = source.split("create or replace function public.set_member_role")[1].split("$$;")[0]

    assert "security definer" in body
    assert "set search_path = public" in body
    # Owner only — g.owner_id = auth.uid(), not is_group_owner_or_professor (that would let a
    # professor promote another professor, which is not the decision this function makes).
    assert "g.owner_id = auth.uid()" in body
    assert "is_group_owner_or_professor" not in body


def test_set_member_role_never_grants_owner(source: str) -> None:
    body = source.split("create or replace function public.set_member_role")[1].split("$$;")[0]

    assert "'professor'" in body
    assert "'student'" in body
    assert "'owner'" not in body, (
        "set_member_role must never be able to set role='owner' — transferring group "
        "ownership is a separate, harder decision and needs its own migration."
    )


# ── class_plan_templates / instructionals RLS ─────────────────────────────


def test_group_scoped_tables_select_is_member_write_is_owner_or_professor(source: str) -> None:
    # Both tables are policed through the same ``for table in (...)`` loop, so the literal SQL
    # carries an f-string placeholder rather than the substituted table name.
    assert '"class_plan_templates"' in source
    assert '"instructionals"' in source
    assert "create policy {table}_select_member on public.{table} for select" in source
    assert "create policy {table}_insert_owner_prof on public.{table} for insert" in source
    assert "create policy {table}_update_owner_prof on public.{table} for update" in source
    assert "create policy {table}_delete_owner_prof on public.{table} for delete" in source

    # Never a raw group_members subquery inside a policy body — that recursion is the 0025 bug.
    assert "public.is_group_member(group_id)" in source
    assert "public.is_group_owner_or_professor(group_id)" in source


# ── storage: instructional-media, a bucket of its own ─────────────────────


def test_instructional_media_is_its_own_bucket(source: str) -> None:
    assert (
        "insert into storage.buckets (id, name, public)\n"
        "    values ('instructional-media', 'instructional-media', false)" in source
    )
    # Not reused as a policy target for either older bucket — session-videos (0024, owner-prefix
    # + professor SELECT, built for round footage) or user-media (0042, owner-only, built for
    # private study attachments); see the migration docstring and 0042's for why a shared bucket
    # was rejected. Only appear here in prose explaining that, never in a `bucket_id = ...`.
    assert "bucket_id = 'session-videos'" not in source
    assert "bucket_id = 'user-media'" not in source


def test_instructional_media_read_is_member_write_is_owner_or_professor(source: str) -> None:
    read = source.split("create policy instructional_media_member_read")[1].split(";\n")[0]
    write = source.split("create policy instructional_media_professor_write")[1].split(
        "with check"
    )[0]

    assert "is_group_member(" in read
    assert "is_group_owner_or_professor(" in write


# ── class_sessions plan columns ────────────────────────────────────────────


def test_class_sessions_gains_focus_and_plan_columns(source: str) -> None:
    assert '"class_sessions"' in source
    assert '"focus_node_keys"' in source
    assert '"plan"' in source


# ── model round-trip (SQLite in-memory) ────────────────────────────────────

_SQLITE_URL = "sqlite:///:memory:"


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


def test_class_plan_template_and_instructional_round_trip(session) -> None:
    """Shape round-trips through SQLAlchemy — FKs, PK, defaults.

    ``focus_node_keys`` is left unset so the INSERT omits it and the column's
    ``server_default`` applies: pysqlite's DBAPI has no array bind type (unlike psycopg
    against real Postgres), so binding a Python ``list`` here would fail on this dialect
    only — a SQLite quirk, not a statement about the real column.
    """
    from db.models import ClassPlanTemplate, Group, Instructional, Profile

    prof = Profile(id="11111111-1111-1111-1111-111111111111", full_name="Professor")
    session.add(prof)
    group = Group(id="22222222-2222-2222-2222-222222222222", owner_id=prof.id, name="GB")
    session.add(group)
    session.commit()

    template = ClassPlanTemplate(group_id=group.id, created_by=prof.id, name="Guard passing week")
    instructional = Instructional(
        group_id=group.id,
        created_by=prof.id,
        title="Knee slice fundamentals",
        external_url="https://example.com/video",
    )
    session.add_all([template, instructional])
    session.commit()

    fetched_template = session.get(ClassPlanTemplate, template.id)
    fetched_instructional = session.get(Instructional, instructional.id)
    assert fetched_template is not None
    assert fetched_template.group_id == group.id
    assert fetched_instructional is not None
    assert fetched_instructional.sort_order == 0
