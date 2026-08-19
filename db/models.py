"""SQLAlchemy 2.0 ORM models — mirrors DB schema in plan."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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
    # RF01: 'emergent' = computed cluster (rewritten on recompute) | 'target' = authored catalog.
    kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default="emergent")
    # Emphasized node types, e.g. ["submission","control"] — dominant deviance dims (emergent)
    # or author-picked (target).
    signature_types: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    key: Mapped[str | None] = mapped_column(Text)  # stable slug for seed cross-reference
    centroid: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # legacy feature centroid
    feature_version: Mapped[str | None] = mapped_column(String(40))
    # Centroid in the graphs embedding space (alembic 0006) = mean of member graph embeddings,
    # for nearest-centroid id + cross-corpus similarity. Backfilled by
    # ``analysis.embeddings.backfill_archetype_embeddings``; NULL until then.
    embedding: Mapped[Any | None] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    full_name: Mapped[str | None] = mapped_column(Text)
    belt_rank: Mapped[str | None] = mapped_column(String(40))
    belt_degrees: Mapped[int] = mapped_column(Integer, default=0)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    # Admin-granted entitlement. Authenticated clients receive no UPDATE privilege on this column
    # (see alembic 0023); RLS alone cannot prevent a user from changing their own field.
    is_pro: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
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
    # User-graph structural comparison vs the nearest archetype (alembic 0015) —
    # {name, similar[], differ[], signature{}}. Written by scripts.assign_user_archetypes;
    # the App reads it under graphs_user_select RLS. NULL for athlete graphs / until run.
    archetype_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    edges: Mapped[list[GraphEdge]] = relationship(
        "GraphEdge", back_populates="graph", cascade="all, delete-orphan"
    )
    # One vector per owner (alembic 0006) — ELO-weighted mean of the graph's node embeddings,
    # for athlete-graph similarity (RF11) + nearest-centroid archetype id. Backfilled by
    # ``analysis.embeddings.backfill_graph_embeddings``; NULL until then.
    embedding: Mapped[Any | None] = mapped_column(Vector(768), nullable=True)


class UserSession(Base):
    """Raw per-device training session synced from the app (``SessionState``, media
    stripped) — the true source data ``graphs``/``graph_edges`` are derived from.
    ``id`` is device-generated (``s-{timestamp}-{random}``); the app merges across
    devices by ``id`` + ``updated_at``. RLS lives in alembic 0023 (schema in 0017)."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable (alembic 0019): a tombstone (deleted_at set) for a session that was never
    # pushed live has nothing to strip-and-upload, so data is NULL on those rows.
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Tombstone (alembic 0019): set when the app deletes a session, so other devices'
    # incremental pull sees the deletion instead of resurrecting the row. NULL = live.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Which class this session was stamped to via attach_to_class() (alembic 0026). NULL for
    # sessions never linked to a class.
    class_session_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("class_sessions.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserProject(Base):
    """A user's training project, synced across their devices (alembic 0030).

    Same shape as UserSession on purpose — device-generated ``id``, whole-record ``data``
    JSONB, ``updated_at`` as the conflict clock, ``deleted_at`` as a tombstone — so the App
    reuses one sync code path instead of growing a second idiom. PK is ``id`` alone; that is
    the App's ``on_conflict`` target and it must keep matching a real constraint.

    Private, owner-scoped, app-fed data: readable only by its owner, never an input to any
    aggregate, export or competitive artefact. RLS lives in alembic 0030.
    """

    __tablename__ = "user_projects"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable for the same reason as UserSession.data (0019): a project created and deleted
    # before it was ever pushed arrives as a tombstone with nothing to upload.
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserStudyNote(Base):
    """One study note the athlete wrote, synced across their devices (alembic 0043).

    Notes used to live nested inside ``UserProject.data`` as ``Project.notes``, which made a
    note the property of exactly one study. A note is usually about a technique or a detail,
    not about a project, so it is a row now, and what it is about travels with it as a list of
    references (technique node key, directed edge key, session id, video id, study id).

    Same shape as UserProject/UserSession — device-generated ``id``, whole-record ``data``
    JSONB, ``updated_at`` as the conflict clock, ``deleted_at`` as a tombstone. PK is ``id``
    alone; that is the App's ``on_conflict`` target and it must keep matching a real constraint.

    The most purely app-fed data in the product: free prose about the athlete's own training.
    Private, owner-scoped, readable only by its owner, and never an input to any aggregate,
    embedding used outside that owner, ranking, export or competitive artefact. Not visible to
    a professor either — ``group_member_sessions`` already strips notes from what a professor
    reads, and this table has no RPC of its own by design. RLS lives in alembic 0043.
    """

    __tablename__ = "user_study_notes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable for the same reason as UserProject.data (0030): a note written and deleted
    # before it was ever pushed arrives as a tombstone with nothing to upload.
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserNodeName(Base):
    """What one user prefers to call one canonical node (alembic 0031).

    Presentation only. ``node_key`` is the canonical normalized key (``names._normalize_name``,
    mirrored by the App's ``normalizeLabel``) and stays the join key for every graph, ELO and
    transition metric — a preferred name never becomes an identity, so two sessions logged
    under two names still collapse onto one node. Deliberately no FK to ``technique_nodes``: a
    preference may name a node that only exists in the App's bundled library or that the user
    created, and a dangling preference must stay inert rather than fail a sync.

    Never modifies the canonical library; one user's choice is invisible to every other user
    and to the public site. RLS lives in alembic 0031.
    """

    __tablename__ = "user_node_names"

    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    node_key: Mapped[str] = mapped_column(Text, primary_key=True)
    preferred_name: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserSyncMeta(Base):
    """Per-user session-sync progress (alembic 0018). One row per owner. RLS lives in
    alembic 0023."""

    __tablename__ = "user_sync_meta"

    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    big_sync_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    session_count: Mapped[int | None] = mapped_column(Integer, server_default="0")


class Group(Base):
    """A gym's students under one professor (alembic 0024). Flat — one owner, many
    members. RLS + join_group()/group_member_sessions view live in alembic 0024."""

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    # 'professor', not 'teacher' — the word the product uses, in the data too.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("role in ('owner','professor','student')", name="ck_group_members_role"),
        Index("idx_group_members_profile", "profile_id"),
    )


