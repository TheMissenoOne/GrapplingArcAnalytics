"""Generate GrappleMap position icons + the App's require-index (Seam A).

Producer-side only. Three outputs:

1. **Full CV set** → ``data/grapplemap_icons/`` — every GrappleMap position rendered
   as a stick-figure PNG (raw use / future CV training). Lives in Analytics only.
2. **App vocab subset** → ``GrapplingArcApp/src/assets/grapplemap_icons/`` — one
   representative icon PNG per matched CURATED technique (root CLAUDE.md tech-library
   contract, all 9 types — not just the guard/control subset ``cv.vocab_map`` restricts
   itself to for its CV-classifier vocabulary; GrappleMap's 588 positions already
   include tagged submission/takedown finishes, e.g. ``"arm lock"``, ``"basic omoplata
   finish"``).
3. **App require-index** → ``GrapplingArcApp/src/data/grapplemapIconIndex.ts`` — a
   code-generated ``GRAPPLEMAP_ICONS: Record<string, number>`` mapping each matched
   technique's node_key (``analysis.names._normalize_name``, space-separated — same key
   `scripts/sync_app_artifacts.py` uses) to a static ``require()`` of its PNG, so Metro
   bundles every referenced asset. (A prior version of this index, keyed by underscored
   alias instead of node_key and living under ``src/assets/``, was removed 2026-09-09 for
   having zero importers — this regenerates it at the new path/shape for the detail-modal
   consumer.)

Matching strategy (high precision over coverage — a wrong icon is worse than none):
  - **Tag pass (primary):** GrappleMap tags (``closed_guard``, ``side_control``,
    ``mount`` …) are a clean position vocabulary. Each tag, normalized, is matched
    against the app vocab index (``cv.vocab_map.build_vocab_index``), which already
    folds in English aliases. A matched tag's app node gets a representative
    position (the most canonical — shortest, plainest name carrying that tag).
  - **Exact-name fill:** nodes still unmatched try an exact ``_normalize_name``
    match against GrappleMap position names (picks up e.g. "honey hole" → Sela).
  - Fuzzy name matching is deliberately NOT used — it mismaps (open guard → "goes
    guard", back control → "k-control").

PNG filenames key off every app-node alias (canonical name + en/pt + variations),
all normalized to ``<key> = _normalize_name(alias).replace(" ","_")`` — identical to
the app's ``normalizeLabel(label).replace(/ /g,'_')`` — so a future app resolver
matches whatever label form it passes. The TS index maps the node_key (not the
underscored alias) to that same PNG.

Reuses, no new rendering/parsing logic:
  - ``grapplemap.parser.parse_grapplemap`` — GrappleMap.txt → positions (+ tags).
  - ``grapplemap.icons.export_all_icons`` / ``_safe_filename`` — render + filename.
  - ``cv.vocab_map.load_app_nodes`` / ``build_vocab_index`` — app position vocab.
  - ``analysis.names._normalize_name`` — match + key normalization.

Usage:
    uv run python -m export.grapplemap_icons_export            # write PNGs + index
    uv run python -m export.grapplemap_icons_export --check     # index only, diff,
                                                                 # exit nonzero on drift
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from analysis.names import _normalize_name
from cv.vocab_map import NodeRef, build_vocab_index, load_app_nodes
from grapplemap.icons import export_all_icons
from grapplemap.parser import GMapGraph, GMapPosition, parse_grapplemap

# ─── paths ───────────────────────────────────────────────────────────────────

_ANALYTICS_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _ANALYTICS_ROOT.parent

DEFAULT_DB_PATH = _ANALYTICS_ROOT / "data" / "GrappleMap.txt"
DEFAULT_FULL_ICONS_DIR = _ANALYTICS_ROOT / "data" / "grapplemap_icons"

DEFAULT_APP_ASSETS_DIR = (
    _REPO_ROOT / "GrapplingArcApp" / "src" / "assets" / "grapplemap_icons"
)
DEFAULT_APP_INDEX_TS = (
    _REPO_ROOT / "GrapplingArcApp" / "src" / "data" / "grapplemapIconIndex.ts"
)


def _node_aliases(node: dict) -> list[str]:
    """Every label form for a node: canonical name, en/pt translation, variations."""
    tr = node.get("translations", {}) or {}
    return [
        str(node.get("name", "")),
        str(tr.get("en", "")),
        str(tr.get("pt", "")),
        *[str(v) for v in node.get("variations", [])],
    ]


def _alias_key(label: str) -> str:
    """Normalized, underscored key — matches app normalizeLabel(label).replace(/ /g,'_')."""
    return _normalize_name(label).replace(" ", "_")


def _pick_representative(
    positions: list[tuple[str, GMapPosition]],
    alias_norms: set[str],
) -> str | None:
    """From positions sharing a tag, pick the cleanest representative's dict key.

    Prefers a position whose normalized name is itself an app alias (the canonical
    pose), then the plainest name (fewest words, then shortest) — situational names
    like "octopus closed guard w/ kimura" lose to a bare "closed guard".
    """
    if not positions:
        return None

    def score(item: tuple[str, GMapPosition]) -> tuple[int, int, int]:
        _, pos = item
        norm = _normalize_name(pos.name)
        return (0 if norm in alias_norms else 1, len(norm.split()), len(norm))

    return min(positions, key=score)[0]


def _resolve_node_positions(
    gmap: GMapGraph,
    nodes: list[dict],
    node_types: tuple[str, ...] | None = None,
) -> dict[str, tuple[str, NodeRef]]:
    """Map app node name → (representative GrappleMap dict-key, NodeRef).

    Tag pass first (high precision), then exact-name fill for the rest.

    ``node_types``: restrict matching to these curated `type` values, or ``None``
    (default) to match every curated type. GrappleMap's 588 positions include
    tagged submission/takedown finishes (``"arm lock"``, ``"basic omoplata
    finish"``, ``"50/50 heel hook"``), not just guard/control poses — unlike
    ``cv.vocab_map.POSITION_TYPES`` (which stays guard/control-only, a CV-classifier
    vocabulary constraint unrelated to this icon export).
    """
    index = build_vocab_index(nodes, position_types=node_types)

    # node name → its alias-norm set + NodeRef (restricted to node_types, or all)
    pos_nodes = [
        n for n in nodes
        if node_types is None or str(n.get("type", "")) in node_types
    ]
    alias_norms: dict[str, set[str]] = {}
    node_ref: dict[str, NodeRef] = {}
    for n in pos_nodes:
        name = str(n.get("name", ""))
        if not name:
            continue
        alias_norms[name] = {_normalize_name(a) for a in _node_aliases(n) if a}
        node_ref[name] = NodeRef(name=name, type=str(n.get("type", "")))

    # positions grouped by tag
    by_tag: dict[str, list[tuple[str, GMapPosition]]] = {}
    for key, pos in gmap.positions.items():
        for tag in pos.tags:
            by_tag.setdefault(_normalize_name(tag.replace("_", " ")), []).append((key, pos))

    resolved: dict[str, tuple[str, NodeRef]] = {}

    # 1. Tag pass — larger tags first so the dominant sense wins a node.
    for tnorm, members in sorted(by_tag.items(), key=lambda kv: -len(kv[1])):
        ref = index.get(tnorm)
        if ref is None or ref.name in resolved:
            continue
        rep = _pick_representative(members, alias_norms.get(ref.name, set()))
        if rep is not None:
            resolved[ref.name] = (rep, ref)

    # 2. Exact-name fill — match remaining nodes to a same-named position.
    name_lookup = {_normalize_name(pos.name): key for key, pos in gmap.positions.items()}
    for node_name, norms in alias_norms.items():
        if node_name in resolved:
            continue
        for nk in norms:
            if nk in name_lookup:
                resolved[node_name] = (name_lookup[nk], node_ref[node_name])
                break

    return resolved


def _render_index_ts(node_key_to_file: dict[str, str]) -> str:
    """Code-gen the GRAPPLEMAP_ICONS module — static requires, one per node_key.
    Uses `json.dumps` for the string literals (not `repr` + global quote-swap) so a
    stray apostrophe never corrupts a comment or a key."""
    lines = [
        "// GENERATED by export/grapplemap_icons_export.py -- do not edit.",
        "// Maps a curated technique's node_key (analysis.names._normalize_name(en),",
        "// same key scripts/sync_app_artifacts.py uses) -> bundled stick-figure PNG.",
        "// require() literals are static so Metro statically bundles each asset.",
        "",
        "export const GRAPPLEMAP_ICONS: Record<string, number> = {",
    ]
    for key in sorted(node_key_to_file):
        file_key = node_key_to_file[key]
        req = json.dumps(f"../assets/grapplemap_icons/{file_key}.png")
        lines.append(f"  {json.dumps(key)}: require({req}),")
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def resolve_matched_nodes(
    db_path: Path | str = DEFAULT_DB_PATH,
    node_types: tuple[str, ...] | None = None,
) -> tuple[GMapGraph, list[dict], dict[str, tuple[str, NodeRef]]]:
    """Parse GrappleMap + resolve node matches — no rendering/file I/O. Cheap enough
    to run twice (render path + `--check` path) without duplicating the match logic."""
    gmap = parse_grapplemap(Path(db_path))
    nodes = load_app_nodes()
    resolved = _resolve_node_positions(gmap, nodes, node_types=node_types)
    return gmap, nodes, resolved


def build_node_key_index(
    nodes: list[dict], resolved: dict[str, tuple[str, NodeRef]]
) -> dict[str, str]:
    """node_key -> file_key for every matched node (file_key = `_alias_key(name)`, the
    PNG filename stem `export_icons` writes — deterministic, no rendering needed)."""
    node_by_name = {str(n.get("name", "")): n for n in nodes}
    out: dict[str, str] = {}
    for node_name in resolved:
        node = node_by_name.get(node_name, {})
        en = str((node.get("translations") or {}).get("en") or node_name)
        out[_normalize_name(en)] = _alias_key(node_name)
    return out


def export_icons(
    db_path: Path | str = DEFAULT_DB_PATH,
    full_icons_dir: Path | str = DEFAULT_FULL_ICONS_DIR,
    app_assets_dir: Path | str = DEFAULT_APP_ASSETS_DIR,
    app_index_ts: Path | str = DEFAULT_APP_INDEX_TS,
    size: int = 128,
    verbose: bool = True,
    node_types: tuple[str, ...] | None = None,
    check: bool = False,
) -> dict[str, NodeRef]:
    """Render all icons (CV) + bundle one representative per matched curated technique
    + write the App's `grapplemapIconIndex.ts`.

    ``node_types``: restrict matching to these curated `type` values, ``None``
    (default) matches every curated technique — see `_resolve_node_positions`.

    ``check=True``: skip rendering/writing PNGs (matching is deterministic without
    them) and diff the generated index text against `app_index_ts`; raises
    `SystemExit(1)` on drift, writes nothing. `--check` CLI flag below.

    Returns ``{file_key: NodeRef}`` for the matched app nodes (file_key = the node's
    canonical-name key, the PNG filename stem).
    """
    app_index_ts = Path(app_index_ts)
    if verbose:
        print(f"Parsing {db_path} …")
    gmap, nodes, resolved = resolve_matched_nodes(db_path, node_types=node_types)
    if verbose:
        print(f"  {len(gmap.positions)} positions")

    node_key_to_file = build_node_key_index(nodes, resolved)
    index_text = _render_index_ts(node_key_to_file)

    if check:
        old_text = app_index_ts.read_text(encoding="utf-8") if app_index_ts.is_file() else None
        if index_text != old_text:
            print(f"grapplemapIconIndex.ts: DRIFT ({len(node_key_to_file)} entries)")
            raise SystemExit(1)
        print(f"grapplemapIconIndex.ts: unchanged ({len(node_key_to_file)} entries)")
        return {_alias_key(name): ref for name, (_, ref) in resolved.items()}

    full_icons_dir = Path(full_icons_dir)
    app_assets_dir = Path(app_assets_dir)

    # 1. Full CV set — every position (Analytics-local, not bundled into the app).
    if verbose:
        print(f"Rendering full icon set → {full_icons_dir}")
    full_saved = export_all_icons(gmap, full_icons_dir, size=size, verbose=False)

    # 2. Bundle one representative PNG per matched app node.
    node_by_name = {str(n.get("name", "")): n for n in nodes}
    app_assets_dir.mkdir(parents=True, exist_ok=True)

    matched: dict[str, NodeRef] = {}          # file_key -> NodeRef
    alias_to_file: dict[str, str] = {}        # alias_key -> file_key (index entries)
    for node_name, (pos_key, ref) in resolved.items():
        src = full_saved.get(pos_key)
        if src is None:
            continue  # render failed upstream
        file_key = _alias_key(node_name)
        shutil.copyfile(src, app_assets_dir / f"{file_key}.png")
        matched[file_key] = ref
        # every alias form of this node resolves to its one icon (first write wins)
        for alias in _node_aliases(node_by_name.get(node_name, {})):
            ak = _alias_key(alias)
            if ak and ak not in alias_to_file:
                alias_to_file[ak] = file_key

    # 3. App require-index, keyed by node_key.
    app_index_ts.parent.mkdir(parents=True, exist_ok=True)
    app_index_ts.write_text(index_text, encoding="utf-8")

    if verbose:
        all_node_names = {
            str(n.get("name", ""))
            for n in nodes
            if (node_types is None or str(n.get("type", "")) in node_types) and n.get("name")
        }
        unmatched = sorted(all_node_names - set(resolved))
        print(f"App assets → {app_assets_dir}")
        print(f"Index TS   → {app_index_ts} ({len(node_key_to_file)} entries)")
        print(
            f"Matched {len(matched)}/{len(all_node_names)} curated technique nodes "
            f"({len(alias_to_file)} alias keys)."
        )
        if unmatched:
            print(f"  Unmatched curated technique nodes ({len(unmatched)}):")
            for n in unmatched:
                print(f"    - {n}")

    return matched


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--check", action="store_true",
        help="diff grapplemapIconIndex.ts only, write nothing, exit nonzero on drift",
    )
    args = ap.parse_args()
    export_icons(check=args.check)
