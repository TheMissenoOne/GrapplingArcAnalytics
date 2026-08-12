"""Class sessions: a professor's live class + QR attach (Piece C, component 1).

``class_sessions`` belongs to a ``group`` (0024) — a professor starts one, the QR carries its
``join_token``, and a student who already belongs to the group scans it to stamp
``user_sessions.class_session_id`` onto that day's session. No attendance insert, no group join —
the QR "only links the session" per the locked decision in
``docs/superpowers/specs/2026-08-11-gym-groups-design.md`` (see plan section "Schema — revisão
0026"). The permanent-membership QR stays ``group_invites.code`` from 0024; this migration adds
no table for it.

``attach_to_class`` follows ``join_group``'s (0024) and the helpers' (0025) shape: ``SECURITY
DEFINER``, ``set search_path = public`` (0020), one error for "bad token" and "not a member" alike
so neither case can be used to probe which groups exist.

The select policy reuses 0025's ``is_group_member`` as-is. Insert/update/delete need "owner or
professor", which neither 0025 helper answers (``is_group_member`` ignores role;
``shares_group_as_professor`` takes a *profile*, not a *group*, and doesn't scope to one group).
Rather than a raw ``group_members`` subquery inside a policy — the exact mistake 0025 fixed — this
adds one more helper, ``is_group_owner_or_professor(group_id)``, built the same way as 0025's pair:
``security definer`` so it reads ``group_members`` with RLS bypassed and terminates, ``stable``,
``search_path`` pinned. No policy in this migration queries ``group_members`` directly.

``group_member_sessions`` (0024/0025) gains ``us.class_session_id`` so a professor can group a
student's sessions by class. The jsonb notes-stripping expression is untouched from 0025.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

# `create or replace view` can only APPEND columns — it cannot insert one in the
# middle (Postgres reads that as renaming the column that used to sit there:
# `cannot change name of view column "updated_at" to "class_session_id"`) and it
# cannot drop one, which the downgrade needs. Both directions therefore drop the
# view and create it again; the grant is re-issued right after, since dropping
# takes the grant with it. Nothing depends on this view — no policy references
# it — so the drop is safe.
_DROP_VIEW = "drop view if exists public.group_member_sessions;"

_VIEW_WITH_CLASS = """
create view public.group_member_sessions with (security_invoker = true) as
select
  us.id,
  us.owner_id,
  us.class_session_id,
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
"""

_VIEW_0025 = """
create view public.group_member_sessions with (security_invoker = true) as
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
"""


def upgrade() -> None:
    op.create_table(
        "class_sessions",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "group_id",
            UUID(as_uuid=False),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "starts_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("join_token", sa.Text(), unique=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_class_sessions_group", "class_sessions", ["group_id"])

    op.add_column(
        "user_sessions",
        sa.Column(
            "class_session_id",
            UUID(as_uuid=False),
            sa.ForeignKey("class_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── attach_to_class — SECURITY DEFINER, one error for bad token and non-member alike ──
    op.execute(
        """
        create or replace function public.attach_to_class(token text)
        returns table (class_id uuid, class_title text)
        language plpgsql
        security definer
        set search_path = public
        as $$
        declare
          target_class public.class_sessions%rowtype;
        begin
          select cs.* into target_class
          from public.class_sessions cs
          where cs.join_token = token
            and (cs.token_expires_at is null or cs.token_expires_at > now());

          if target_class.id is null or not public.is_group_member(target_class.group_id) then
            raise exception 'invalid_or_not_member' using errcode = 'P0001';
          end if;

          return query
            select target_class.id, target_class.title;
        end;
        $$;
        """
    )
    op.execute("revoke all on function public.attach_to_class(text) from public;")
    op.execute("grant execute on function public.attach_to_class(text) to authenticated;")

    # ── is_group_owner_or_professor — same shape as 0025's helpers, one more question ──
    op.execute(
        """
        create or replace function public.is_group_owner_or_professor(target_group uuid)
        returns boolean
        language sql
        stable
        security definer
        set search_path = public
        as $$
          select exists (
            select 1 from public.groups g
            where g.id = target_group and g.owner_id = auth.uid()
          ) or exists (
            select 1 from public.group_members m
            where m.group_id = target_group and m.profile_id = auth.uid() and m.role = 'professor'
          );
        $$;
        """
    )
    op.execute("revoke all on function public.is_group_owner_or_professor(uuid) from public;")
    op.execute(
        "grant execute on function public.is_group_owner_or_professor(uuid) to authenticated;"
    )

    # ── Policies — helpers only, never a group_members subquery in the policy body (0025) ──
    op.execute("alter table public.class_sessions enable row level security;")

    op.execute("drop policy if exists class_sessions_select_member on public.class_sessions;")
    op.execute(
        """
        create policy class_sessions_select_member on public.class_sessions for select
        to authenticated
        using (public.is_group_member(group_id));
        """
    )

    op.execute("drop policy if exists class_sessions_insert_owner_prof on public.class_sessions;")
    op.execute(
        """
        create policy class_sessions_insert_owner_prof on public.class_sessions
        for insert to authenticated
        with check (public.is_group_owner_or_professor(group_id));
        """
    )

    op.execute("drop policy if exists class_sessions_update_owner_prof on public.class_sessions;")
    op.execute(
        """
        create policy class_sessions_update_owner_prof on public.class_sessions
        for update to authenticated
        using (public.is_group_owner_or_professor(group_id))
        with check (public.is_group_owner_or_professor(group_id));
        """
    )

    op.execute("drop policy if exists class_sessions_delete_owner_prof on public.class_sessions;")
    op.execute(
        """
        create policy class_sessions_delete_owner_prof on public.class_sessions
        for delete to authenticated
        using (public.is_group_owner_or_professor(group_id));
        """
    )

    # ── the view gains class_session_id, notes-stripping untouched ──
    op.execute(_DROP_VIEW)
    op.execute(_VIEW_WITH_CLASS)
    op.execute("grant select on public.group_member_sessions to authenticated;")


def downgrade() -> None:
    op.execute(_DROP_VIEW)
    op.execute(_VIEW_0025)
    op.execute("grant select on public.group_member_sessions to authenticated;")

    op.execute("drop policy if exists class_sessions_delete_owner_prof on public.class_sessions;")
    op.execute("drop policy if exists class_sessions_update_owner_prof on public.class_sessions;")
    op.execute("drop policy if exists class_sessions_insert_owner_prof on public.class_sessions;")
    op.execute("drop policy if exists class_sessions_select_member on public.class_sessions;")

    op.execute("drop function if exists public.attach_to_class(text);")
    op.execute("drop function if exists public.is_group_owner_or_professor(uuid);")

    op.drop_column("user_sessions", "class_session_id")
    op.drop_index("idx_class_sessions_group", table_name="class_sessions")
    op.drop_table("class_sessions")
