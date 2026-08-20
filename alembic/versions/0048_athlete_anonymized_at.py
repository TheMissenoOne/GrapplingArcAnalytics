"""An athlete can be anonymised in place, so removing a person never orphans their graph.

Two different things can make an athlete row stop describing somebody, and they must not share
a path:

**Invalid data** — a duplicate, a phantom produced by a bad name mapping, an audit finding. The
athlete was never real, so the graph derived from their bouts is not real either. Both go.

**A rights request (LGPD Art. 18)** — the person is real and so are the bouts. Nothing about the
data was wrong; what must stop is the data identifying them. The graph is anonymised and KEPT.

An ``ON DELETE CASCADE`` cannot express that, and could not exist here anyway: ``graphs.owner_id``
is polymorphic (``owner_kind`` is ``'athlete'`` or ``'user'``, pointing at different tables) and
Postgres has no conditional foreign key. Even if it could, a cascade fires on every delete
regardless of WHY — it would destroy exactly the graph the second case says to keep.

**So the rights-request path does not delete the row at all.** It clears the identifying columns
and stamps this one. ``id`` stays, because it is already a pseudonymous UUID carrying no identity
of its own, and because keeping it is what lets the graph keep a valid owner.

That is the real payoff, and it is structural rather than procedural: **every graph with
``owner_kind='athlete'`` has a row in ``athletes``, with no exceptions.** An orphan is a defect,
always, and no reader has to ask whether this particular one was intentional. The alternative —
delete the athlete and mark the graph — creates a second, legitimate kind of orphan, and the
guard becomes "an orphan WITHOUT a marker", which is the same ambiguity that let seven of them
accumulate unnoticed until 2026-08-19.

``is_published`` is set false by the same operation (in code, not here): an anonymised athlete
has no individual page to render. The graph stays available to aggregate work over the athlete
corpus — archetype centroids and the like — which is public-class data throughout and never
becomes more identifying by being averaged.

Privacy class: **A, public competition data**, plus the mechanism by which a person can have
their identity removed from it.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory and never
executes a Postgres migration (0019's scope note). The behaviour is covered by
``tests/test_athlete_removal.py``; the DDL is covered by the migrations-smoke job.

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable with no default: NULL means "this athlete is a named person, normally". Only the
    # rights-request path ever writes it, and it is a timestamp rather than a boolean because
    # when the identity was removed is part of what the record has to be able to show.
    op.add_column(
        "athletes",
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
    )

    # The read is "is this row anonymised", over a table where almost none are — a partial index
    # so the common case costs nothing.
    op.execute(
        "create index if not exists ix_athletes_anonymized "
        "on athletes (anonymized_at) where anonymized_at is not null"
    )


def downgrade() -> None:
    op.execute("drop index if exists ix_athletes_anonymized")
    op.drop_column("athletes", "anonymized_at")
