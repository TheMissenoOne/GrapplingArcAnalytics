"""Full per-match event timeline (all events, not just graph-clean techniques).

Nullable ``matches.timeline`` (JSONB) — every raw event of the bout, incl. strikes / resets /
penalties / referee calls, actor-mapped ('a'/'b'/None) with ts kept. Drives the interactive
momentum timeline on the public breakdown page. ``matches.sequence`` stays the graph/ELO-clean
subset (unchanged) — the graph is still built from it, so ELO is untouched.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("timeline", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("matches", "timeline")
