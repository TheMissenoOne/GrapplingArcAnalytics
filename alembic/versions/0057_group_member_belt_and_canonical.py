"""Two measured gaps in the professor's read of a student, both projection-only.

**1. The corpus match was missing.** ``group_member_graph_edges`` (0054) joins
``graph_nodes sn``/``tn`` for label/type but never selected ``canonical_node_key`` — a student
whose own labels are in PT never lines up with the (English) public corpus in the professor's
view, the same gap ``derive/corpusInsights.ts`` already closes for the athlete's OWN graph
(``graph.ts``'s ``canonicalNodeKey``). ``sn``/``tn`` are already joined; this is two extra
columns off an existing join, no new join needed.

**2. The belt was missing.** ``group_member_names`` (0045) projects `profile_id, full_name`
only — deliberately, per its own docstring: "Adding `belt_rank` here later is a privacy
decision, not a convenience, and it deserves its own revision saying so." This is that revision.
Same access gate (`shares_group_as_professor`), same table (`profiles`), two more columns.
``is_pro``/``archetype_id``/``is_guest`` stay out, same as before — `profiles` has no email
column, so there is nothing to leak there either.

**Why DROP before CREATE OR REPLACE.** Postgres will not let `create or replace function`
change a `returns table(...)` column list — that's a return-type change, not a body edit, and
raises `cannot change return type of existing function`. Both functions here are drop-then-
recreate, same final signature (`(uuid)` / `(uuid, uuid)`) so nothing that calls them by name
breaks, but the DROP wipes the grants 0045/0054 set up, so both are reconceded here rather than
assumed to survive.

Privacy class: **C, user cloud-synced private data**, same class 0045/0054 already assigned —
nothing here is aggregated, exported, or visible outside the professor relation, and no new
column crosses that boundary that wasn't already discussed and deferred by name.

Scope note (test coverage): source-scan, same shape as 0045/0054 — this repo's suite runs
against SQLite in-memory and never executes a Postgres migration (0019's scope note).
``tests/test_group_member_names.py`` / ``tests/test_group_member_rating.py``.

Revision ID: 0057
Revises: 0056
Create Date: 2026-09-03
"""

from __future__ import annotations

from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


_NAMES_FN = """
create or replace function public.group_member_names(p_group_id uuid)
returns table (
  profile_id uuid,
  full_name text,
  belt_rank text,
  belt_degrees integer
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    p.id as profile_id,
    p.full_name,
    p.belt_rank,
    p.belt_degrees
  from public.group_members gm
  join public.profiles p on p.id = gm.profile_id
  where gm.group_id = p_group_id
    and shares_group_as_professor(gm.profile_id);
$$;
"""

_NAMES_FN_0045 = """
create or replace function public.group_member_names(p_group_id uuid)
returns table (
  profile_id uuid,
  full_name text
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    p.id as profile_id,
    p.full_name
  from public.group_members gm
  join public.profiles p on p.id = gm.profile_id
  where gm.group_id = p_group_id
    and shares_group_as_professor(gm.profile_id);
$$;
"""

_GRAPH_EDGES_FN = """
create or replace function public.group_member_graph_edges(p_group_id uuid, p_profile_id uuid)
returns table (
  source_key text,
  source_label text,
  source_type text,
  source_canonical text,
  target_key text,
  target_label text,
  target_type text,
  target_canonical text,
  elo double precision
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    ge.source_key, sn.label, sn.node_type, sn.canonical_node_key,
    ge.target_key, tn.label, tn.node_type, tn.canonical_node_key,
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

_GRAPH_EDGES_FN_0054 = """
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


def upgrade() -> None:
    # ── group_member_names: + belt_rank, belt_degrees ──────────────────────
    op.execute("drop function if exists public.group_member_names(uuid);")
    op.execute(_NAMES_FN)
    op.execute("revoke all on function public.group_member_names(uuid) from public;")
    op.execute("revoke all on function public.group_member_names(uuid) from anon;")
    op.execute("grant execute on function public.group_member_names(uuid) to authenticated;")

    # ── group_member_graph_edges: + source_canonical, target_canonical ─────
    op.execute("drop function if exists public.group_member_graph_edges(uuid, uuid);")
    op.execute(_GRAPH_EDGES_FN)
    op.execute(
        "revoke all on function public.group_member_graph_edges(uuid, uuid) from public;"
    )
    op.execute(
        "revoke all on function public.group_member_graph_edges(uuid, uuid) from anon;"
    )
    op.execute(
        "grant execute on function public.group_member_graph_edges(uuid, uuid) to authenticated;"
    )


def downgrade() -> None:
    op.execute("drop function if exists public.group_member_graph_edges(uuid, uuid);")
    op.execute(_GRAPH_EDGES_FN_0054)
    op.execute(
        "revoke all on function public.group_member_graph_edges(uuid, uuid) from public;"
    )
    op.execute(
        "revoke all on function public.group_member_graph_edges(uuid, uuid) from anon;"
    )
    op.execute(
        "grant execute on function public.group_member_graph_edges(uuid, uuid) to authenticated;"
    )

    op.execute("drop function if exists public.group_member_names(uuid);")
    op.execute(_NAMES_FN_0045)
    op.execute("revoke all on function public.group_member_names(uuid) from public;")
    op.execute("revoke all on function public.group_member_names(uuid) from anon;")
    op.execute("grant execute on function public.group_member_names(uuid) to authenticated;")
