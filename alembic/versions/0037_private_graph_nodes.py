"""A user's own words for their own game, kept out of the shared vocabulary.

``technique_nodes`` is a CURATED, WORLD-READABLE library. Its read policy is literally
``using (true)`` for ``anon`` and ``authenticated``, which is correct for a public technique
vocabulary and catastrophic for anything else. And yet the App's graph sync has been upserting
every label a user typed straight into it (``source='user'``), because until now edges had
nowhere else to point: ``graph_nodes`` was dropped in 0007 and node identity was reconstructed
from edge endpoints against the shared library.

Measured on production before writing this: of 328 ``source='user'`` rows, **215 are used by
athlete graphs** — legitimate public vocabulary carrying the wrong provenance — and **48 are
used only by user graphs**: "Guarda Fechada", "Chave de Braço", "Estrutura". Fifty private
labels, readable by anonymous.

So the table comes back, with a different job. 0007's ``graph_nodes`` was a per-user copy of the
library and was rightly deleted for it. This one is not a copy of anything: it is the graph's
OWN node identity, private by construction, with an OPTIONAL link to the curated library.

    graph_nodes(graph_id, node_key)  →  what this graph calls this node
      canonical_node_key             →  which curated technique it is, when that is known

The direction of the link is the whole point. A private node may reference public vocabulary;
public vocabulary never learns anything about a private node. Purpose limitation, expressed as
a foreign key.

**What this revision deliberately does NOT do.** It does not revoke the App's
``technique_nodes_user_insert`` policy, and it does not touch a single existing row. A deployed
App version still writes the old way, and breaking graph sync for everyone who has not updated
would be a worse privacy outcome than the one being fixed — users turn sync off and stop
getting backups. The revocation and the data repair are 0038, applied after the App ships. This
revision only makes the correct destination exist.

**The old endpoint FKs are the mechanism, not a detail.** 0005 added
``graph_edges_source_key_fkey`` and ``graph_edges_target_key_fkey``, both pointing at
``technique_nodes(node_key)``. That constraint is *why* the App leaked: an edge could not exist
unless both of its endpoints already had a row in the shared public library, so writing a
private label there was not a shortcut the client took — it was the only way to store an edge at
all. 0005 even backfills the library from edge endpoints for exactly that reason. Repointing
these two constraints at ``graph_nodes`` is therefore the fix itself; leaving them and adding
private nodes beside them would change nothing.

(``db/models.py`` describes these columns with a *comment* saying "FK → technique_nodes" and no
``ForeignKey`` declaration, so the model and the live schema have been out of step here since
0005. Both endpoint columns are corrected in the model in this change.)

The backfill has to run in the same revision, after the old constraints are dropped and before
the new ones are added, or no deployment with existing edges upgrades.

The backfill derives ``graph_nodes`` from the edges themselves, so it is complete by
construction: every endpoint that exists gets a row. Verified against production — 4,637
distinct ``(graph_id, node_key)`` pairs across 508 graphs with edges, and every one of them
resolves to a ``technique_nodes`` row for its label and type.

**Public exposure gets its own surface.** ``published_athlete_graph_nodes`` and
``published_athlete_graph_edges`` are what a client should read for athlete data, rather than
querying the private tables and relying on a policy to filter. The existing direct policies stay
for now so the deployed App and GrapplingArcWeb keep working; tightening them is a client-visible
change and belongs with those repos' PRs.

**``replace_user_graph``** replaces the App's four-step client-side write (upsert graph, upsert
library, upsert edges, and — until now — never prune anything, so a deleted edge lived in the
cloud forever). It is transactional, it derives the owner from ``auth.uid()`` and never from a
caller-supplied id, and it cannot write to ``technique_nodes`` because it does not mention it.

Privacy class: **C, user cloud-synced private data**, for the ``owner_kind='user'`` rows. Athlete
rows in the same table are public-by-publication and reachable only through the views and the
existing athlete read policy.

Scope note (test coverage): pytest here runs against SQLite in-memory (``tests/test_db.py``),
which round-trips the ``db/models.py`` shape but does NOT execute this migration's Postgres-only
policies, views or function. Same caveat as 0017/0019/0030/0031.

Revision ID: 0037
Revises: 0036
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


# Derive one row per (graph, endpoint) from the edges that already exist. `label` and `type`
# come from the curated library when the key is known there; when it is not, the key is its own
# label, which is exactly what the App's own defensive branch already does.
_BACKFILL = """
insert into public.graph_nodes (graph_id, node_key, label, type, node_type, canonical_node_key)
select
    e.graph_id,
    e.node_key,
    coalesce(tn.label, e.node_key)      as label,
    coalesce(tn.type, 'technique')      as type,
    tn.node_type,
    tn.node_key                          as canonical_node_key
from (
    select graph_id, source_key as node_key from public.graph_edges
    union
    select graph_id, target_key from public.graph_edges
) e
left join public.technique_nodes tn on tn.node_key = e.node_key
where e.node_key <> ''
on conflict (graph_id, node_key) do nothing;
"""

_PUBLISHED_NODES_VIEW = """
create view public.published_athlete_graph_nodes with (security_invoker = true) as
select gn.graph_id, gn.node_key, gn.label, gn.type, gn.node_type, gn.canonical_node_key,
       g.owner_id as athlete_id
  from public.graph_nodes gn
  join public.graphs g on g.id = gn.graph_id
  join public.athletes a on a.id = g.owner_id
 where g.owner_kind = 'athlete' and a.is_published;
