#!/usr/bin/env python
"""Merge duplicate athlete rows created by dirty scraped names, then re-replay.

The dump corpus split single humans into many ``athletes`` rows: transcript timestamps
(``Gordon Ryan [1:16:11]``), nicknames (``Lucas 'Hulk' Barbosa``), accents
(``Mica Galvão`` vs ``Mica Galvao``) and initials (``M. Galvão``). That produced bogus
"X vs X" self-matches and fragmented graphs. This clusters athletes by ``athlete_key``
(cleaned + de-accented), repoints every match/graph to one canonical row, drops the
resulting self/duplicate matches, deletes the dup rows, and re-replays the survivors.

    uv run python -m scripts.dedupe_athletes --dry-run   # report, no writes
    uv run python -m scripts.dedupe_athletes             # execute (destructive)
"""

from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from typing import Any

from analysis.names import athlete_key, clean_athlete_name

logger = logging.getLogger(__name__)


def _score(name: str, n_matches: int) -> tuple[int, int, int, int]:
    """Higher = better canonical. Prefer rows with matches, full (non-initial) accented names."""
    clean = clean_athlete_name(name)
    not_initial = 0 if (len(clean) >= 2 and clean[1] == ".") else 1
    has_accent = 1 if any(ord(c) > 127 for c in clean) else 0
    return (1 if n_matches else 0, not_initial, has_accent, len(clean))


def run(dry_run: bool) -> int:
    from sqlalchemy import delete, func, select, update

    from db.base import db_session
    from db.models import Athlete, Graph, Match
    from db.repository import replay_and_persist_athlete

    with db_session() as session:
        athletes = list(session.execute(select(Athlete)).scalars())

        def n_matches(aid: str) -> int:
            return session.execute(
                select(func.count()).select_from(Match).where(
                    (Match.athlete_a_id == aid) | (Match.athlete_b_id == aid)
                )
            ).scalar_one()

        clusters: dict[str, list[Any]] = defaultdict(list)
        for a in athletes:
            clusters[athlete_key(a.name)].append(a)

        repoint = 0
        merged_rows = 0
        touched: set[str] = set()
        id_map: dict[str, str] = {}  # dup athlete id → canonical id (for sequence actor_id rewrite)
        for key, rows in clusters.items():
            if len(rows) < 2:
                continue
            canon = max(rows, key=lambda a: _score(a.name, n_matches(a.id)))
            canon_clean = clean_athlete_name(canon.name)
            dups = [a for a in rows if a.id != canon.id]
            logger.info("MERGE %-28s canonical=%r  <- %s", key, canon_clean,
                        [clean_athlete_name(d.name) for d in dups])
            touched.add(canon.id)
            for d in dups:
                merged_rows += 1
                id_map[d.id] = canon.id
                if dry_run:
                    repoint += n_matches(d.id)
                    continue
                for col in (Match.athlete_a_id, Match.athlete_b_id, Match.winner_id):
                    res = session.execute(
                        update(Match).where(col == d.id).values({col.key: canon.id})
                    )
                    repoint += getattr(res, "rowcount", 0) or 0
                session.execute(delete(Graph).where(Graph.owner_id == d.id,
                                                     Graph.owner_kind == "athlete"))
                session.execute(delete(Athlete).where(Athlete.id == d.id))
            if not dry_run:
                canon.name = canon_clean
                # Preserve the ADCC leaderboard target: if the canonical row lost its rank_elo
                # (the matches-row often won _score over the seeded row), re-sync it by name so
                # the fighter doesn't fall off the leaderboard / show "Unranked".
                from db.repository import rank_elo_for_athlete
                lb = rank_elo_for_athlete(canon.name)
                if lb is not None:
                    canon.rank_elo = lb
                elif canon.rank_elo is None:
                    seeded = [d.rank_elo for d in dups if d.rank_elo is not None]
                    if seeded:
                        canon.rank_elo = max(seeded)

        # CRITICAL: repoint actor_ids INSIDE each match's sequence JSONB too — the FK update
        # above doesn't touch them, and stale ids break _perspective_view (empty graph, floored
        # ELO). Resolve dup → canonical via id_map.
        seq_entries_fixed = 0
        if not dry_run and id_map:
            from sqlalchemy.orm.attributes import flag_modified
            session.flush()
            for m in list(session.execute(select(Match)).scalars()):
                seq = m.sequence or []
                changed = False
                for e in seq:
                    if isinstance(e, dict) and e.get("actor_id") in id_map:
                        e["actor_id"] = id_map[e["actor_id"]]
                        seq_entries_fixed += 1
                        changed = True
                if changed:
                    flag_modified(m, "sequence")
            session.flush()

        # Drop self-matches + duplicate pairings (frozenset(participants)+year) created by merge.
        self_deleted = 0
        dup_deleted = 0
        if not dry_run:
            session.flush()
            self_res = session.execute(
                delete(Match).where(Match.athlete_a_id == Match.athlete_b_id)
            )
            self_deleted = getattr(self_res, "rowcount", 0) or 0
            seen: set[tuple[frozenset[str], int | None]] = set()
            for m in list(session.execute(select(Match)).scalars()):
                sig = (frozenset((m.athlete_a_id, m.athlete_b_id)), m.year)
                if sig in seen:
                    session.delete(m)
                    dup_deleted += 1
                else:
                    seen.add(sig)
            session.flush()
            for aid in touched:
                ath = session.get(Athlete, aid)
                if ath is not None:
                    replay_and_persist_athlete(ath, session)

        logger.info("%s: %d clusters merged, %d dup rows, %d match refs repointed, "
                    "%d seq actor_ids re-tagged, %d self-matches + %d dup-pairings deleted, "
                    "%d athletes replayed",
                    "DRY-RUN" if dry_run else "DONE",
                    sum(1 for r in clusters.values() if len(r) > 1),
                    merged_rows, repoint, seq_entries_fixed, self_deleted, dup_deleted,
                    len(touched))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Merge duplicate athlete rows")
    ap.add_argument("--dry-run", action="store_true", help="report, no DB writes")
    return run(ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
