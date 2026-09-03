#!/usr/bin/env python
"""Delete athlete graph_nodes with zero incident edges — the orphans a replay's edge
prune leaves behind (`db.repository.upsert_graph_from_athlete_graph` deletes stale
edges but only ever upserts nodes, so a node whose last edge got pruned survives with
none). Athlete graphs only (`owner_kind='athlete'`) — a user's own edge-less node is
legitimate (they typed it, it's theirs) and this script never touches it.

Measured 2026-09-03: 395 orphan nodes across athlete graphs, 37 of them stale alias
keys ("snap down", "north south control", ...) whose graph already carries the
canonical replacement. `db.repository.upsert_graph_from_athlete_graph` now prunes
these going forward (same PR); this script cleans up what already accumulated.

    uv run python -m scripts.prune_orphan_graph_nodes             # report (default)
    uv run python -m scripts.prune_orphan_graph_nodes --apply      # delete
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from db.models import Graph, GraphEdge, GraphNode

logger = logging.getLogger("prune_orphan_graph_nodes")


def orphan_athlete_nodes(session: Session) -> list[GraphNode]:
    """Athlete graph_nodes with no incident graph_edges row, in either direction."""
    incident = exists(
        select(GraphEdge.graph_id).where(
            GraphEdge.graph_id == GraphNode.graph_id,
            or_(
                GraphEdge.source_key == GraphNode.node_key,
                GraphEdge.target_key == GraphNode.node_key,
            ),
        )
    )
    stmt = (
        select(GraphNode)
        .join(Graph, Graph.id == GraphNode.graph_id)
        .where(Graph.owner_kind == "athlete", ~incident)
    )
    return list(session.execute(stmt).scalars())


def prune(session: Session, apply: bool) -> Counter[str]:
    """Report (or, with ``apply``, delete) orphan athlete nodes. Returns counts by node_key."""
    orphans = orphan_athlete_nodes(session)
    counts = Counter(n.node_key for n in orphans)

    if not counts:
        logger.info("no orphan athlete graph nodes — the invariant holds.")
        return counts

    logger.info("%d orphan athlete graph node(s), %d distinct key(s):", len(orphans), len(counts))
    for node_key, count in counts.most_common(20):
        logger.info("  %5d  %s", count, node_key)

    if not apply:
        logger.info("dry run — nothing deleted (pass --apply to delete).")
        return counts

    for node in orphans:
        session.delete(node)
    session.commit()
    logger.info("deleted %d orphan node(s).", len(orphans))
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="delete (default: dry-run report)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from dotenv import load_dotenv

    load_dotenv()

    from db.base import get_session_factory

    with get_session_factory()() as session:
        prune(session, apply=args.apply)
        if args.apply:
            remaining = len(orphan_athlete_nodes(session))
            logger.info("verification: %d orphan(s) remain in athlete graphs.", remaining)


if __name__ == "__main__":
    main()
