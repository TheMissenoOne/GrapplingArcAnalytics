"""``technique_nodes.origin`` — where a library row's content came from.

Distinct from ``source`` (App/user semantics: ``'library'`` vs ``'user'`` — a row is either
part of the curated vocabulary or a novel technique a user typed; ``export/tech_library.py:249-
270`` and the App both read it). ``source`` never changes here.

``origin`` answers a different question — *within* the ``'library'`` rows, which of the
export's three provenance tiers produced this entry (``export/tech_library.py``):

- ``'curated'`` — the human-reviewed primary list (``analysis/data/technique_library.json``,
  ``source: 'library'`` in the exported JSON).
- ``'dataset'`` — Kaggle ``grappling_techniques``/ADCC submission data
  (``source: 'grappling_techniques_dataset'``/``'adcc_submission_data'``).
- ``'corpus'`` — derived from entered athlete matches, novel-only (``source: 'athlete_match'``).

Motivation: the Web technique picker (`GrapplingArcWeb/src/components/FocusPicker.tsx`) was
showing every one of the 263 processed-library rows as an equal chip — "Omoplata", "Omoplata
(Shoulder Lock)", "Crucifix / Omoplata" side by side with no origin signal, because
``scripts/seed_technique_nodes.py`` stamps every row ``source='library'`` regardless of tier.
``origin`` lets the picker default to curated only and disclose the rest behind a toggle.

NULL = not yet backfilled (pre-0065 rows, or a row this seed run couldn't classify) — the Web
picker treats NULL the same as ``'curated'`` until a re-seed fills it in, so nothing regresses
on apply. ``source='user'`` rows (novel techniques an app user typed live, not from the
processed export) are left NULL by the seed script — they were never part of a library tier.

RLS: no change. ``technique_nodes_public_read`` (0004) is ``using (true)`` and the existing
``grant select on public.technique_nodes to anon, authenticated`` is table-level — a new column
is covered by both automatically, no new policy/grant needed.

Revision ID: 0065
Revises: 0064
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "technique_nodes",
        sa.Column("origin", sa.Text(), nullable=True),
    )
    op.execute(
        "alter table technique_nodes add constraint ck_technique_nodes_origin "
        "check (origin is null or origin in ('curated', 'dataset', 'corpus'))"
    )
    op.execute(
        "create index if not exists idx_technique_nodes_origin on technique_nodes (origin)"
    )


def downgrade() -> None:
    op.execute("drop index if exists idx_technique_nodes_origin")
    op.execute("alter table technique_nodes drop constraint if exists ck_technique_nodes_origin")
    op.drop_column("technique_nodes", "origin")
