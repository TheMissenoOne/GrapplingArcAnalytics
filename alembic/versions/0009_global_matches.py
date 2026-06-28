"""Global two-sided match model — replace per-perspective athlete_matches.

A match is now ONE global row referencing both athletes by id (athlete_a_id /
athlete_b_id), with sequence events tagged by actor_id. Each athlete's graph is built
by replaying the match from their side (double pass), so a match is stored once instead
of duplicated per perspective. Adds athletes.elo_series (per-athlete convergence series,
replacing the now-meaningless per-match graph_elo_after) and drops athlete_matches.

The old athlete_matches data is reproducible from scripts/gordon_matches.py, so it's
dropped rather than transformed.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "matches",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("athlete_a_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("athlete_b_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("winner_id", postgresql.UUID(as_uuid=False)),
        sa.Column("event", sa.Text),
        sa.Column("year", sa.Integer),
        sa.Column("weight_class", sa.String(40)),
        sa.Column("win_type", sa.String(20)),
        sa.Column("stage", sa.String(10)),
        sa.Column("submission", sa.Text),
        sa.Column("sequence", postgresql.JSONB),
        sa.Column("status", sa.String(10), nullable=False, server_default="final"),
        sa.Column("created_by", postgresql.UUID(as_uuid=False)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["athlete_a_id"], ["athletes.id"]),
        sa.ForeignKeyConstraint(["athlete_b_id"], ["athletes.id"]),
        sa.ForeignKeyConstraint(["winner_id"], ["athletes.id"]),
    )
    op.create_index("ix_matches_athlete_a_id", "matches", ["athlete_a_id"])
    op.create_index("ix_matches_athlete_b_id", "matches", ["athlete_b_id"])

    op.add_column("athletes", sa.Column("elo_series", postgresql.JSONB))

    op.execute("DROP TABLE IF EXISTS athlete_matches")


def downgrade() -> None:
    # Best-effort restore of athlete_matches (per-perspective shape from 0001/0002/0008).
    op.create_table(
        "athlete_matches",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("athlete_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("opponent_name", sa.Text),
        sa.Column("opponent_athlete_id", postgresql.UUID(as_uuid=False)),
        sa.Column("opponent_elo", sa.Float),
        sa.Column("graph_elo_after", sa.Float),
        sa.Column("event", sa.Text),
        sa.Column("year", sa.Integer),
        sa.Column("weight_class", sa.String(40)),
        sa.Column("win_type", sa.String(20)),
        sa.Column("stage", sa.String(10)),
        sa.Column("submission", sa.Text),
        sa.Column("won", sa.Boolean),
        sa.Column("sequence", postgresql.JSONB),
        sa.Column("status", sa.String(10), nullable=False, server_default="final"),
        sa.Column("created_by", postgresql.UUID(as_uuid=False)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["athlete_id"], ["athletes.id"]),
        sa.ForeignKeyConstraint(["opponent_athlete_id"], ["athletes.id"]),
    )
    op.create_index("ix_athlete_matches_athlete_id", "athlete_matches", ["athlete_id"])

    op.drop_column("athletes", "elo_series")
    op.drop_index("ix_matches_athlete_b_id", "matches")
    op.drop_index("ix_matches_athlete_a_id", "matches")
    op.drop_table("matches")
