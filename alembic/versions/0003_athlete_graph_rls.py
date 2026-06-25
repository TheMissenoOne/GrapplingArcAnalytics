"""Athlete-graph RLS + published-view hardening.

Brings the athlete-graph access control into version control so
``alembic upgrade head`` applies it (fail-closed) instead of relying on a
manually-run .sql that a fresh deploy can skip — skipping it would leave the
graph tables with RLS disabled and world-readable.

Idempotent: safe on databases where these objects were already created
out-of-band (drop-if-exists on policies; enable-RLS / grant / alter-view are
naturally idempotent).

Scope note: user-graph RLS and the ``authenticated`` table grants still live in
db/auth_setup.sql (manual), because they are coupled to Supabase's auth.users
triggers. Only the pure public-schema athlete policies + the view's security
model move here.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- Enable RLS (fail closed) — idempotent.
        alter table public.graphs      enable row level security;
        alter table public.graph_nodes enable row level security;
        alter table public.graph_edges enable row level security;
        alter table public.athletes    enable row level security;

        -- athletes: published profiles are world-readable.
        drop policy if exists athletes_published_read on public.athletes;
        create policy athletes_published_read on public.athletes
          for select to anon, authenticated
          using (is_published);

        -- athlete graphs/nodes/edges: readable only when the owning athlete is published.
        drop policy if exists graphs_athlete_read on public.graphs;
        create policy graphs_athlete_read on public.graphs
          for select to anon, authenticated
          using (
            owner_kind = 'athlete'
            and exists (
              select 1 from public.athletes a
              where a.id = graphs.owner_id and a.is_published
            )
          );

        drop policy if exists nodes_athlete_read on public.graph_nodes;
        create policy nodes_athlete_read on public.graph_nodes
          for select to anon, authenticated
          using (
            exists (
              select 1 from public.graphs g
              join public.athletes a on a.id = g.owner_id
              where g.id = graph_nodes.graph_id
                and g.owner_kind = 'athlete' and a.is_published
            )
          );

        drop policy if exists edges_athlete_read on public.graph_edges;
        create policy edges_athlete_read on public.graph_edges
          for select to anon, authenticated
          using (
            exists (
              select 1 from public.graphs g
              join public.athletes a on a.id = g.owner_id
              where g.id = graph_edges.graph_id
                and g.owner_kind = 'athlete' and a.is_published
            )
          );

        -- Read grants (RLS still filters rows). `authenticated` already holds
        -- select on the graph tables via db/auth_setup.sql §5, so only anon on
        -- those tables + athletes + the view are granted here.
        grant select on public.graphs, public.graph_nodes, public.graph_edges to anon;
        grant select on public.athletes to anon, authenticated;
        grant select on public.published_athlete_graphs to anon, authenticated;

        -- Published view honours the caller's RLS instead of bypassing it as a
        -- SECURITY DEFINER view (Supabase lint 0010).
        alter view public.published_athlete_graphs set (security_invoker = true);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        alter view public.published_athlete_graphs reset (security_invoker);
        revoke select on public.published_athlete_graphs from anon, authenticated;
        revoke select on public.athletes from anon, authenticated;
        revoke select on public.graphs, public.graph_nodes, public.graph_edges from anon;
        drop policy if exists edges_athlete_read on public.graph_edges;
        drop policy if exists nodes_athlete_read on public.graph_nodes;
        drop policy if exists graphs_athlete_read on public.graphs;
        drop policy if exists athletes_published_read on public.athletes;
        """
    )
