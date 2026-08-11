"""Ship the technique taxonomy to the App as a bundled, version-gated seed.

``docs/taxonomy.json`` is curated reference data — 9 categories, 86 subcategories, 26 concepts
— and the App needs it to group techniques by family rather than by the flat ``node_type``
bucket it uses today.

Writes **directly into the App repo** (`GrapplingArcApp/src/data/taxonomy_seed.json`), the way
``export/grapplemap_icons_export.py`` does. ``export/ontology.py`` and ``export/tech_library.py``
stop at ``data/processed/`` and depend on somebody remembering to copy the file across; that
manual step is exactly how a seed goes stale, so this exporter does not repeat it.

**There is no migration on the App side and there should not be.** Taxonomy is reference data,
so the App re-seeds when a version string changes (the ``ontology_seed`` / ``nodes_library``
pattern). After regenerating, bump ``TAXONOMY_VERSION`` in
``GrapplingArcApp/src/utils/storage/taxonomyStorage.ts`` or the App will keep the old copy.

    uv run python -m export.taxonomy_seed
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from analysis.taxonomy import load_taxonomy

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_SEED = _REPO_ROOT / "GrapplingArcApp" / "src" / "data" / "taxonomy_seed.json"


def build_seed() -> dict[str, Any]:
    """Validated taxonomy -> the App's bundled shape.

    Emits `parents` as a list because a subcategory can belong to two families
    (``guard-recovery`` is both an Escape and a Transition); flattening it to one parent is
    what broke schema v1. `principle` rides along as a facet on concepts rather than a
    separate kind, so the App references ONE vocabulary.
    """
    tax = load_taxonomy()
    nodes = [
        {
            "id": n.id,
            "name": n.name,
            "kind": n.kind,
            "parents": list(n.parents),
            "aliases": list(n.aliases),
            **({"principle": True} if n.principle else {}),
        }
        for n in sorted(tax.nodes.values(), key=lambda x: (x.kind, x.id))
    ]
    return {"version": tax.version, "nodes": nodes}


def export_taxonomy_seed(out_path: Path | None = None) -> Path:
    path = out_path or _APP_SEED
    if not path.parent.exists():
        raise FileNotFoundError(f"App data directory not found: {path.parent}")
    seed = build_seed()
    # newline-terminated, stable key order, so a regenerated seed diffs cleanly
    path.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "taxonomy v%s -> %s (%d nodes)", seed["version"], path, len(seed["nodes"])
    )
    logger.warning(
        "remember to bump TAXONOMY_VERSION in "
        "GrapplingArcApp/src/utils/storage/taxonomyStorage.ts, or the App keeps the old seed"
    )
    return path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Export the taxonomy seed into the App repo")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    export_taxonomy_seed(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
