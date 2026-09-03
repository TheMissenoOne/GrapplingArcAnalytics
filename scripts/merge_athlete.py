#!/usr/bin/env python
"""Merge ONE athlete row into another — verified duplicate, not name-similarity.

Owner decision 2026-09-03: duplicates get merged only on hand-verified evidence, never by
name-similarity heuristics (that's ``scripts/dedupe_athletes.py``'s job, and it stays
automatic only for the dirty-scrape-name case it was built for). This script is the manual
counterpart: one src id, one dst id, a human already confirmed they're the same person.

Reuses ``scripts.dedupe_athletes.merge_into`` for the FK repoint + sequence actor_id
rewrite + self-match cleanup + ``remove_athlete(..., INVALID_DATA)`` on ``src`` — same
mechanism `dedupe_athletes` uses per duplicate, just for a single explicit pair.

    uv run python -m scripts.merge_athlete SRC_UUID --into DST_UUID --dry-run   # report
    uv run python -m scripts.merge_athlete SRC_UUID --into DST_UUID            # execute
    uv run python -m scripts.merge_athlete SRC_UUID --into DST_UUID --rename "New Name"
"""

from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)


def run(src_id: str, dst_id: str, dry_run: bool, rename: str | None) -> int:
    from db.base import db_session
    from db.models import Athlete
    from db.repository import replay_and_persist_athlete
    from scripts.dedupe_athletes import merge_into

    if src_id == dst_id:
        logger.error("src and dst are the same id: %s", src_id)
        return 1

    with db_session() as session:
        src = session.get(Athlete, src_id)
        dst = session.get(Athlete, dst_id)
        if src is None:
            logger.error("src athlete not found: %s", src_id)
            return 1
        if dst is None:
            logger.error("dst athlete not found: %s", dst_id)
            return 1
        # A rights-request row was anonymised IN PLACE precisely so it keeps its own graph
        # (`db.repository.remove_athlete`'s RIGHTS_REQUEST path) — merging it away would
        # destroy that graph via `merge_into`'s INVALID_DATA removal, undoing the request.
        if src.anonymized_at is not None:
            logger.error(
                "src %s (%r) was anonymised by a rights request at %s — refusing to merge",
                src_id, src.name, src.anonymized_at,
            )
            return 1

        stats = merge_into(session, src, dst, dry_run=dry_run)
        logger.info(
            "%s: merge %s (%r) -> %s (%r): %d matches repointed, %d actor_ids rewritten, "
            "%d self-matches would-be/deleted",
            "DRY-RUN" if dry_run else "DONE", src_id, src.name, dst_id, dst.name,
            stats.matches_repointed, stats.seq_entries_fixed, stats.self_matches_deleted,
        )
        if dry_run:
            return 0

        if rename:
            dst.name = rename
        replay_and_persist_athlete(dst, session)
        logger.info("replayed dst %s (%r)", dst_id, dst.name)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Merge one verified-duplicate athlete into another")
    ap.add_argument("src", metavar="SRC_UUID", help="duplicate row, removed after merge")
    ap.add_argument("--into", dest="dst", metavar="DST_UUID", required=True,
                     help="surviving row, everything repoints here")
    ap.add_argument("--dry-run", action="store_true", help="report, no DB writes")
    ap.add_argument("--rename", metavar="DST_NAME", default=None,
                     help="also rename dst after merging (rarely needed)")
    args = ap.parse_args()
    return run(args.src, args.dst, args.dry_run, args.rename)


if __name__ == "__main__":
    raise SystemExit(main())
