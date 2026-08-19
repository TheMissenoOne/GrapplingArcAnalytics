"""Study notes, as their own record rather than a field inside a project.

Notes already existed — as ``Project.notes``, an array nested inside the ``user_projects``
``data`` blob. That made every note the property of exactly one project, which is the wrong
shape for what the athlete actually writes: a note about a detail that fixed a sweep belongs to
the sweep, and possibly to three studies, and sometimes to none of them. Nesting also meant a
note could only ever be found by first opening the project that happened to own it.

So a note becomes a row, and what it is *about* becomes a list of references it carries —
technique node, graph edge, session, video, or a study. The App lifts the legacy nested notes
into this table on first load, keeping each note's original id so the lift is idempotent and a
second device re-running it upserts instead of duplicating.

Same shape as ``user_projects`` (0030) and ``user_sessions`` (0017/0019), deliberately: an
owner-scoped row, a device-generated ``id``, the whole record in ``data`` JSONB, ``updated_at``
as the conflict clock, ``deleted_at`` as a tombstone. That is one sync idiom in this product,
not a third one.

**PK is ``id`` alone.** The App upserts with ``on_conflict`` and PostgreSQL requires that target
to match a real unique constraint; guessing ``(owner_id, id)`` is the 42P10 this repo already
paid for once on ``user_sessions``. RLS keeps writes owner-scoped, so the owner does not need to
be in the key.

``data`` is nullable for the same reason as on the two tables above: a note written and deleted
before it was ever pushed arrives as a tombstone INSERT with nothing to upload, and NOT NULL
there would wedge a fail-closed sync permanently.

Privacy class: **C, user cloud-synced private data** (workspace CLAUDE.md, "Public vs Private
Data"). A study note is the most purely app-fed thing in the product — free prose the athlete
typed about their own training. It is owner-scoped and readable only by its owner. It is never
an input to a centroid, an embedding used outside its owner's scope, a ranking, a scouting
report, the public ``site/`` bundle, or any export. A note's *references* name canonical node
keys, which is public vocabulary; the prose around them is not, and the two never travel
together out of the owner's scope.

Note that this table is NOT visible to a professor. ``group_member_sessions`` (the SECURITY
DEFINER RPC that lets a professor read their students' sessions) already strips ``reflection``
and ``notes`` from what it returns; there is no equivalent RPC here and none should be added
without its own decision, because a study note is the athlete's private thinking, not a
training record the gym co-owns.

Cleanup on account deletion is the ``profiles.id`` cascade, same as ``user_projects`` — no
Storage objects hang off a note, so ``delete-account`` needs no new sweep for it.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory
(``tests/test_db.py``), which validates the ``db/models.py`` shape round-trips through
SQLAlchemy but does NOT execute this migration or the Postgres-only trigger/policies. Same
caveat as 0017/0018/0019/0030.

Revision ID: 0043
Revises: 0042
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


_GUARD_FN_UP = """
create or replace function public.guard_user_study_notes_stale_write()
returns trigger language plpgsql as $$
begin
  -- Same race, same rule as guard_user_projects_stale_write (0030): a losing racer's older
  -- updated_at must not overwrite the newer row already on the server. Return NULL to skip
  -- the UPDATE. Equal timestamps pass through so a re-push stays idempotent, and a stale
  -- tombstone is dropped by the same comparison.
  if NEW.updated_at < OLD.updated_at then
    return null;
  end if;
  return NEW;
end;
$$;
"""

_TRIGGER_UP = """
drop trigger if exists trg_user_study_notes_stale_write on public.user_study_notes;
create trigger trg_user_study_notes_stale_write
  before update on public.user_study_notes
  for each row execute function public.guard_user_study_notes_stale_write();
"""


def upgrade() -> None:
    op.create_table(
        "user_study_notes",
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
        "idx_user_study_notes_owner_updated",
        "user_study_notes",
        ["owner_id", "updated_at"],
        if_not_exists=True,
    )

    op.execute(_GUARD_FN_UP)
    op.execute(_TRIGGER_UP)

    op.execute("alter table public.user_study_notes enable row level security;")

    # Revoke from BOTH roles before granting anything back — Supabase grants the whole public
    # schema to anon/authenticated by default, and the default set includes TRUNCATE, which RLS
    # cannot gate (no row-level policy stops a statement that removes every row at once). Same
    # reasoning as 0030; see 0032 for what the default cost us once.
    op.execute("revoke all on public.user_study_notes from anon, authenticated;")
    op.execute(
        "grant select, insert, update, delete on public.user_study_notes to authenticated;"
    )

    # USING gates what you can see and modify; WITH CHECK gates what you can leave behind.
    # Both, or an authenticated user can INSERT a note owned by someone else.
    op.execute("drop policy if exists user_study_notes_owner_all on public.user_study_notes;")
    op.execute(
        """
        create policy user_study_notes_owner_all on public.user_study_notes for all
          to authenticated
          using (owner_id = auth.uid())
          with check (owner_id = auth.uid());
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists user_study_notes_owner_all on public.user_study_notes;")
    op.execute(
        "drop trigger if exists trg_user_study_notes_stale_write on public.user_study_notes;"
    )
    op.execute("drop function if exists public.guard_user_study_notes_stale_write();")
    op.drop_index(
        "idx_user_study_notes_owner_updated", table_name="user_study_notes", if_exists=True
    )
    op.drop_table("user_study_notes")
