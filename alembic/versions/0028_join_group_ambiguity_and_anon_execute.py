"""join_group never worked, and anon could tell which invite codes are real.

Two independent defects, both found by calling the functions as real roles
against prod (rolled back), and both invisible to every test written so far
because the checklists only exercised the paths that fail EARLY.

1. ``join_group`` is broken for everyone. ``returns table (group_id uuid, ...)``
   makes ``group_id`` a plpgsql variable, and the body's
   ``on conflict (group_id, profile_id)`` then resolves ambiguously against
   ``group_members.group_id``: ``column reference "group_id" is ambiguous``. A
   wrong code returns ``invalid_invite`` before reaching that line, so the
   0024 checklist — which tested only a bad code and a revoked one — passed
   while the happy path had never once succeeded. Joining a gym by code has
   never worked in production.

   Fixed by naming the conflict target as the constraint
   (``on conflict on constraint group_members_pkey``), which is not a variable
   context. The RETURNS TABLE column names stay ``group_id``/``group_name``
   because the app reads exactly those (``groupService.joinGroup``).

2. ``anon`` could execute all five SECURITY DEFINER functions. Supabase's default
   privileges grant EXECUTE to ``anon`` and ``authenticated`` when a function is
   created in ``public``; ``revoke all ... from public`` does not remove those —
   ``proacl`` showed ``anon=X`` on every one. Combined with defect 1 that was an
   enumeration oracle: unauthenticated, an invalid code answered
   ``invalid_invite`` while a VALID one answered ``column reference ... is
   ambiguous``, so anyone could brute-force which invite codes exist — precisely
   what the identical-error design was meant to prevent.

   ``authenticated`` keeps EXECUTE: RLS policies call ``is_group_member`` and
   ``is_group_owner_or_professor`` in their own expressions, which are evaluated
   as the querying role, and the storage policy calls
   ``shares_group_as_professor``. Only ``anon`` loses it — the app's guests never
   touch Supabase at all, so nothing legitimate calls these without a session.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-11
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_FUNCTIONS = (
    "public.join_group(text)",
    "public.attach_to_class(text)",
    "public.is_group_member(uuid)",
    "public.shares_group_as_professor(uuid)",
    "public.is_group_owner_or_professor(uuid)",
)


def upgrade() -> None:
    op.execute("""
    create or replace function public.join_group(invite_code text)
    returns table (group_id uuid, group_name text)
    language plpgsql
    security definer
    set search_path = public
    as $$
    declare
      target_group uuid;
    begin
      select gi.group_id into target_group
      from public.group_invites gi
      where gi.code = invite_code
        and gi.revoked_at is null
        and gi.expires_at > now();

      -- One error for unknown, expired and revoked alike: a wrong code must not
      -- reveal whether a group exists.
      if target_group is null then
        raise exception 'invalid_invite' using errcode = 'P0001';
      end if;

      -- Conflict target named as the CONSTRAINT, not as columns: `group_id` is
      -- also this function's OUT parameter, and a column list here is a variable
      -- context, which is what made every successful join fail as ambiguous.
      insert into public.group_members (group_id, profile_id, role)
      values (target_group, auth.uid(), 'student')
      on conflict on constraint group_members_pkey do nothing;

      return query
        select g.id, g.name from public.groups g where g.id = target_group;
    end;
    $$;
    """)

    for fn in _FUNCTIONS:
        op.execute(f"revoke execute on function {fn} from anon;")
        op.execute(f"revoke execute on function {fn} from public;")
        op.execute(f"grant execute on function {fn} to authenticated;")


def downgrade() -> None:
    for fn in _FUNCTIONS:
        op.execute(f"grant execute on function {fn} to anon;")

    op.execute("""
    create or replace function public.join_group(invite_code text)
    returns table (group_id uuid, group_name text)
    language plpgsql
    security definer
    set search_path = public
    as $$
    declare
      target_group uuid;
    begin
      select gi.group_id into target_group
      from public.group_invites gi
      where gi.code = invite_code
        and gi.revoked_at is null
        and gi.expires_at > now();

      if target_group is null then
        raise exception 'invalid_invite' using errcode = 'P0001';
      end if;

      insert into public.group_members (group_id, profile_id, role)
      values (target_group, auth.uid(), 'student')
      on conflict (group_id, profile_id) do nothing;

      return query
        select g.id, g.name from public.groups g where g.id = target_group;
    end;
    $$;
    """)
