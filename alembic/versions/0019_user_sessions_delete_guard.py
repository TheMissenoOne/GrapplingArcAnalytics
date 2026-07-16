"""Session-sync delete tombstones + concurrent-push stale-write guard.

Two app-side sync bugs need schema support here:

Bug 1 — deletes never propagate. Deleting a session on one device just drops it from
what that device pushes; other devices never learn it was deleted and keep resurrecting
it via incremental pull. Fix: a nullable ``deleted_at`` column turns a delete into a
*tombstone* — the app upserts the row with ``deleted_at`` set instead of removing it,
so every other device's incremental pull (``get_user_sessions_since``) sees the deletion
and can locally delete + suppress resurrection. The row itself is kept as the durable
"this id is dead" marker.

Bug 4 — concurrent pushes clobber newer data. Two devices racing to push the same ``id``:
the loser's payload can carry an OLDER ``updated_at`` than what's already on the server, and
a naive upsert (PostgREST ``merge-duplicates`` or this repo's ``ON CONFLICT DO UPDATE``)
overwrites the newer server row with stale data. The client can't be trusted to order these
under a race, so the guard lives in Postgres: a ``BEFORE UPDATE`` trigger that skips the
update whenever ``NEW.updated_at < OLD.updated_at``. It fires on the ``DO UPDATE`` arm of any
upsert regardless of writer (app-via-PostgREST or admin-via-repo). Tombstone writes get the
exact same treatment — a stale tombstone can't override a newer live row, nor vice versa —
because the comparison is on ``updated_at`` alone; ``deleted_at`` needs no ordering of its own.

Bug 1 corollary — a session that's created and deleted on-device before it was ever pushed has
no server row yet, so its tombstone push is an INSERT, not an UPDATE. The app tombstone payload
carries no ``data`` (nothing to strip-and-upload for a dead session), so ``data`` must accept
NULL or that INSERT violates the original ``0017`` NOT NULL constraint and wedges the fail-closed
sync permanently. Fix: relax ``data`` to nullable here — a tombstone-only row (``deleted_at`` set,
``data`` null) is a legitimate, permanent state, not a transient one to be repaired later.

Scope note (mirrors 0017): RLS for ``user_sessions`` already lives in ``db/auth_setup.sql``
(``user_sessions_owner_all``, owner-scoped ``FOR ALL``) and covers the whole row incl.
``deleted_at`` — no policy change needed, do NOT re-shape it. The guard trigger is a plain
data-integrity trigger (no ``auth.users`` coupling, not RLS), so it belongs here in the
migration, not in the hand-run auth file.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory
(``tests/test_db.py``), which validates the ``db/models.py`` shape (incl. ``deleted_at``
round-trip) but does NOT execute this migration or the Postgres-only trigger — the
stale-write guard itself is validated in Postgres, not pytest (same caveat 0017/0018 note).

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


_GUARD_FN_UP = """
create or replace function public.guard_user_sessions_stale_write()
returns trigger language plpgsql as $$
begin
  -- Bug 4: drop a losing racer's stale overwrite (older updated_at than the row on the
  -- server). Return NULL to skip the UPDATE, keeping the newer OLD row. Equal timestamps
  -- pass through (idempotent re-write). A stale tombstone is dropped the same way — the
  -- check is on updated_at alone, so deleted_at needs no ordering of its own.
  if NEW.updated_at < OLD.updated_at then
    return null;
  end if;
  return NEW;
end;
$$;
"""

_TRIGGER_UP = """
drop trigger if exists trg_user_sessions_stale_write on public.user_sessions;
create trigger trg_user_sessions_stale_write
  before update on public.user_sessions
  for each row execute function public.guard_user_sessions_stale_write();
"""


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("user_sessions", "data", existing_type=JSONB, nullable=True)
    op.execute(_GUARD_FN_UP)
    op.execute(_TRIGGER_UP)


def downgrade() -> None:
    op.execute(
        "drop trigger if exists trg_user_sessions_stale_write on public.user_sessions;"
    )
    op.execute("drop function if exists public.guard_user_sessions_stale_write();")
    # NOTE: a downgrade with tombstone-only (data IS NULL) rows already present will fail
    # re-imposing NOT NULL — that's expected, this repo's downgrades assume no data written
    # under the new revision yet (same convention as the rest of alembic/versions/*).
    op.alter_column("user_sessions", "data", existing_type=JSONB, nullable=False)
    op.drop_column("user_sessions", "deleted_at")
