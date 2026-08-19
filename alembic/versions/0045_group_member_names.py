"""A professor can read their students' names, and only their names.

The Web roster has never worked, for two independent reasons that hid behind each other.

**The visible one.** `GrapplingArcWeb/src/lib/students.ts` asks PostgREST for
``profiles(display_name)``. There is no such column — ``profiles`` carries ``id, full_name,
belt_rank, belt_degrees, is_guest, is_pro, archetype_id, created_at, updated_at`` — so the
request 400s and ``/students`` renders an error.

**The one underneath.** Fixing the column name would not fix the page. ``profiles_select_own``
(0023) is the ONLY select policy on ``profiles``, so an embedded join returns ``null`` for
every row that is not ``auth.uid()``. A professor would get a roster of blanks.

**Why not simply widen that policy.** RLS grants a ROW, not a column. A policy that lets a
professor see their students' ``profiles`` rows hands over ``belt_rank``, ``is_pro`` and
``archetype_id`` along with the name. Nobody asked for that, and "we only select the name in
the client" is not a boundary — it is a habit that the next query breaks.

So this is the shape 0032 already chose for exactly this problem: a SECURITY DEFINER function
that projects ONLY the columns the caller needs, with the access question answered inside it.
``shares_group_as_professor`` (0025) reads ``auth.uid()`` itself, so no argument can be used to
ask about somebody else's students — the group id narrows WHICH of your students, never WHOSE.

The projection is two columns and it should stay two columns. Adding ``belt_rank`` here later
is a privacy decision, not a convenience, and it deserves its own revision saying so.

Privacy class: **C, user cloud-synced private data**, read across an owner boundary under the
one deliberate exception the gym flow needs. Nothing here is aggregated, exported, or visible to
anyone outside the professor relation.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory and never
executes a Postgres migration (0019's scope note). The guard is a source scan —
``tests/test_group_member_names.py``.

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-19
"""

from __future__ import annotations

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


_FN_UP = """
create or replace function public.group_member_names(p_group_id uuid)
returns table (
  profile_id uuid,
  full_name text
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    p.id as profile_id,
    p.full_name
  from public.group_members gm
  join public.profiles p on p.id = gm.profile_id
  where gm.group_id = p_group_id
    and shares_group_as_professor(gm.profile_id);
$$;
"""


def upgrade() -> None:
    op.execute(_FN_UP)

    # Supabase's default schema grants would otherwise leave this callable by `anon` over REST,
    # and 0028 records that revoking from PUBLIC alone does NOT remove the default anon=X — both
    # are needed. `anon` has no `auth.uid()`, so the predicate would return nothing anyway; a
    # function nobody meant to expose is still a function nobody meant to expose.
    op.execute("revoke all on function public.group_member_names(uuid) from public;")
    op.execute("revoke all on function public.group_member_names(uuid) from anon;")
    op.execute("grant execute on function public.group_member_names(uuid) to authenticated;")


def downgrade() -> None:
    op.execute("drop function if exists public.group_member_names(uuid);")
