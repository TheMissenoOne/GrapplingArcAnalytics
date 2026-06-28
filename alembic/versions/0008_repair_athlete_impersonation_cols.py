"""Repair: re-add athlete-impersonation columns missing on prod.

0002 was stamped on prod (alembic_version reached 0007) but its columns never
landed — ``athletes.rank_elo`` and ``athlete_matches.opponent_athlete_id /
opponent_elo / graph_elo_after`` (+ the opponent FK) are absent on the live DB.
This migration re-applies them idempotently so a partially-migrated DB converges
without erroring on an already-present column.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-26
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE athletes ADD COLUMN IF NOT EXISTS rank_elo double precision")
    op.execute(
        "ALTER TABLE athlete_matches ADD COLUMN IF NOT EXISTS opponent_athlete_id uuid"
    )
    op.execute(
        "ALTER TABLE athlete_matches ADD COLUMN IF NOT EXISTS opponent_elo double precision"
    )
    op.execute(
        "ALTER TABLE athlete_matches ADD COLUMN IF NOT EXISTS graph_elo_after double precision"
    )
    # Add the opponent FK only if it isn't already present.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_athlete_matches_opponent_athlete_id'
            ) THEN
                ALTER TABLE athlete_matches
                    ADD CONSTRAINT fk_athlete_matches_opponent_athlete_id
                    FOREIGN KEY (opponent_athlete_id) REFERENCES athletes (id);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE athlete_matches "
        "DROP CONSTRAINT IF EXISTS fk_athlete_matches_opponent_athlete_id"
    )
    op.execute("ALTER TABLE athlete_matches DROP COLUMN IF EXISTS graph_elo_after")
    op.execute("ALTER TABLE athlete_matches DROP COLUMN IF EXISTS opponent_elo")
    op.execute("ALTER TABLE athlete_matches DROP COLUMN IF EXISTS opponent_athlete_id")
    op.execute("ALTER TABLE athletes DROP COLUMN IF EXISTS rank_elo")
