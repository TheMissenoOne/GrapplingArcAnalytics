"""Frame annotations: a reviewable label per event that has a video timestamp.

There are 1936 events in the DB carrying both a ``matches.video_url`` and an
``ts`` inside ``matches.sequence`` — real frames with a human-written label
already attached. That is a labelled corpus nobody has looked at as one.

This table holds ONE row per (match, event) frame:

  - what the pipeline predicted (``predicted``), so review starts from a
    proposal rather than a blank form;
  - what a human decided (``status`` + ``corrected``);
  - enough provenance to redo it (``model_version``, ``frame_ts``).

The prediction is deliberately kept SEPARATE from the correction. Overwriting
the proposal with the fix would destroy the only record of where the model was
wrong — which is the whole reason to build this.

``status``:
  pending   — pre-labelled, nobody has looked
  approved  — a human agreed with ``predicted``
  corrected — a human disagreed; ``corrected`` holds the truth
  skipped   — unjudgeable (frame missing, occluded, bout not in shot)

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "frame_annotations",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "match_id",
            UUID(as_uuid=False),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Index into matches.sequence. Together with match_id this identifies the
        # frame, and it stays valid as long as the sequence is not reordered.
        sa.Column("event_index", sa.Integer(), nullable=False),
        # Seconds into the video. Denormalized from the event so a frame can be
        # re-fetched without re-reading the whole sequence.
        sa.Column("frame_ts", sa.Float(), nullable=False),
        # The human-written event this frame came from, copied so the annotation
        # survives an edit to the match sequence.
        sa.Column("event_label", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        # {position, role, state, *_conf, identity_resolved, identity_broken}
        sa.Column("predicted", JSONB(), nullable=True),
        sa.Column("corrected", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        # Which pipeline produced `predicted`. A re-prediction under a new model
        # must not silently overwrite a human decision made against the old one.
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status in ('pending','approved','corrected','skipped')",
            name="frame_annotations_status_valid",
        ),
        # A corrected row without a correction is a lie about being reviewed.
        sa.CheckConstraint(
            "status <> 'corrected' or corrected is not null",
            name="frame_annotations_corrected_has_payload",
        ),
        sa.UniqueConstraint("match_id", "event_index", name="frame_annotations_frame_unique"),
    )
    op.create_index("idx_frame_annotations_status", "frame_annotations", ["status"])
    op.create_index("idx_frame_annotations_match", "frame_annotations", ["match_id"])

    # This is admin-only data reached through the local dashboard's own service
    # credentials. RLS on with no policy = deny to anon/authenticated, which is
    # the correct default: nothing in the app or the web client should read it.
    op.execute("alter table public.frame_annotations enable row level security;")


def downgrade() -> None:
    op.drop_index("idx_frame_annotations_match", table_name="frame_annotations")
    op.drop_index("idx_frame_annotations_status", table_name="frame_annotations")
    op.drop_table("frame_annotations")
