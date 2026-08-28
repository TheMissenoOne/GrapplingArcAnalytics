"""Unlock the professor view: a group that actually gets an owner, class plans, instructionals.

**The gap this closes.** ``groups_insert_own`` (0024) lets a client INSERT a row into
``groups``. Nothing ever inserted the matching ``group_members`` row for the creator, because
the only path into ``group_members`` was ``join_group()`` (student, by invite code). So every
group created through the client API has an owner column pointing at a profile that is not a
*member* of their own group — ``is_group_member``/``is_group_owner_or_professor`` (0025/0026)
both say no, and every policy gated on them (class sessions, the roster, session video) locks
the professor out of the thing they just made. ``scripts/group_admin.py`` never hit this because
it writes both rows directly in one session, bypassing RLS entirely — that path is for the admin
console, not the App/Web client. ``create_group()`` is the client-facing equivalent: one
SECURITY DEFINER call, ``groups`` and ``group_members`` insert in the same transaction, same
shape as ``join_group``'s definer pattern (0024) so a client can never end up with one row and
not the other.

``set_member_role()`` is the only path that promotes a student to professor (or reverses it).
Gated on the group's ``owner_id = auth.uid()`` directly, not ``is_group_owner_or_professor`` —
promoting to professor is an owner-only decision. Deliberately cannot set ``'owner'``: handing
off ownership is a different, harder decision (single point of failure, billing, deletion
rights) and gets its own migration if it's ever built. Role is validated inside the function
body, not by widening the existing ``ck_group_members_role`` check constraint, because the
constraint still has to allow ``'owner'`` for the row ``create_group`` writes.

**Class planning.** ``class_sessions`` (0026) gains ``focus_node_keys`` (canonical
``technique_nodes.node_key`` strings — no FK, same reason ``UserNodeName`` skips one: a focus
may name a node that only exists in the App's bundled library) and ``plan`` (free text, the
written roteiro). ``class_plan_templates`` is the reusable version, scoped to the group so one
academy's "guard passing week" is invisible to another's — read by anyone in the group so a
student can see what's coming, written only by owner/professor, same split as every other
group-scoped table since 0026.

**Instructionals.** A professor's authored teaching material for the whole academy: title,
focus, either a video (bucket path) or a link, with a sort order for a syllabus-style list.
Read policy is ``is_group_member`` on purpose — a student in the academy is exactly who this
content is for, unlike ``group_member_sessions`` which flows the other direction (student to
professor). Write is owner/professor only, same helper as class plans.

**Privacy boundary — what this migration does NOT open.** A class plan and an instructional are
content the PROFESSOR authored, not data ABOUT a student — there is no student identity, score,
graph, or session reference anywhere in either table, so none of the private-data rules in root
``CLAUDE.md`` ("Public vs Private Data") apply to them the way they apply to ``user_sessions`` or
``user_study_notes``. Nothing here grants a professor one inch more reach into a student's own
data: no graph, no ``user_elo``, no ``profiles`` column beyond what ``group_member_names`` (0045)
already projects, no ``reflection``/notes, and critically no CLASS-LEVEL AGGREGATE of student
data — no average, ranking, or benchmark across the roster. That would be a new purpose over
private data and needs its own consent-aware decision, not a side effect of a scheduling table.

**Storage.** ``instructional-media`` is a new, separate bucket — NOT ``session-videos`` (0024,
owner-prefix + professor-read, built for a student's own round footage) and NOT ``user-media``
(0042, owner-only, built for the student's private study attachments; see that migration's
docstring for why a shared bucket was rejected once already). A professor's instructional
upload is neither of those: it is authored BY the professor FOR the whole group, so the read
side is ``is_group_member`` of the leading path segment (``<group_id>/<file>``) rather than an
owner match, and the write side is ``is_group_owner_or_professor`` of that same segment. Giving
this its own bucket keeps each bucket's policy answering exactly one access question, which is
the lesson 0042 already paid for.

Revision ID: 0050
Revises: 0049
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

_FUNCTIONS = (
    "public.create_group(text)",
    "public.set_member_role(uuid, uuid, text)",
)


def upgrade() -> None:
    # ── class planning columns ────────────────────────────────────────────
    op.add_column(
        "class_sessions",
        sa.Column(
            "focus_node_keys",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.add_column("class_sessions", sa.Column("plan", sa.Text(), nullable=True))

    # ── class_plan_templates ──────────────────────────────────────────────
    op.create_table(
        "class_plan_templates",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "focus_node_keys",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_class_plan_templates_group", "class_plan_templates", ["group_id"])

    # ── instructionals ─────────────────────────────────────────────────────
    op.create_table(
        "instructionals",
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
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "focus_node_keys",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("video_path", sa.Text(), nullable=True),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_instructionals_group", "instructionals", ["group_id"])

    # ── RLS: same select/write split as every group-scoped table since 0026 ──
    for table in ("class_plan_templates", "instructionals"):
        op.execute(f"alter table public.{table} enable row level security;")
        # RLS governs SELECT/INSERT/UPDATE/DELETE; 0039 already fixed the default privileges so
        # TRUNCATE/REFERENCES/TRIGGER never land on a table created after it. This explicit
        # revoke-then-grant is the belt the last few tables (0043) added on top of that anyway.
        op.execute(f"revoke all on public.{table} from anon, authenticated;")
        op.execute(f"grant select, insert, update, delete on public.{table} to authenticated;")

        op.execute(f"drop policy if exists {table}_select_member on public.{table};")
        op.execute(f"""
        create policy {table}_select_member on public.{table} for select to authenticated
        using (public.is_group_member(group_id));
        """)

        op.execute(f"drop policy if exists {table}_insert_owner_prof on public.{table};")
        op.execute(f"""
        create policy {table}_insert_owner_prof on public.{table} for insert to authenticated
        with check (public.is_group_owner_or_professor(group_id));
        """)

        op.execute(f"drop policy if exists {table}_update_owner_prof on public.{table};")
        op.execute(f"""
        create policy {table}_update_owner_prof on public.{table} for update to authenticated
        using (public.is_group_owner_or_professor(group_id))
        with check (public.is_group_owner_or_professor(group_id));
        """)

        op.execute(f"drop policy if exists {table}_delete_owner_prof on public.{table};")
        op.execute(f"""
        create policy {table}_delete_owner_prof on public.{table} for delete to authenticated
        using (public.is_group_owner_or_professor(group_id));
        """)

    # ── create_group — SECURITY DEFINER, groups + group_members(owner) in one transaction ──
    op.execute("""
    create or replace function public.create_group(p_name text)
    returns uuid
    language plpgsql
    security definer
    set search_path = public
    as $$
    declare
      new_group_id uuid;
    begin
      insert into public.groups (owner_id, name)
      values (auth.uid(), p_name)
      returning id into new_group_id;

      insert into public.group_members (group_id, profile_id, role)
      values (new_group_id, auth.uid(), 'owner');

      return new_group_id;
    end;
    $$;
    """)

    # ── set_member_role — owner only, never sets 'owner' (transfer is a separate decision) ──
    op.execute("""
    create or replace function public.set_member_role(
      p_group_id uuid, p_profile_id uuid, p_role text
    )
    returns void
    language plpgsql
    security definer
    set search_path = public
    as $$
    begin
      if p_role not in ('professor', 'student') then
        raise exception 'invalid_role' using errcode = 'P0001';
      end if;

      if not exists (
        select 1 from public.groups g
        where g.id = p_group_id and g.owner_id = auth.uid()
      ) then
        raise exception 'not_group_owner' using errcode = 'P0001';
      end if;

      update public.group_members
      set role = p_role
      where group_id = p_group_id and profile_id = p_profile_id;
    end;
    $$;
    """)

    for fn in _FUNCTIONS:
        op.execute(f"revoke all on function {fn} from public;")
        op.execute(f"revoke all on function {fn} from anon;")
        op.execute(f"grant execute on function {fn} to authenticated;")

    # ── storage: instructional-media, private, group-prefix policies ──────
    op.execute("""
    insert into storage.buckets (id, name, public)
    values ('instructional-media', 'instructional-media', false)
    on conflict (id) do nothing;
    """)

    op.execute("drop policy if exists instructional_media_professor_write on storage.objects;")
    op.execute("""
    create policy instructional_media_professor_write on storage.objects for all to authenticated
    using (bucket_id = 'instructional-media'
           and public.is_group_owner_or_professor(((storage.foldername(name))[1])::uuid))
    with check (bucket_id = 'instructional-media'
                and public.is_group_owner_or_professor(((storage.foldername(name))[1])::uuid));
    """)

    op.execute("drop policy if exists instructional_media_member_read on storage.objects;")
    op.execute("""
    create policy instructional_media_member_read on storage.objects for select to authenticated
    using (bucket_id = 'instructional-media'
           and public.is_group_member(((storage.foldername(name))[1])::uuid));
    """)


def downgrade() -> None:
    op.execute("drop policy if exists instructional_media_member_read on storage.objects;")
    op.execute("drop policy if exists instructional_media_professor_write on storage.objects;")
    # Bucket itself is NOT dropped — same reasoning as 0042: deleting storage.buckets on a
    # non-empty bucket either errors on the objects FK or destroys uploaded files, and a
    # downgrade must not be a data-destroying operation. An empty bucket left behind costs
    # nothing; re-upgrading is a no-op on conflict.

    for fn in _FUNCTIONS:
        op.execute(f"drop function if exists {fn};")

    for table in ("class_plan_templates", "instructionals"):
        op.execute(f"drop policy if exists {table}_delete_owner_prof on public.{table};")
        op.execute(f"drop policy if exists {table}_update_owner_prof on public.{table};")
        op.execute(f"drop policy if exists {table}_insert_owner_prof on public.{table};")
        op.execute(f"drop policy if exists {table}_select_member on public.{table};")

    op.drop_index("idx_instructionals_group", table_name="instructionals")
    op.drop_table("instructionals")
    op.drop_index("idx_class_plan_templates_group", table_name="class_plan_templates")
    op.drop_table("class_plan_templates")

    op.drop_column("class_sessions", "plan")
    op.drop_column("class_sessions", "focus_node_keys")
