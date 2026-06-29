"""Global grappling-map transition table.

One row per ``source_key → target_key`` over the whole corpus — the persisted general
grappling map (``analysis.grappling_map`` / ``export.grappling_map``). Distinct from the
per-graph ``graph_edges``; keyed on normalized technique node keys (``_normalize_name``).

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "map_edges",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("target_key", sa.Text, nullable=False),
        sa.Column("count", sa.Integer, server_default="0"),
        sa.Column("suggested", sa.Boolean, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_key", "target_key", name="uq_map_edges_src_tgt"),
    )
    op.create_index("ix_map_edges_source_key", "map_edges", ["source_key"])


def downgrade() -> None:
    op.drop_index("ix_map_edges_source_key", table_name="map_edges")
    op.drop_table("map_edges")
