"""One-off + repeatable cleanup: canonicalise every stored match's technique labels
against the library (``analysis.technique_match``).

New matches are cleaned at import time (``db/scraped_import.py``); this back-fills the
matches already in the DB. Idempotent — re-running is a no-op once everything is canon.

    uv run python -m scripts.clean_match_techniques            # apply + commit
    uv run python -m scripts.clean_match_techniques --dry-run  # report only
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from analysis.technique_match import clean_label, clean_sequence
from db.base import db_session
from db.models import Match

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description="Canonicalise match technique labels")
    ap.add_argument("--dry-run", action="store_true", help="report changes, don't write")
    args = ap.parse_args()

    renames: Counter[str] = Counter()
    matches_changed = events_changed = 0
    with db_session() as session:
        for match in session.execute(select(Match)).scalars():
            new_seq, n = clean_sequence(match.sequence)
            if n:
                for old, new in zip(match.sequence or [], new_seq):
                    if isinstance(old, dict) and old.get("label") != new.get("label"):
                        renames[f"{old['label']} → {new['label']}"] += 1
                matches_changed += 1
                events_changed += n
                if not args.dry_run:
                    match.sequence = new_seq
                    flag_modified(match, "sequence")
        # Also canonicalise the stored submission name (used for win_type display).
        for match in session.execute(select(Match).where(Match.submission.isnot(None))).scalars():
            cleaned = clean_label(str(match.submission))
            if cleaned != match.submission and not args.dry_run:
                match.submission = cleaned
        if not args.dry_run:
            session.commit()

    logger.info("%s %d event label(s) across %d match(es)",
                "Would rename" if args.dry_run else "Renamed", events_changed, matches_changed)
    for change, count in renames.most_common(30):
        logger.info("  %4d  %s", count, change)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
