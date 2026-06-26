"""Drop deprecated graph_nodes + finalize athlete-read RLS.

Completes the edge-only-graph cutover. The shared ``technique_nodes`` library
(0004) holds technique identity and ``graph_edges`` (0005, FK into it) holds the
per-owner graph, so the per-user ``graph_nodes`` rows are redundant:
- node identity → already seeded into ``technique_nodes``;
- per-user stats (computed_elo/usage_count/trend) → derived from incident edges
  (export + clustering reconstruct them);
- isolated (edge-less) nodes → intentionally not persisted server-side (low signal).

The edge→technique_nodes FK is already enforced (0005), so app builds that don't
write ``technique_nodes`` already can't sync novel-technique edges — dropping this
table adds no new client breakage.

RLS finalize: the athlete read path is now ``technique_nodes`` (public) +
``graph_edges`` (athlete, is_published — policy from 0003), so the
``nodes_athlete_read`` policy on ``graph_nodes`` is obsolete and removed with the
table.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- Policy + grants go away with the table, but drop explicitly so a
        -- re-run after a manual table drop stays clean.
        drop policy if exists nodes_athlete_read on public.graph_nodes;
        drop table if exists public.graph_nodes cascade;
        """
    )


def downgrade() -> None:
    # Recreate the table shell so a downgrade leaves a usable (empty) graph_nodes;
    # data is not restored (it was redundant with technique_nodes + edges).
    op.execute(
        """
        create table if not exists public.graph_nodes (
          id           uuid primary key default gen_random_uuid(),
          graph_id     uuid not null references public.graphs (id),
          node_key     text not null,
          label        text not null,
          type         varchar(20) not null default 'technique',
          node_type    varchar(40) not null default '',
          computed_elo double precision,
          usage_count  integer not null default 0,
          trend        varchar(20) not null default '',
          unique (graph_id, node_key)
        );

        alter table public.graph_nodes enable row level security;

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

        grant select on public.graph_nodes to anon, authenticated;
        """
    )
