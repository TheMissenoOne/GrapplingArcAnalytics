"""Backfill ``technique_nodes.taxonomy_id`` from the proposed mapping (card 017).

Writes ONLY the ``auto`` tier by default — proposals where the label resolved to a taxonomy
node exactly *and* that node sits inside the category the row's ``node_type`` already implies.
Two independent signals agreeing is what makes bulk application safe; the ``review`` and
``manual`` tiers are human decisions and are never written by this script.

Dry-run by default. ``--apply`` is required to write, matching the other mutation scripts.

    uv run python -m scripts.backfill_taxonomy_id            # show what would change
    uv run python -m scripts.backfill_taxonomy_id --apply    # write it
    uv run python -m scripts.backfill_taxonomy_id --tier auto --tier review --apply
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAP = Path(__file__).resolve().parents[1] / "data" / "taxonomy_map.json"


def load_mapping(path: Path, tiers: set[str]) -> dict[str, str]:
    """node_key -> taxonomy_id for the requested tiers, validated against the taxonomy.

    A proposal naming a node the taxonomy does not contain is a stale mapping file, not
    something to write into the DB — so it is dropped loudly rather than persisted.
    """
    from analysis.taxonomy import load_taxonomy

    tax = load_taxonomy()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("taxonomy_version") != tax.version:
        raise SystemExit(
            f"mapping was generated for taxonomy v{payload.get('taxonomy_version')}, "
            f"but docs/taxonomy.json is v{tax.version} — regenerate analysis.taxonomy_map"
        )

    out: dict[str, str] = {}
    unknown = 0
    for p in payload.get("proposals", []):
        if p.get("tier") not in tiers or not p.get("subcategory"):
            continue
        if tax.get(p["subcategory"]) is None:
            unknown += 1
            continue
        out[p["node_key"]] = p["subcategory"]
    if unknown:
        logger.warning("%d proposals name a taxonomy node that no longer exists — skipped", unknown)
    return out


def backfill(session: Any, mapping: dict[str, str], *, apply: bool) -> dict[str, int]:
    """Set taxonomy_id where it differs. Returns {set, unchanged, missing}."""
    from sqlalchemy import select

    from db.models import TechniqueNode as T

    rows = {
        r.node_key: r
        for r in session.execute(
            select(T).where(T.node_key.in_(list(mapping)))
        ).scalars()
    }
    stats = {"set": 0, "unchanged": 0, "missing": 0}
    for key, taxonomy_id in sorted(mapping.items()):
        row = rows.get(key)
        if row is None:
            stats["missing"] += 1
            continue
        if row.taxonomy_id == taxonomy_id:
            stats["unchanged"] += 1
            continue
        logger.info("  %-38s %s -> %s", key, row.taxonomy_id or "NULL", taxonomy_id)
        if apply:
            row.taxonomy_id = taxonomy_id
        stats["set"] += 1
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
    ap = argparse.ArgumentParser(description="Backfill technique_nodes.taxonomy_id")
    ap.add_argument("--map", type=Path, default=_MAP)
    ap.add_argument(
        "--tier", action="append", dest="tiers", choices=["auto", "review", "manual"],
        help="repeatable; defaults to auto only",
    )
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args()
    tiers = set(args.tiers or ["auto"])

    mapping = load_mapping(args.map, tiers)
    logger.info("tiers=%s -> %d mappings", sorted(tiers), len(mapping))
    if not mapping:
        logger.warning("nothing to backfill")
        return 0

    from db.base import db_session

    with db_session() as session:
        stats = backfill(session, mapping, apply=args.apply)
    logger.info(
        "%s: %d set, %d already correct, %d node_key not in DB",
        "APPLIED" if args.apply else "DRY RUN", stats["set"], stats["unchanged"],
        stats["missing"],
    )
    if not args.apply:
        logger.info("re-run with --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
