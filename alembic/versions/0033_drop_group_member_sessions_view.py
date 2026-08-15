"""Retire the group_member_sessions VIEW, now that everything reads the function.

Second half of the swap 0032 set up. 0032 closed the security hole (revoked the write verbs
and all of anon) and created the SECURITY DEFINER function alongside the view, so both were
live at once and nothing had to break. This drops the view.

**Do not apply this until the GrapplingArcWeb change is deployed.** That repo is the view's
only consumer — three call sites in ``src/lib/students.ts`` and ``src/lib/classes.ts``, all
moved from ``.from('group_member_sessions')`` to ``.rpc('group_member_sessions')``. The App
never touched it (``grep -rn group_member_sessions GrapplingArcApp/src`` → zero hits), so no
mobile release gates this. Order: 0032 → deploy web → 0033.

What this buys, and it is only the lint: Supabase's advisor reports
``0010_security_definer_view`` at level ERROR for any definer view, and ours was a deliberate
one (0027 explains why, and it is still correct — invoker rights would restore the bug where a
professor sees only their own sessions). Carrying a permanent ERROR in the one report that
should always be empty teaches people to skim it. The function that replaced the view lints at
WARN instead, alongside the five definer functions this schema already depends on.

The real fix already landed in 0032. This is the cleanup that makes the report readable again.

Downgrade recreates the view in its 0027 state — definer + barrier — not the broken 0026
invoker state, and without the anon grants Supabase's schema default originally handed it.
A downgrade should undo one revision, not reopen a hole two revisions back.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


# Verbatim from the live pg_get_viewdef captured before the drop, so a downgrade restores
# exactly what was there rather than a re-derivation of it.
_VIEW_DOWN = """
create view public.group_member_sessions as
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
"""


def upgrade() -> None:
    op.execute("drop view if exists public.group_member_sessions;")


def downgrade() -> None:
    op.execute(_VIEW_DOWN)
    # security_invoker = false is the 0027 repair, NOT an oversight: with invoker rights the
    # base table's owner-only RLS filters rows before the view's predicate runs, and the
    # professor sees only themselves. security_barrier is what keeps the planner from pushing
    # a cheap caller predicate below the access-control WHERE.
    op.execute("alter view public.group_member_sessions set (security_invoker = false);")
    op.execute("alter view public.group_member_sessions set (security_barrier = true);")
    op.execute("revoke all on public.group_member_sessions from anon;")
    op.execute("grant select on public.group_member_sessions to authenticated;")
