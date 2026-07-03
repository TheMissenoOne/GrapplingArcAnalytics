#!/usr/bin/env python
"""Assign every user graph to its nearest archetype + write the structural report.

The App-side DB seam for archetype detection (App analytics Part B). A user pushes
their graph to Supabase (``owner_kind='user'``); this batch job — run by an admin,
like the embedding backfills — matches each to its nearest emergent archetype and
writes the ``graphs.archetype_report`` the App reads under its own RLS.

Population baseline comes from the **athlete** graphs (the real-grappler corpus),
exactly as ``run_archetype_pipeline``. The 768-d embedding decides *which*
archetype (semantic nearest); the 18-d feature vector explains *why*
(``compare_feature_vectors``). Requires archetypes to exist first
(``analysis.embeddings graphs`` + ``run_archetype_pipeline``).

    uv run python -m scripts.assign_user_archetypes            # write reports
    uv run python -m scripts.assign_user_archetypes --dry-run  # report only
    uv run python -m scripts.assign_user_archetypes --embed    # re-embed graphs first
"""

from __future__ import annotations

import argparse
import logging

import numpy as np

logger = logging.getLogger(__name__)

MIN_GRAPH_NODES = 3  # mirror analysis.archetype.MIN_GRAPH_NODES (skip near-empty graphs)


def run(dry_run: bool = False, embed: bool = False) -> int:
    from dotenv import load_dotenv
    load_dotenv()

    from sqlalchemy import select

    from analysis.archetype import assign_user_archetype
    from analysis.deviance import node_population_stats
    from analysis.embeddings import backfill_graph_embeddings
    from db.base import db_session
    from db.models import Graph, GraphEdge
    from db.repository import archetype_refs, assign_user_archetype_to_graph, graphs_for_clustering

    with db_session() as session:
        athlete_rows = [
            (gid, nodes) for gid, nodes in graphs_for_clustering(session, owner_kind="athlete")
            if len(nodes) >= MIN_GRAPH_NODES
        ]
        if not athlete_rows:
            logger.warning("No athlete graphs — population baseline unavailable; aborting")
            return 1
        by_key, by_type = node_population_stats(athlete_rows)

        refs = archetype_refs(session)
        if not refs:
            logger.warning("No archetypes with a feature centroid — run run_archetype_pipeline first")  # noqa: E501
            return 1

        if embed and not dry_run:
            backfill_graph_embeddings(session)  # refresh graph embeddings (commits internally)

        user_rows = [
            (gid, nodes) for gid, nodes in graphs_for_clustering(session, owner_kind="user")
            if len(nodes) >= MIN_GRAPH_NODES
        ]
        assigned = 0
        for gid, nodes in user_rows:
            edges: list[object] = list(
                session.execute(select(GraphEdge).where(GraphEdge.graph_id == gid)).scalars()
            )
            g = session.get(Graph, gid)
            emb = (
                np.asarray(g.embedding, dtype=np.float64)
                if (g is not None and g.embedding is not None) else None
            )
            report = assign_user_archetype(
                nodes, by_key, by_type, refs, edges=edges, user_embedding=emb
            )
            if report is None:
                continue
            if dry_run:
                logger.info("  graph %s → %s (similar=%d, differ=%d)", gid, report["name"],
                            len(report["similar"]), len(report["differ"]))
            else:
                assign_user_archetype_to_graph(gid, report["archetype_id"], report, session)
            assigned += 1

        refreshed = " (embeddings refreshed)" if (embed and not dry_run) else ""
        logger.info("%s %d user graph(s) across %d archetypes%s",
                    "Would assign" if dry_run else "Assigned", assigned, len(refs), refreshed)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Assign user graphs to nearest archetype")
    ap.add_argument("--dry-run", action="store_true", help="match + report, no DB writes")
    ap.add_argument("--embed", action="store_true", help="refresh graph embeddings first")
    args = ap.parse_args()
    return run(dry_run=args.dry_run, embed=args.embed)


if __name__ == "__main__":
    raise SystemExit(main())
