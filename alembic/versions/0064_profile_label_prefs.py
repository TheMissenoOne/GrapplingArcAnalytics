"""Per-user technique naming-preference sync: profiles.label_prefs.

The App learns, per account, which alias of a technique the athlete actually types/picks
(`services/labelPrefs.ts` — nodeKey -> normalizeLabel(alias) -> {display, count, lastAt}) and
uses it to pick a DISPLAY name only; node identity (`node_key`) never changes. It was device-
local (`@grapplingarch:label_prefs:{userId}` AsyncStorage only) — this column makes it
cross-device: the App merges local+remote (`mergeLabelPrefs`: union of nodeKeys/aliases,
`count = max`, `lastAt = max`, `display` from the newer) rather than overwrite, so a vote cast
on one device is never lost by syncing from another.

Privacy class: **PRIVATE** (root `CLAUDE.md` / this repo's `CLAUDE.md` "Public vs Private
Data"). A naming preference is the athlete's own vocabulary, never a fact about their
performance — it is never read by ELO, archetypes, the site, or any competitive artefact, and
it never feeds `analysis/*` at all. It exists purely so the App can render the SAME preferred
name on a second device.

Same convention as `face_ref_path`/`face_consent_at` (0058): a plain `jsonb` column, no new RLS
policy (`profiles_update_own`, 0023, already gates the row to `id = auth.uid()`), just a column
grant extending 0023's explicit per-column list so `authenticated` can read/write their own
value. `not null default '{}'::jsonb` matches the App's `emptyLabelPrefsState()` shape
(`{version: 1, votes: {}}` is written by the client, not assumed by the column default — the
column default is just "no preferences recorded yet", `{}`) so an un-synced account never
gets a NULL where the App/Python side expects an object.

Revision ID: 0064
Revises: 0063
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "label_prefs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )

    # Extends 0023's per-column grant list on profiles (0051/0058's own convention: a column
    # added after 0023 is implicitly ungranted to `authenticated` until named explicitly) — the
    # owner reads/writes their own naming votes, no new RLS policy needed.
    op.execute("grant update (label_prefs) on public.profiles to authenticated;")


def downgrade() -> None:
    op.execute("revoke update (label_prefs) on public.profiles from authenticated;")
    op.drop_column("profiles", "label_prefs")