class GroupInvite(Base):
    """Its own table, not a column on ``groups``, so a code can expire/rotate without
    touching the group or its members."""

    __tablename__ = "group_invites"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassSession(Base):
    """A professor's live class inside a group (alembic 0026). ``join_token`` is what the
    class QR carries — a student who scans it and already belongs to the group gets that
    day's ``user_sessions`` row stamped with this class's id via ``attach_to_class()``. RLS
    lives in alembic 0026."""

    __tablename__ = "class_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    join_token: Mapped[str | None] = mapped_column(Text, unique=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_class_sessions_group", "group_id"),)


class FrameAnnotation(Base):
    """One reviewable frame: an event that carries both a video URL and a timestamp.

    Prediction and correction are separate columns on purpose. Overwriting the
    proposal with the fix would destroy the only record of where the model was
    wrong, which is the point of collecting these. RLS is ON with no policy —
    admin-only data, denied to anon and authenticated. See alembic 0029.
    """

    __tablename__ = "frame_annotations"
    __table_args__ = (
        UniqueConstraint("match_id", "event_index", name="frame_annotations_frame_unique"),
        Index("idx_frame_annotations_status", "status"),
        Index("idx_frame_annotations_match", "match_id"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    match_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_ts: Mapped[float] = mapped_column(Float, nullable=False)
    event_label: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(Text)
    predicted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    corrected: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # pending | approved | corrected | skipped
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    reviewer: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserPerformanceSnapshot(Base):
    """Versioned, batch-generated Pro analytics for one user's completed period."""

    __tablename__ = "user_performance_snapshots"
    __table_args__ = (
        UniqueConstraint("owner_id", "cadence", "period_end", name="uq_pro_snapshot_period"),
        CheckConstraint("cadence IN ('daily', 'weekly')", name="ck_pro_snapshot_cadence"),
        CheckConstraint(
            "status IN ('ready', 'insufficient_data', 'failed')",
            name="ck_pro_snapshot_status",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    cadence: Mapped[str] = mapped_column(String(10), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AthleteDossier(Base):
    """Versioned, batch-generated athlete dossier gated to entitled users."""

    __tablename__ = "athlete_dossiers"

    athlete_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id", ondelete="CASCADE"), primary_key=True
    )
    graph_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("graphs.id", ondelete="SET NULL"), nullable=True
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


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
    # How far this technique's outcomes sit from the corpus norm — seeds a computed initial
    # athlete ELO (alembic 0014). Was applied to the DB but never mapped here; that drift is
    # closed now, so the model and the live schema agree column-for-column.
    elo_deviance: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    # Node id in docs/taxonomy.json v2 (usually a subcategory, e.g. "pressure-pass"; a
    # category when the label names the whole family). NULL = not yet classified — the
    # mapping is confirmed tier by tier (alembic 0022, card 017).
    taxonomy_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GraphNode(Base):
    """One graph's own name for one of its nodes — private by construction.

    Re-created in alembic 0037, and NOT the table 0007 dropped. That one was a per-user copy of
    the shared library and was rightly deleted for it. This one holds identity the shared library
    must never hold: whatever the athlete actually typed. ``technique_nodes`` is world-readable
    (``using (true)`` for ``anon``), which is right for a curated vocabulary and wrong for a
    user's own words, and the App had been writing the latter into the former for want of
    anywhere else to point.

    ``canonical_node_key`` links a private node to curated vocabulary when the match is known.
    The direction is the point: private may reference public, public never learns about private.
    Nullable, because a node the user invented has nothing to point at and must still sync.

    Privacy class **C** for ``owner_kind='user'`` rows. Athlete rows in this same table are
    public-by-publication and are exposed through ``published_athlete_graph_nodes``.
    """

    __tablename__ = "graph_nodes"

    graph_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("graphs.id", ondelete="CASCADE"), primary_key=True
    )
    node_key: Mapped[str] = mapped_column(Text, primary_key=True)  # == _normalize_name(label)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False, server_default="technique")
    node_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_node_key: Mapped[str | None] = mapped_column(
        Text, ForeignKey("technique_nodes.node_key", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
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
    __table_args__ = (
        UniqueConstraint("graph_id", "edge_key"),
        ForeignKeyConstraint(
            ["graph_id", "source_key"],
            ["graph_nodes.graph_id", "graph_nodes.node_key"],
            name="graph_edges_source_node_fk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["graph_id", "target_key"],
            ["graph_nodes.graph_id", "graph_nodes.node_key"],
            name="graph_edges_target_node_fk",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    graph_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("graphs.id"), nullable=False
    )
    edge_key: Mapped[str] = mapped_column(Text, nullable=False)  # "{source_key}→{target_key}"
    # Composite FKs → graph_nodes(graph_id, node_key), declared in __table_args__ because they
    # span two columns. Until alembic 0037 these pointed at technique_nodes(node_key), which is
    # what forced every user label into the shared public library; the comment here said "FK"
    # while no ForeignKey was declared, so model and schema were out of step since 0005.
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Denormalized from the owning graph so athlete vs user edge vector spaces can
    # be split by a partial index (see alembic 0005). 'user' | 'athlete'.
    owner_kind: Mapped[str | None] = mapped_column(String(10))
    elo: Mapped[float] = mapped_column(Float, default=0.0)
    setup: Mapped[str] = mapped_column(Text, default="")
    # Transition vector (alembic 0006): embedding of "{source} to {target}", athlete vs user
    # spaces split by partial index on owner_kind. Backfilled by
    # ``analysis.embeddings.backfill_graph_edge_embeddings``; NULL until then.
    embedding: Mapped[Any | None] = mapped_column(Vector(768), nullable=True)

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
    # Full raw event timeline (superset of ``sequence``): every event incl. strikes / resets /
    # penalties / referee calls, actor ∈ {'a','b',None}, ts kept. Drives the breakdown timeline;
    # the graph + ELO still read ``sequence`` (the clean subset), so this never affects scoring.
    timeline: Mapped[list[Any] | None] = mapped_column(JSONB)
    # 'final' (counts toward both graphs) | 'draft' (scraped, awaiting review — excluded
    # from the replay until approved). Manually-entered matches default final.
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="final")
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
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


class RatingEngineRun(Base):
    """One Glicko-2 (rating_v2) replay invocation — alembic 0035.

    Identifies exactly which ``engine_version`` + config produced a snapshot in
    ``athlete_rating_states_v2``. ``engine_version`` is a required read key (ADR-02,
    ``docs/rating_v2/01_DECISOES.md``), not audit decoration. Written by the replay
    job (service role); not touched by the App."""

    __tablename__ = "rating_engine_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    source_hash: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    notes: Mapped[str | None] = mapped_column(Text)


class AthleteRatingStateV2(Base):
    """One athlete's Glicko-2 state at the end of one run — alembic 0035.

    Athlete (public) data only — rating_v2 replays ``matches``/``athletes``, never a
    user session or graph. PK ``(run_id, athlete_id)``: a run's full leaderboard and an
    athlete's history across runs are both the expected query shapes."""

    __tablename__ = "athlete_rating_states_v2"
    # Só athlete_id: a PK já indexa run_id como coluna líder (ver alembic 0035).
    __table_args__ = (Index("ix_athlete_rating_states_v2_athlete_id", "athlete_id"),)

    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("rating_engine_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    athlete_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id", ondelete="CASCADE"), primary_key=True
    )
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    rating_deviation: Mapped[float] = mapped_column(Float, nullable=False)
    volatility: Mapped[float] = mapped_column(Float, nullable=False)
    periods: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    bout_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AthleteNodeRatingStateV2(Base):
    """One athlete's Glicko-2 state for one technique node at the end of one run —
    alembic 0036. ``node_key`` is the canonical identity (``analysis/names.py:
    _normalize_name``, == ``technique_nodes.node_key``), never a display label — same
    char-for-char contract the App's ``normalizeLabel()`` depends on elsewhere in this
    schema, even though this table isn't App-read today. No DB FK to
    ``technique_nodes.node_key``, matching the existing ``GraphEdge.source_key``/
    ``target_key`` convention (commented, not enforced).

    ``bouts_observed`` and ``occurrences`` are deliberately separate: a node can fire more
    than once within a single bout's event sequence. ``constellation_fingerprint`` is
    nullable and carries no FK — the constellation layer that produces it is a parallel,
    still-landing change; wiring the real value is a follow-up, not invented here.
    ``first_seen_at``/``last_seen_at`` stay nullable for the same reason
    ``AthleteRatingStateV2.last_active_at`` does: ``matches`` has no per-bout date yet
    (ADR-04 debt, only ``year``)."""

    __tablename__ = "athlete_node_rating_states_v2"
    __table_args__ = (
        Index("ix_athlete_node_rating_states_v2_athlete_id", "athlete_id"),
        Index("ix_athlete_node_rating_states_v2_node_key", "node_key"),
    )

    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("rating_engine_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    athlete_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id", ondelete="CASCADE"), primary_key=True
    )
    node_key: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    rating_deviation: Mapped[float] = mapped_column(Float, nullable=False)
    volatility: Mapped[float] = mapped_column(Float, nullable=False)
    bouts_observed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    constellation_fingerprint: Mapped[str | None] = mapped_column(Text)


class AthleteConstellationV2(Base):
    """One detected, guaranteed-connected constellation (``analysis/constellations/
    detect.py``) plus its bootstrap-stability summary (``analysis/constellations/
    stability.py``), for one athlete at the end of one run — alembic 0036.
    ``fingerprint`` is opaque to this model; deriving a stable identifier for "this same
    cluster of nodes" across runs is the constellation layer's decision."""

    __tablename__ = "athlete_constellations_v2"
    __table_args__ = (Index("ix_athlete_constellations_v2_athlete_id", "athlete_id"),)

    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("rating_engine_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    athlete_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id", ondelete="CASCADE"), primary_key=True
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    hub_node_key: Mapped[str] = mapped_column(Text, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    internal_edge_count: Mapped[int] = mapped_column(Integer, nullable=False)
    support_bouts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    modularity: Mapped[float] = mapped_column(Float, nullable=False)
    stability_mean: Mapped[float] = mapped_column(Float, nullable=False)
    stability_p10: Mapped[float] = mapped_column(Float, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class AthleteConstellationMemberV2(Base):
    """One member node of one constellation, with its in-constellation PageRank —
    alembic 0036. No DB FK to ``AthleteConstellationV2``: this schema's existing
    convention (see ``GraphEdge.source_key``/``target_key``) is application-enforced
    referential integrity for cross-table node/composite identity, not a Postgres
    constraint — a composite FK here would be a pattern found nowhere else in this file."""

    __tablename__ = "athlete_constellation_members_v2"
    __table_args__ = (Index("ix_athlete_constellation_members_v2_node_key", "node_key"),)

    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("rating_engine_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    athlete_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id", ondelete="CASCADE"), primary_key=True
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    node_key: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)
    pagerank: Mapped[float] = mapped_column(Float, nullable=False)
    weighted_pagerank: Mapped[float] = mapped_column(Float, nullable=False)
    degree: Mapped[int] = mapped_column(Integer, nullable=False)
