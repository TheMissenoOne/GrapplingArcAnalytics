#!/usr/bin/env python
"""Sync Analytics-generated artifacts into the App's bundled data files — the ONE
command that makes the App's `src/data/*.json` contract files match Analytics'
current outputs, so nobody hand-copies them out of sync again.

    uv run python -m scripts.sync_app_artifacts            # sync, write files
    uv run python -m scripts.sync_app_artifacts --check     # regenerate + diff, write nothing, exit nonzero on drift

Four artifacts (root CLAUDE.md cross-module contracts):
  a. `data/rating/markov_action_weights.json` -> App `src/data/markov_action_weights.json`
     (byte copy — generator: `scripts/build_markov_action_weights.py --check`).
  b. `data/processed/ontology_seed.json` -> App `src/data/ontology_seed.json` (byte copy,
     sanity-gated first — generator: `export/ontology.py`).
  c. `analysis/data/technique_library.json` (curated, hand-maintained) is node IDENTITY for
     the App's bundled technique library (`src/data/grappling-arch.nodes.json`):
     `merge_curated_identity` re-derives `type`/`translations`/`variations` (+ `name` for a
     brand-new entry only) from it, in
     CURATED ORDER (so a label collision resolves the same way on both sides'
     first-writer-wins lookups — App `techniqueCategory.buildCategoryResolver`, Analytics
     `technique_match._index`), preserving `_id`/`createdAt` for a curated entry that already
     has an App row. An App-only entry (never in curated) is kept, appended after the
     curated block — never silently deleted, only reported.
  d. `data/processed/app_node_scores.json`'s `rrb`/`eloPercentile` INJECTED into the (now
     identity-synced) library, keyed by normalized name (`analysis.names._normalize_name`,
     the App's `normalizeLabel` port) — App's `nodeCorpusScores.ts` reads these two fields
     straight off each `NodeLibraryItem` (`src/utils/storage/libraryStorage.ts`). Fetched
     fresh every run (see `load_fresh_scores`).

The App file is GENERATED output of this script — never hand-edited; `--check` (this file)
is asserted in Analytics CI by `tests/test_sync_app_artifacts.py`.

Run after any change to the markov weights artifact, the ontology exporter, or the curated
technique library — and after a sync, bump `NODE_LIBRARY_VERSION`
(App `src/utils/defaultDataLoader.ts`) and/or `ONTOLOGY_VERSION`
(App `src/utils/storage/ontologyStorage.ts`) so cold start re-seeds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name

REPO = Path(__file__).resolve().parent.parent
APP = REPO.parent / "GrapplingArcApp"

MARKOV_SRC = REPO / "data" / "rating" / "markov_action_weights.json"
MARKOV_DST = APP / "src" / "data" / "markov_action_weights.json"
ONTOLOGY_SRC = REPO / "data" / "processed" / "ontology_seed.json"
ONTOLOGY_DST = APP / "src" / "data" / "ontology_seed.json"
CURATED_LIB = REPO / "analysis" / "data" / "technique_library.json"
NODES_LIB = APP / "src" / "data" / "grappling-arch.nodes.json"

SCORE_FIELDS = ("rrb", "eloPercentile")

# A new curated entry's createdAt/updatedAt is a FIXED constant, never
# datetime.now() — `--check` regenerates in memory on every call, and a
# wall-clock stamp would make a rerun look drifted from the very file it just
# wrote. Bump only when hand-running a real sync that adds curated entries.
NEW_ENTRY_DATE = "2026-09-13T00:00:00.000Z"


def verify_ontology_seed(doc: dict[str, Any]) -> tuple[int, int]:
    """Sanity gate before shipping the seed to the App: an empty/broken exporter run
    must never silently overwrite the App's bundled ontology (a defect, not "nothing
    changed" — root CLAUDE.md). Returns (position_decision_space keys, athlete_profiles
    count); raises SystemExit if either is empty."""
    pds = doc.get("position_decision_space")
    profiles = doc.get("athlete_profiles")
    n_pds = len(pds) if isinstance(pds, dict) else 0
    n_profiles = len(profiles) if isinstance(profiles, list) else 0
    if n_pds == 0 or n_profiles == 0:
        raise SystemExit(
            f"ABORT: ontology_seed.json looks degenerate (position_decision_space="
            f"{n_pds} keys, athlete_profiles={n_profiles}) — refusing to copy a broken "
            f"export over the App's bundled seed. Regenerate via `export/ontology.py` first."
        )
    return n_pds, n_profiles


def sync_text_file(src: Path, dst: Path, *, check: bool, label: str) -> tuple[bool, str]:
    """Byte-copy `src` -> `dst` when they differ. Returns (changed, message).
    `check=True` never writes; a diff is reported as `changed=True` for the caller
    to turn into a nonzero exit."""
    if not src.is_file():
        raise SystemExit(f"ABORT: {label} source missing: {src}")
    text = src.read_text(encoding="utf-8")
    if dst.is_file() and dst.read_text(encoding="utf-8") == text:
        return False, f"{label}: unchanged"
    if check:
        return True, f"{label}: DRIFT ({dst})"
    dst.write_text(text, encoding="utf-8")
    return True, f"{label}: updated ({dst})"


def load_curated_nodes(path: Path = CURATED_LIB) -> list[dict[str, Any]]:
    """The curated technique library (`{en, pt, type, variants}` per entry) — node
    IDENTITY source of truth (root CLAUDE.md tech-library contract), read fresh
    every run like the scores. Mirrors `export.tech_library.load_curated_library`'s
    contract but kept local: importing that module drags in the whole Kaggle/ADCC
    pipeline (pandas, kagglehub) for what is otherwise a plain JSON read."""
    if not path.is_file():
        raise SystemExit(f"ABORT: curated technique library missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data) if isinstance(data, list) else []


def _deterministic_oid(en: str) -> dict[str, str]:
    """Stable `_id.$oid` for a curated entry with no existing App row: first 24 hex
    chars of sha1(normalized en) — no counter/state to keep in sync, so two syncs
    (and a `--check` right after a real run) produce the identical id."""
    digest = hashlib.sha1(_normalize_name(en).encode("utf-8")).hexdigest()
    return {"$oid": digest[:24]}


def _entry_key(node: dict[str, Any]) -> str:
    en = (node.get("translations") or {}).get("en") or node.get("name") or ""
    return _normalize_name(str(en))


def merge_curated_identity(
    curated: list[dict[str, Any]], existing_nodes: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Re-derive the App's node list from the curated library — the curated file is
    node IDENTITY (root CLAUDE.md tech-library contract), emitted in CURATED ORDER
    so a label collision resolves to the same entry on both sides' first-writer-wins
    lookups (App `techniqueCategory.buildCategoryResolver`, Analytics
    `technique_match._index`).

    Matched by normalized `en` against an existing App row: keeps that row's `_id`/
    `createdAt`/`name`/score fields/anything else it carries — `name` is NOT curated's
    to overwrite, a real reader keys a hardcoded list against it verbatim
    (`SessionStartSheet.curatedFallbackTopics`) — only `type`/`translations`/
    `variations` are replaced with curated's. No match: a fresh node, `name` = curated
    `pt` (the file's own dominant convention), deterministic `_id`
    (`_deterministic_oid`), `createdAt`/`updatedAt` pinned to `NEW_ENTRY_DATE`.

    An App row with no curated match (App-only) is APPENDED after the curated
    block, untouched — never dropped; the owner decides whether it enters the
    curated file (see `report["app_only_names"]`).
    """
    existing_by_key: dict[str, dict[str, Any]] = {}
    for node in existing_nodes:
        key = _entry_key(node)
        if key and key not in existing_by_key:
            existing_by_key[key] = node

    curated_keys: set[str] = set()
    merged: list[dict[str, Any]] = []
    new_count = matched = 0

    for item in curated:
        en = str(item.get("en", "")).strip()
        if not en:
            continue
        key = _normalize_name(en)
        if key in curated_keys:
            continue  # curated file is expected pre-de-duped; defensive only
        curated_keys.add(key)

        pt = str(item.get("pt") or en).strip()
        node_type = item.get("type")
        variations = list(item.get("variants") or [])
        translations = {"pt": pt, "en": en}

        existing = existing_by_key.get(key)
        if existing is not None:
            matched += 1
            # `name` is untouched — a real production reader (`SessionStartSheet.
            # curatedFallbackTopics`) keys a hardcoded list against it verbatim
            # (measured: `getDefaultNodes().map(n => [n.name, n])`), so it is NOT
            # free-standing identity curated owns; only translations/type/variations are.
            node = dict(existing)
            node["type"] = node_type
            node["translations"] = translations
            node["variations"] = variations
        else:
            new_count += 1
            node = {
                "_id": _deterministic_oid(en),
                # New entry, no existing `name` to preserve: `pt` matches the file's own
                # dominant convention (127/142 pre-existing entries have name==pt) and
                # the App's Portuguese-first `getDisplayName` default.
                "name": pt,
                "createdAt": {"$date": NEW_ENTRY_DATE},
                "type": node_type,
                "translations": translations,
                "updatedAt": {"$date": NEW_ENTRY_DATE},
                "variations": variations,
            }
        merged.append(node)

    app_only = [n for n in existing_nodes if _entry_key(n) not in curated_keys]
    merged.extend(app_only)

    report = {
        "curated_count": len(curated_keys),
        "matched": matched,
        "new_count": new_count,
        "app_only_count": len(app_only),
        "app_only_names": sorted(
            str((n.get("translations") or {}).get("en") or n.get("name") or "")
            for n in app_only
        ),
    }
    return merged, report


