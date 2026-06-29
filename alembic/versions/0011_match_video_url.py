"""Optional per-match YouTube link.

Nullable ``matches.video_url`` — surfaced on the public breakdown page only when set, fully
hidden otherwise.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("video_url", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("matches", "video_url")
