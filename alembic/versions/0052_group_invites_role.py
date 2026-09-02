"""Invite carries the role it grants, so an academy can mint a professor by code.

An academy account is `groups.owner_id` — no new role, that identity already exists (owner-only
minting, `group_invites_owner_all`, unchanged by this revision). What it could not do until now
is invite a PROFESSOR: `join_group()` (0024, fixed 0028) always inserted `role = 'student'`,
whatever the invite said, because the invite never said anything — the code alone decided.

`group_invites.role` names what the code grants, `not null default 'student'` so every invite
minted before this revision (including any still live and unexpired) keeps behaving exactly as
it did — silently promoting no one. `join_group()` now reads `gi.role` instead of hardcoding the
literal, same `on conflict on constraint group_members_pkey do nothing` as 0028 — an existing
member's row is untouched by a re-join, so a professor who scans a student invite by mistake, or
scans the SAME invite twice, is never demoted by this path. (The other direction — a student
scanning a professor invite and self-promoting — is exactly the feature: the owner controls that
by which code they hand out, same trust boundary as `group_invites_owner_all` already draws.)

`set_member_role` (0050) is untouched: it already refuses `'owner'` and already gates on
`groups.owner_id = auth.uid()`, so this revision doesn't touch it. It does NOT stop an owner from
demoting themselves via `set_member_role(group_id, <own profile id>, 'student')` — that gap
predates this revision and promoting/demoting the owner's own membership row is a different
decision (ownership transfer, 0050's docstring already scopes that out) than what this revision
is about, so it is named here rather than silently fixed as a drive-by.

Revision ID: 0052
Revises: 0051
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "group_invites",
        sa.Column("role", sa.Text(), nullable=False, server_default="student"),
    )
    op.execute(
        "alter table public.group_invites "
        "add constraint ck_group_invites_role check (role in ('student', 'professor'));"
    )

    op.execute("""
    create or replace function public.join_group(invite_code text)
    returns table (group_id uuid, group_name text)
    language plpgsql
    security definer
    set search_path = public
    as $$
    declare
      target_group uuid;
      target_role text;
    begin
      select gi.group_id, gi.role into target_group, target_role
      from public.group_invites gi
      where gi.code = invite_code
        and gi.revoked_at is null
        and gi.expires_at > now();

      -- One error for unknown, expired and revoked alike: a wrong code must not
      -- reveal whether a group exists.
      if target_group is null then
        raise exception 'invalid_invite' using errcode = 'P0001';
      end if;

      -- Conflict target named as the CONSTRAINT, not as columns (0028's fix — `group_id`
      -- is also this function's OUT parameter). An already-existing member row is left
      -- alone: a re-join never rebases (or demotes) the role a member already has.
      insert into public.group_members (group_id, profile_id, role)
      values (target_group, auth.uid(), target_role)
      on conflict on constraint group_members_pkey do nothing;

      return query
        select g.id, g.name from public.groups g where g.id = target_group;
    end;
    $$;
    """)


def downgrade() -> None:
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
      on conflict on constraint group_members_pkey do nothing;

      return query
        select g.id, g.name from public.groups g where g.id = target_group;
    end;
    $$;
    """)

    op.execute(
        "alter table public.group_invites drop constraint if exists ck_group_invites_role;"
    )
    op.drop_column("group_invites", "role")