def load_fresh_scores() -> dict[str, dict[str, float]]:
    """Always fresh: shells out to `export.app_node_scores --stdout`, which rebuilds the
    whole artifact from the corpus + the App's current library with no disk write.
    ponytail: skips a separate staleness-check dance — a fresh run each call is already
    correct, same DB cost, less code. Needs a DB session (read-only prod)."""
    proc = subprocess.run(
        [sys.executable, "-m", "export.app_node_scores", "--stdout"],
        cwd=REPO, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"ABORT: export.app_node_scores failed:\n{proc.stderr}")
    return dict(json.loads(proc.stdout)["scores"])


def inject_scores(
    nodes: list[dict[str, Any]], scores: dict[str, dict[str, float]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Set/update `rrb`/`eloPercentile` on each node whose normalized name variant
    matches a scores key (first match wins, same variant order `_name_variants`
    returns — mirrors `export.app_node_scores.build_scores`'s own matching). A node
    with no match, or whose match lacks a field, ends up with that field ABSENT —
    never a fabricated/null value. Pure function — no I/O. Returns (new_nodes, counts)."""
    from analysis.names import _normalize_name
    from export.app_node_scores import _name_variants

    out: list[dict[str, Any]] = []
    changed = with_rrb = with_elo = 0
    for node in nodes:
        keys = [_normalize_name(v) for v in _name_variants(node)]
        entry = next((scores[k] for k in keys if k in scores), None)

        new_node = dict(node)
        for field in SCORE_FIELDS:
            if entry is not None and field in entry:
                new_node[field] = entry[field]
            else:
                new_node.pop(field, None)

        if "rrb" in new_node:
            with_rrb += 1
        if "eloPercentile" in new_node:
            with_elo += 1
        if new_node != node:
            changed += 1
        out.append(new_node)
    return out, {"changed": changed, "with_rrb": with_rrb, "with_elo_percentile": with_elo}


def sync_nodes_library(
    path: Path,
    scores: dict[str, dict[str, float]],
    curated: list[dict[str, Any]],
    *,
    check: bool,
) -> tuple[bool, str]:
    old_text = path.read_text(encoding="utf-8")
    nodes = json.loads(old_text)
    identity_nodes, identity_report = merge_curated_identity(curated, nodes)
    new_nodes, counts = inject_scores(identity_nodes, scores)
    text = json.dumps(new_nodes, indent=2, ensure_ascii=False) + "\n"

    summary = (
        f"grappling-arch.nodes.json: {identity_report['curated_count']} curated "
        f"({identity_report['new_count']} new, {identity_report['matched']} matched), "
        f"{identity_report['app_only_count']} app-only kept "
        f"({', '.join(identity_report['app_only_names']) or 'none'}); "
        f"{counts['with_rrb']} with rrb, {counts['with_elo_percentile']} with eloPercentile"
    )
    if text == old_text:
        return False, f"{summary} (unchanged)"
    if check:
        return True, f"{summary} — DRIFT ({counts['changed']} node(s))"
    path.write_text(text, encoding="utf-8")
    return True, f"{summary} — updated ({counts['changed']} node(s))"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--check", action="store_true",
        help="regenerate + diff, write nothing, exit nonzero on any drift",
    )
    args = ap.parse_args()

    lines: list[str] = []
    drift = False

    changed, msg = sync_text_file(
        MARKOV_SRC, MARKOV_DST, check=args.check, label="markov_action_weights.json"
    )
    lines.append(msg)
    drift = drift or changed

    ontology_doc = json.loads(ONTOLOGY_SRC.read_text(encoding="utf-8"))
    n_pds, n_profiles = verify_ontology_seed(ontology_doc)
    lines.append(
        f"ontology_seed.json source: {n_pds} position_decision_space keys, "
        f"{n_profiles} athlete_profiles"
    )
    changed, msg = sync_text_file(
        ONTOLOGY_SRC, ONTOLOGY_DST, check=args.check, label="ontology_seed.json"
    )
    lines.append(msg)
    drift = drift or changed

    scores = load_fresh_scores()
    curated = load_curated_nodes()
    changed, msg = sync_nodes_library(NODES_LIB, scores, curated, check=args.check)
    lines.append(msg)
    drift = drift or changed

    print("\n".join(lines))
    if not args.check:
        print(
            "\nIf anything above changed: bump NODE_LIBRARY_VERSION "
            "(GrapplingArcApp/src/utils/defaultDataLoader.ts) and/or ONTOLOGY_VERSION "
            "(GrapplingArcApp/src/utils/storage/ontologyStorage.ts) so cold start re-seeds."
        )

    return 1 if (args.check and drift) else 0


if __name__ == "__main__":
    raise SystemExit(main())