"""

_PUBLISHED_EDGES_VIEW = """
create view public.published_athlete_graph_edges with (security_invoker = true) as
select ge.graph_id, ge.edge_key, ge.source_key, ge.target_key, ge.elo, ge.setup,
       g.owner_id as athlete_id
  from public.graph_edges ge
  join public.graphs g on g.id = ge.graph_id
  join public.athletes a on a.id = g.owner_id
 where g.owner_kind = 'athlete' and a.is_published;
"""

# One trusted write for the whole user graph.
#
# `security definer` so the prune can delete rows the caller could otherwise only delete one by
# one, but ownership is re-derived from `auth.uid()` inside — a caller-supplied id is never
# consulted, and there is no parameter that could carry one. `set search_path` is mandatory on a
# definer function (0034 exists because it was once missing).
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
            synced_at = excluded.synced_at
    returning id into v_graph_id;

    -- Nodes first: the edge constraints point at them.
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

    -- Prune what the device no longer has. The client-side writer never did this, so a deleted
    -- edge stayed in the cloud forever and came back on the next device that pulled.
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


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column(
            "graph_id",
            UUID(as_uuid=False),
            sa.ForeignKey("graphs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("node_key", sa.Text(), primary_key=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False, server_default="technique"),
        sa.Column("node_type", sa.Text(), nullable=True),
        # Optional, and only ever points OUTWARD: a private node may name a curated technique;
        # a curated technique never learns about a private node.
        sa.Column(
            "canonical_node_key",
            sa.Text(),
            sa.ForeignKey("technique_nodes.node_key", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Must precede the endpoint constraints below, or no deployment with existing edges upgrades.
    op.execute(_BACKFILL)

    # Drop 0005's endpoint FKs into the public library. These are what forced the App to publish
    # user labels: an edge was rejected unless both endpoints already existed in
    # `technique_nodes`, so there was no way to store a private transition privately.
    for _old_fk in ("graph_edges_source_key_fkey", "graph_edges_target_key_fkey"):
        op.execute(f"alter table public.graph_edges drop constraint if exists {_old_fk};")

    op.create_foreign_key(
        "graph_edges_source_node_fk",
        "graph_edges",
        "graph_nodes",
        ["graph_id", "source_key"],
        ["graph_id", "node_key"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "graph_edges_target_node_fk",
        "graph_edges",
        "graph_nodes",
        ["graph_id", "target_key"],
        ["graph_id", "node_key"],
        ondelete="CASCADE",
    )

    op.execute("alter table public.graph_nodes enable row level security;")
    op.execute("revoke all on public.graph_nodes from anon, authenticated;")
    op.execute("grant select, insert, update, delete on public.graph_nodes to authenticated;")
    op.execute("grant select on public.graph_nodes to anon;")

    op.execute("drop policy if exists graph_nodes_user_all on public.graph_nodes;")
    op.execute(
        """
        create policy graph_nodes_user_all on public.graph_nodes for all
          using (exists (
              select 1 from public.graphs g
               where g.id = graph_nodes.graph_id
                 and g.owner_kind = 'user'
                 and g.owner_id = auth.uid()
          ))
          with check (exists (
              select 1 from public.graphs g
               where g.id = graph_nodes.graph_id
                 and g.owner_kind = 'user'
                 and g.owner_id = auth.uid()
          ));
        """
    )

    # Mirrors the existing `edges_athlete_read`: published athletes only, read only. The views
    # below are `security_invoker`, so they need this to return anything.
    op.execute("drop policy if exists graph_nodes_athlete_read on public.graph_nodes;")
    op.execute(
        """
        create policy graph_nodes_athlete_read on public.graph_nodes for select
          using (exists (
              select 1 from public.graphs g
                join public.athletes a on a.id = g.owner_id
               where g.id = graph_nodes.graph_id
                 and g.owner_kind = 'athlete'
                 and a.is_published
          ));
        """
    )

    op.execute("drop view if exists public.published_athlete_graph_nodes;")
    op.execute("drop view if exists public.published_athlete_graph_edges;")
    op.execute(_PUBLISHED_NODES_VIEW)
    op.execute(_PUBLISHED_EDGES_VIEW)
    op.execute("grant select on public.published_athlete_graph_nodes to anon, authenticated;")
    op.execute("grant select on public.published_athlete_graph_edges to anon, authenticated;")

    op.execute(_REPLACE_FN)
    op.execute(f"revoke all on function {_REPLACE_SIG} from public, anon;")
    op.execute(f"grant execute on function {_REPLACE_SIG} to authenticated;")


def downgrade() -> None:
    op.execute(f"drop function if exists {_REPLACE_SIG};")
    op.execute("drop view if exists public.published_athlete_graph_edges;")
    op.execute("drop view if exists public.published_athlete_graph_nodes;")
    op.execute("drop policy if exists graph_nodes_athlete_read on public.graph_nodes;")
    op.execute("drop policy if exists graph_nodes_user_all on public.graph_nodes;")
    op.drop_constraint("graph_edges_target_node_fk", "graph_edges", type_="foreignkey")
    op.drop_constraint("graph_edges_source_node_fk", "graph_edges", type_="foreignkey")
    op.drop_table("graph_nodes")

    # Restoring 0005's constraints can only work if every endpoint still has a library row.
    # After the private nodes exist that is no longer guaranteed, so they come back NOT VALID:
    # future writes are checked, existing rows are left alone rather than blocking the
    # downgrade on data this revision legitimised.
    op.execute(
        """
        alter table public.graph_edges
          add constraint graph_edges_source_key_fkey
          foreign key (source_key) references public.technique_nodes (node_key) not valid;
        alter table public.graph_edges
          add constraint graph_edges_target_key_fkey
          foreign key (target_key) references public.technique_nodes (node_key) not valid;
        """
    )
