#!/usr/bin/env python
"""Backfill ``matches.video_url`` / ``video_start_seconds`` / ``ts_origin`` from dump-declared
timing, without a full ``reprocess_all`` re-run.

Q5 (video plumbing). As of this script, ``scripts.dump_import.run_dump`` resolves the video
columns at import time via ``_resolve_video`` — see that function's docstring for the full
precedence rule. This script applies the SAME function to every match already in prod against
the CURRENT dump corpus, so a bout imported before its dump carried a ``start``/``bout_start_s``
field (or before alembic 0047 added the two numeric columns) gets caught up without touching
``sequence``/ELO/graph state a full reimport would also replay.

Precedence (identical to ``_resolve_video``, restated): an existing non-null DB value is NEVER
changed unless a dump's frame-pdf field EXPLICITLY declares that column (``video_start_seconds``/
``ts_origin``, alembic 0047) — that channel always wins, matching import-time behavior. A NULL
column is filled from the transcript pipeline's own offset (spliced ``bout_start_s`` or the raw
ref-block ``start`` string) in preference to ``url_mapping.json``'s ``&t=``, and a hand fix
applied straight to the DB (``scripts/apply_video_fixes.py``) is never touched because it left a
non-null value behind.

    uv run python -m scripts.backfill_video_offsets              # dry-run, report only (default)
    uv run python -m scripts.backfill_video_offsets --dry-run    # same, explicit
    uv run python -m scripts.backfill_video_offsets --write      # apply — ORCHESTRATOR ONLY

## Retiring url_mapping.json

Not safe to delete yet — the file is still the ONLY source for some bouts. Safe once, for every
entry under every event key's ``matches[]``, one of:

  - a dump in ``scripts/dumps/`` declares an offset for that same (pair, year) — the mapping
    entry is then REDUNDANT (this script's report counts these as "covered"), or
  - the corresponding DB row already carries a resolved ``video_url``/``video_start_seconds``
    that no longer depends on this script re-reading the file (e.g. a hand fix).

This script's report ends with a retirement count: entries with NO dump-side counterpart today.
Non-zero means deleting the file now would silently lose those bouts' only known offset/URL —
migrate them into a dump's ``start`` field (or a frame-pdf ``video_start_seconds``) first.
"""

from __future__ import annotations

import argparse
import glob
import importlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DUMPS_GLOB = str(Path(__file__).resolve().parent / "dumps" / "*_data.py")

PairKey = tuple[frozenset[str], int | None]
# (seconds, ts_origin, is_explicit [frame-pdf field vs transcript-pipeline offset], source module)
Declared = tuple[int | None, str | None, bool, str]


def _iter_dump_modules() -> list[tuple[str, Any]]:
    out = []
    for path in sorted(glob.glob(DUMPS_GLOB)):
        name = f"scripts.dumps.{Path(path).stem}"
        out.append((name, importlib.import_module(name)))
    return out


def _dump_declared() -> dict[PairKey, Declared]:
    """(pair, year) -> the first dump (sorted filename order) that declares a start offset for
    it, distinguishing frame-pdf's EXPLICIT ``video_start_seconds`` from the transcript
    pipeline's fill-a-gap-only ``bout_start_s``/``start`` (see ``CanonicalMatch``)."""
    from analysis.names import athlete_key
    from scripts.dump_import import build_matches

    out: dict[PairKey, Declared] = {}
    for mod_name, mod in _iter_dump_modules():
        for cm in build_matches(mod.RAW, clean=False):
            if cm.video_start_seconds is not None:
                seconds, origin, explicit = cm.video_start_seconds, cm.ts_origin, True
            elif cm.dump_bout_start_s is not None:
                seconds, origin, explicit = cm.dump_bout_start_s, "video_absolute", False
            else:
                continue
            key = (frozenset((athlete_key(cm.a_name), athlete_key(cm.b_name))), cm.year)
            out.setdefault(key, (seconds, origin, explicit, mod_name))
    return out


