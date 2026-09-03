"""Consent at join, a scoped rating read for the professor, and the professor's own note.

Three pieces of the owner's 2026-09-03 decision log (items 5/6, "acesso professor-aluno" and
"ratings para o professor"), all group-scoped and none of them touching the Elo/Glicko-2 write
path — see root ``CLAUDE.md``'s ELO contract row and ``docs/rating_v2/`` for why that path is
untouchable from here.

**1. Consent at join.** ``group_members.consent_at`` records the moment a fresh join confirmed
the disclosure the Web now shows before calling ``join_group()`` ("your professors here can see
your sessions and rating"). Nullable: NULL means "predates this column" or "the owner's own row
from ``create_group()``", never "declined" — there is no declined state, a decline just never
calls the RPC. ``join_group()`` stamps ``now()`` only on the INSERT branch; the existing ``on
conflict ... do nothing`` means a re-join (already a member) leaves an existing member's
``consent_at`` alone, same non-destructive shape 0052 already committed to for ``role``.

Deliberately NOT extended to the "non-member scans a class QR" case from the same decision item:
measured against the current schema, ``attach_to_class`` (0026) already ``raise``s
``invalid_or_not_member`` for anyone who is not `is_group_member` first — a non-member cannot
reach a class today, by construction, so there is no live "professor sees beyond the one class"
path to correct here. Building an actual drop-in (attend one class without joining the gym) is a
new ``attach_to_class`` shape, App-side QR UX, and its own consent copy — out of scope for this
revision; flagged for the product owner rather than built speculatively.

**2. A scoped rating read.** ``group_member_rating``/``group_member_graph_edges`` are the
GA-02 shape (root roadmap): SECURITY DEFINER, project only what's needed, answer the access
question inside the function — copying ``group_member_names`` (0045). Two differences from
0045's own shape, both deliberate:

- They read the student's OWN ``graphs``/``graph_edges`` (``owner_kind = 'user'``), never an
  athlete graph — this is a professor reading a student's private data under the one narrow
  exception the gym relationship grants, not a window into the public corpus (decision item 3,
  "escopo sem sangramento").
- They filter on ``gm.group_id = p_group_id`` in addition to ``shares_group_as_professor``. The
  roadmap's GA-31 finding is that ``shares_group_as_professor`` alone answers "do we share ANY
  group", not "is this student in THIS gym" — multi-gym is shipped but has 0 profiles exercising
  it in production today, so this is latent rather than live, but a brand-new read model gets to
  not repeat a known-wrong shape from day one rather than needing a second migration later.

No rating deviation, no confidence tier, no history. ``graphs.user_elo``/``graph_edges.elo`` are
the ONLY rating fields synced for a user graph — Glicko-2's RD/volatility only ever land in
``athlete_rating_states_v2``/``athlete_node_rating_states_v2`` (0035/0036), explicitly athlete-
only, because the App's ``ratingV2Projection.ts`` only ever overwrites ``computedElo`` (the
point estimate) on sync, never RD. Mirroring the App's 100/200 RD confidence-tier cuts
(``ratingV2Presentation.ts`` / ``analysis/rating_v2/presentation.py``, ADR-14) is therefore not
buildable from what actually reaches this database for a user graph — the Web side shows the
point estimate, relative and unlabelled-percent per the existing "Grappling Rating" convention,
and says plainly that no history or confidence figure is synced yet, rather than fabricating one.

**3. The professor's own note.** ``professor_evaluations`` — see ``db/models.py``'s docstring on
the model. Read by group staff or by the student about themselves; written by staff, in their own
name. No update/delete policy: a zero-row table doesn't justify solving the "professor mistypes a
note" problem before it has one real row, same call 0050 already made for instructional deletes.

Revision ID: 0054
Revises: 0053
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_JOIN_GROUP_WITH_CONSENT = """
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

_JOIN_GROUP_0052 = """
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

  insert into public.group_members (group_id, profile_id, role)
  values (target_group, auth.uid(), target_role)
  on conflict on constraint group_members_pkey do nothing;

  return query
    select g.id, g.name from public.groups g where g.id = target_group;
end;
$$;
"""

