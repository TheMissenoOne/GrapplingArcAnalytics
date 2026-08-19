"""A session the athlete deleted stops being readable by their professor.

Two halves of one leak, and they were invisible apart. Found in the GrapplingArcWeb audit of
2026-08-13 as finding "D"; confirmed on both sides on 2026-08-19.

**The server half.** ``group_member_sessions()`` (0032) reads
``from public.user_sessions us where shares_group_as_professor(us.owner_id)``. There is no
``deleted_at`` predicate, and the column is not even in the ``returns table`` projection, so a
professor's client has nothing to filter on either. 0032's own comment says "a tombstone row
(data IS NULL) yields data NULL, exactly as the view did; the Web client already filters those
out" — and that sentence is the mistake this revision fixes. It assumes every tombstone carries
a NULL ``data``.

**The client half that breaks the assumption.** The App pushes a delete as
``upsert({id, owner_id, updated_at, deleted_at})`` with ``on_conflict = id`` and **no ``data``
key** (``GrapplingArcApp/src/services/sessionSync.ts``). PostgREST's merge-duplicates leaves any
column the payload omits exactly as it was. So for the ordinary case — a session that synced
before it was deleted — the row ends up with ``deleted_at`` SET and ``data`` still holding the
full record: title, goal, rounds, positions, outcomes. The tombstone-is-null assumption only
ever held for a session created and deleted before its first push.

The App is being fixed in the same wave to send ``data: null``, but that is defence in depth and
covers only future deletes. **The predicate here is the actual fix**, and it also covers every
row already tombstoned in production.

Art. 18 of the LGPD: the data subject asked for elimination and a third party kept reading it.

**Latent, not active** — production has 0 tombstones and 0 professor relations today. Latent is
not fixed; it is untriggered.

**Two halves, deliberately in one revision.**

``upgrade()`` step 1 re-issues the function with ``and us.deleted_at is null``. 0032 already
uses ``create or replace``, so no drop and no grant churn: the existing
``grant execute ... to authenticated`` survives a replace.

``upgrade()`` step 2 is **destructive and is the point**: ``update public.user_sessions set
data = null where deleted_at is not null``. This erases content whose owner already asked for
it to be erased, which is compliance rather than convenience — leaving it would mean a row that
says "deleted" while still storing the thing that was deleted. It is written to report the
affected row count so the operator sees what it did instead of trusting it. Applying any
migration in this repo is an explicit human decision; this one especially.

``downgrade()`` restores the predicate-free function so the revision is reversible in the sense
that matters (the access rule). It **does not** restore the erased payloads: there is nowhere to
restore them from, and a downgrade that pretended otherwise would be a lie.

Privacy class: **C, user cloud-synced private data**. A session is app-fed and owner-scoped. The
professor projection is the single, deliberate exception — narrowed by 0032 to drop
``reflection`` and every round's ``notes``, and narrowed here to drop the whole record once the
athlete has withdrawn it. Nothing about this revision widens what anyone can see.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory and never
executes a Postgres-only migration (see 0019's scope note). The guard for this revision is
therefore a source-scan test asserting the predicate is present in the most recent definition of
the function — ``tests/test_group_member_sessions_hides_deleted.py``.

Revision ID: 0044
Revises: 0043
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


# Byte-identical to 0032's `_FN_UP` except for the added `and us.deleted_at is null`. Kept as a
# full copy rather than a patch so the live definition is readable in one place, which is how
# 0032 justified copying it verbatim off `pg_get_viewdef` in the first place.
_FN_UP = """
create or replace function public.group_member_sessions()
returns table (
  id text,
  owner_id uuid,
  class_session_id uuid,
  updated_at timestamptz,
  data jsonb
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    us.id,
    us.owner_id,
    us.class_session_id,
    us.updated_at,
    jsonb_set(
      us.data - 'reflection',
      '{rounds}',
      coalesce(
        (select jsonb_agg(r.value - 'notes')
           from jsonb_array_elements(us.data -> 'rounds') r),
        '[]'::jsonb
      )
    ) as data
  from public.user_sessions us
  where shares_group_as_professor(us.owner_id)
    and us.deleted_at is null;
$$;
"""

# 0032's body, restored verbatim on downgrade.
_FN_DOWN = """
create or replace function public.group_member_sessions()
returns table (
  id text,
  owner_id uuid,
  class_session_id uuid,
  updated_at timestamptz,
  data jsonb
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    us.id,
    us.owner_id,
    us.class_session_id,
    us.updated_at,
    jsonb_set(
      us.data - 'reflection',
      '{rounds}',
      coalesce(
        (select jsonb_agg(r.value - 'notes')
           from jsonb_array_elements(us.data -> 'rounds') r),
        '[]'::jsonb
      )
    ) as data
  from public.user_sessions us
  where shares_group_as_professor(us.owner_id);
$$;
"""


def upgrade() -> None:
    # STEP 1 — the access rule. `create or replace` keeps the existing grants.
    op.execute(_FN_UP)

    # STEP 2 — erase the payloads the owners already withdrew.
    #
    # Reported, not silent: an operator running this needs to see how many rows carried a body
    # they should not have been carrying. On a clean database this prints 0, which is the
    # answer that means "the client-side fix landed before anyone deleted anything".
    result = op.get_bind().execute(
        sa.text(
            "update public.user_sessions set data = null "
            "where deleted_at is not null and data is not null"
        )
    )
    print(f"[0044] cleared the body of {result.rowcount} already-deleted session row(s)")


def downgrade() -> None:
    # Restores the access rule only. The bodies erased by step 2 are gone, and there is nowhere
    # to restore them from — which is the correct outcome for data the owner deleted.
    op.execute(_FN_DOWN)
