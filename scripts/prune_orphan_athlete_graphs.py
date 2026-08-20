#!/usr/bin/env python
"""Delete athlete-owned graphs whose owner no longer exists, and report if any appear again.

**The invariant.** Every graph with ``owner_kind='athlete'`` must have a row in ``athletes``.
There is no legitimate state in which one does not: a graph is derived from an athlete's bouts,
so a graph without an athlete is a graph about nobody. It is still world-readable — the RLS policy
on athlete graphs grants ``select`` to ``anon`` — so it is public data attributed to a person the
database no longer knows.

**Why this cannot be a foreign key.** ``graphs.owner_id`` is polymorphic: ``owner_kind`` is either
``'athlete'`` or ``'user'``, and the two point at different tables. Postgres has no conditional
foreign key, which is why the column has none and why these orphans could accumulate silently.

**And why an ``ON DELETE CASCADE`` would be wrong even if it were possible.** A cascade fires on
every delete regardless of WHY the athlete row went away, and the two reasons must not share a
path. An athlete deleted because the data was invalid — a duplicate, a phantom produced by a bad
name mapping, an audit finding — takes their graph with them, because the graph is derived from
the same invalid data. An athlete removed at their own request under LGPD is the opposite case:
nothing about the bouts was wrong, so the graph is anonymised and KEPT. A blind cascade would
destroy the second one. `db.repository.delete_athlete` is where that decision lives.

Measured 2026-08-19: seven such graphs in production, holding seventeen edges and zero bout
provenance. Four dated to the 2026-06-29 dedupe and three to the AA-011 repair — in both cases an
athlete was deleted by hand and the graph was left behind.

    uv run python -m scripts.prune_orphan_athlete_graphs --dry-run   # report
    uv run python -m scripts.prune_orphan_athlete_graphs             # delete
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import text

logger = logging.getLogger("prune_orphan_athlete_graphs")

#: An athlete-owned graph whose owner is not in `athletes`. Reused verbatim by both the report
#: and the delete so the two can never disagree about what an orphan is.
_ORPHAN = """
    g.owner_kind = 'athlete'
    and not exists (select 1 from athletes a where a.id = g.owner_id)
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report and roll back")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from dotenv import load_dotenv

    load_dotenv()

    from db.base import get_session_factory

    with get_session_factory()() as session:
        rows = session.execute(text(f"""
            select g.id, g.owner_id, g.updated_at,
                   (select count(*) from graph_edges e where e.graph_id = g.id) as edges
            from graphs g where {_ORPHAN}
            order by g.updated_at
        """)).all()

        if not rows:
            logger.info("no orphan athlete graphs — the invariant holds.")
            return

        logger.info("%d orphan athlete graph(s):", len(rows))
        for graph_id, owner_id, updated_at, edges in rows:
            logger.info(
                "  graph %s  owner %s  last written %s  %d edge(s)",
                str(graph_id)[:8], str(owner_id)[:8], updated_at.date(), edges,
            )

        if args.dry_run:
            logger.info("dry run — nothing deleted.")
            return

        # `graph_edges` cascades from `graphs`, and `graph_edge_bouts` cascades from the edge, so
        # deleting the graph row is the whole operation.
        deleted = session.execute(text(f"""
            delete from graphs g where {_ORPHAN}
        """)).rowcount
        session.commit()
        logger.info("deleted %d orphan graph(s).", deleted)


if __name__ == "__main__":
    main()
