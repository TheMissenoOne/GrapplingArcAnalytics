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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from analysis.names import athlete_key, clean_athlete_name, raw_athlete_key

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from db.models import Athlete

logger = logging.getLogger(__name__)


@dataclass
class MergeStats:
    """What ``merge_into`` did (or, in dry-run, would do) to fold one athlete into another."""

    matches_repointed: int
    seq_entries_fixed: int
    self_matches_deleted: int


def merge_into(session: Session, src: Athlete, dst: Athlete, *, dry_run: bool = False) -> MergeStats:
    """Fold ``src`` into ``dst``: repoint every ``matches`` FK, rewrite the stale
    ``actor_id`` inside each affected match's ``sequence`` JSONB (same transaction — see
    ``repair_actor_ids.py``'s docstring for why that split bit production once), drop any
    self-match the repoint creates, then remove ``src`` via ``remove_athlete(...,
    INVALID_DATA)`` — a merged row was never a separate person.

    Does NOT replay ``dst`` — batch callers (this module's ``run``) replay once per canonical
    row after all its dups are folded in; a single-pair caller (``scripts/merge_athlete.py``)
    replays right after calling this.
    """
    from sqlalchemy import delete, select, update
    from sqlalchemy.orm.attributes import flag_modified

    from db.models import Match
    from db.repository import AthleteRemovalReason, remove_athlete

    matches_touching = list(session.execute(
        select(Match).where(
            (Match.athlete_a_id == src.id)
            | (Match.athlete_b_id == src.id)
            | (Match.winner_id == src.id)
        )
    ).scalars())
    matches_repointed = len(matches_touching)

    if dry_run:
        seq_entries_fixed = sum(
            1 for m in matches_touching for e in (m.sequence or [])
            if isinstance(e, dict) and e.get("actor_id") == src.id
        )
        self_matches_deleted = sum(
            1 for m in matches_touching
            if {m.athlete_a_id, m.athlete_b_id} == {src.id, dst.id}
        )
        return MergeStats(matches_repointed, seq_entries_fixed, self_matches_deleted)

    for col in (Match.athlete_a_id, Match.athlete_b_id, Match.winner_id):
        session.execute(update(Match).where(col == src.id).values({col.key: dst.id}))
    session.flush()

    seq_entries_fixed = 0
    for m in matches_touching:
        seq = m.sequence or []
        changed = False
        for e in seq:
            if isinstance(e, dict) and e.get("actor_id") == src.id:
                e["actor_id"] = dst.id
                seq_entries_fixed += 1
                changed = True
        if changed:
            flag_modified(m, "sequence")
    session.flush()

    self_res = session.execute(delete(Match).where(Match.athlete_a_id == Match.athlete_b_id))
    self_matches_deleted = getattr(self_res, "rowcount", 0) or 0
    session.flush()

    remove_athlete(src, session, reason=AthleteRemovalReason.INVALID_DATA)

    return MergeStats(matches_repointed, seq_entries_fixed, self_matches_deleted)


def _score(name: str, n_matches: int) -> tuple[int, int, int, int]:
    """Higher = better canonical. Prefer rows with matches, full (non-initial) accented names."""
    clean = clean_athlete_name(name)
    not_initial = 0 if (len(clean) >= 2 and clean[1] == ".") else 1
    has_accent = 1 if any(ord(c) > 127 for c in clean) else 0
    return (1 if n_matches else 0, not_initial, has_accent, len(clean))


def run(dry_run: bool) -> int:
    from sqlalchemy import func, select

    from db.base import db_session
    from db.models import Athlete, Match
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
        seq_entries_fixed = 0
        self_deleted = 0
        touched: set[str] = set()
        for key, rows in clusters.items():
            if len(rows) < 2:
                continue
            canon = max(rows, key=lambda a: _score(a.name, n_matches(a.id)))
            # Keep the top-scored row (most bouts) as the FK target, but DISPLAY the spelling
            # whose own key IS the cluster key — i.e. the alias target / canonical spelling —
            # so a typo'd row with more bouts (e.g. "Felipe Pena SF", "Nicky Rodriguez") doesn't
            # name the merged athlete. Falls back to the top row's name when no row matches.
            preferred = next((r.name for r in rows if raw_athlete_key(r.name) == key), canon.name)
            canon_clean = clean_athlete_name(preferred)
            dups = [a for a in rows if a.id != canon.id]
            logger.info("MERGE %-28s canonical=%r  <- %s", key, canon_clean,
                        [clean_athlete_name(d.name) for d in dups])
            touched.add(canon.id)
            for d in dups:
                merged_rows += 1
                # A duplicate is the textbook invalid-data case: this row was never a separate
                # person, so the graph derived from "their" bouts is not a separate game either.
                # Routed through `merge_into` (FK repoint + sequence actor_id rewrite + removal
                # via `remove_athlete`) so there is ONE place that decides what a merge does to
                # a graph — the hand-written version of this is what left seven orphans in prod.
                stats = merge_into(session, d, canon, dry_run=dry_run)
                repoint += stats.matches_repointed
                seq_entries_fixed += stats.seq_entries_fixed
                self_deleted += stats.self_matches_deleted
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

        # Drop duplicate pairings (frozenset(participants)+year) created by merge — self-matches
        # are already gone, `merge_into` drops those as part of each pair's own merge.
        dup_deleted = 0
        if not dry_run:
            session.flush()
            # Which duplicate SURVIVES is a data decision, not an iteration accident. This used
            # to keep whatever an unordered `select(Match)` returned first and delete the rest —
            # the same first-writer-wins defect class as the PtV node type — and it cost the
            # 30+-event reading of the ADCC 2022 women's final, leaving the 5-event one. Keep the
            # RICHEST sequence (then the one with a winner, then the oldest id): a duplicate
            # pairing is the same bout read twice, and the fuller read is the one worth keeping.
            def _keep_rank(m: Match) -> tuple[int, int, str]:
                return (-len(m.sequence or []), 0 if m.winner_id else 1, str(m.id))

            by_sig: dict[tuple[frozenset[str], int | None], list[Match]] = {}
            for m in session.execute(select(Match).order_by(Match.id)).scalars():
                by_sig.setdefault((frozenset((m.athlete_a_id, m.athlete_b_id)), m.year), []).append(m)
            for dupes in by_sig.values():
                if len(dupes) < 2:
                    continue
                for loser in sorted(dupes, key=_keep_rank)[1:]:
                    session.delete(loser)
                    dup_deleted += 1
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
