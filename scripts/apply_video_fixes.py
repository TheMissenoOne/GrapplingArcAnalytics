#!/usr/bin/env python
"""Apply 18 hand-verified ``Match.video_url`` corrections to the prod DB.

Ground truth: the user manually fixed these 18 matches in the WRONG place (the sibling
``GrapplingArc`` site repo's committed output ``assets/matches/<slug>.json``, commit
``c622592``) instead of the DB / ``url_mapping.json`` that actually feeds the export. This
script re-applies those 18 corrections to ``Match.video_url`` directly (the fixture files
are NOT read at runtime — the corrected values are baked into ``FIXES`` below so this script
has no dependency on the sibling repo's working tree).

Three categories, from the sibling commit's diff:
- Gaudio: video swapped (``xN0HUe8e2z0``, no offset) + every ``sequence[].ts`` shifted by
  -8085s (the old broken video's ``&t=8085s`` start offset — confirmed constant across all
  50 events, see ``run()``).
- 10 NCAA-2026 matches: swapped to the full finals replay (``jT5wAzLN014``, same ``&t=`` secs).
- 7 WNO-2025 matches: no public video exists — ``video_url`` stripped to NULL.

    uv run python -m scripts.apply_video_fixes --dry-run   # report only, no writes
    uv run python -m scripts.apply_video_fixes             # apply
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models import Match

logger = logging.getLogger(__name__)

# slug -> (athlete_a_name, athlete_b_name, year, new_video_url | None)
# Names/years are each match's ``meta.a.name`` / ``meta.b.name`` / ``meta.year`` from the
# corrected fixture (ground truth for identity, NOT re-read at runtime).
FIXES: dict[str, tuple[str, str, int, str | None]] = {
    "aden-valencia-vs-shayne-van-ness-2026": (
        "Aden Valencia", "Shayne Van Ness", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=1278s",
    ),
    "andrew-tackett-vs-p-barch-2025": ("Andrew Tackett", "P. Barch", 2025, None),
    "b-stemarie-vs-elisabeth-clay-2025": ("B. Ste-Marie", "Elisabeth Clay", 2025, None),
    "diogo-reis-vs-gabriel-sousa-2025": ("Diogo Reis", "Gabriel Sousa", 2025, None),
    "diogo-reis-vs-k-krikorian-2025": ("Diogo Reis", "K. Krikorian", 2025, None),
    "gabriel-sousa-vs-a-williams-2025": ("Gabriel Sousa", "A. Williams", 2025, None),
    "gordon-ryan-vs-p-gaudio-2025": (
        "Gordon Ryan", "P. Gaudio", 2025, "https://www.youtube.com/watch?v=xN0HUe8e2z0",
    ),
    "isaac-trumble-vs-yonger-bastida-2026": (
        "Isaac Trumble", "Yonger Bastida", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=6988s",
    ),
    "jax-forrest-vs-ben-davino-2026": (
        "Jax Forrest", "Ben Davino", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=8678s",
    ),
    "josh-barr-vs-cody-merrill-2026": (
        "Josh Barr", "Cody Merrill", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=6683s",
    ),
    "landon-robideau-vs-antrell-taylor-2026": (
        "Landon Robideau", "Antrell Taylor", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=2894s",
    ),
    "levi-haines-vs-christopher-minto-2026": (
        "Levi Haines", "Christopher Minto", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=4553s",
    ),
    "luke-lilledahl-vs-mark-anthony-mcgowan-2026": (
        "Luke Lilledahl", "Mark Anthony McGowan", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=7852s",
    ),
    "max-mcenelly-vs-rocco-welsh-2026": (
        "Max McEnelly", "Rocco Welsh", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=5419s",
    ),
    "mica-galvo-vs-j-rodriguez-2025": ("Mica Galvão", "J. Rodriguez", 2025, None),
    "mica-galvo-vs-p-barch-2025": ("Mica Galvão", "P. Barch", 2025, None),
    "mitchell-mesenbrink-vs-mikey-caliendo-2026": (
        "Mitchell Mesenbrink", "Mikey Caliendo", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=3773s",
    ),
    "sergio-vega-vs-jesse-mendez-2026": (
        "Sergio Vega", "Jesse Mendez", 2026,
        "https://www.youtube.com/watch?v=jT5wAzLN014&t=366s",
    ),
}

GAUDIO_SLUG = "gordon-ryan-vs-p-gaudio-2025"
# Old video's "&t=8085s" start offset; new video (xN0HUe8e2z0) starts at the match, so every
# absolute-seconds ts shifts by this much. Confirmed constant across all 50 sequence events
# in the sibling repo's before/after diff (commit c622592) — no per-event guessing needed.
GAUDIO_TS_OFFSET = 8085


def _apply_gaudio_ts(match: Match, dry_run: bool) -> bool:
    """Shift every ``ts`` by -GAUDIO_TS_OFFSET in BOTH ``sequence`` AND ``timeline`` — the
    breakdown graph reads ``sequence`` but the UI timeline strip (_ui_timeline) prefers
    ``timeline``, so both must move together or the timeline desyncs from the video. Returns
    True if applied/applicable, False if it bailed (any ts would go negative → offset wrong)."""
    fields = {f: (getattr(match, f) or []) for f in ("sequence", "timeline")}
    all_ts = [e["ts"] for evs in fields.values() for e in evs
              if isinstance(e, dict) and isinstance(e.get("ts"), int)]
    if all_ts and min(all_ts) - GAUDIO_TS_OFFSET < 0:
        logger.warning(
            "Gaudio ts offset -%ds would go negative (min ts %d) — NOT applying.",
            GAUDIO_TS_OFFSET, min(all_ts),
        )
        return False
    logger.info("Gaudio ts shift -%d across sequence(%d)+timeline(%d)", GAUDIO_TS_OFFSET,
                len(fields["sequence"]), len(fields["timeline"]))
    if not dry_run:
        from sqlalchemy.orm.attributes import flag_modified

        for field, evs in fields.items():
            touched = False
            for e in evs:
                if isinstance(e, dict) and isinstance(e.get("ts"), int):
                    e["ts"] -= GAUDIO_TS_OFFSET
                    touched = True
            if touched:
                flag_modified(match, field)
    return True


def run(dry_run: bool) -> int:
    from sqlalchemy import select

    from analysis.names import athlete_key
    from db.base import db_session

    with db_session() as session:
        from db.models import Athlete, Match

        by_norm = {athlete_key(a.name): a for a in session.execute(select(Athlete)).scalars()}

        applied = 0
        for slug, (a_name, b_name, year, new_url) in FIXES.items():
            a = by_norm.get(athlete_key(a_name))
            b = by_norm.get(athlete_key(b_name))
            if a is None or b is None:
                logger.warning("%s: athlete not found (a=%s b=%s) — skipped", slug, a_name, b_name)
                continue

            matches = list(
                session.execute(
                    select(Match).where(
                        Match.year == year,
                        (
                            (Match.athlete_a_id == a.id) & (Match.athlete_b_id == b.id)
                            | (Match.athlete_a_id == b.id) & (Match.athlete_b_id == a.id)
                        ),
                    )
                ).scalars()
            )
            if len(matches) != 1:
                logger.warning(
                    "%s: expected 1 match for %s vs %s (%s), found %d — skipped",
                    slug, a_name, b_name, year, len(matches),
                )
                continue
            match = matches[0]

            logger.info("%s: video_url %r -> %r", slug, match.video_url, new_url)
            if not dry_run:
                match.video_url = new_url
            applied += 1

            if slug == GAUDIO_SLUG:
                _apply_gaudio_ts(match, dry_run)

        logger.info(
            "%s: %d/%d matches %s", "DRY-RUN" if dry_run else "DONE",
            applied, len(FIXES), "would be updated" if dry_run else "updated",
        )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Apply the 18 hand-verified video_url fixes")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    args = ap.parse_args()
    return run(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
