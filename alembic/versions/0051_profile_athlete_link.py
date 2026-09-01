"""Link one profile to one athlete — a user's account and their row in the public corpus.

An App user who is *also* a published athlete in the competition corpus (matched by hand,
off-band — this migration only stores the result) gets one nullable pointer:
``profiles.athlete_id -> athletes.id``, ``ON DELETE SET NULL`` so removing/anonymising the
athlete (alembic 0048's two paths) never breaks the owning profile, and a partial unique
index so the same athlete can never be claimed by two profiles.

**Why the pointer lives on `profiles`, not on `athletes`.** `profiles` is RLS owner-only
(alembic 0023: `profiles_select_own`/`_insert_own`/`_update_own`, all `id = auth.uid()`);
`athletes` is read publicly (`is_published=true` rows, `alembic/versions/0003`). Putting the
FK on the private side means the public, world-readable table learns nothing about which
private account it is linked to — the same one-way shape as `graph_nodes.canonical_node_key`
(0037): private may point at public, public never points back. An FK on `athletes` pointing at
`profiles` would have inverted that, and the site export / any athlete-corpus query would have
had to remember to never SELECT it.

**Why a client cannot self-link.** `alembic/versions/0023_grants.py` grants
`authenticated` INSERT/UPDATE on `public.profiles` as an explicit per-column list
(`grant insert (id, full_name, belt_rank, belt_degrees, is_guest, archetype_id)` /
`grant update (full_name, belt_rank, belt_degrees, is_guest, archetype_id) ...`), not a
table-level grant — Postgres column privileges apply only to the columns named at grant time,
so a column added afterward is implicitly ungranted until explicitly listed. `athlete_id` is
deliberately left OUT of both lists here: an authenticated user's own INSERT/UPDATE naming
`athlete_id` fails on a permission-denied column error, so nobody can declare themselves Gordon
Ryan. `grant select on public.profiles to authenticated` (also 0023) IS table-level, so it
already covers every future column including this one — the profile's owner can read their own
link with no grant change needed here. Linking a profile to an athlete is therefore an
admin-only write (service-role session, e.g. `scripts/import_user_bundle.py --athlete`), same
trust tier as `is_pro`.

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "athlete_id",
            UUID(as_uuid=False),
            sa.ForeignKey("athletes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Partial unique — one athlete claimed by at most one profile; unlinked profiles (the
    # overwhelming majority) all carry NULL and a plain unique index would collide on them.
    # Raw SQL like every other partial index in this repo (0048's `ix_athletes_anonymized`) —
    # db/models.py represents columns/FKs only, never indexes (see that migration's docstring).
    op.execute(
        "create unique index if not exists ux_profiles_athlete_id "
        "on public.profiles (athlete_id) where athlete_id is not null"
    )


def downgrade() -> None:
    op.execute("drop index if exists ux_profiles_athlete_id")
    op.drop_column("profiles", "athlete_id")
