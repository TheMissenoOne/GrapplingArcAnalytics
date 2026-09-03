"""``trains_here`` — separates "runs this gym" from "trains at this gym".

The owner's 2026-09-03 request: the owner of a gym should ALSO show up as a training member —
roster, class attendance, sessions read by any professor there — the same as any student, without
losing owner powers. ``group_members.role`` already answers "what can this person DO here"
(``owner``/``professor``/``student``, gates ``set_member_role``/invites/RLS everywhere); it was
never meant to also answer "does this person train here", and the two questions turned out to
collide in exactly one place: the Web's ``Group.tsx`` hides the athlete-facing panel (rating,
instructionals, the leave-gym button, the consent copy) whenever ``role !== 'student'`` — so an
owner or professor, who by definition never has that role, can never see their OWN training as a
member of their own gym.

Every server-side read that answers "is this profile a member a professor may see" —
``shares_group_as_professor``, ``group_member_names``, ``group_member_sessions``,
``group_member_rating``, ``group_member_graph_edges``, ``attach_to_class`` (via
``is_group_member``) — already ignores ``role`` on the TARGET side (verified by reading each: none
of them filter ``role = 'student'``), so an owner's own sessions/rating were already visible to
themselves-as-professor and to any co-professor. Nothing server-side was actually blocking the
data; only the Web UI's role check was. ``trains_here`` still gets its own column rather than
inferring "training member" from role in the client, because the request is explicitly for the
owner/professor to OPT IN per gym (a professor teaching at one gym may not train there; a
professor who does can flip it on) — a plain boolean is the whole feature, no new access surface.

Existing rows backfill by the same rule new joins get: a plain ``'student'`` invite always trains
there, everyone else starts opted out and can flip it on. ``join_group()`` is rewritten to set
this explicitly at insert (a bare column DEFAULT cannot reference the ``role`` value being
inserted in the same statement) rather than leaving it to the new column's own default, which
would otherwise leave every fresh student join stuck at ``false``. ``create_group()`` is untouched
— the owner's own row keeps the column default (``false``); they opt in via ``set_trains_here``
same as any professor, so the owner's role privileges gain no new implicit meaning.

``set_trains_here(group_id, bool)`` is the ONLY write path, same SECURITY DEFINER shape as
``join_group``/``set_member_role`` — it updates only the caller's own row
(``profile_id = auth.uid()``), so it needs no role check: anyone in the gym may say whether they
personally train there.

Revision ID: 0055
Revises: 0054
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None

_JOIN_GROUP_WITH_TRAINS_HERE = """
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

  if target_group is null then
    raise exception 'invalid_invite' using errcode = 'P0001';
  end if;

  insert into public.group_members (group_id, profile_id, role, consent_at, trains_here)
  values (target_group, auth.uid(), target_role, now(), target_role = 'student')
  on conflict on constraint group_members_pkey do nothing;

  return query
    select g.id, g.name from public.groups g where g.id = target_group;
end;
$$;
"""

_JOIN_GROUP_0054 = """
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

  if target_group is null then
    raise exception 'invalid_invite' using errcode = 'P0001';
  end if;

  insert into public.group_members (group_id, profile_id, role, consent_at)
  values (target_group, auth.uid(), target_role, now())
  on conflict on constraint group_members_pkey do nothing;

  return query
    select g.id, g.name from public.groups g where g.id = target_group;
end;
$$;
"""

_SET_TRAINS_HERE = """
create or replace function public.set_trains_here(p_group_id uuid, p_trains_here boolean)
returns void
language sql
security definer
set search_path = public
as $$
  update public.group_members
  set trains_here = p_trains_here
  where group_id = p_group_id and profile_id = auth.uid();
$$;
"""


def upgrade() -> None:
    op.add_column(
        "group_members",
        sa.Column("trains_here", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Same rule new joins get (see docstring): a plain student invite always trains here.
    op.execute("update public.group_members set trains_here = true where role = 'student';")

    op.execute(_JOIN_GROUP_WITH_TRAINS_HERE)

    op.execute(_SET_TRAINS_HERE)
    op.execute("revoke all on function public.set_trains_here(uuid, boolean) from public;")
    op.execute("revoke all on function public.set_trains_here(uuid, boolean) from anon;")
    op.execute(
        "grant execute on function public.set_trains_here(uuid, boolean) to authenticated;"
    )


def downgrade() -> None:
    op.execute("drop function if exists public.set_trains_here(uuid, boolean);")
    op.execute(_JOIN_GROUP_0054)
    op.drop_column("group_members", "trains_here")
