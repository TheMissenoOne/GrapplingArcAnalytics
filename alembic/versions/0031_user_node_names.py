"""What each user calls a position, when they don't call it what the library calls it.

Grappling nomenclature is not standardised. The same control is "Shoulder Crunch" in one gym
and "Shoulder Clamp" in another; "Gift Wrap" and "Arm Wrap"; "Saddle", "Honey Hole", "411" and
"Inside Sankaku" are one position with four names. The App's canonical library answers this on
the *search* side already — every node carries ``variations[]`` and translations, so any of
those names finds the node. What it could not do is remember that a particular person wants to
*see* their name for it.

So: one canonical identity, many searchable names, one preferred display name per user. This
table owns only the last of those three.

**Identity is never the display string.** The canonical node key stays what
``analysis/names.py:_normalize_name`` produces (and what the App's ``normalizeLabel`` mirrors
char-for-char — that contract is not touched by this feature and must not be). ``node_key``
here is a foreign concept only in the loose sense: it is that same normalized key, stored as
text, deliberately WITHOUT a FK to ``technique_nodes``. A user can hold a preference for a node
that exists only in the bundled App library, or for one they created themselves, and neither is
guaranteed to have a ``technique_nodes`` row. A dangling preference is inert; a FK here would
turn "you renamed a node the server hasn't seen" into a sync failure.

**This never edits the canonical library.** User A preferring "Shoulder Clamp" leaves the
library, the athlete corpus, the public site and every other user reading "Shoulder Crunch".
The preference is presentation, per account, and nothing downstream joins on it — graphs, ELO,
transition counts and suggestions all key on the canonical node key, so two sessions logged
under two names still land on one node. That invariant is what makes the whole feature safe to
have.

Row-per-preference, PK ``(owner_id, node_key)``, rather than one JSON blob per user: a
preference set on the phone and a different one set on the web then merge per node instead of
one device's whole map clobbering the other's. The composite PK is also what makes the App's
``on_conflict: 'owner_id,node_key'`` legal — unlike ``user_sessions``, where that same pair was
guessed without the constraint existing and returned 42P10 on every push (0017 keys on ``id``
alone). Here the constraint is real because this revision creates it.

Privacy class: **C, user cloud-synced private data**. It is app-fed, owner-scoped, and serves
only the user who set it. It never feeds an aggregate, a centroid, an export or the public
site. Note the mild PII shape — a preferred name is free text the user typed — which is another
reason it stays owner-scoped and out of every derivation path.

Scope note (test coverage): pytest here runs against SQLite in-memory (``tests/test_db.py``),
which round-trips the ``db/models.py`` shape but does NOT execute this migration's
Postgres-only policies. Same caveat as 0017/0019/0030.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_node_names",
        sa.Column(
            "owner_id",
            UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Deliberately no FK to technique_nodes — see the module docstring. A preference for a
        # node the server has never seen is valid and must not fail the sync.
        sa.Column("node_key", sa.Text(), primary_key=True),
        sa.Column("preferred_name", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "idx_user_node_names_owner_updated",
        "user_node_names",
        ["owner_id", "updated_at"],
        if_not_exists=True,
    )

    op.execute("alter table public.user_node_names enable row level security;")
    # Revoke from both roles before granting back — see 0030 for why `authenticated` is in
    # there too (the schema default includes TRUNCATE, which RLS cannot gate).
    op.execute("revoke all on public.user_node_names from anon, authenticated;")
    op.execute(
        "grant select, insert, update, delete on public.user_node_names to authenticated;"
    )

    op.execute("drop policy if exists user_node_names_owner_all on public.user_node_names;")
    op.execute(
        """
        create policy user_node_names_owner_all on public.user_node_names for all
          to authenticated
          using (owner_id = auth.uid())
          with check (owner_id = auth.uid());
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists user_node_names_owner_all on public.user_node_names;")
    op.drop_index(
        "idx_user_node_names_owner_updated", table_name="user_node_names", if_exists=True
    )
    op.drop_table("user_node_names")
