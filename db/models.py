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
    Numeric,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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
    # This user is also a published athlete in the public corpus (alembic 0051). Nullable,
    # one-way (private -> public, never the reverse — same shape as GraphNode.canonical_node_key),
    # partial-unique so one athlete can't be claimed by two profiles. `authenticated` has no
    # column grant for it (0023's explicit insert/update column lists don't name it) — only an
    # admin/service-role write (e.g. scripts/import_user_bundle.py --athlete) can set it.
    athlete_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("athletes.id", ondelete="SET NULL")
    )
    # Video analysis pipeline (alembic 0058) — a reference selfie for actor identification in
    # the worker's frame reading, purely opt-in. NULL face_consent_at = worker never reads the
    # selfie, whatever face_ref_path holds. `authenticated` gets a column grant for both (0058
    # extends 0023's per-column list) so the owner can set/revoke consent themselves; no RLS
    # policy change needed since `profiles_update_own` (0023) already gates the row.
    face_ref_path: Mapped[str | None] = mapped_column(Text)
    face_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # 'f' | 'm' | NULL. NULL = no evidence, and prose MUST render neutral for it (never defaults
    # masculine) — see alembic 0049 and analysis/gendered_text.py for the full convention.
    gender: Mapped[str | None] = mapped_column(String(1))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    elo: Mapped[float] = mapped_column(Float, default=1000.0)  # grown graph ELO
    rank_elo: Mapped[float | None] = mapped_column(Float)  # ADCC leaderboard target
    # Per-athlete chronological graph-ELO snapshots from the last replay (one entry per
    # final match the athlete participates in) — drives the admin convergence sparkline.
    elo_series: Mapped[list[Any] | None] = mapped_column(JSONB)
    archetype_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("archetypes.id"))
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set when this athlete exercised a rights request (LGPD Art. 18) and their identity was
    # removed IN PLACE (alembic 0048). The row survives so the graph keeps a valid owner — see
    # `db.repository.delete_athlete`, and the invariant it protects: every athlete-owned graph
    # has an athletes row, with no exceptions, so an orphan is always a defect.
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    members. RLS + join_group()/group_member_sessions view live in alembic 0024.

    ``logo_url``/``description``/``accent_color`` (alembic 0056) — branding, owner-only writable
    via ``groups_update_owner`` (same shape as every other write on this table); ``accent_color``
    is CHECK-constrained to ``#rrggbb`` at the DB, not re-validated here."""

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    accent_color: Mapped[str | None] = mapped_column(Text)
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
    # When this member's ``join_group()`` call confirmed the disclosure screen (Web, alembic
    # 0054) — "your training data is visible to this gym's professors". NULL for rows created
    # via `create_group()` (the owner's own membership, nothing to disclose to themselves) or
    # for any row that predates 0054; NULL is never read as consent given.
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "Does this person train here" — independent of ``role`` ("what can this person DO here").
    # Alembic 0055. Server default false; `join_group()` sets it true for a `'student'` invite,
    # `set_trains_here()` (self-only RPC) is how an owner/professor opts themselves in.
    trains_here: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    __table_args__ = (
        CheckConstraint("role in ('owner','professor','student')", name="ck_group_members_role"),
        Index("idx_group_members_profile", "profile_id"),
    )


class GroupInvite(Base):
    """Its own table, not a column on ``groups``, so a code can expire/rotate without
    touching the group or its members. ``role`` (alembic 0052) is what ``join_group()``
    grants the redeemer — 'student' by default, 'professor' for an academy-minted code;
    minting stays owner-only (``group_invites_owner_all``)."""

    __tablename__ = "group_invites"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="student")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("role in ('student','professor')", name="ck_group_invites_role"),
    )


class ClassSession(Base):
    """A professor's live class inside a group (alembic 0026). ``join_token`` is what the
    class QR carries — a student who scans it and already belongs to the group gets that
    day's ``user_sessions`` row stamped with this class's id via ``attach_to_class()``. RLS
    lives in alembic 0026.

    ``focus_node_keys``/``plan`` (alembic 0050) are the professor's own written class plan —
    canonical node keys and free-text roteiro, no student data of any kind."""

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
    focus_node_keys: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    plan: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("idx_class_sessions_group", "group_id"),)


class ClassPlanTemplate(Base):
    """A reusable class theme/focus, scoped to one academy (alembic 0050).

    Readable by anyone in the group (a student can see what's coming); written only by the
    group's owner/professor, same split every group-scoped table has used since 0026. No
    student data — this is the professor's own authored content."""

    __tablename__ = "class_plan_templates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    focus_node_keys: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_class_plan_templates_group", "group_id"),)


class Instructional(Base):
    """A professor's authored teaching material for the whole academy (alembic 0050) — a
    video (bucket path in ``instructional-media``, alembic 0050) or an external link, plus
    a focus and a sort order for a syllabus-style list.

    Read policy is ``is_group_member``: a student in the academy is exactly who this content
    is for (the opposite direction from ``group_member_sessions``, which flows student to
    professor). Write is owner/professor only. No student data of any kind."""

    __tablename__ = "instructionals"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    focus_node_keys: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    video_path: Mapped[str | None] = mapped_column(Text)
    external_url: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_instructionals_group", "group_id"),)


