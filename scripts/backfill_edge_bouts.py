#!/usr/bin/env python
"""Populate ``graph_edge_bouts`` for every athlete, and touch nothing else.

Alembic 0046 created the table; nothing filled it, because provenance is written by the replay
that derives the edges (``db.repository.upsert_graph_from_athlete_graph``) and no replay has run
since. This is that replay, and only that replay.

**Why not ``scripts.reprocess_all``.** That is the documented corpus-wide operation, and it would
work — but it re-imports every event dump first, de-duping by ``frozenset(participants)+year``
taken from the DUMP. Bout participants in the DB have since been corrected by hand (the AA-011
repair: three phantom athletes deleted, a duplicate WNO 24 bout merged, 866 -> 865 matches).
The dumps still carry the names that produced those phantoms, so a re-import would put them
back. Re-exporting the whole public site as a side effect of filling a provenance table is also
more blast radius than the job needs.

So this walks athletes and replays each one from the matches ALREADY in the database. No dump is
read, no match row is written, no site asset is regenerated.

**Idempotent by construction.** ``upsert_graph_from_athlete_graph`` replaces an athlete's
provenance rather than merging it — a replay derives the full current set, so a bout that no
longer contributes to an edge must lose its row. Running this twice gives what running it once
gives.

**What it does rewrite.** The graph, ``athlete.elo`` and ``athlete.elo_series``, because those
come out of the same replay. The maths is unchanged, so a stable corpus reproduces the values it
already had — but this IS a corpus-wide write, and it belongs to the same human-gated class as a
full replay. It is not something automation should start on its own.

**Commits in batches, not once at the end.** The corpus is ~1300 athletes and the replay is not
fast; a single transaction would hold locks on ``graphs``, ``graph_edges`` and ``athletes`` for
the better part of an hour against production. Batching gives that up in exchange for
resumability, which is the better trade here precisely because the work is idempotent: an
interrupted run leaves a prefix of athletes correctly filled, and re-running redoes them to the
same values.

    uv run python -m scripts.backfill_edge_bouts --dry-run   # count what would be written
    uv run python -m scripts.backfill_edge_bouts             # write it
    uv run python -m scripts.backfill_edge_bouts --limit 5   # smoke test on five athletes
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import func, select

logger = logging.getLogger("backfill_edge_bouts")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="replay in memory, roll back")
    ap.add_argument("--limit", type=int, default=None, help="only the first N athletes")
    ap.add_argument(
        "--batch", type=int, default=50,
        help="commit every N athletes (ignored under --dry-run, which never commits)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from dotenv import load_dotenv

    load_dotenv()

    from analysis.rating_v2.node_rating import build_corpus_node_ratings
    from db.base import get_session_factory
    from db.models import Athlete, GraphEdgeBout
    from db.repository import replay_and_persist_athlete

    session_factory = get_session_factory()

    with session_factory() as session:
        before = session.execute(select(func.count()).select_from(GraphEdgeBout)).scalar_one()
        # ADR-16: ONE corpus-wide Glicko-2 node replay for the whole batch. Built here rather
        # than per athlete because it reads every match — per-athlete it would be a full
        # corpus pass times 1300.
        corpus = build_corpus_node_ratings(session)
        logger.info("node rating corpus: %s", corpus.coverage)
        # Ordered so a `--limit` run is reproducible and a resumed run covers the same prefix.
        athletes = list(
            session.execute(select(Athlete).order_by(Athlete.id)).scalars()
        )
        if args.limit is not None:
            athletes = athletes[: args.limit]

        logger.info("%d athletes to replay; graph_edge_bouts holds %d rows", len(athletes), before)

        failed: list[tuple[str, str]] = []
        for i, athlete in enumerate(athletes, start=1):
            try:
                # A SAVEPOINT per athlete, so one bad replay costs one athlete rather than the
                # whole uncommitted batch. A bare `session.rollback()` here would silently throw
                # away up to `--batch` athletes that had already replayed fine — and "silently"
                # is what this entire fix exists to stop happening.
                with session.begin_nested():
                    replay_and_persist_athlete(athlete, session, node_ratings=corpus)
            except Exception as exc:  # noqa: BLE001 - one bad athlete must not end the corpus run
                # Recorded and reported rather than raised: this is a long write over the whole
                # corpus, and losing 1300 replays to one malformed sequence is the worst outcome.
                failed.append((athlete.id, str(exc)))
                continue
            if not args.dry_run and i % args.batch == 0:
                session.commit()
            if i % 100 == 0:
                held = session.execute(
                    select(func.count()).select_from(GraphEdgeBout)
                ).scalar_one()
                logger.info("  ... %d/%d athletes, %d rows", i, len(athletes), held)

        after = session.execute(select(func.count()).select_from(GraphEdgeBout)).scalar_one()

        if args.dry_run:
            session.rollback()
            logger.info("dry run — rolled back. Would hold %d rows (was %d).", after, before)
        else:
            session.commit()
            logger.info("done. graph_edge_bouts: %d -> %d rows.", before, after)

        if failed:
            logger.warning("%d athletes failed to replay:", len(failed))
            for athlete_id, message in failed[:20]:
                logger.warning("  %s: %s", athlete_id, message)


if __name__ == "__main__":
    main()
