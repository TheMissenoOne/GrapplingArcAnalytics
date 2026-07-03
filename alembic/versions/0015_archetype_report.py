"""User archetype report (App analytics — DB seam for Part B).

Adds ``graphs.archetype_report jsonb`` (nullable): the structural similar/differ
comparison of a *user* graph against its nearest archetype, computed by
``scripts.assign_user_archetypes`` (embedding-cosine nearest + non-vectorized
feature-vector compare). The App reads it alongside the existing ``archetype_id``
under the existing ``graphs_user_select`` RLS policy (owner_id = auth.uid()) —
no RLS change needed, additive JSONB only.

Shape: ``{archetype_id, name, similar: [{aspect,label,delta}], differ: [...],
signature: {shared, missing, extra}}``.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-03
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        set local search_path = public, extensions;
        alter table public.graphs
            add column if not exists archetype_report jsonb;
        """
    )


def downgrade() -> None:
    op.execute("alter table public.graphs drop column if exists archetype_report;")
