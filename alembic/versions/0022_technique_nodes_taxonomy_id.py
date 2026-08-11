"""Taxonomy classification per technique node (kanban card 017).

Adds a nullable ``taxonomy_id`` to ``technique_nodes``, pointing at a node in
``docs/taxonomy.json`` (schema v2) — normally a subcategory such as ``pressure-pass``, or a
category such as ``pass`` when the label names the whole family.

Nullable on purpose. The technique→subcategory mapping is proposed by
``analysis/taxonomy_map.py`` in three tiers; only the ``auto`` tier (label and node_type agree
independently) is backfilled now, and the ``review``/``manual`` tiers fill in as they are
confirmed. A NULL means "not yet classified", never "has no taxonomy".

No RLS change: ``technique_nodes`` is already world-readable reference data (0004), and this
adds no new access surface. No index either — nothing filters on it yet, and an unused index
on a 430-row table is cost without benefit.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "technique_nodes",
        sa.Column("taxonomy_id", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("technique_nodes", "taxonomy_id")
