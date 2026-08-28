"""Athlete gender, for prose agreement only — never for ranking, filtering, or matchmaking.

The public dossier / breakdown prose (``export/narrative.py``, ``export/site_data.py``) hardcoded
masculine pronouns ("he", "his", "dele", "ele") on every athlete, because nothing on the row said
otherwise. That is wrong for the corpus's female athletes (ADCC women's divisions, trials) and it
is wrong in principle — defaulting to masculine on missing data is a chosen bias, not a neutral one.

**NULL is the honest default**, not 'm'. Two explicit values plus unknown:

- ``'f'`` — evidenced by a named women's roster/event manifest (ADCC women's brackets, trials).
- ``'m'`` — evidenced the same way (a named men's event), never assumed by omission.
- ``NULL`` — no evidence either way. Prose generators MUST render a gender-neutral sentence for
  NULL, never fall back to masculine (``analysis/gendered_text.py`` is the single place that
  decides — see its docstring for the convention).

Backfill lives in ``scripts/backfill_athlete_gender.py`` (evidence-driven, dry-run by default,
never guesses).

Privacy class: **A, public competition data** — a fact about a public athlete's public identity,
same class as ``weight_class``/``belt``. Not sourced from and never applied to App-fed private
data (root CLAUDE.md public/private boundary; no `user_sessions`/user-graph table gets this).

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory and never
executes a Postgres migration (0019's scope note); DDL covered by the migrations-smoke job.

Revision ID: 0049
Revises: 0048
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "athletes",
        sa.Column("gender", sa.String(1), nullable=True),
    )
    op.execute(
        "alter table athletes add constraint ck_athletes_gender "
        "check (gender is null or gender in ('f', 'm'))"
    )


def downgrade() -> None:
    op.execute("alter table athletes drop constraint if exists ck_athletes_gender")
    op.drop_column("athletes", "gender")
