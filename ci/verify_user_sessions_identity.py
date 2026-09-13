"""Verify the user_sessions identity cutover against disposable CI PostgreSQL."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import Cursor
from psycopg.types.json import Jsonb

from alembic import command

DOWNGRADE_ERROR = "cannot restore global user_sessions.id primary key"


@dataclass(frozen=True)
class Fixture:
    owners: tuple[str, str]
    session_id: str

    @classmethod
    def create(cls) -> Fixture:
        return cls(
            owners=(str(uuid4()), str(uuid4())),
            session_id=f"ci-shared-session-{uuid4()}",
        )


def compact_sql(expression: str | None) -> str:
    """Normalize catalog-rendered SQL for stable expression comparisons."""
    compact = re.sub(r"\s+", "", expression or "")
    return compact[1:-1] if compact.startswith("(") and compact.endswith(")") else compact


def require_local_ci_database(url: str) -> str:
    """Return a validated loopback PostgreSQL URL for a database named ``*_ci``."""
    parsed = urlparse(url)
    database = parsed.path.lstrip("/")
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or not database.endswith("_ci")
        or "?" in url
        or "#" in url
        or parsed.params
        or url != url.strip()
    ):
        raise ValueError("verification requires a local CI PostgreSQL database")
    return parsed.geturl()


def expect(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def column_order(cursor: Cursor[tuple[Any, ...]], constraint: str) -> list[str]:
    cursor.execute(
        """
        select array_agg(a.attname order by key.ordinality)
        from pg_constraint c
        cross join lateral unnest(c.conkey) with ordinality as key(attnum, ordinality)
        join pg_attribute a on a.attrelid = c.conrelid and a.attnum = key.attnum
        where c.conrelid = 'public.user_sessions'::regclass
          and c.contype = %s
        """,
        (constraint,),
    )
    row = cursor.fetchone()
    return list(row[0]) if row and row[0] else []


def index_column_order(cursor: Cursor[tuple[Any, ...]], index_name: str) -> list[str]:
    cursor.execute(
        """
        select array_agg(a.attname order by key.ordinality)
        from pg_index i
        cross join lateral unnest(i.indkey) with ordinality as key(attnum, ordinality)
        join pg_attribute a on a.attrelid = i.indrelid and a.attnum = key.attnum
        where i.indrelid = 'public.user_sessions'::regclass
          and i.indexrelid = to_regclass(%s)
        """,
        (f"public.{index_name}",),
    )
    row = cursor.fetchone()
    return list(row[0]) if row and row[0] else []


def verify_catalog(cursor: Cursor[tuple[Any, ...]]) -> None:
    expect("primary key columns", column_order(cursor, "p"), ["owner_id", "id"])
    expect(
        "owner/update index columns",
        index_column_order(cursor, "idx_user_sessions_owner_updated"),
        ["owner_id", "updated_at"],
    )

    cursor.execute(
        "select relrowsecurity from pg_class where oid = 'public.user_sessions'::regclass"
    )
    expect("user_sessions RLS", cursor.fetchone(), (True,))

    cursor.execute(
        """
        select cmd, qual, with_check
        from pg_policies
        where schemaname = 'public'
          and tablename = 'user_sessions'
          and policyname = 'user_sessions_owner_all'
        """
    )
    policy = cursor.fetchone()
    if policy is None:
        raise AssertionError("user_sessions_owner_all policy is missing")
    expect("owner policy command", policy[0], "ALL")
    expect("owner policy USING", compact_sql(policy[1]), "owner_id=auth.uid()")
    expect("owner policy WITH CHECK", compact_sql(policy[2]), "owner_id=auth.uid()")

    cursor.execute(
        """
        select t.tgenabled, p.proname, pg_get_triggerdef(t.oid)
        from pg_trigger t
        join pg_proc p on p.oid = t.tgfoid
        where t.tgrelid = 'public.user_sessions'::regclass
          and t.tgname = 'trg_user_sessions_stale_write'
          and not t.tgisinternal
        """
    )
    trigger = cursor.fetchone()
    if trigger is None:
        raise AssertionError("trg_user_sessions_stale_write is missing")
    expect("LWW trigger enabled", trigger[0], "O")
    expect("LWW trigger function", trigger[1], "guard_user_sessions_stale_write")
    if "BEFORE UPDATE" not in str(trigger[2]).upper():
        raise AssertionError(f"unexpected LWW trigger definition: {trigger[2]}")

    # group_member_sessions was a VIEW through 0031; 0033 dropped it for good (0032 stood the
    # SECURITY DEFINER FUNCTION up first, both live at once, then 0033 removed the view once
    # GrapplingArcWeb moved off `.from()` onto `.rpc()`). Check the function that replaced it.
    #
    # No `security_barrier` equivalent: 0032's own docstring says why — the function takes no
    # caller-supplied predicate for the planner to push a cheap user expression below, which is
    # what security_barrier on the view existed to prevent. The view's `security_invoker=false`
    # requirement (run as the definer, so the predicate — not the caller's own RLS — is the
    # access control) is `prosecdef = true` here; same guarantee, function-shaped.
    cursor.execute(
        """
        select p.prosecdef, p.proconfig, pg_get_functiondef(p.oid)
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = 'group_member_sessions'
          and pg_get_function_identity_arguments(p.oid) = ''
        """
    )
    fn = cursor.fetchone()
    if fn is None:
        raise AssertionError("group_member_sessions function is missing")
    expect("group_member_sessions is SECURITY DEFINER", fn[0], True)
    proconfig = set(fn[1] or [])
    if "search_path=public" not in proconfig:
        raise AssertionError(f"group_member_sessions did not pin search_path: {sorted(proconfig)}")
    fn_sql = str(fn[2]).lower()
    # `shares_group_as_professor` moved one hop down: 0060 routed the predicate through
    # `can_read_member_row` (member OR class-guest), so the member-path guarantee is checked
    # on that function's own body below rather than as a literal token here.
    for token in ("class_session_id", "reflection", "notes", "deleted_at", "can_read_member_row"):
        if token not in fn_sql:
            raise AssertionError(f"group_member_sessions lost {token!r}: {fn[2]}")

    cursor.execute(
        "select has_function_privilege("
        "'authenticated', 'public.group_member_sessions()', 'execute')"
    )
    expect("authenticated may call group_member_sessions", cursor.fetchone(), (True,))
    cursor.execute(
        "select has_function_privilege('anon', 'public.group_member_sessions()', 'execute')"
    )
    expect("anon may NOT call group_member_sessions", cursor.fetchone(), (False,))

    # The member-path predicate itself: can_read_member_row must still fall back to
    # shares_group_as_professor (0032's original guarantee, untouched by 0060 per that
    # revision's own docstring — "first disjunct byte-identical to the predicate it replaces").
    cursor.execute(
        """
        select pg_get_functiondef(p.oid)
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public' and p.proname = 'can_read_member_row'
        """
    )
    predicate = cursor.fetchone()
    if predicate is None:
        raise AssertionError("can_read_member_row function is missing")
    predicate_sql = str(predicate[0]).lower()
    if "shares_group_as_professor" not in predicate_sql:
        raise AssertionError(f"can_read_member_row lost the member path: {predicate[0]}")


def exercise_identity(
    cursor: Cursor[tuple[Any, ...]], fixture: Fixture
) -> None:
    # Scaffold's auth.users has no raw_user_meta_data column (Supabase provides it live);
    # the profile-provision trigger (0023) reads it, so add it here same as the other
    # verify_*.py scripts.
    cursor.execute("alter table auth.users add column if not exists raw_user_meta_data jsonb")
    cursor.executemany(
        "insert into auth.users (id, raw_user_meta_data) values (%s, '{}'::jsonb)",
        [(owner,) for owner in fixture.owners],
    )
    inserted_at = datetime(2026, 8, 14, 12, tzinfo=UTC)
    cursor.executemany(
        """
        insert into public.user_sessions (owner_id, id, data, updated_at)
        values (%s, %s, %s, %s)
        """,
        [
            (
                fixture.owners[0],
                fixture.session_id,
                Jsonb({"state": "owner-0"}),
                inserted_at,
            ),
            (
                fixture.owners[1],
                fixture.session_id,
                Jsonb({"state": "owner-1"}),
                inserted_at,
            ),
        ],
    )

    stale_at = datetime(2026, 8, 14, 11, tzinfo=UTC)
    cursor.execute(
        """
        update public.user_sessions
        set data = %s, updated_at = %s
        where owner_id = %s and id = %s
        """,
        (
            Jsonb({"state": "stale"}),
            stale_at,
            fixture.owners[0],
            fixture.session_id,
        ),
    )
    expect("stale update rowcount", cursor.rowcount, 0)

    newer_at = datetime(2026, 8, 14, 13, tzinfo=UTC)
    cursor.execute(
        """
        update public.user_sessions
        set data = %s, updated_at = %s
        where owner_id = %s and id = %s
        """,
        (
            Jsonb({"state": "newer"}),
            newer_at,
            fixture.owners[0],
            fixture.session_id,
        ),
    )
    expect("newer update rowcount", cursor.rowcount, 1)

    deleted_at = datetime(2026, 8, 14, 14, tzinfo=UTC)
    cursor.execute(
        """
        update public.user_sessions
        set data = null, deleted_at = %s, updated_at = %s
        where owner_id = %s and id = %s
        """,
        (deleted_at, deleted_at, fixture.owners[0], fixture.session_id),
    )
    expect("tombstone update rowcount", cursor.rowcount, 1)

    cursor.execute(
        """
        select owner_id::text, data, updated_at, deleted_at
        from public.user_sessions
        where id = %s
        order by owner_id
        """,
        (fixture.session_id,),
    )
    rows = cursor.fetchall()
    expect("same id under two owners", len(rows), 2)
    rows_by_owner = {row[0]: row for row in rows}
    owner_0 = rows_by_owner[fixture.owners[0]]
    owner_1 = rows_by_owner[fixture.owners[1]]
    expect("owner-0 tombstone data", owner_0[1], None)
    expect("owner-0 newer timestamp", owner_0[2], deleted_at)
    expect("owner-0 deleted_at", owner_0[3], deleted_at)
    expect("owner-1 data unaffected", owner_1[1], {"state": "owner-1"})


def current_revision(url: str) -> str:
    with psycopg.connect(url) as connection:
        row = connection.execute("select version_num from alembic_version").fetchone()
    return str(row[0]) if row else ""


def run_alembic(url: str, operation: Callable[[Config], None]) -> None:
    """Run Alembic with the same validated URL used by direct connections."""
    safe_url = require_local_ci_database(url)
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = safe_url
    try:
        operation(Config("alembic.ini"))
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def current_head() -> str:
    """Head revision per the script directory — never hardcode it, it moves with every migration."""
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    if head is None:
        raise AssertionError("alembic script directory has no head revision")
    return head


def verify_downgrade_fails_closed(url: str) -> None:
    try:
        run_alembic(url, lambda config: command.downgrade(config, "0060"))
    except Exception as exc:
        if DOWNGRADE_ERROR not in str(exc):
            raise AssertionError(f"downgrade failed for the wrong reason: {exc}") from exc
    else:
        raise AssertionError("revision 0061 downgrade accepted duplicate owner-scoped ids")
    # 0061's downgrade guard raises before any DDL commits, and alembic runs a multi-step
    # `downgrade(..., "0060")` as one transaction on a transactional-DDL dialect, so a
    # rejected downgrade rolls all the way back to wherever the DB started — head, not
    # necessarily 0061 (0062+ may already be applied by the time this runs).
    expect("revision after rejected downgrade", current_revision(url), current_head())


def cleanup(url: str, fixture: Fixture) -> None:
    with psycopg.connect(url) as connection:
        connection.execute(
            "delete from auth.users where id = any(%s)", (list(fixture.owners),)
        )


def verify_database(url: str, fixture: Fixture) -> None:
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            verify_catalog(cursor)
            exercise_identity(cursor, fixture)
    verify_downgrade_fails_closed(url)


def ensure_head(url: str) -> None:
    run_alembic(url, lambda config: command.upgrade(config, "head"))


def assert_head(url: str) -> None:
    # Not hardcoded: this file was written when 0061 was head, and the very next revision
    # (0062) already made a literal "0061" here stale.
    expect("final revision", current_revision(url), current_head())


def run_verification(url: str, fixture: Fixture) -> None:
    primary_error: Exception | None = None
    recovery_errors: list[Exception] = []
    try:
        verify_database(url, fixture)
    except Exception as exc:
        primary_error = exc
    finally:
        recovery_operations: tuple[Callable[[], None], ...] = (
            lambda: ensure_head(url),
            lambda: cleanup(url, fixture),
            lambda: assert_head(url),
        )
        for operation in recovery_operations:
            try:
                operation()
            except Exception as exc:
                recovery_errors.append(exc)

    recovery_failure = (
        ExceptionGroup("verification recovery failed", recovery_errors)
        if recovery_errors
        else None
    )
    if primary_error is not None:
        raise primary_error from recovery_failure
    if recovery_failure is not None:
        raise recovery_failure


def main() -> None:
    url = require_local_ci_database(os.environ.get("DATABASE_URL", ""))
    os.environ["DATABASE_URL"] = url
    run_verification(url, Fixture.create())
    print("user_sessions PostgreSQL identity verification passed")


if __name__ == "__main__":
    main()
