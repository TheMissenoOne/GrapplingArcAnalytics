"""Projects, so they stop being a thing that only exists on one phone.

Projects were pure on-device state: ``@grapplingarch:projects:{userId}`` in AsyncStorage, a
single whole-array JSON blob, with no cloud surface at all (verified 2026-08-15: zero Supabase
calls anywhere in the App's project path). The only way one ever left a device was inside a
Google Drive backup bundle, which is a blob, not a sync. So a project created on Android was
invisible in the PWA — the exact "it exists on Android but disappeared when I opened the web
app" surprise the cross-platform storage contract is meant to rule out.

This is deliberately the same shape as ``user_sessions`` (0017/0019) rather than a new idea:
owner-scoped row, device-generated ``id``, whole-record ``data`` JSONB, ``updated_at`` as the
conflict clock, ``deleted_at`` as a tombstone. The App reuses its session-sync code path for
it, so there is one sync idiom in this product, not two.

**The primary key is ``id`` ALONE, and that is load-bearing.** The App upserts with
``on_conflict`` and PostgreSQL requires that target to match a real unique constraint —
``user_sessions`` already cost this repo a round of 42P10 errors from guessing
``owner_id,id`` when only the ``id`` PK existed (see the comment on
``USER_SESSIONS_CONFLICT_TARGET`` in the App's ``services/sessionSync.ts``). Project ids are
device-generated (``project-{ms}``) and RLS keeps writes owner-scoped, so the PK does not need
the owner in it. If you ever add ``(owner_id, id)``, add the constraint FIRST and change the
client in the same wave.

``data`` is nullable for the same reason it is on ``user_sessions``: a project created and
deleted before it was ever pushed arrives as a tombstone INSERT with nothing to upload, and a
NOT NULL there would wedge the fail-closed sync permanently.

The stale-write guard is the same ``BEFORE UPDATE`` trigger 0019 introduced, for the same race:
two devices pushing the same id, and the loser carrying an older ``updated_at``. It is a plain
data-integrity trigger, not RLS.

Privacy class: **C, user cloud-synced private data** (workspace CLAUDE.md, "Public vs Private
Data"). Projects are fed directly by the user through the App, so they are private and
owner-scoped — readable only by their owner, never aggregated, never an input to anything
competitive, public, or third-party-facing. Device-local video URIs inside ``data`` stay on the
device: the App strips media to the portable metadata allowlist before upload, exactly as it
does for sessions.

RLS lives here rather than in a later revision — that is the convention from 0024 onward
(0017/0018 predate it and were retrofitted by 0023).

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory
(``tests/test_db.py``), which validates the ``db/models.py`` shape round-trips through
SQLAlchemy but does NOT execute this migration or the Postgres-only trigger/policies. Same
caveat as 0017/0018/0019.

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


_GUARD_FN_UP = """
create or replace function public.guard_user_projects_stale_write()
returns trigger language plpgsql as $$
begin
  -- Same race, same rule as guard_user_sessions_stale_write (0019): a losing racer's
  -- older updated_at must not overwrite the newer row already on the server. Return NULL
  -- to skip the UPDATE. Equal timestamps pass through so a re-push stays idempotent, and
  -- a stale tombstone is dropped by the same comparison — deleted_at needs no ordering
  -- of its own.
  if NEW.updated_at < OLD.updated_at then
    return null;
  end if;
  return NEW;
end;
$$;
"""

_TRIGGER_UP = """
drop trigger if exists trg_user_projects_stale_write on public.user_projects;
create trigger trg_user_projects_stale_write
  before update on public.user_projects
  for each row execute function public.guard_user_projects_stale_write();
"""


def upgrade() -> None:
    op.create_table(
        "user_projects",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("data", JSONB, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # The incremental pull is `where owner_id = ? and updated_at >= ? order by updated_at`.
    op.create_index(
        "idx_user_projects_owner_updated",
        "user_projects",
        ["owner_id", "updated_at"],
        if_not_exists=True,
    )

    op.execute(_GUARD_FN_UP)
    op.execute(_TRIGGER_UP)

    op.execute("alter table public.user_projects enable row level security;")

    # Supabase grants the whole public schema to anon/authenticated by default, which is how a
    # view ended up anon-writable in 0024 (see 0032). Revoke from BOTH roles first, then grant
    # back only the four verbs the client uses, rather than adding a grant on top of a default
    # nobody chose.
    #
    # Revoking from `authenticated` matters and is not paranoia: the default set includes
    # TRUNCATE, and **RLS does not gate TRUNCATE** — a row-level policy cannot stop a statement
    # that removes every row at once. It is not reachable through PostgREST (there is no REST
    # verb for it), so this is defence in depth rather than a live hole, but it is a privilege
    # nobody meant to hand out. `user_sessions`/`user_sync_meta` still carry the full default
    # set for both roles, anon included; tightening those is a separate change with its own
    # blast radius, deliberately not smuggled in here.
    op.execute("revoke all on public.user_projects from anon, authenticated;")
    op.execute(
        "grant select, insert, update, delete on public.user_projects to authenticated;"
    )

    # One owner-scoped FOR ALL policy, mirroring user_sessions_owner_all (0023). USING gates
    # what you can see/modify; WITH CHECK gates what you can leave behind — both are needed,
    # or an authenticated user can INSERT a row owned by someone else.
    op.execute("drop policy if exists user_projects_owner_all on public.user_projects;")
    op.execute(
        """
        create policy user_projects_owner_all on public.user_projects for all
          to authenticated
          using (owner_id = auth.uid())
          with check (owner_id = auth.uid());
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists user_projects_owner_all on public.user_projects;")
    op.execute("drop trigger if exists trg_user_projects_stale_write on public.user_projects;")
    op.execute("drop function if exists public.guard_user_projects_stale_write();")
    op.drop_index("idx_user_projects_owner_updated", table_name="user_projects", if_exists=True)
    op.drop_table("user_projects")