class ProfessorEvaluation(Base):
    """A professor's own coaching note on a student (alembic 0054) — the OTHER kind of rating.

    Deliberately separate from every Elo/Glicko table (``graphs``, ``graph_edges``, ``athletes``,
    ``rating_engine_runs``): this is the professor's opinion, not a number derived from the
    student's replayed sequences, and nothing in this module ever writes it back into the rating
    pipeline. ``score`` has no fixed scale yet — that is a professor-UI decision, not a schema
    one; add the CHECK constraint when a scale is picked.

    RLS: read by the group's owner/professor (any of their students) or by the student about
    themselves; write by the authoring professor/owner only, and only in their own name
    (``professor_id = auth.uid()``) — same ``is_group_owner_or_professor`` split as
    ``class_plan_templates``/``instructionals`` (0050). No update/delete policy yet — a wrong
    note today is a data-loss risk this table's zero rows don't justify solving until it has
    users; add the policy the day someone needs to correct one."""

    __tablename__ = "professor_evaluations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    professor_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    rating_note: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_professor_evaluations_group", "group_id"),
        Index("idx_professor_evaluations_student", "student_id"),
    )


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


class GraphEdgeBout(Base):
    """Which published bouts an athlete's edge was observed in (alembic 0046).

    ``graph_edges`` is deduplicated to one row per edge and carries a count, so a bootstrap over
    it can only resample EDGES. That is the gap ADR-08 is blocked on: wave 6b compared two
    community detectors on an edge bootstrap and said plainly that deciding on it would repeat
    the error it had just exposed — reading a number that depends on the input's shape as if it
    described the algorithm. One row per (edge, bout) is what lets a resample draw bouts and
    rebuild the edge set from what it drew.

    Athlete graphs only, and that is structural rather than conventional: the FK points at
    ``matches``, and a user's private sessions have no match to reference.

    Privacy class: **A, public competition data.** It records which already-published match an
    already-derived public edge came from, and asserts no new fact about anyone.
    """

    __tablename__ = "graph_edge_bouts"
    __table_args__ = (
        PrimaryKeyConstraint("graph_id", "source_key", "target_key", "match_id"),
        # Cascades from the EDGE, not merely from the graph: an edge pruned by a replay must
        # not leave provenance behind claiming a transition nobody makes any more.
        ForeignKeyConstraint(
            ["graph_id", "source_key", "target_key"],
            ["graph_edges.graph_id", "graph_edges.source_key", "graph_edges.target_key"],
            name="graph_edge_bouts_edge_fk",
            ondelete="CASCADE",
        ),
    )

    graph_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    match_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("graph_id", "edge_key"),
        # A second unique on the SAME row identity, spelled the other way. `edge_key` is
        # `f"{source}→{target}"`, so the triple determines the row either way — but Postgres
        # requires a unique index on exactly the referenced columns, and `graph_edge_bouts`
        # references the triple. Added by alembic 0046.
        UniqueConstraint(
            "graph_id", "source_key", "target_key",
            name="graph_edges_graph_source_target_key",
        ),
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
    # Where this bout starts inside ``video_url`` (alembic 0047, applied 2026-08-19).
    # Backfilled from the ``t=`` already in the URL; also settable when the offset is known
    # but the link is not — `url_mapping.json` carries one for 307 bouts.
    video_start_seconds: Mapped[int | None] = mapped_column(Integer)
    # Which clock ``sequence[].ts`` is on: 'video_absolute' (offsets into the whole video) or
    # 'bout_relative' (offsets from the bout's own start). The corpus mixes both and NULL means
    # nobody has established which — a reader must treat NULL as "cannot locate", never as a
    # default of either. Guessing wrong silently places frames in a different fight (AA-010).
    ts_origin: Mapped[str | None] = mapped_column(String(16))
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


class SessionVideoJob(Base):
    """One user-recorded round/session video, queued for the video-analysis worker
    (alembic 0058). Written only via the `enqueue_video_job` RPC (ownership proven by the
    storage path prefix, same rule as the `session-videos` bucket policy from 0024) or by the
    service-role worker claiming/updating status. No FK to `user_sessions.session_id` on
    purpose — the session push is offline-first and may not have landed yet when a video
    enqueues; `owner_id`'s cascade already covers account deletion.
    `(status, created_at) where status = 'queued'` is the worker's claim index — partial, so
    it's raw SQL in the migration only (see 0051's own note on partial indexes), not modeled
    here."""

    __tablename__ = "session_video_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "media_id", name="uq_session_video_jobs_owner_media"),
        CheckConstraint(
            "round_kind IN ('round', 'full_session')", name="ck_session_video_jobs_round_kind"
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'done', 'failed')",
            name="ck_session_video_jobs_status",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    round_id: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    round_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="round")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SessionVideoAnalysis(Base):
    """The worker's output for one `SessionVideoJob` — 1:1, keyed by `job_id` (no separate
    id, same shape as `AthleteDossier.athlete_id`). Written only by the service-role worker;
    read owner-only via RLS, deliberately without an `is_pro` check (alembic 0058, ADR D9 in
    the video-pro plan) — an expired entitlement must not revoke access to an analysis the
    user already owns."""

    __tablename__ = "session_video_analysis"
    __table_args__ = (Index("ix_session_video_analysis_owner_session", "owner_id", "session_id"),)

    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("session_video_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    round_id: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    motion: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    events: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    sequences: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    difficulty_derived: Mapped[float | None] = mapped_column(Numeric(4, 1))
    difficulty_inputs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    confidence: Mapped[str | None] = mapped_column(Text)
    highlights: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    pdf_path: Mapped[str | None] = mapped_column(Text)
    clip_paths: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
