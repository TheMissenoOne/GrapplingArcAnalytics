"""SQLAlchemy 2.0 ORM models — mirrors DB schema in plan."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Archetype(Base):
    __tablename__ = "archetypes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    centroid: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    feature_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    full_name: Mapped[str | None] = mapped_column(Text)
    belt_rank: Mapped[str | None] = mapped_column(String(40))
    belt_degrees: Mapped[int] = mapped_column(Integer, default=0)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    archetype_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("archetypes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Athlete(Base):
    __tablename__ = "athletes"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    nickname: Mapped[str | None] = mapped_column(Text)
    team: Mapped[str | None] = mapped_column(Text)
    weight_class: Mapped[str | None] = mapped_column(String(40))
    belt: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    elo: Mapped[float] = mapped_column(Float, default=1000.0)  # grown graph ELO
    rank_elo: Mapped[float | None] = mapped_column(Float)  # ADCC leaderboard target
    archetype_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("archetypes.id"))
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    matches: Mapped[list[AthleteMatch]] = relationship(
        "AthleteMatch",
        back_populates="athlete",
        foreign_keys="AthleteMatch.athlete_id",
    )


class Graph(Base):
    __tablename__ = "graphs"
    __table_args__ = (UniqueConstraint("owner_kind", "owner_id"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # 'user' | 'athlete'
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    user_elo: Mapped[float | None] = mapped_column(Float)
    schema_version: Mapped[int] = mapped_column(Integer, default=3)
    archetype_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("archetypes.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    nodes: Mapped[list[GraphNode]] = relationship(
        "GraphNode", back_populates="graph", cascade="all, delete-orphan"
    )
    edges: Mapped[list[GraphEdge]] = relationship(
        "GraphEdge", back_populates="graph", cascade="all, delete-orphan"
    )


class TechniqueNode(Base):
    """Shared canonical technique library — one row per distinct node_key, reused
    across all user/athlete graphs. Replaces the per-user GraphNode identity rows.
    Holds the (future) pgvector technique embedding. See alembic 0004."""

    __tablename__ = "technique_nodes"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    node_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # == _normalize_name
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="technique")
    node_type: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(10), default="user")  # 'library' | 'user'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GraphNode(Base):
    """DEPRECATED per-user node rows — superseded by TechniqueNode (shared) + edges.
    Kept for dual-read during rollout; dropped in a later migration once the app
    writes edges + shared nodes only."""

    __tablename__ = "graph_nodes"
    __table_args__ = (UniqueConstraint("graph_id", "node_key"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    graph_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("graphs.id"), nullable=False
    )
    node_key: Mapped[str] = mapped_column(Text, nullable=False)  # _normalize_name(label)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="technique")
    node_type: Mapped[str] = mapped_column(String(40), default="")
    computed_elo: Mapped[float | None] = mapped_column(Float)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    trend: Mapped[str] = mapped_column(String(20), default="")

    graph: Mapped[Graph] = relationship("Graph", back_populates="nodes")


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (UniqueConstraint("graph_id", "edge_key"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    graph_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("graphs.id"), nullable=False
    )
    edge_key: Mapped[str] = mapped_column(Text, nullable=False)  # "{source_key}→{target_key}"
    source_key: Mapped[str] = mapped_column(Text, nullable=False)  # FK → technique_nodes.node_key
    target_key: Mapped[str] = mapped_column(Text, nullable=False)  # FK → technique_nodes.node_key
    # Denormalized from the owning graph so athlete vs user edge vector spaces can
    # be split by a partial index (see alembic 0005). 'user' | 'athlete'.
    owner_kind: Mapped[str | None] = mapped_column(String(10))
    elo: Mapped[float] = mapped_column(Float, default=0.0)
    setup: Mapped[str] = mapped_column(Text, default="")

    graph: Mapped[Graph] = relationship("Graph", back_populates="edges")


class AthleteMatch(Base):
    __tablename__ = "athlete_matches"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    athlete_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id"), nullable=False
    )
    opponent_name: Mapped[str | None] = mapped_column(Text)
    opponent_athlete_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id")
    )
    opponent_elo: Mapped[float | None] = mapped_column(Float)
    graph_elo_after: Mapped[float | None] = mapped_column(Float)
    event: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    weight_class: Mapped[str | None] = mapped_column(String(40))
    win_type: Mapped[str | None] = mapped_column(String(20))
    stage: Mapped[str | None] = mapped_column(String(10))
    submission: Mapped[str | None] = mapped_column(Text)
    won: Mapped[bool] = mapped_column(Boolean, default=True)
    sequence: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    athlete: Mapped[Athlete] = relationship(
        "Athlete", back_populates="matches", foreign_keys=[athlete_id]
    )


class BundleImport(Base):
    __tablename__ = "bundle_imports"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
