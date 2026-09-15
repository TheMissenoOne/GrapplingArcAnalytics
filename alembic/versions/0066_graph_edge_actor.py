"""``graph_edges.actor`` — whose game a transition belongs to, on the SYNCED graph.

Owner rule (binding, 2026-09-15): "No mapa, as edges devem poder ter actor, pois certas
ações vêm do usuário e outras do parceiro." The App already carries actor at three layers —
``RoundEntry.actor``, ``ChainAction.actor``, ``MapEdge.actor`` (derived by
``services/map/mapGraph.ts:edgeArrow``-adjacent ``edgeActor()``, rendered: partner = orange) —
but the SYNCED graph (what ``graphSync.ts`` pushes through ``replace_user_graph``, and what a
professor reads back through ``group_member_graph_edges``) only ever carried ``elo``/``setup``
per edge. A professor reading a student's persisted graph therefore has no way to tell "the
student's own move" from "something their partner did to them" — this revision closes that gap
at the two producers/consumers that touch the persisted table.

Column is nullable and NEVER backfilled: a pre-existing synced edge has no reliable single
actor (it predates this field), and NULL is exactly the honest answer, not a default that
picks a side. The App re-derives ``actor`` at push time from the same session aggregation the
map already uses (``ponytail:`` note at the App call site), so a normal re-sync fills it in
organically — no bulk migration script, no replay.

Athlete rows (``owner_kind='athlete'``) never set this column — the public corpus's event
model already has its own ``actor`` (which fighter a node's sequence event belongs to, a
different axis entirely: match participant, not you/partner) and nothing here touches it. This
column only means something for ``owner_kind='user'`` rows, same partition ``owner_kind``
already carries.

Two functions change together because both are the ends of the same pipe:
  - ``replace_user_graph`` — the only write path (``graphSync.ts``'s RPC call) — gains an
    optional ``actor`` key per edge object in ``p_edges``; missing/unrecognised value stores
    NULL, so an old (not-yet-updated) App build keeps working unchanged.
  - ``group_member_graph_edges`` — the professor's scoped read — gains an ``actor`` output
    column off the same row, no new join (the column already lives on ``graph_edges``).

Same ``drop`` + ``create or replace`` shape 0057 used for the same reason: Postgres refuses to
change ``returns table(...)`` in place.

Privacy class: **C, user cloud-synced private data**, same class the touched table already
carries (0037) — ``actor`` is ``'you'``/``'partner'``/``'both'``, never an identity, and the
professor read stays gated by the same ``shares_group_as_professor`` predicate 0054/0057 use.

Scope note (test coverage): source-scan, same convention as
``tests/test_technique_nodes_origin.py`` — SQLite in-memory never executes a Postgres migration.

Revision ID: 0066
Revises: 0065
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


_REPLACE_FN = """
create or replace function public.replace_user_graph(
    p_user_elo double precision,
    p_nodes jsonb,
    p_edges jsonb
)
returns uuid
language plpgsql
security definer
set search_path to 'public'
as $$
declare
    v_owner uuid := auth.uid();
    v_graph_id uuid;
begin
    if v_owner is null then
        raise exception 'replace_user_graph requires an authenticated caller'
            using errcode = '42501';
    end if;

    insert into public.graphs (owner_kind, owner_id, user_elo, schema_version, synced_at)
    values ('user', v_owner, p_user_elo, 3, now())
    on conflict (owner_kind, owner_id) do update
        set user_elo = excluded.user_elo,
            schema_version = excluded.schema_version,
            synced_at = excluded.synced_at,
            updated_at = now()
    returning id into v_graph_id;

    insert into public.graph_nodes (graph_id, node_key, label, type, node_type, canonical_node_key)
    select v_graph_id,
           n->>'node_key',
           coalesce(n->>'label', n->>'node_key'),
           coalesce(n->>'type', 'technique'),
           n->>'node_type',
           tn.node_key
      from jsonb_array_elements(coalesce(p_nodes, '[]'::jsonb)) as n
      left join public.technique_nodes tn on tn.node_key = n->>'node_key'
     where coalesce(n->>'node_key', '') <> ''
    on conflict (graph_id, node_key) do update
        set label = excluded.label,
            type = excluded.type,
            node_type = excluded.node_type,
            canonical_node_key = excluded.canonical_node_key;

    insert into public.graph_edges
        (graph_id, edge_key, source_key, target_key, owner_kind, elo, setup, actor)
    select v_graph_id,
           e->>'edge_key',
           e->>'source_key',
           e->>'target_key',
           'user',
           coalesce((e->>'elo')::double precision, 0),
           coalesce(e->>'setup', ''),
           nullif(e->>'actor', '')
      from jsonb_array_elements(coalesce(p_edges, '[]'::jsonb)) as e
     where coalesce(e->>'source_key', '') <> '' and coalesce(e->>'target_key', '') <> ''
    on conflict (graph_id, edge_key) do update
        set source_key = excluded.source_key,
            target_key = excluded.target_key,
            elo = excluded.elo,
            setup = excluded.setup,
            actor = excluded.actor;

    delete from public.graph_edges ge
     where ge.graph_id = v_graph_id
       and ge.edge_key not in (
           select e->>'edge_key'
             from jsonb_array_elements(coalesce(p_edges, '[]'::jsonb)) as e
            where coalesce(e->>'edge_key', '') <> ''
       );

    delete from public.graph_nodes gn
     where gn.graph_id = v_graph_id
       and gn.node_key not in (
           select n->>'node_key'
             from jsonb_array_elements(coalesce(p_nodes, '[]'::jsonb)) as n
            where coalesce(n->>'node_key', '') <> ''
       );

    return v_graph_id;
