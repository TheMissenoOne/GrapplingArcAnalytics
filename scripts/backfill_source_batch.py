#!/usr/bin/env python
"""Backfill ``matches.source_batch`` (alembic 0063) for the 911 rows imported before the
column existed — closes the E16 within/cross-batch provenance gap (``docs/research/
e16_technique_jaccard_prereg.md`` §3a: ``created_by`` is NULL on every one of them, so today's
control falls back to a 30-minute ``created_at`` gap heuristic instead of the real ingestion run).

``matches`` carries no durable pointer back to the dump module that inserted a row. The only
handle is re-deriving it: every module in ``scripts.reprocess_all.DATASETS`` (plus the two
modules ``run_dump`` also covers but that registry omits — ``ufc_card_data``/``ufc_matches_data``,
see below) is re-parsed with ``scripts.dump_import.build_matches`` (pure, no DB write), each bout
resolved to an athlete-id pair, and keyed by ``(frozenset(athlete_a_id, athlete_b_id), year,
event)`` — the exact identity ``run_dump``'s own delete-then-insert uses, so a DB row and the
module that would produce it collide on the same key. A row's existing ``event``/``year`` and its
athletes' current names are read straight from the DB; no dump content is trusted over the row.

Coverage gaps, reported not guessed:
  - **ambiguous** — two different modules derive the same key (e.g. a pair that fought the same
    opponent twice in one year under the same event tag). Left NULL rather than picked at random.
  - **no candidate** — the row's (pair, year, event) matches no module in the registry at all.
    Two known live gaps: ``scripts.insert_ufc_matches.main`` writes via ``register_match``
    directly (never went through ``run_dump``/``label``, so those rows were never taggable this
    way), and any row hand-entered outside every dump path (admin paste).

    uv run python -m scripts.backfill_source_batch              # dry-run, report only (default)
    uv run python -m scripts.backfill_source_batch --dry-run    # same, explicit
    uv run python -m scripts.backfill_source_batch --write      # apply — ORCHESTRATOR ONLY
"""

from __future__ import annotations

import argparse
import importlib
import logging
from collections import Counter

logger = logging.getLogger(__name__)

# Two modules ``run_dump`` already tags on import (insert_ufc_card.py, event=None,
# label="UFC card") but that ``reprocess_all.DATASETS`` never registers — see that file's
# comment for why (one-off wrapper scripts, not part of the mass-reprocess registry).
_EXTRA_REGISTRY: list[tuple[str, str | None, str]] = [
    ("scripts.dumps.ufc_card_data", None, "UFC card"),
    ("scripts.dumps.ufc_matches_data", None, "UFC matches"),
]

Key = tuple[frozenset[str], int | None, str | None]


def _registry() -> list[tuple[str, str | None, str]]:
    from scripts.reprocess_all import DATASETS

    return [*DATASETS, *_EXTRA_REGISTRY]


def _derive_batches() -> tuple[dict[Key, str], Counter[Key]]:
    """key -> label for every unambiguous (module, event) bout; a counter of every key seen,
    so the caller can tell "ambiguous" (count > 1) from "no candidate" (absent)."""
    from sqlalchemy import select

    from analysis.names import athlete_key
    from db.base import db_session
    from db.models import Athlete
    from scripts.dump_import import build_matches

    with db_session() as session:
        by_norm = {
            athlete_key(a.name): a.id for a in session.execute(select(Athlete)).scalars()
        }

    derived: dict[Key, str] = {}
    seen: Counter[Key] = Counter()
    unresolved_names: set[str] = set()
    for module_path, event, label in _registry():
        try:
            raw = importlib.import_module(module_path).RAW
        except ModuleNotFoundError:
            logger.warning("Skipping %s: module not found", module_path)
            continue
        for cm in build_matches(raw, clean=False):
            a_id = by_norm.get(athlete_key(cm.a_name))
            b_id = by_norm.get(athlete_key(cm.b_name))
            if a_id is None or b_id is None:
                unresolved_names.add(cm.a_name if a_id is None else cm.b_name)
                continue
            key: Key = (frozenset((a_id, b_id)), cm.year, event)
            seen[key] += 1
            if seen[key] == 1:
                derived[key] = label
            elif key in derived:
                del derived[key]  # second sighting: now ambiguous, drop the guess

    if unresolved_names:
        logger.info(
            "%d dump-side name(s) did not resolve to an athlete row (skipped): %s",
            len(unresolved_names), ", ".join(sorted(unresolved_names)[:10]),
        )
    return derived, seen


def run(dry_run: bool) -> int:
    from sqlalchemy import select, update

    from db.base import db_session
    from db.models import Match

    derived, seen = _derive_batches()

    histogram: Counter[str] = Counter()
    ambiguous = 0
    no_candidate = 0
    proposed: list[tuple[str, str]] = []  # (match id, label)

    with db_session() as session:
        rows = list(
            session.execute(select(Match).where(Match.source_batch.is_(None))).scalars()
        )
        total = len(rows)
        for m in rows:
            key: Key = (frozenset((m.athlete_a_id, m.athlete_b_id)), m.year, m.event)
            if key not in seen:
                no_candidate += 1
                continue
            label = derived.get(key)
            if label is None:
                ambiguous += 1
                continue
            histogram[label] += 1
            proposed.append((m.id, label))

        logger.info(
            "%d row(s) with source_batch NULL: %d resolvable, %d ambiguous (two modules "
            "claim the same pair/year/event), %d have no candidate in the registry",
            total, len(proposed), ambiguous, no_candidate,
        )
        logger.info("Per-batch histogram (%d batches):", len(histogram))
        for label, count in histogram.most_common():
            logger.info("  %-30s %d", label, count)

        if not dry_run and proposed:
            for match_id, label in proposed:
                session.execute(
                    update(Match).where(Match.id == match_id).values(source_batch=label)
                )
            logger.info("DONE: %d row(s) updated", len(proposed))
        elif dry_run:
            logger.info("DRY-RUN: %d row(s) would be updated", len(proposed))

    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Backfill matches.source_batch from dump modules")
    ap.add_argument("--write", action="store_true", help="apply the updates (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes (default)")
    args = ap.parse_args()
    return run(dry_run=not args.write)


if __name__ == "__main__":
    raise SystemExit(main())