_GROUP_MEMBER_RATING = """
create or replace function public.group_member_rating(p_group_id uuid, p_profile_id uuid)
returns table (
  user_elo double precision,
  updated_at timestamptz
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select g.user_elo, g.updated_at
  from public.group_members gm
  join public.graphs g
    on g.owner_kind = 'user' and g.owner_id = p_profile_id
  where gm.group_id = p_group_id
    and gm.profile_id = p_profile_id
    and shares_group_as_professor(p_profile_id);
$$;
"""

_GROUP_MEMBER_GRAPH_EDGES = """
create or replace function public.group_member_graph_edges(p_group_id uuid, p_profile_id uuid)
returns table (
  source_key text,
  source_label text,
  source_type text,
  target_key text,
  target_label text,
  target_type text,
  elo double precision
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    ge.source_key, sn.label, sn.node_type,
    ge.target_key, tn.label, tn.node_type,
    ge.elo
  from public.group_members gm
  join public.graphs g
    on g.owner_kind = 'user' and g.owner_id = p_profile_id
  join public.graph_edges ge on ge.graph_id = g.id
  join public.graph_nodes sn on sn.graph_id = g.id and sn.node_key = ge.source_key
  join public.graph_nodes tn on tn.graph_id = g.id and tn.node_key = ge.target_key
  where gm.group_id = p_group_id
    and gm.profile_id = p_profile_id
    and shares_group_as_professor(p_profile_id);
$$;
"""

_RATING_FUNCTIONS = (
    "public.group_member_rating(uuid, uuid)",
    "public.group_member_graph_edges(uuid, uuid)",
)


def upgrade() -> None:
    # ── 1. consent at join ────────────────────────────────────────────────
    op.add_column("group_members", sa.Column("consent_at", sa.DateTime(timezone=True)))
    op.execute(_JOIN_GROUP_WITH_CONSENT)

    # ── 2. scoped rating read (GA-02 shape, group_id-scoped from day one) ──
    op.execute(_GROUP_MEMBER_RATING)
    op.execute(_GROUP_MEMBER_GRAPH_EDGES)
    for fn in _RATING_FUNCTIONS:
        # Same belt-and-braces as 0032/0045/0050: revoking from PUBLIC alone does not remove
        # Supabase's default anon grant (0028), so both are revoked explicitly.
        op.execute(f"revoke all on function {fn} from public;")
        op.execute(f"revoke all on function {fn} from anon;")
        op.execute(f"grant execute on function {fn} to authenticated;")

    # ── 3. the professor's own note, separate from every Elo table ─────────
    op.create_table(
        "professor_evaluations",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "group_id", UUID(as_uuid=False), sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id", UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "professor_id", UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating_note", sa.Text(), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_professor_evaluations_group", "professor_evaluations", ["group_id"])
    op.create_index("idx_professor_evaluations_student", "professor_evaluations", ["student_id"])

    op.execute("alter table public.professor_evaluations enable row level security;")
    # Only select/insert granted — no update/delete verb, no update/delete policy. See the
    # module docstring: a zero-row table doesn't justify the correction UX yet.
    op.execute("revoke all on public.professor_evaluations from anon, authenticated;")
    op.execute("grant select, insert on public.professor_evaluations to authenticated;")

    op.execute(
        "drop policy if exists professor_evaluations_select on public.professor_evaluations;"
    )
    op.execute("""
    create policy professor_evaluations_select on public.professor_evaluations
    for select to authenticated
    using (public.is_group_owner_or_professor(group_id) or student_id = auth.uid());
    """)

    op.execute(
        "drop policy if exists professor_evaluations_insert on public.professor_evaluations;"
    )
    op.execute("""
    create policy professor_evaluations_insert on public.professor_evaluations
    for insert to authenticated
    with check (public.is_group_owner_or_professor(group_id) and professor_id = auth.uid());
    """)


def downgrade() -> None:
    op.execute(
        "drop policy if exists professor_evaluations_insert on public.professor_evaluations;"
    )
    op.execute(
        "drop policy if exists professor_evaluations_select on public.professor_evaluations;"
    )
    op.drop_index("idx_professor_evaluations_student", table_name="professor_evaluations")
    op.drop_index("idx_professor_evaluations_group", table_name="professor_evaluations")
    op.drop_table("professor_evaluations")

    for fn in _RATING_FUNCTIONS:
        op.execute(f"drop function if exists {fn};")

    op.execute(_JOIN_GROUP_0052)
    op.drop_column("group_members", "consent_at")