def _retirement_report(declared: dict[PairKey, Declared]) -> None:
    from analysis.names import athlete_key
    from scripts.dump_import import _STAGE_RE, _load_url_mapping

    total = 0
    covered = 0
    still_needed: list[str] = []
    for event_key, mapping in _load_url_mapping().items():
        for m in mapping.get("matches", []):
            a = _STAGE_RE.sub("", str(m.get("athlete") or "").strip())
            b = str(m.get("opponent") or "").strip()
            if " vs " in a:
                a, b = (s.strip() for s in a.split(" vs ", 1))
            if b and athlete_key(a) == athlete_key(b):
                b = str(m.get("winner") or "").strip()
            if not a or not b or athlete_key(a) == athlete_key(b):
                continue
            total += 1
            key = (frozenset((athlete_key(a), athlete_key(b))), m.get("year"))
            if key in declared:
                covered += 1
            else:
                still_needed.append(f"{event_key}: {a} vs {b} ({m.get('year')})")

    logger.info(
        "url_mapping.json retirement check: %d/%d bout entries covered by a dump-side "
        "counterpart (redundant); %d still rely on url_mapping.json alone",
        covered, total, len(still_needed),
    )
    if still_needed:
        logger.info("  NOT safe to delete url_mapping.json — still needed for:")
        for line in still_needed[:20]:
            logger.info("    - %s", line)
        if len(still_needed) > 20:
            logger.info("    ... and %d more", len(still_needed) - 20)
    else:
        logger.info("  every url_mapping.json entry is now redundant — safe to retire")


def run(dry_run: bool) -> int:
    from sqlalchemy import select

    from analysis.names import athlete_key
    from db.base import db_session
    from db.models import Athlete, Match
    from scripts.dump_import import _resolve_video, video_index

    declared = _dump_declared()
    mapped = video_index()

    proposed = 0
    applied = 0
    with db_session() as session:
        athletes = {a.id: a for a in session.execute(select(Athlete)).scalars()}
        for m in session.execute(select(Match)).scalars():
            a = athletes.get(m.athlete_a_id)
            b = athletes.get(m.athlete_b_id)
            if a is None or b is None:
                continue
            key = (frozenset((athlete_key(a.name), athlete_key(b.name))), m.year)
            dsecs, dorigin, dexplicit, dsource = declared.get(key, (None, None, False, None))
            murl = mapped.get(key)

            new_url, new_secs, new_origin = _resolve_video(
                old_url=m.video_url, old_seconds=m.video_start_seconds, old_ts_origin=m.ts_origin,
                mapped_url=murl,
                explicit_seconds=dsecs if dexplicit else None,
                explicit_ts_origin=dorigin if dexplicit else None,
                dump_bout_start_s=dsecs if not dexplicit else None,
            )
            if (new_url, new_secs, new_origin) == (m.video_url, m.video_start_seconds, m.ts_origin):
                continue

            proposed += 1
            source = dsource or ("url_mapping.json" if murl else "-")
            logger.info(
                "%s vs %s (%s) [%s]: video_url %r -> %r, video_start_seconds %r -> %r, "
                "ts_origin %r -> %r",
                a.name, b.name, m.year, source,
                m.video_url, new_url, m.video_start_seconds, new_secs, m.ts_origin, new_origin,
            )
            if not dry_run:
                m.video_url, m.video_start_seconds, m.ts_origin = new_url, new_secs, new_origin
                applied += 1

        logger.info(
            "%s: %d bout(s) %s", "DRY-RUN" if dry_run else "DONE",
            proposed, "would be updated" if dry_run else f"updated ({applied} applied)",
        )

    _retirement_report(declared)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(
        description="Backfill matches.video_url/video_start_seconds/ts_origin from dumps"
    )
    ap.add_argument("--write", action="store_true", help="apply the updates (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes (default)")
    args = ap.parse_args()
    return run(dry_run=not args.write)


if __name__ == "__main__":
    raise SystemExit(main())
