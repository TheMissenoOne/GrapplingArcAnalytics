"""Backfill ``athletes.gender = 'f'`` from named women's-event evidence — never masculine
by omission (alembic 0049; ``analysis/gendered_text.py`` is the prose side of this).

Evidence sources, all named events/rosters, no name-based guessing:

- ``data/scouting/adcc_2026_women.json`` — ADCC 2026 women's bracket roster
- ``data/scouting/adcc_women_65_extended.json`` — extended ±65 kg women's corpus roster
- ``scripts/dumps/adcc2022_women_data.py`` — every winner/opponent in this dump fought on
  the "ADCC 2022 - Women" card
- ``scripts/dumps/polaris36_women_data.py`` — "Polaris 36 (women's superfights)"

**Scope decision:** the ADCC Trials dumps (``scripts/dumps/adcc_trials*.py``) interleave men's
and women's divisions in one file with no structured per-match gender/division field —
resolving those would mean guessing from first names, which this script refuses to do. Not
covered here; the upgrade path is adding a division marker to those dumps (or a curated
per-event roster like the two above) if the Trials women need marking too.

Only ever writes ``'f'``. There is no equivalent men's-evidence source wired in — an athlete
already flagged ``'m'`` by some other process that also shows up in a women's-event roster is
a genuine conflict and gets flagged, not silently overwritten.

Dry-run by default, matching the other ``scripts/backfill_*.py`` scripts.

    uv run python -m scripts.backfill_athlete_gender            # report only
    uv run python -m scripts.backfill_athlete_gender --apply    # write it
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
from pathlib import Path
from typing import Any

from analysis.names import athlete_key

logger = logging.getLogger(__name__)

_SCOUTING = Path(__file__).resolve().parents[1] / "data" / "scouting"
_ROSTER_FILES = ("adcc_2026_women.json", "adcc_women_65_extended.json")
_DUMP_MODULES = (
    "scripts.dumps.adcc2022_women_data",
    "scripts.dumps.polaris36_women_data",
)


def _roster_names(path: Path) -> set[str]:
    """Every athlete name (+ alias) under a scouting manifest's ``divisions[].athletes[]``."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for div in payload.get("divisions", []):
        for ath in div.get("athletes", []):
            if ath.get("name"):
                names.add(ath["name"])
            names.update(ath.get("aliases", []))
    return names


def _dump_names(module: str) -> set[str]:
    """Every winner/opponent name in a bjj-match-analyzer-schema dump module."""
    raw = importlib.import_module(module).RAW
    names: set[str] = set()
    for block in raw:
        for match in block.values():
            for k in ("winner", "opponent"):
                if match.get(k):
                    names.add(match[k])
    return names


def women_evidence_names() -> set[str]:
    """Union of every named-women's-event source. Raw display names — caller resolves identity."""
    names: set[str] = set()
    for fname in _ROSTER_FILES:
        names |= _roster_names(_SCOUTING / fname)
    for module in _DUMP_MODULES:
        names |= _dump_names(module)
    return names


def backfill(session: Any, names: set[str], *, apply: bool) -> dict[str, int]:
    """Mark matching athletes 'f'. Returns {marked, already_f, conflict_m, not_found}."""
    from sqlalchemy import select

    from db.models import Athlete

    wanted_keys = {athlete_key(n) for n in names}
    athletes = list(session.execute(select(Athlete)).scalars())
    by_key: dict[str, Athlete] = {}
    for a in athletes:
        by_key.setdefault(athlete_key(a.name), a)

    stats = {"marked": 0, "already_f": 0, "conflict_m": 0, "not_found": 0}
    for key in sorted(wanted_keys):
        row = by_key.get(key)
        if row is None:
            stats["not_found"] += 1
            continue
        if row.gender == "f":
            stats["already_f"] += 1
            continue
        if row.gender == "m":
            stats["conflict_m"] += 1
            logger.warning(
                "  %-30s already 'm', but a women's-event source names them -- skipped", row.name)
            continue
        logger.info("  %-30s NULL -> f", row.name)
        if apply:
            row.gender = "f"
        stats["marked"] += 1
    if apply:
        session.commit()
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Backfill athletes.gender='f' from evidence")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()

    names = women_evidence_names()
    logger.info("%d distinct names across evidence sources", len(names))

    from db.base import db_session

    with db_session() as session:
        stats = backfill(session, names, apply=args.apply)
    logger.info(
        "%s: %d marked f, %d already f, %d conflict with existing 'm', %d not found in DB",
        "APPLIED" if args.apply else "DRY RUN", stats["marked"], stats["already_f"],
        stats["conflict_m"], stats["not_found"],
    )
    if not args.apply:
        logger.info("re-run with --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
