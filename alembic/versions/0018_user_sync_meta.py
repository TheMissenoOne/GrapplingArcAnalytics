"""Per-user session-sync progress tracker.

``user_sync_meta`` — one row per user, tracking the app's ``SessionState[]`` sync progress
against ``user_sessions`` (0017): ``big_sync_completed_at`` marks whether the initial full
upload finished, ``last_sync_at``/``session_count`` are informational for the app + admin to
show sync status. One row per ``owner_id`` (PK doubles as the FK, mirrors ``profiles``).

Scope note (mirrors 0003's split): RLS for this table is added separately in
``db/auth_setup.sql`` (hand-run, coupled to `auth.users`/`profiles`), not here — same reasoning
as 0017's docstring. Real FK to ``profiles(id)``, cascade delete automatic.

Scope note (test coverage): SQLite-in-memory pytest exercises the ``db/models.py`` shape only,
not this migration file or Postgres-only DDL.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sync_meta",
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("big_sync_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_sync_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("session_count", sa.Integer(), server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("user_sync_meta")
