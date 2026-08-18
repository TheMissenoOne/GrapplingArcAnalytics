"""The one owner column that really is unhandled on deletion.

Auditing what survives an account deletion turned up three `public` tables with a uuid owner
column and no foreign key:

  - ``graphs.owner_id`` — polymorphic (athlete id or profile id, per ``owner_kind``), so a column
    FK is impossible. Already handled: 0023's ``handle_user_delete`` trigger on
    ``before delete on auth.users`` deletes the user's graph, and it is live in production.
    Nothing to do here.
  - ``matches.created_by`` — curation provenance, not user data. 893 rows, every one NULL.
  - ``bundle_imports.owner_id`` — genuinely unhandled.

``bundle_imports`` holds ``raw jsonb``: a whole user bundle, the single most complete dump of one
person's app data that this schema contains. Deleting their account would have left it behind.

It has not actually leaked anything. The table has zero rows, no writer in any of the four
repos, and RLS enabled with **zero policies** — so PostgREST denies every read regardless of the
table grant. The exposure is entirely prospective. But a table designed to hold whole user
bundles must not be the one place a deletion forgets, and the fix is a foreign key.

Question worth answering separately, not here: this table appears to be dead. No writer, no
reader, no rows. Either something is meant to fill it — in which case it needs an owner policy
before it does — or it should be dropped. A revision that silently maintains a table nobody uses
is how dead schema becomes permanent, so this one names the question instead of settling it.

Revision ID: 0038
Revises: 0037
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None

_FK = "bundle_imports_owner_id_fkey"


def upgrade() -> None:
    # Nullable column, so ON DELETE CASCADE removes the row rather than orphaning it: a bundle
    # with no owner is not a record of anything, and SET NULL would keep the raw payload while
    # discarding the only thing that says whose it was.
    op.execute(f"alter table public.bundle_imports drop constraint if exists {_FK};")
    op.execute(
        f"""
        alter table public.bundle_imports
          add constraint {_FK}
          foreign key (owner_id) references public.profiles(id) on delete cascade;
        """
    )


def downgrade() -> None:
    op.execute(f"alter table public.bundle_imports drop constraint if exists {_FK};")
