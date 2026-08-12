"""Make the professor's view actually able to read the student's sessions.

``group_member_sessions`` was created in 0024 with ``security_invoker = true``,
carried through 0025 and 0026 unchanged. That flag makes the view run with the
CALLER's rights, so ``user_sessions``' owner-only RLS is applied *before* the
view's own ``where shares_group_as_professor(us.owner_id)`` ever runs. A
professor therefore read exactly one thing through it: their own sessions. The
whole point of the view — letting a professor see their students' training —
never worked in production.

It failed CLOSED (a professor saw less than intended, never more), which is why
this is a repair. But it went unnoticed longer than it should have: the piece-B
checklist printed "rows: 1" and that was read as success, when the single row was
the professor's own session. A count is not a proof; WHOSE row came back is.

The fix is the ordinary Postgres pattern for exactly this shape: the view runs
with definer rights, so its ``where`` clause becomes the access control instead of
being pre-empted by the base table's RLS. Two things make that safe:

* ``security_barrier = true`` — with definer rights the ``where`` is the ONLY
  protection, so the planner must not push a cheap user-supplied predicate below
  it and leak rows it would have filtered.
* the predicate itself takes no argument from the caller: ``shares_group_as_professor``
  reads ``auth.uid()`` internally (0025), so nobody can ask the view about a
  profile that is not theirs to ask about.

``user_sessions`` keeps its owner-only policies untouched — a professor still
cannot select the raw table, which is what keeps ``reflection`` and each round's
``notes`` out of reach. Verified against prod as real authenticated users, in a
rolled-back transaction: professor sees the student's row and not an outsider's,
with both note fields stripped; the student sees nothing through the view.

Supabase's advisor lints definer views (``0010_security_definer_view``). This one
is deliberate and is the reason the view exists; expect that lint and do not
"fix" it by restoring ``security_invoker``, which restores the bug.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter view public.group_member_sessions set (security_invoker = false);")
    op.execute("alter view public.group_member_sessions set (security_barrier = true);")


def downgrade() -> None:
    op.execute("alter view public.group_member_sessions reset (security_barrier);")
    op.execute("alter view public.group_member_sessions set (security_invoker = true);")
