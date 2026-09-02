"""Link private `graph_nodes.canonical_node_key` (NULL) to the curated technique library.

Linkage gap (measured in prod, 2026-09-02): a `graph_nodes` row's `node_key` is
`_normalize_name(label)` computed CLIENT-SIDE at write time (App `normalizeLabel`, char-for-char
port of `analysis.names._normalize_name`). A pt-BR label ("Guarda Fechada") normalizes to
`"guarda fechada"`, which never matches `technique_nodes.node_key` ("closed guard", derived from
the EN name) — so the FK-optional `canonical_node_key` stays NULL forever even though the
technique is curated and known.

This script re-resolves by the node's LABEL (not its already-diverged node_key), the same way
`export/tech_library.py._name_in_nodes` and the App's alias picker already do: normalize +
synonym-canonicalize (`analysis.names`), match against every `en`/`pt`/`variants` name in
`analysis/data/technique_library.json`, and propose that entry's canonical node_key.

EXACT match only (mirrors the App's `nodeIdentityResolution.ts` — never fuzzy: graph identity
does not tolerate "closest match wins").

Private data (`graph_nodes` may belong to `owner_kind='user'` graphs): --dry-run prints only
node_key -> canonical proposals and counts, NEVER `graph_id`/`owner_id`.

Usage:
    uv run python -m scripts.link_graph_node_canonicals --dry-run   # read-only (default)
    uv run python -m scripts.link_graph_node_canonicals --apply     # UPDATE — orchestrator only
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from analysis.names import _normalize_name, canonicalize
from db.base import get_session_factory

LIBRARY_PATH = Path(__file__).resolve().parent.parent / "analysis" / "data" / "technique_library.json"


def _key(name: str) -> str:
    """Normalize + synonym-canonicalize a raw name — the same funnel any other name in this
    module resolves through before being compared for identity."""
    return canonicalize(_normalize_name(name))


def load_label_index(library_path: Path = LIBRARY_PATH) -> dict[str, str]:
    """Every curated name variant (normalized) -> that entry's canonical node_key."""
    entries = json.loads(library_path.read_text(encoding="utf-8"))
    index: dict[str, str] = {}
    for entry in entries:
        en = entry.get("en", "")
        if not en:
            continue
        canonical_node_key = _key(en)
        for name in (en, entry.get("pt", ""), *entry.get("variants", [])):
            if not name:
                continue
            index.setdefault(_key(name), canonical_node_key)
    return index


def resolve_label(label: str, index: dict[str, str]) -> str | None:
    """The curated canonical node_key for a free-form label, or None when unknown."""
    if not label:
        return None
    return index.get(_key(label))


def find_matches(
    rows: list[tuple[str, str, str]], index: dict[str, str]
) -> tuple[list[tuple[str, str, str]], Counter[str]]:
    """`rows` = (graph_id, node_key, label). Returns (matched rows w/ canonical, unmatched
    node_key counts) — pure, so the dry-run report and --apply share one resolution pass."""
    matched: list[tuple[str, str, str]] = []
    unmatched: Counter[str] = Counter()
    for graph_id, node_key, label in rows:
        canonical = resolve_label(label, index)
        if canonical:
            matched.append((graph_id, node_key, canonical))
        else:
            unmatched[node_key] += 1
    return matched, unmatched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="UPDATE canonical_node_key (orchestrator only)")
    # Read-only is the default behavior; --dry-run is accepted (no-op) so callers can be explicit.
    parser.add_argument("--dry-run", action="store_true", help="Explicit no-op — read-only is already the default")
    args = parser.parse_args()

    index = load_label_index()
    session_factory = get_session_factory()
    with session_factory() as session:
        rows = session.execute(
            text("select graph_id, node_key, label from graph_nodes where canonical_node_key is null")
        ).all()
        typed_rows = [(str(r.graph_id), r.node_key, r.label) for r in rows]
        matched, unmatched = find_matches(typed_rows, index)

        print(f"graph_nodes with canonical_node_key IS NULL: {len(typed_rows)}")
        print(f"resolved via curated label match: {len(matched)}")
        print(f"left unresolved: {len(typed_rows) - len(matched)}")
        print()

        # node_key -> canonical only — no graph_id/owner_id, grouped + deduped.
        proposals = sorted({(node_key, canonical) for _, node_key, canonical in matched})
        print(f"sample proposals (up to 10 of {len(proposals)} distinct node_keys):")
        for node_key, canonical in proposals[:10]:
            print(f"  {node_key!r} -> {canonical!r}")
        print()

        print(f"unresolved node_keys, grouped (top 10 of {len(unmatched)} distinct):")
        for node_key, count in unmatched.most_common(10):
            print(f"  {node_key!r} x{count}")

        if args.apply:
            for graph_id, node_key, canonical in matched:
                session.execute(
                    text(
                        "update graph_nodes set canonical_node_key = :canonical "
                        "where graph_id = :graph_id and node_key = :node_key"
                    ),
                    {"canonical": canonical, "graph_id": graph_id, "node_key": node_key},
                )
            session.commit()
            print(f"\napplied: {len(matched)} rows updated")
        else:
            print("\n--dry-run: no writes")


if __name__ == "__main__":
    main()