end;
$$;
"""

_REPLACE_FN_0037 = """
create or replace function public.replace_user_graph(
    p_user_elo double precision,
    p_nodes jsonb,
    p_edges jsonb
)
returns uuid
language plpgsql
security definer
set search_path to 'public'
as $$
declare
    v_owner uuid := auth.uid();
    v_graph_id uuid;
begin
    if v_owner is null then
        raise exception 'replace_user_graph requires an authenticated caller'
            using errcode = '42501';
    end if;

    insert into public.graphs (owner_kind, owner_id, user_elo, schema_version, synced_at)
    values ('user', v_owner, p_user_elo, 3, now())
    on conflict (owner_kind, owner_id) do update
        set user_elo = excluded.user_elo,
            schema_version = excluded.schema_version,
            synced_at = excluded.synced_at,
            updated_at = now()
    returning id into v_graph_id;

    insert into public.graph_nodes (graph_id, node_key, label, type, node_type, canonical_node_key)
    select v_graph_id,
           n->>'node_key',
           coalesce(n->>'label', n->>'node_key'),
           coalesce(n->>'type', 'technique'),
           n->>'node_type',
           tn.node_key
      from jsonb_array_elements(coalesce(p_nodes, '[]'::jsonb)) as n
      left join public.technique_nodes tn on tn.node_key = n->>'node_key'
     where coalesce(n->>'node_key', '') <> ''
    on conflict (graph_id, node_key) do update
        set label = excluded.label,
            type = excluded.type,
            node_type = excluded.node_type,
            canonical_node_key = excluded.canonical_node_key;

    insert into public.graph_edges
        (graph_id, edge_key, source_key, target_key, owner_kind, elo, setup)
    select v_graph_id,
           e->>'edge_key',
           e->>'source_key',
           e->>'target_key',
           'user',
           coalesce((e->>'elo')::double precision, 0),
           coalesce(e->>'setup', '')
      from jsonb_array_elements(coalesce(p_edges, '[]'::jsonb)) as e
     where coalesce(e->>'source_key', '') <> '' and coalesce(e->>'target_key', '') <> ''
    on conflict (graph_id, edge_key) do update
        set source_key = excluded.source_key,
            target_key = excluded.target_key,
            elo = excluded.elo,
            setup = excluded.setup;

    delete from public.graph_edges ge
     where ge.graph_id = v_graph_id
       and ge.edge_key not in (
           select e->>'edge_key'
             from jsonb_array_elements(coalesce(p_edges, '[]'::jsonb)) as e
            where coalesce(e->>'edge_key', '') <> ''
       );

    delete from public.graph_nodes gn
     where gn.graph_id = v_graph_id
       and gn.node_key not in (
           select n->>'node_key'
             from jsonb_array_elements(coalesce(p_nodes, '[]'::jsonb)) as n
            where coalesce(n->>'node_key', '') <> ''
       );

    return v_graph_id;
end;
$$;
"""

_REPLACE_SIG = "public.replace_user_graph(double precision, jsonb, jsonb)"

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
  elo double precision,
  actor text
)
language sql
stable
security definer
set search_path to 'public'
as $$
  select
    ge.source_key, sn.label, sn.node_type, sn.canonical_node_key,
    ge.target_key, tn.label, tn.node_type, tn.canonical_node_key,
    ge.elo,
    ge.actor
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

_GRAPH_EDGES_FN_0057 = """
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


def upgrade() -> None:
    op.add_column("graph_edges", sa.Column("actor", sa.Text(), nullable=True))
    op.execute(
        "alter table graph_edges add constraint ck_graph_edges_actor "
        "check (actor is null or actor in ('you', 'partner', 'both'))"
    )

    op.execute(_REPLACE_FN)
    op.execute(f"revoke all on function {_REPLACE_SIG} from public, anon;")
    op.execute(f"grant execute on function {_REPLACE_SIG} to authenticated;")

    op.execute("drop function if exists public.group_member_graph_edges(uuid, uuid);")
    op.execute(_GRAPH_EDGES_FN)
    op.execute("revoke all on function public.group_member_graph_edges(uuid, uuid) from public;")
    op.execute("revoke all on function public.group_member_graph_edges(uuid, uuid) from anon;")
    op.execute(
        "grant execute on function public.group_member_graph_edges(uuid, uuid) to authenticated;"
    )


def downgrade() -> None:
    op.execute("drop function if exists public.group_member_graph_edges(uuid, uuid);")
    op.execute(_GRAPH_EDGES_FN_0057)
    op.execute("revoke all on function public.group_member_graph_edges(uuid, uuid) from public;")
    op.execute("revoke all on function public.group_member_graph_edges(uuid, uuid) from anon;")
    op.execute(
        "grant execute on function public.group_member_graph_edges(uuid, uuid) to authenticated;"
    )

    op.execute(_REPLACE_FN_0037)
    op.execute(f"revoke all on function {_REPLACE_SIG} from public, anon;")
    op.execute(f"grant execute on function {_REPLACE_SIG} to authenticated;")

    op.execute("alter table graph_edges drop constraint if exists ck_graph_edges_actor;")
    op.drop_column("graph_edges", "actor")
