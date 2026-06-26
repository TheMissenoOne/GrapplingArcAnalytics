"""pgvector embedding columns + ANN indexes (three vector spaces).

Adds nullable ``vector(768)`` embedding columns to the three spaces from the
plan and an ANN index per space (HNSW, cosine). 768 = mpnet/Gemma-family text
embedder width (chosen 2026-06-25). Columns are nullable and backfilled later
by a separate embedding job — building HNSW indexes on the empty columns is
instant.

Spaces:
- ``technique_nodes.embedding`` — one vector per canonical technique (embed once).
- ``graph_edges.embedding``     — transition/structure vectors; user vs athlete
  spaces split by a PARTIAL HNSW index on ``owner_kind`` (0005).
- ``graphs.embedding``          — one vector per owner (archetype id + similarity);
  also split user vs athlete by partial index.
- ``archetypes.embedding``      — centroid in the same space as ``graphs.embedding``
  (nearest-centroid classification). Few rows → no index needed.

The extension lives in the ``extensions`` schema (Supabase convention; avoids the
``extension_in_public`` lint); ``search_path`` is set for the migration so the
unqualified ``vector`` type + ``vector_cosine_ops`` opclass resolve.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create extension if not exists vector with schema extensions;
        set local search_path = public, extensions;

        alter table public.technique_nodes add column if not exists embedding vector(768);
        alter table public.graph_edges     add column if not exists embedding vector(768);
        alter table public.graphs          add column if not exists embedding vector(768);
        alter table public.archetypes      add column if not exists embedding vector(768);

        -- ANN indexes (HNSW, cosine). Empty columns build instantly.
        create index if not exists idx_technique_nodes_embedding
          on public.technique_nodes using hnsw (embedding vector_cosine_ops);

        -- user vs athlete edge/graph spaces split by partial index on owner_kind.
        create index if not exists idx_graph_edges_embedding_user
          on public.graph_edges using hnsw (embedding vector_cosine_ops)
          where owner_kind = 'user';
        create index if not exists idx_graph_edges_embedding_athlete
          on public.graph_edges using hnsw (embedding vector_cosine_ops)
          where owner_kind = 'athlete';

        create index if not exists idx_graphs_embedding_user
          on public.graphs using hnsw (embedding vector_cosine_ops)
          where owner_kind = 'user';
        create index if not exists idx_graphs_embedding_athlete
          on public.graphs using hnsw (embedding vector_cosine_ops)
          where owner_kind = 'athlete';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        drop index if exists public.idx_graphs_embedding_athlete;
        drop index if exists public.idx_graphs_embedding_user;
        drop index if exists public.idx_graph_edges_embedding_athlete;
        drop index if exists public.idx_graph_edges_embedding_user;
        drop index if exists public.idx_technique_nodes_embedding;
        alter table public.archetypes      drop column if exists embedding;
        alter table public.graphs          drop column if exists embedding;
        alter table public.graph_edges     drop column if exists embedding;
        alter table public.technique_nodes drop column if exists embedding;
        """
    )
