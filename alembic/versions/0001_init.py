"""Initial schema — profiles, athletes, graphs, nodes, edges, matches, archetypes, bundle_imports.

Revision ID: 0001
Revises:
Create Date: 2026-06-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "archetypes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("centroid", postgresql.JSONB),
        sa.Column("feature_version", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("full_name", sa.Text),
        sa.Column("belt_rank", sa.String(40)),
        sa.Column("belt_degrees", sa.Integer, server_default="0"),
        sa.Column("is_guest", sa.Boolean, server_default="false"),
        sa.Column("archetype_id", sa.Integer, sa.ForeignKey("archetypes.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "athletes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("nickname", sa.Text),
        sa.Column("team", sa.Text),
        sa.Column("weight_class", sa.String(40)),
        sa.Column("belt", sa.String(40)),
        sa.Column("source", sa.String(20), server_default="manual"),
        sa.Column("elo", sa.Float, server_default="1000"),
        sa.Column("archetype_id", sa.Integer, sa.ForeignKey("archetypes.id")),
        sa.Column("is_published", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "graphs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_kind", sa.String(10), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_elo", sa.Float),
        sa.Column("schema_version", sa.Integer, server_default="3"),
        sa.Column("archetype_id", sa.Integer, sa.ForeignKey("archetypes.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_kind", "owner_id", name="uq_graphs_owner"),
    )

    op.create_table(
        "graph_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "graph_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("graphs.id"), nullable=False
        ),
        sa.Column("node_key", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("type", sa.String(20), server_default="technique"),
        sa.Column("node_type", sa.String(40), server_default=""),
        sa.Column("computed_elo", sa.Float),
        sa.Column("usage_count", sa.Integer, server_default="0"),
        sa.Column("trend", sa.String(20), server_default=""),
        sa.UniqueConstraint("graph_id", "node_key", name="uq_graph_nodes_key"),
    )
    op.create_index("ix_graph_nodes_graph_id", "graph_nodes", ["graph_id"])

    op.create_table(
        "graph_edges",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "graph_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("graphs.id"), nullable=False
        ),
        sa.Column("edge_key", sa.Text, nullable=False),
        sa.Column("source_key", sa.Text, nullable=False),
        sa.Column("target_key", sa.Text, nullable=False),
        sa.Column("elo", sa.Float, server_default="0"),
        sa.Column("setup", sa.Text, server_default=""),
        sa.UniqueConstraint("graph_id", "edge_key", name="uq_graph_edges_key"),
    )
    op.create_index("ix_graph_edges_graph_id", "graph_edges", ["graph_id"])

    op.create_table(
        "athlete_matches",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "athlete_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("athletes.id"),
            nullable=False,
        ),
        sa.Column("opponent_name", sa.Text),
        sa.Column("event", sa.Text),
        sa.Column("year", sa.Integer),
        sa.Column("weight_class", sa.String(40)),
        sa.Column("win_type", sa.String(20)),
        sa.Column("stage", sa.String(10)),
        sa.Column("submission", sa.Text),
        sa.Column("won", sa.Boolean, server_default="true"),
        sa.Column("sequence", postgresql.JSONB),
        sa.Column("created_by", postgresql.UUID(as_uuid=False)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_athlete_matches_athlete_id", "athlete_matches", ["athlete_id"])

    op.create_table(
        "bundle_imports",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False)),
        sa.Column("raw", postgresql.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Published athlete graphs view
    op.execute("""
        CREATE VIEW published_athlete_graphs AS
        SELECT g.id, g.owner_id, g.user_elo, g.updated_at,
               a.name, a.nickname, a.team, a.weight_class, a.belt, a.elo AS athlete_elo
        FROM graphs g
        JOIN athletes a ON a.id = g.owner_id
        WHERE g.owner_kind = 'athlete' AND a.is_published = true
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS published_athlete_graphs")
    op.drop_table("bundle_imports")
    op.drop_index("ix_athlete_matches_athlete_id", "athlete_matches")
    op.drop_table("athlete_matches")
    op.drop_index("ix_graph_edges_graph_id", "graph_edges")
    op.drop_table("graph_edges")
    op.drop_index("ix_graph_nodes_graph_id", "graph_nodes")
    op.drop_table("graph_nodes")
    op.drop_table("graphs")
    op.drop_table("athletes")
    op.drop_table("profiles")
    op.drop_table("archetypes")
