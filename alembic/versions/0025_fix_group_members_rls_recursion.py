"""Break the RLS recursion the groups policies walked into.

``0024``'s policies asked ``group_members`` who is allowed to read
``group_members``: ``group_members_select_same_group`` selects from the very
table it protects, and ``groups_select_member``, the ``group_member_sessions``
view and the storage read policy all reach the same table through a subquery.
Postgres evaluates the policy while evaluating the subquery, forever, and
answers ``infinite recursion detected in policy for relation "group_members"``.

Caught by running the Task 9 checklist as a real ``authenticated`` user against
prod — the SQLite suite cannot execute a policy, so nothing earlier could have
seen it. It failed CLOSED (every read errored, no row escaped), which is why
this is a repair and not an incident.

The fix is the standard one: two ``SECURITY DEFINER`` helpers own the membership
question. Being definer-rights, they read ``group_members`` with RLS bypassed, so
a policy calling them terminates. They are ``stable``, take the caller's identity
from ``auth.uid()`` internally rather than as an argument (so no caller can ask
about somebody else), and pin ``search_path`` per 0020.

``is_group_member(uuid)`` answers "am I in this group". ``shares_group_as_professor(uuid)``
answers "am I the owner/professor of a group that this profile is in" — the
question the session view and the video policy actually ask.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── the two questions, answered with definer rights so RLS cannot recurse ──
    op.execute("""
    create or replace function public.is_group_member(target_group uuid)
    returns boolean
    language sql
    stable
    security definer
    set search_path = public
    as $$
      select exists (
        select 1 from public.group_members m
        where m.group_id = target_group and m.profile_id = auth.uid()
      );
    $$;
    """)

    op.execute("""
    create or replace function public.shares_group_as_professor(target_profile uuid)
    returns boolean
    language sql
    stable
    security definer
    set search_path = public
    as $$
      select exists (
        select 1
        from public.group_members me
        join public.group_members them using (group_id)
        where me.profile_id = auth.uid()
          and me.role in ('owner','professor')
          and them.profile_id = target_profile
      );
    $$;
    """)

    op.execute("revoke all on function public.is_group_member(uuid) from public;")
    op.execute("revoke all on function public.shares_group_as_professor(uuid) from public;")
    op.execute("grant execute on function public.is_group_member(uuid) to authenticated;")
    op.execute("grant execute on function public.shares_group_as_professor(uuid) to authenticated;")

    # ── policies rewritten to ask the function instead of the table ──
    op.execute("drop policy if exists group_members_select_same_group on public.group_members;")
    op.execute("""
    create policy group_members_select_same_group on public.group_members
    for select to authenticated
    using (public.is_group_member(group_id));
    """)

    op.execute("drop policy if exists group_members_delete_self_or_owner on public.group_members;")
    op.execute("""
    create policy group_members_delete_self_or_owner on public.group_members
    for delete to authenticated
    using (profile_id = auth.uid()
           or exists (select 1 from public.groups g
                      where g.id = group_members.group_id and g.owner_id = auth.uid()));
    """)

    op.execute("drop policy if exists groups_select_member on public.groups;")
    op.execute("""
    create policy groups_select_member on public.groups for select to authenticated
    using (public.is_group_member(id));
    """)

    # ── the view asks the same way ──
    op.execute("""
    create or replace view public.group_member_sessions with (security_invoker = true) as
    select
      us.id,
      us.owner_id,
      us.updated_at,
      jsonb_set(
        us.data - 'reflection',
        '{rounds}',
        coalesce(
          (select jsonb_agg(r - 'notes') from jsonb_array_elements(us.data->'rounds') r),
          '[]'::jsonb
        )
      ) as data
    from public.user_sessions us
    where public.shares_group_as_professor(us.owner_id);
    """)
    op.execute("grant select on public.group_member_sessions to authenticated;")

    # ── and so does the video read policy ──
    op.execute("drop policy if exists session_videos_professor_read on storage.objects;")
    op.execute("""
    create policy session_videos_professor_read on storage.objects for select to authenticated
    using (bucket_id = 'session-videos'
           and public.shares_group_as_professor(((storage.foldername(name))[1])::uuid));
    """)


def downgrade() -> None:
    # Back to 0024's shape — recursion included, which is why this only exists
    # to keep the chain reversible.
    op.execute("drop policy if exists session_videos_professor_read on storage.objects;")
    op.execute("""
    create policy session_videos_professor_read on storage.objects for select to authenticated
    using (bucket_id = 'session-videos' and exists (
      select 1 from public.group_members me
      join public.group_members them using (group_id)
      where me.profile_id = auth.uid()
        and me.role in ('owner','professor')
        and them.profile_id::text = (storage.foldername(name))[1]
    ));
    """)

    op.execute("""
    create or replace view public.group_member_sessions with (security_invoker = true) as
    select
      us.id,
      us.owner_id,
      us.updated_at,
      jsonb_set(
        us.data - 'reflection',
        '{rounds}',
        coalesce(
          (select jsonb_agg(r - 'notes') from jsonb_array_elements(us.data->'rounds') r),
          '[]'::jsonb
        )
      ) as data
    from public.user_sessions us
    where exists (
      select 1
      from public.group_members me
      join public.group_members them using (group_id)
      where me.profile_id = auth.uid()
        and me.role in ('owner','professor')
        and them.profile_id = us.owner_id
    );
    """)
    op.execute("grant select on public.group_member_sessions to authenticated;")

    op.execute("drop policy if exists groups_select_member on public.groups;")
    op.execute("""
    create policy groups_select_member on public.groups for select to authenticated
    using (exists (select 1 from public.group_members m
                   where m.group_id = groups.id and m.profile_id = auth.uid()));
    """)

    op.execute("drop policy if exists group_members_select_same_group on public.group_members;")
    op.execute("""
    create policy group_members_select_same_group on public.group_members
    for select to authenticated
    using (exists (select 1 from public.group_members me
                   where me.group_id = group_members.group_id and me.profile_id = auth.uid()));
    """)

    op.execute("drop function if exists public.shares_group_as_professor(uuid);")
    op.execute("drop function if exists public.is_group_member(uuid);")
