"""Add the Pro entitlement and batch analytics storage contract.

The analytics publisher writes snapshots/dossiers with a service-role database connection.
Authenticated application clients can only read rows while their own profile has ``is_pro``.
Policies and grants live in ``db/auth_setup.sql`` because they depend on Supabase ``auth.uid()``;
the production handoff must run that file after applying this migration.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("is_pro", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "user_performance_snapshots",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cadence", sa.String(length=10), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("metrics", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("cadence IN ('daily', 'weekly')", name="ck_pro_snapshot_cadence"),
        sa.CheckConstraint(
            "status IN ('ready', 'insufficient_data', 'failed')",
            name="ck_pro_snapshot_status",
        ),
        sa.UniqueConstraint("owner_id", "cadence", "period_end", name="uq_pro_snapshot_period"),
    )
    op.create_table(
        "athlete_dossiers",
        sa.Column(
            "athlete_id",
            UUID(as_uuid=False),
            sa.ForeignKey("athletes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "graph_id",
            UUID(as_uuid=False),
            sa.ForeignKey("graphs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("athlete_dossiers")
    op.drop_table("user_performance_snapshots")
    op.drop_column("profiles", "is_pro")
