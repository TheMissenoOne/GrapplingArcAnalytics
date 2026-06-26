#!/usr/bin/env python
"""Seed the shared ``technique_nodes`` library from the curated technique export.

``technique_nodes`` is first populated by alembic 0004 from the distinct node_keys
already present in ``graph_nodes`` (``source='user'``). This script layers the
curated library (``data/processed/technique_library.json``, produced by
``export/tech_library.py``) on top: it upserts one row per technique keyed by the
normalized canonical (English) label, marking it ``source='library'`` and refreshing
``label``/``type`` — so a key first seen as a raw user node gets promoted to a curated
library entry.

node_key uses ``analysis.names._normalize_name`` — the SAME normalization the app's
``graphSync.normalizeLabel`` applies — so curated rows match what clients sync.

Alias note: a technique has several surface forms (en/pt/variations). This seeds the
canonical English key; cross-form alias resolution (so a pt label resolves to the same
row) is a follow-up — see plan "Open decision".

Usage:
    uv run python scripts/seed_technique_nodes.py [--file PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.names import _normalize_name  # noqa: E402
from pipelines.etl import PROCESSED_DIR  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_FILE = PROCESSED_DIR / "technique_library.json"

# Raw SQL kept as a string; sqlalchemy.text() is wrapped lazily in seed() so
# --dry-run works without the optional `postgres` extra installed.
_UPSERT_SQL = """
    insert into public.technique_nodes (node_key, label, type, node_type, source)
    values (:node_key, :label, :type, :node_type, 'library')
    on conflict (node_key) do update set
        label     = excluded.label,
        type      = excluded.type,
        node_type = excluded.node_type,
        source    = 'library',
        updated_at = now()
"""

# dataset technique type -> app node `type` bucket (mirrors export/tech_library.py)
_TYPE_TO_NODE_TYPE = {
    "submission": "submission",
    "sweep": "sweep",
    "takedown": "takedown",
    "guard": "guard",
    "control": "control",
    "escape": "escape",
    "transition": "transition",
    "pass": "pass",
    "concept": "concept",
}

def _canonical_label(item: dict[str, Any]) -> str:
    """Prefer the English translation as the canonical, locale-independent label."""
    tr = item.get("translations") or {}
    return str(tr.get("en") or item.get("name") or "").strip()


def seed(path: Path, dry_run: bool = False) -> int:
    items = json.loads(path.read_text(encoding="utf-8"))
    rows: dict[str, dict[str, Any]] = {}
    for item in items:
        label = _canonical_label(item)
        if not label:
            continue
        node_key = _normalize_name(label)
        if not node_key:
            continue
        dataset_type = str(item.get("type") or "").lower().strip()
        rows[node_key] = {
            "node_key": node_key,
            "label": label,
            "type": "technique",
            "node_type": _TYPE_TO_NODE_TYPE.get(dataset_type, dataset_type),
        }

    logger.info("Prepared %d distinct library node_keys from %s", len(rows), path.name)
    if dry_run:
        for r in list(rows.values())[:10]:
            logger.info("  %s -> %s (%s)", r["node_key"], r["label"], r["node_type"])
        return len(rows)

    from sqlalchemy import text  # lazy — only needed for actual DB writes

    from db.base import db_session

    upsert = text(_UPSERT_SQL)
    with db_session() as session:
        for r in rows.values():
            session.execute(upsert, r)
    logger.info("Upserted %d library technique_nodes", len(rows))
    return len(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Seed technique_nodes from the curated library")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE, help="technique_library.json path")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, no DB writes")
    args = ap.parse_args()

    if not args.file.exists():
        logger.error("Library file not found: %s (run export/tech_library.py first)", args.file)
        return 1
    seed(args.file, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
