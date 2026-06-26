"""Shared technique-node library — edge-only user graphs (pgvector-ready).

Creates ``technique_nodes``: one canonical row per distinct technique (``node_key``),
shared across all user/athlete graphs, replacing the per-user ``graph_nodes`` identity
rows that grow O(users x techniques). Edges already key by ``node_key``
(``source_key``/``target_key``), so the per-user node rows only ever added duplicated
static identity + derived stats.

Additive + idempotent: ``graph_nodes`` is kept for dual-read during rollout and dropped
in a later migration once the app writes edges + shared nodes. Seeds ``technique_nodes``
from the distinct ``node_key`` values already in ``graph_nodes`` (``source='user'``); the
curated library (``data/processed/technique_library.json``) is layered on top by
``scripts/seed_technique_nodes.py``. pgvector ``embedding`` columns land in a later
migration once the embedding model/dimension is chosen.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- ── Shared canonical technique library ──────────────────────────────
        create table if not exists public.technique_nodes (
          id         uuid primary key default gen_random_uuid(),
          node_key   text not null unique,          -- == analysis/names._normalize_name
          label      text not null,
          type       varchar(20) not null default 'technique',
          node_type  varchar(40) not null default '',
          source     varchar(10) not null default 'user',   -- 'library' | 'user'
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );

        -- Seed from the distinct node_keys already present in per-user graph_nodes.
        -- Representative label/type/node_type = the most common value per node_key.
        insert into public.technique_nodes (node_key, label, type, node_type, source)
        select g.node_key,
               (array_agg(g.label     order by g.cnt desc))[1],
               (array_agg(g.type      order by g.cnt desc))[1],
               (array_agg(g.node_type order by g.cnt desc))[1],
               'user'
        from (
          select node_key, label, type, node_type, count(*) as cnt
          from public.graph_nodes
          group by node_key, label, type, node_type
        ) g
        group by g.node_key
        on conflict (node_key) do nothing;

        -- ── RLS: the library is a public, world-readable vocabulary ─────────
        alter table public.technique_nodes enable row level security;

        drop policy if exists technique_nodes_public_read on public.technique_nodes;
        create policy technique_nodes_public_read on public.technique_nodes
          for select to anon, authenticated
          using (true);

        -- Signed-in users may add NOVEL techniques (source='user') so a synced
        -- edge's endpoints exist; they cannot edit/curate library rows.
        drop policy if exists technique_nodes_user_insert on public.technique_nodes;
        create policy technique_nodes_user_insert on public.technique_nodes
          for insert to authenticated
          with check (source = 'user');

        grant select on public.technique_nodes to anon, authenticated;
        grant insert on public.technique_nodes to authenticated;
        """
    )


def downgrade() -> None:
    op.execute("drop table if exists public.technique_nodes cascade;")
