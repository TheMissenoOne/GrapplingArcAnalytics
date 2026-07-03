"""ELO deviance per technique — seeding computed initial athlete ELO.

Adds ``elo_deviance`` to ``technique_nodes``: signed float offset derived from
path-to-victory analysis. Used in app's ``graphDomain.findOrCreateNode`` to seed
a user node's initial computed ELO as ``baseline + library_deviance``, capturing
the inherent advantage/disadvantage of a technique relative to the average.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-02
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "technique_nodes",
        sa.Column("elo_deviance", sa.Float, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("technique_nodes", "elo_deviance")
