"""Close the anon write path into user_sessions, and stop tripping the definer-view lint.

Two problems, one root cause: ``group_member_sessions`` was a SECURITY DEFINER *view*.

**The real one.** Supabase grants the whole ``public`` schema to ``anon`` and ``authenticated``
by default, and 0024 added ``grant select ... to authenticated`` on top of that without taking
the defaults away. Measured on prod 2026-08-15::

    grants: anon SELECT/INSERT/UPDATE/DELETE, authenticated SELECT/INSERT/UPDATE/DELETE
    information_schema.views: is_insertable_into = YES, is_updatable = YES
    owner: postgres, security_invoker = false

An auto-updatable view owned by ``postgres`` and running with definer rights applies the base
table's RLS as its OWNER, not as the caller. So writes through that view reached
``user_sessions`` without ``user_sessions_owner_all`` ever being consulted — with the
publishable key, which ships in every installed client. The view's own ``where`` clause
narrowed what a write could touch, and ``auth.uid()`` is NULL for ``anon`` so
``shares_group_as_professor`` returned false and the blast radius was empty in practice. It was
still a write path nobody designed, standing open. This is finding "A" in the security audit.

**The cosmetic one.** Supabase's advisor lints every definer view (``0010_security_definer_view``,
level ERROR). 0027 marked ours as a deliberate exception and said to expect the lint. That was
true and remains true, but "expect this ERROR forever" is a bad steady state for a security
report — it trains people to skim past the one list that should always be empty.

**What NOT to do.** Setting ``security_invoker = true`` is the obvious-looking fix and it is
wrong. It is what 0024-0026 shipped and what 0027 repaired: with invoker rights,
``user_sessions``' owner-only RLS filters the rows BEFORE the view's
``where shares_group_as_professor(owner_id)`` runs, so a professor sees exactly their own
sessions and nothing else. The professor view never worked in production for that reason.
GrapplingArcWeb has three real call sites. Do not "fix the lint" by breaking the feature again.

**What this does instead.** The same projection, the same predicate, as a SECURITY DEFINER
set-returning function. That keeps every property 0027 was protecting:

* the ``where`` clause is still the access control, still evaluated with definer rights;
* the predicate still takes nothing from the caller — ``shares_group_as_professor`` reads
  ``auth.uid()`` internally (0025), so nobody can ask about a profile that is not theirs to ask
  about. This is also why the function needs no ``security_barrier`` equivalent: with no
  caller-supplied predicate there is no cheap user function for the planner to push down ahead
  of it;
* ``user_sessions`` keeps its owner-only policies untouched, which is what keeps ``reflection``
  and each round's ``notes`` out of a professor's reach. The projection below is copied
  verbatim from the live ``pg_get_viewdef`` so the privacy filter is provably unchanged, and
  the consent text in the App's JoinGroupSheet ("não vê sua reflexão, nem as notas") stays
  true.

And it fixes both problems by construction: a function cannot be INSERTed into, so the write
path is gone structurally rather than by a grant we have to remember to keep revoked; and once
the view is gone the definer-view lint has nothing left to fire on. The function lints instead
as ``0029_authenticated_security_definer_function_executable`` (level WARN), the same class as
the five definer functions this schema already depends on — ``join_group``,
``attach_to_class``, ``is_group_member``, ``is_group_owner_or_professor`` and
``shares_group_as_professor`` itself.

**Sequencing — this revision does NOT drop the view.** Dropping it here would force a choice
between two broken orderings: drop first and the already-deployed GrapplingArcWeb 404s on a
view that no longer exists, or deploy the web change first and it calls a function that does
not exist yet. Both are avoidable, because a relation and a function can share a name
(``pg_class`` and ``pg_proc`` are separate namespaces) and PostgREST serves them at different
endpoints — ``/rest/v1/group_member_sessions`` vs ``/rest/v1/rpc/group_member_sessions``.

So this revision does the security fix in full and stands the function up beside the view, both
live. The order becomes: apply 0032 → deploy the GrapplingArcWeb ``.from`` → ``.rpc`` change →
apply 0033, which drops the view and clears the advisor ERROR. No window in which anything is
broken, and the part that actually matters — the anon write path — is closed at step one rather
than waiting on a frontend deploy.

Verification is on WHOSE row comes back, never how many. 0027's own docstring records the trap:
a checklist printed ``rows: 1`` and that was read as success, when the single row was the
professor's own session and the feature was broken. The authorization matrix for this revision
is: professor in the group sees the student's row with both note fields stripped; an
authenticated non-member sees zero rows; the student sees zero rows through the RPC (their own
data comes from ``user_sessions`` directly); ``anon`` gets permission denied.

Consumer change (separate repo, separate PR): GrapplingArcWeb ``src/lib/students.ts`` and
``src/lib/classes.ts`` move from ``.from('group_member_sessions')`` to
``.rpc('group_member_sessions')``. PostgREST supports ``eq``/``order``/``limit`` and
``{ count: 'exact', head: true }`` on set-returning functions, so both call sites are
mechanical.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


# Projection copied verbatim from the live pg_get_viewdef (prod, 2026-08-15) so the privacy
# filter is byte-for-byte the one that was audited: `data - 'reflection'`, and every round
# stripped of `notes`. COALESCE covers a session with no rounds (jsonb_agg over zero rows is
# NULL). A tombstone row (data IS NULL) yields data NULL, exactly as the view did; the Web
# client already filters those out.
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
  where shares_group_as_professor(us.owner_id);
$$;
"""

