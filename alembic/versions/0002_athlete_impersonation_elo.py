"""Athlete impersonation ELO — rank_elo target + per-match opponent/graph-ELO snapshot.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-23
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("athletes", sa.Column("rank_elo", sa.Float))

    op.add_column(
        "athlete_matches",
        sa.Column("opponent_athlete_id", postgresql.UUID(as_uuid=False)),
    )
    op.add_column("athlete_matches", sa.Column("opponent_elo", sa.Float))
    op.add_column("athlete_matches", sa.Column("graph_elo_after", sa.Float))
    op.create_foreign_key(
        "fk_athlete_matches_opponent_athlete_id",
        "athlete_matches",
        "athletes",
        ["opponent_athlete_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_athlete_matches_opponent_athlete_id", "athlete_matches", type_="foreignkey"
    )
    op.drop_column("athlete_matches", "graph_elo_after")
    op.drop_column("athlete_matches", "opponent_elo")
    op.drop_column("athlete_matches", "opponent_athlete_id")
    op.drop_column("athletes", "rank_elo")
