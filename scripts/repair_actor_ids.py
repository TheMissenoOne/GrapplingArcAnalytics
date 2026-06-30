#!/usr/bin/env python
"""Repair stale ``matches.sequence[].actor_id`` left behind by athlete merges.

When the dedupe script merges a duplicate athlete row into a canonical one it repoints the
``Match`` FK columns but NOT the ``actor_id`` embedded in each ``sequence[]`` JSONB entry. The
orphaned ids then fail ``_perspective_view`` (no "you" entries) → empty graphs + floored ELO
(e.g. Gordon Ryan, 98 wins, stuck at 800).

Each bout's sequence references exactly two actors (subject + opponent). The correctly-tagged
one is still a current participant; the stale one is whatever id is no longer ``athlete_a/b``.
We remap the stale id → the participant it must be (the one of {a,b} not already present).

    uv run python -m scripts.repair_actor_ids --dry-run   # report
    uv run python -m scripts.repair_actor_ids             # fix + (optionally) replay
"""

from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)


def run(dry_run: bool, replay: bool) -> int:
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from db.base import db_session
    from db.models import Athlete, Match

    with db_session() as session:
        matches = list(session.execute(select(Match)).scalars())
        fixed_matches = 0
        fixed_entries = 0
        unresolved = 0
        touched_athletes: set[str] = set()

        for m in matches:
            seq = m.sequence or []
            if not seq:
                continue
            valid = {m.athlete_a_id, m.athlete_b_id}
            actors = {e.get("actor_id") for e in seq if isinstance(e, dict) and e.get("actor_id")}
            stale = actors - valid
            if not stale:
                continue
            present = actors & valid
            # Infer only when exactly one stale id maps to one free participant slot.
            missing = valid - present
            if len(stale) == 1 and len(missing) == 1:
                stale_id = next(iter(stale))
                target_id = next(iter(missing))
                cnt = 0
                for e in seq:
                    if isinstance(e, dict) and e.get("actor_id") == stale_id:
                        if not dry_run:
                            e["actor_id"] = target_id
                        cnt += 1
                fixed_matches += 1
                fixed_entries += cnt
                touched_athletes.update(valid)
                if not dry_run:
                    flag_modified(m, "sequence")
            else:
                unresolved += 1
                logger.warning("Cannot infer match %s: stale=%s present=%s", m.id, stale, present)

        if not dry_run:
            session.flush()
            if replay:
                from db.repository import replay_and_persist_athlete

                for aid in touched_athletes:
                    ath = session.get(Athlete, aid)
                    if ath is not None:
                        replay_and_persist_athlete(ath, session)

        verb = "to replay" if dry_run else ("replayed" if replay else "touched")
        logger.info(
            "%s: %d matches re-tagged (%d entries), %d unresolved, %d athletes %s",
            "DRY-RUN" if dry_run else "DONE", fixed_matches, fixed_entries, unresolved,
            len(touched_athletes), verb,
        )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Repair stale sequence actor_ids")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-replay", action="store_true", help="skip re-replay after repair")
    args = ap.parse_args()
    return run(args.dry_run, replay=not args.no_replay)


if __name__ == "__main__":
    raise SystemExit(main())