def upgrade() -> None:
    # STEP 1 — close the actual hole, immediately, with zero consumer impact.
    #
    # This is the whole security fix and it costs nothing: nothing has ever written through
    # this view, so taking the write verbs away breaks no caller. Read access for
    # `authenticated` is untouched, so the deployed GrapplingArcWeb keeps working exactly as
    # it does today. Revoking anon outright also removes its SELECT, which was only ever
    # returning zero rows anyway (auth.uid() is NULL for anon, so the predicate is false).
    op.execute("revoke all on public.group_member_sessions from anon;")
    op.execute("revoke all on public.group_member_sessions from authenticated;")
    op.execute("grant select on public.group_member_sessions to authenticated;")

    # STEP 2 — stand the function up ALONGSIDE the view.
    #
    # A relation and a function may share a name (pg_class and pg_proc are separate
    # namespaces), and PostgREST exposes them at different endpoints —
    # /rest/v1/group_member_sessions vs /rest/v1/rpc/group_member_sessions. So both work at
    # once, which is what makes this deployable without a window: apply this revision, deploy
    # the GrapplingArcWeb change that moves to .rpc(), and only then run 0033 to drop the view.
    #
    # Doing it in one step is what forces a choice between two broken orderings — drop the view
    # first and the deployed web app 404s, deploy the web app first and it calls a function
    # that does not exist yet. Neither is necessary.
    op.execute(_FN_UP)

    # Default schema grants would otherwise leave this callable by anon over REST. Revoke from
    # PUBLIC as well — 0028 records that revoking from PUBLIC alone does NOT remove Supabase's
    # default anon=X, so both are needed.
    op.execute("revoke all on function public.group_member_sessions() from public;")
    op.execute("revoke all on function public.group_member_sessions() from anon;")
    op.execute("grant execute on function public.group_member_sessions() to authenticated;")


def downgrade() -> None:
    op.execute("drop function if exists public.group_member_sessions();")
    # Restore the grants as they stood before this revision — including the anon write verbs
    # Supabase's schema default had handed out. A downgrade should reproduce the old state
    # honestly, hole and all, rather than quietly keep half the fix.
    op.execute(
        "grant select, insert, update, delete, truncate, references, trigger "
        "on public.group_member_sessions to anon, authenticated;"
    )
