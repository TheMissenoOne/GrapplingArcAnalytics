"""SQLAlchemy 2.0 ORM models — mirrors DB schema in plan."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
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
    centroid: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # legacy feature centroid
    feature_version: Mapped[str | None] = mapped_column(String(40))
    # NB: `embedding vector(768)` in DB (alembic 0006) — centroid in the graphs
    # embedding space for nearest-centroid archetype id. Unmapped (SQL backfill).
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
    # Per-athlete chronological graph-ELO snapshots from the last replay (one entry per
    # final match the athlete participates in) — drives the admin convergence sparkline.
    elo_series: Mapped[list[Any] | None] = mapped_column(JSONB)
    archetype_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("archetypes.id"))
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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

    edges: Mapped[list[GraphEdge]] = relationship(
        "GraphEdge", back_populates="graph", cascade="all, delete-orphan"
    )
    # NB: `embedding vector(768)` exists in the DB (alembic 0006) — one vector per
    # owner for archetype id + similarity. Intentionally unmapped here (managed by
    # the SQL embedding backfill job; avoids a pgvector ORM dependency).


class TechniqueNode(Base):
    """Shared canonical technique library — one row per distinct node_key, reused
    across all user/athlete graphs. Replaces the per-user node identity rows.
    The pgvector ``embedding vector(768)`` (alembic 0006) is the semantic position vector
    (``analysis.embeddings``) — mapped here so the grappling-map backfill + cosine queries can
    read/write it. Nullable; rows without a backfilled embedding stay NULL."""

    __tablename__ = "technique_nodes"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    node_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # == _normalize_name
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(20), default="technique")
    node_type: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(10), default="user")  # 'library' | 'user'
    embedding: Mapped[Any | None] = mapped_column(Vector(768), nullable=True)
    # Decision Space (DS-01/04): {offensive[], defensive[], expected_reactions[],
    # constraints[], attacker_score, defender_score}. ds_mode (DS-16) = 'expert' | 'learned'.
    decision_space: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ds_mode: Mapped[str] = mapped_column(String(10), nullable=False, server_default="expert")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MapEdge(Base):
    """Global aggregate transition for the general grappling map — one row per
    ``source_key → target_key`` over the whole corpus (``analysis.grappling_map``).
    Distinct from per-graph ``graph_edges``; keyed on normalized technique node keys."""

    __tablename__ = "map_edges"
    __table_args__ = (UniqueConstraint("source_key", "target_key"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)  # == _normalize_name
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    suggested: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    # NB: `embedding vector(768)` in DB (alembic 0006) — transition/structure vector,
    # athlete vs user spaces split by partial index on owner_kind. Unmapped (SQL backfill).

    graph: Mapped[Graph] = relationship("Graph", back_populates="edges")


class Match(Base):
    """One GLOBAL match between two athletes (not stored per perspective).

    Both participants are athletes (``athlete_a_id``/``athlete_b_id``); sequence events
    are tagged with ``actor_id`` (one of the two). Each athlete's graph is built by
    replaying the match FROM THEIR SIDE — their events become their nodes, the opponent's
    rating is the other athlete's ranked ELO. One stored row feeds both graphs."""

    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    athlete_a_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id"), nullable=False, index=True
    )
    athlete_b_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id"), nullable=False, index=True
    )
    # Winner athlete id; NULL = draw / no-contest / unknown.
    winner_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id")
    )
    event: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    weight_class: Mapped[str | None] = mapped_column(String(40))
    win_type: Mapped[str | None] = mapped_column(String(20))
    stage: Mapped[str | None] = mapped_column(String(10))
    submission: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str | None] = mapped_column(Text)  # optional YouTube link (hidden if null)
    # Events: [{label, type, actor_id, successful?}], actor_id ∈ {athlete_a_id, athlete_b_id}.
    sequence: Mapped[list[Any] | None] = mapped_column(JSONB)
    # 'final' (counts toward both graphs) | 'draft' (scraped, awaiting review — excluded
    # from the replay until approved). Manually-entered matches default final.
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="final")
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BundleImport(Base):
    __tablename__ = "bundle_imports"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Strategic ontology (RF04-06, RF20) + Decision Space (DS-*) ──────────────────
# Canonical knowledge entities authored in the admin, exported to the bundled app seed
# (export/ontology.py) and synced to Supabase. Position/Transition are NOT re-modelled —
# they stay as TechniqueNode / GraphEdge / MapEdge, soft-referenced here by ``node_key``.


class Principle(Base):
    """Invariant strategic constraint (e.g. 'control the opponent's hips'). Embeddable."""

    __tablename__ = "principles"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # normalized slug
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(40))  # control | pressure | escape | ...
    embedding: Mapped[Any | None] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Intent(Base):
    """What a move aims to achieve."""

    __tablename__ = "intents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reaction(Base):
    """Expected opponent response (app-side proto: ``EdgeReaction``)."""

    __tablename__ = "reactions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Dilemma(Base):
    """Decision fork (option_a vs option_b) referencing principles. Embeddable."""

    __tablename__ = "dilemmas"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    situation: Mapped[str | None] = mapped_column(Text)
    option_a: Mapped[str | None] = mapped_column(Text)
    option_b: Mapped[str | None] = mapped_column(Text)
    principle_keys: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")  # soft refs
    embedding: Mapped[Any | None] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class System(Base):
    """RF04 strategic system — reusable across athletes; not owned by any one.

    Position references (``entry_positions``) are ``node_key`` strings into the shared
    ``technique_nodes`` library. ``ds_progression`` (DS-10) is the expected Decision-Space
    arc per milestone stage; principles/dilemmas attach via the join tables.
    """

    __tablename__ = "systems"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str | None] = mapped_column(Text)
    entry_positions: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")  # node_keys
    activation_conditions: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    expected_opponent_responses: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    alternative_paths: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    mastery_criteria: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    ds_progression: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    ds_mode: Mapped[str] = mapped_column(String(10), nullable=False, server_default="expert")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    milestones: Mapped[list[Milestone]] = relationship(
        "Milestone", back_populates="system", cascade="all, delete-orphan"
    )


class Milestone(Base):
    """RF06 generic per-system mastery ladder; may carry a Decision-Space objective (DS-11)."""

    __tablename__ = "milestones"
    __table_args__ = (UniqueConstraint("system_id", "ordinal"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    system_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("systems.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    # conceptual | execution | dilemma | chaining | resistance | recovery
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    ds_objective: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    system: Mapped[System] = relationship("System", back_populates="milestones")


class SystemImplementation(Base):
    """RF05 per-athlete overlay of a base system — deltas only, no knowledge duplication.

    ``overrides`` = {node_priorities, preferred_sequences[node_key[]], edge_emphasis, notes}.
    """

    __tablename__ = "system_implementations"
    __table_args__ = (UniqueConstraint("system_id", "athlete_id"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    system_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("systems.id", ondelete="CASCADE"), nullable=False
    )
    athlete_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(Text)
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    milestone_overrides: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SystemPrinciple(Base):
    __tablename__ = "system_principles"

    system_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("systems.id", ondelete="CASCADE"), primary_key=True
    )
    principle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("principles.id", ondelete="CASCADE"), primary_key=True
    )


class SystemDilemma(Base):
    __tablename__ = "system_dilemmas"

    system_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("systems.id", ondelete="CASCADE"), primary_key=True
    )
    dilemma_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("dilemmas.id", ondelete="CASCADE"), primary_key=True
    )
