"""Which bouts an athlete's edge was observed in — the missing unit ADR-08 is blocked on.

``graph_edges`` is deduplicated to one row per ``(graph_id, edge_key)``. It carries a count and
an ELO and says nothing about WHERE the transitions came from, so a bootstrap over that table
can only resample **edges**. Wave 6b did exactly that and then said so plainly: comparing two
community detectors on an edge bootstrap is a weaker test than wave 6's bout bootstrap, and
deciding on it would repeat the error 6b had just exposed — reading a number that depends on the
shape of the input as if it described the algorithm.

That is the whole reason ADR-08 is reopened rather than closed, and it names this as condition
(a). This is condition (a).

**A side table, not a column.** Provenance is many-to-one: one edge is normally observed across
several bouts, which is precisely what makes it resamplable. A column could hold one id, or an
array nobody can join on.

**No new derivation.** The pairs are recorded by the code that already produces the edges —
``athlete_elo.py``'s replay, inside its per-match loop, keyed by
``canonicalize(_normalize_name(...))`` exactly as the edge it belongs to. A second walk over
``matches.sequence`` would be a second opinion about what an edge is, and the two would drift.
Wave 6b lost a round to a key-space mismatch of exactly that kind.

**Athlete graphs only, by construction.** Nothing writes bout provenance for a user graph — a
user's sessions are private app-fed data and have no bout to point at. The FK to ``matches``
makes that structural rather than conventional: there is no row a user edge could reference.

Privacy class: **A, public competition data.** A match is already published by the event; this
records which published match an already-derived public edge came from. It adds no new fact
about anyone, and it touches nothing owned by a user.

Scope note (test coverage): this repo's pytest suite runs against SQLite in-memory and never
executes a Postgres migration (0019's scope note). The derivation is covered by
``tests/test_edge_bout_provenance.py``; the schema is covered by the migrations-smoke job.

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `graph_edges` is unique on (graph_id, edge_key), which is a DIFFERENT key than the FK
    # below needs. Postgres requires a unique index on exactly the referenced columns, so this
    # is the constraint that makes the composite FK legal, and it has to exist BEFORE the table
    # that references it — creating them the other way round fails with "there is no unique
    # constraint matching given keys", which is what migrations-smoke caught.
    #
    # It is also true independently: `edge_key` is `f"{source}→{target}"`, so the triple
    # determines the row either way.
    op.create_unique_constraint(
        "graph_edges_graph_source_target_key",
        "graph_edges",
        ["graph_id", "source_key", "target_key"],
    )

    op.create_table(
        "graph_edge_bouts",
        sa.Column("graph_id", UUID(as_uuid=False), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column(
            "match_id",
            UUID(as_uuid=False),
            sa.ForeignKey("matches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The edge is the unit, the bout is the observation: one row per (edge, bout), so a
        # bootstrap can resample bouts and rebuild the edge set from what it drew.
        sa.PrimaryKeyConstraint(
            "graph_id", "source_key", "target_key", "match_id",
            name="graph_edge_bouts_pkey",
        ),
        # Cascades from the edge, not merely from the graph: an edge pruned by a replay
        # (technique renamed, game re-derived smaller) must not leave provenance behind
        # claiming a transition nobody makes any more.
        sa.ForeignKeyConstraint(
            ["graph_id", "source_key", "target_key"],
            ["graph_edges.graph_id", "graph_edges.source_key", "graph_edges.target_key"],
            name="graph_edge_bouts_edge_fk",
            ondelete="CASCADE",
        ),
    )

    # The bootstrap's read is "give me every edge observed in this set of bouts".
    op.create_index(
        "idx_graph_edge_bouts_match",
        "graph_edge_bouts",
        ["match_id"],
        if_not_exists=True,
    )

    op.execute("alter table public.graph_edge_bouts enable row level security;")

    # Public competition data, read the same way the athlete graphs it belongs to are read.
    # Writes stay server-side: nothing in any client derives this, and a client that could
    # would be asserting which published bout a public edge came from.
    op.execute("revoke all on public.graph_edge_bouts from anon, authenticated;")
    op.execute("grant select on public.graph_edge_bouts to anon, authenticated;")

    op.execute("drop policy if exists graph_edge_bouts_read on public.graph_edge_bouts;")
    op.execute(
        """
        create policy graph_edge_bouts_read on public.graph_edge_bouts for select
          to anon, authenticated
          using (
            exists (
              select 1 from public.graphs g
              where g.id = graph_edge_bouts.graph_id
                and g.owner_kind = 'athlete'
            )
          );
        """
    )


def downgrade() -> None:
    op.execute("drop policy if exists graph_edge_bouts_read on public.graph_edge_bouts;")
    op.drop_index("idx_graph_edge_bouts_match", table_name="graph_edge_bouts", if_exists=True)
    op.drop_table("graph_edge_bouts")
    op.drop_constraint(
        "graph_edges_graph_source_target_key", "graph_edges", type_="unique"
    )
