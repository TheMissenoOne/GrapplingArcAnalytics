"""Loader for the curated technique definitions — `analysis/data/technique_definitions.json`
(root CLAUDE.md `technique-definitions-file` backlog item). One `{en, pt, source, reviewed}`
entry per curated technique (`analysis/data/technique_library.json`), keyed by node_key
(`analysis.names._normalize_name(en)` — same key `scripts/sync_app_artifacts.py`'s
`merge_curated_identity` uses for curated identity). Synced verbatim (filtered to the App's
current node set) into `GrapplingArcApp/src/data/technique_definitions.json` by
`scripts/sync_app_artifacts.py`; see that module's docstring for the regen command.

`source` is `"draft"` (model-authored, awaiting review), `"external-review"` (2026-09-18,
GPT-reviewed against public sources — still `reviewed: false`, the owner confirms in the
admin page), or `"human"` (owner-edited via `admin/audit.py:save_definition`). Optional
`review_note` — a one-line flag for the owner (ambiguous term needing corpus context, or a
factual correction applied) — see
`docs/repairs/2026-09-18_technique_definitions_external_review.md`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import NotRequired, TypedDict


class TechniqueDefinition(TypedDict):
    en: str
    pt: str
    source: str
    reviewed: bool
    review_note: NotRequired[str]


DEFINITIONS_PATH = Path(__file__).resolve().parent / "data" / "technique_definitions.json"


@lru_cache(maxsize=1)
def load_definitions(path: Path = DEFINITIONS_PATH) -> dict[str, TechniqueDefinition]:
    """Load the curated definitions file, keyed by node_key. Cached — the file is static
    curated content, re-read only if a test passes a different `path`."""
    if not path.is_file():
        raise SystemExit(f"ABORT: technique definitions file missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data) if isinstance(data, dict) else {}


def definition_for(node_key: str) -> TechniqueDefinition | None:
    """The definition for one node_key, or None if this technique has no curated entry."""
    return load_definitions().get(node_key)


if __name__ == "__main__":
    defs = load_definitions()
    assert len(defs) > 0, "expected at least one definition"
    sample_key, sample = next(iter(defs.items()))
    assert definition_for(sample_key) == sample
    assert definition_for("__not_a_real_key__") is None
    for key, entry in defs.items():
        assert entry["en"].strip(), f"{key}: empty en"
        assert entry["pt"].strip(), f"{key}: empty pt"
    print(f"OK — {len(defs)} technique definitions loaded")
