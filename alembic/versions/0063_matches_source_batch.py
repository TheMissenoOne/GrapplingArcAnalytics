"""Which ingestion run wrote each match.

E16 (``docs/research/e16_technique_jaccard_prereg.md`` §3a) names the gap directly: 911
final+sequence rows carry ``created_by = NULL`` for all of them, so the within/cross-batch
provenance control that gates the technique-Jaccard reading falls back to a 30-minute
``created_at`` gap heuristic — 19 inferred "sessions" standing in for the ~90 real dump-module
runs. ``matches`` has no import/dump/batch column; this adds one.

``source_batch`` is the dump module's own human label — the same string every
``scripts.dump_import.run_dump(..., label=...)`` caller already carries (``scripts.
reprocess_all.DATASETS``'s third tuple element, e.g. ``"ADCC2024-ABS"``, ``"Khabib"``). Nullable,
no default: NULL means "not yet backfilled" (``scripts/backfill_source_batch.py``) for the 911
existing rows, or "written by a path other than ``run_dump``" going forward (``insert_ufc_matches.
main`` calls ``register_match`` directly and is not touched by this migration).

No backfill here — deriving the label for an existing row means re-running every dump module's
``build_matches`` and matching bouts back by (athlete pair, year, event), which belongs in a
script with a dry-run + coverage report, not silent migration DDL guessing at data.

Privacy class: **A, public competition data** — describes provenance of already-published bouts,
nothing user-fed.

Revision ID: 0063
Revises: 0062
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("source_batch", sa.Text(), nullable=True))
    op.create_index("ix_matches_source_batch", "matches", ["source_batch"])


def downgrade() -> None:
    op.drop_index("ix_matches_source_batch", table_name="matches")
    op.drop_column("matches", "source_batch")
