#!/usr/bin/env python
"""Batch-insert Gordon Ryan's career into the DB and build his athlete graph.

Reuses the tested repository pipeline (no hand-rolled SQL):
  seed_athletes_from_leaderboard  → every ADCC-ranked fighter gets an Athlete row
                                     with rank_elo (so opponent ELO comes from the
                                     ranking), Gordon Ryan included.
  register_match × 110            → stores each global Match (both sides are
                                     athletes) + registers its techniques into the
                                     shared technique_nodes lib.
  replay_and_persist_athlete      → chronological ELO replay (per perspective) →
                                     graph + athlete.elo + athlete.elo_series.

Opponent input ELO: an opponent who matches a leaderboard athlete (by normalized
name) contributes their own rank_elo on the double pass; opponents NOT on the
leaderboard fall back to the black-belt floor (800), logged for later fill-in.

Idempotent: re-running deletes Gordon's existing matches first, then re-inserts.

Usage:
    uv run python -m scripts.insert_gordon_matches            # write to DB
    uv run python -m scripts.insert_gordon_matches --dry-run  # parse + report only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from analysis.names import _normalize_name  # noqa: E402
from scripts.gordon_matches import ATHLETE, MATCHES  # noqa: E402

logger = logging.getLogger(__name__)


def run(dry_run: bool = False) -> int:
    logger.info("Gordon import: %d career matches staged", len(MATCHES))
    if dry_run:
        ranked = sum(1 for m in MATCHES if m["sequence"])
        logger.info("  %d with detailed sequences; %d metadata-only",
                    ranked, len(MATCHES) - ranked)
        return 0

    from sqlalchemy import delete, or_, select

    from db.base import db_session
    from db.models import Athlete, Match
    from db.repository import (
        register_match,
        replay_and_persist_athlete,
        seed_athletes_from_leaderboard,
        upsert_athlete,
    )

    with db_session() as session:
        # 1. Seed all leaderboard athletes (idempotent) → opponents resolve rank_elo.
        created = seed_athletes_from_leaderboard(session)
        logger.info("Seeded %d new athletes from the ADCC leaderboard", created)

        by_norm = {
            _normalize_name(a.name): a
            for a in session.execute(select(Athlete)).scalars()
        }

        def resolve(name: str, source: str) -> Athlete:
            """Get-or-create an athlete by normalized name (both participants are athletes)."""
            key = _normalize_name(name)
            ath = by_norm.get(key)
            if ath is None:
                aid = upsert_athlete(name=name, belt="black", source=source, session=session)
                ath = session.get(Athlete, aid)
                assert ath is not None
                by_norm[key] = ath
            return ath

        # 2. Gordon + an athlete row for every opponent (global model: both are athletes).
        gordon = resolve(ATHLETE, source="manual")

        # 3. Idempotency: clear Gordon's existing matches (either side) before re-inserting.
        session.execute(
            delete(Match).where(
                or_(Match.athlete_a_id == gordon.id, Match.athlete_b_id == gordon.id)
            )
        )
        session.flush()

        # 4. Register every match as a GLOBAL match; map you→Gordon, opponent→rival id.
        participants: set[str] = {gordon.id}
        unranked: list[str] = []
        for m in MATCHES:
            opp = resolve(m["opponent"], source="opponent")
            participants.add(opp.id)
            if opp.rank_elo is None:
                unranked.append(m["opponent"])
            seq = [
                {**{k: v for k, v in e.items() if k != "actor"},
                 "actor_id": gordon.id if e.get("actor") == "you" else opp.id}
                for e in m["sequence"]
            ]
            if m["win_type"] == "DRAW":
                winner_id = None
            else:
                winner_id = gordon.id if m["won"] else opp.id
            register_match(
                gordon.id,
                opp.id,
                winner_id=winner_id,
                win_type=m["win_type"],
                submission=m["submission"],
                event=m["event"],
                year=m["year"],
                weight_class=m["weight_class"],
                stage=m["stage"],
                sequence=seq,
                created_by=None,
                session=session,
            )

        # 5. Double pass — replay EVERY participant so opponents get graphs from their side.
        for aid in participants:
            athlete = session.get(Athlete, aid)
            if athlete is not None:
                replay_and_persist_athlete(athlete, session)

        logger.info(
            "Inserted %d global matches; Gordon graph ELO %.1f (rank_elo=%s); replayed %d athletes",
            len(MATCHES), gordon.elo, gordon.rank_elo, len(participants))
        distinct_unranked = sorted(set(unranked))
        logger.info("%d/%d opponents not on the leaderboard (belt-floor input ELO): %s",
                    len(distinct_unranked), len({m["opponent"] for m in MATCHES}),
                    ", ".join(distinct_unranked))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Insert Gordon Ryan's career + build graph")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    return run(dry_run=ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
