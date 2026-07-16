"""Raw per-device training sessions synced from the app (SessionState[]).

``user_sessions`` holds the app's true source data (one row per on-device session, ``id``
already device-generated as ``s-{timestamp}-{random}``), synced/merged across devices by
``id`` + ``updated_at`` (the app resolves conflicts client-side, last-write-wins on
``updated_at``; this table just stores whatever the app upserts). ``data`` is the full
``SessionState`` JSON with media stripped by the app before upload. Distinct from
``graphs``/``graph_edges`` (the derived technique graph) — this is the raw log those are
built from.

Scope note (mirrors 0003's split): RLS for this table is added separately in
``db/auth_setup.sql`` (hand-run, coupled to `auth.users`/`profiles` — see that file's owner-row
pattern for ``graphs``), not here. ``owner_id`` is a real FK to ``profiles(id)`` (not the
polymorphic ``owner_kind``/``owner_id`` pair `graphs` uses), so cascade delete is automatic via
the FK — no manual on-delete trigger needed.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory
(``tests/test_db.py``), which validates the ``db/models.py`` shape round-trips through
SQLAlchemy but does not execute this migration file or exercise Postgres-only DDL/indexes.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("data", JSONB, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "idx_user_sessions_owner_updated",
        "user_sessions",
        ["owner_id", "updated_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_user_sessions_owner_updated", table_name="user_sessions", if_exists=True)
    op.drop_table("user_sessions")
