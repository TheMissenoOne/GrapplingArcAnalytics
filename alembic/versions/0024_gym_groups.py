"""Gym groups: a professor's group, invites, RLS, the notes-stripping view, video storage.

Component 1-4 of the gym-groups piece (``docs/superpowers/specs/2026-08-11-gym-groups-design.md``):
``groups``/``group_members``/``group_invites`` tables, ``join_group()`` (``SECURITY DEFINER`` — RLS
cannot validate an invite code without letting a client enumerate codes), the RLS policies, the
``group_member_sessions`` view (the professor's ONLY read path — strips the session's ``reflection``
and each round's own ``notes``), and the ``session-videos`` storage bucket + policies.

No ``insert`` policy on ``group_members``: joining goes through ``join_group`` only. Leaving is a
plain delete of the caller's own row, expressed by ordinary RLS.

The ``create table`` statements below also fire the ``ensure_rls`` event trigger adopted in 0023,
which enables RLS on each new table a second time. Harmless (idempotent) — the explicit
``alter table ... enable row level security`` calls below stay regardless, because this migration
must not depend on that event trigger being installed to be correct.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "group_members",
        sa.Column(
            "group_id",
            UUID(as_uuid=False),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "profile_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role in ('owner','professor','student')", name="ck_group_members_role"),
    )
    op.create_index("idx_group_members_profile", "group_members", ["profile_id"])
    op.create_table(
        "group_invites",
        sa.Column("code", sa.Text(), primary_key=True),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )

    # ── join_group — SECURITY DEFINER, the only path into group_members ──────
    op.execute(
        """
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

          insert into public.group_members (group_id, profile_id, role)
          values (target_group, auth.uid(), 'student')
          on conflict (group_id, profile_id) do nothing;

          return query
            select g.id, g.name from public.groups g where g.id = target_group;
        end;
        $$;
        """
    )
    op.execute("revoke all on function public.join_group(text) from public;")
    op.execute("grant execute on function public.join_group(text) to authenticated;")

    # ── Policies ───────────────────────────────────────────────────────────
    op.execute("alter table public.groups enable row level security;")
    op.execute("alter table public.group_members enable row level security;")
    op.execute("alter table public.group_invites enable row level security;")

    op.execute("drop policy if exists groups_select_member on public.groups;")
    op.execute(
        """
        create policy groups_select_member on public.groups for select to authenticated
        using (exists (select 1 from public.group_members m
                       where m.group_id = groups.id and m.profile_id = auth.uid()));
        """
    )

    op.execute("drop policy if exists groups_insert_own on public.groups;")
    op.execute(
        """
        create policy groups_insert_own on public.groups for insert to authenticated
        with check (owner_id = auth.uid());
        """
    )

    op.execute("drop policy if exists group_members_select_same_group on public.group_members;")
    op.execute(
        """
        create policy group_members_select_same_group on public.group_members for select
        to authenticated
        using (exists (select 1 from public.group_members me
                       where me.group_id = group_members.group_id and me.profile_id = auth.uid()));
        """
    )

    # The student leaves; the owner removes.
    op.execute("drop policy if exists group_members_delete_self_or_owner on public.group_members;")
    op.execute(
        """
        create policy group_members_delete_self_or_owner on public.group_members for delete
        to authenticated
        using (profile_id = auth.uid()
               or exists (select 1 from public.groups g
                          where g.id = group_members.group_id and g.owner_id = auth.uid()));
        """
    )

    op.execute("drop policy if exists group_invites_owner_all on public.group_invites;")
    op.execute(
        """
        create policy group_invites_owner_all on public.group_invites for all to authenticated
        using (exists (select 1 from public.groups g
                       where g.id = group_invites.group_id and g.owner_id = auth.uid()))
        with check (exists (select 1 from public.groups g
                            where g.id = group_invites.group_id and g.owner_id = auth.uid()));
        """
    )

    # ── The view — this is where the notes are stripped ───────────────────────
    # Both levels matter: the session's reflection and every round's own notes.
    # RoundSnapshot does not declare `notes`, but RoundSheet writes it — strip only
    # `reflection` and the diary leaks through the rounds.
    op.execute(
        """
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
        """
    )
    op.execute("grant select on public.group_member_sessions to authenticated;")

    # ── Storage: private video bucket, owner-prefix policies ──────────────────
    op.execute(
        """
        insert into storage.buckets (id, name, public)
        values ('session-videos', 'session-videos', false)
        on conflict (id) do nothing;
        """
    )

    op.execute("drop policy if exists session_videos_owner_write on storage.objects;")
    op.execute(
        """
        create policy session_videos_owner_write on storage.objects for all to authenticated
        using (bucket_id = 'session-videos'
               and (storage.foldername(name))[1] = auth.uid()::text)
        with check (bucket_id = 'session-videos'
                    and (storage.foldername(name))[1] = auth.uid()::text);
        """
    )

    op.execute("drop policy if exists session_videos_professor_read on storage.objects;")
    op.execute(
        """
        create policy session_videos_professor_read on storage.objects for select to authenticated
        using (bucket_id = 'session-videos' and exists (
          select 1 from public.group_members me
          join public.group_members them using (group_id)
          where me.profile_id = auth.uid()
            and me.role in ('owner','professor')
            and them.profile_id::text = (storage.foldername(name))[1]
        ));
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists session_videos_professor_read on storage.objects;")
    op.execute("drop policy if exists session_videos_owner_write on storage.objects;")
    op.execute("delete from storage.buckets where id = 'session-videos';")
    op.execute("drop view if exists public.group_member_sessions;")
    op.execute("drop policy if exists group_invites_owner_all on public.group_invites;")
    op.execute("drop policy if exists group_members_delete_self_or_owner on public.group_members;")
    op.execute("drop policy if exists group_members_select_same_group on public.group_members;")
    op.execute("drop policy if exists groups_insert_own on public.groups;")
    op.execute("drop policy if exists groups_select_member on public.groups;")
    op.execute("drop function if exists public.join_group(text);")
    op.drop_table("group_invites")
    op.drop_index("idx_group_members_profile", table_name="group_members")
    op.drop_table("group_members")
    op.drop_table("groups")
