# 2026-09-17 — technique definitions + GrappleMap icon index (App detail modal)

New per-technique content for an App detail modal: a short description and (where
GrappleMap has a matching position) a stick-figure figure, for each of the 211 curated
techniques (`analysis/data/technique_library.json`).

## Descriptions

- Source: `analysis/data/technique_definitions.json` — `{en, pt, source, reviewed}` per
  node_key (`analysis.names._normalize_name(en)`). All 211 entries `source: "draft"`,
  `reviewed: false` — model-authored, awaiting owner review before flipping either field.
- Loader: `analysis/technique_definitions.py` (`load_definitions()` / `definition_for(node_key)`).
- Synced (filtered to node_keys the App's library actually carries) into
  `GrapplingArcApp/src/data/technique_definitions.json` by `scripts/sync_app_artifacts.py`
  (artifact **e** in that module's docstring).

Regenerate after editing the curated definitions file:

```bash
uv run python -m scripts.sync_app_artifacts            # writes, needs DB (fresh scores)
uv run python -m scripts.sync_app_artifacts --check     # CI parity, no writes
```

## GrappleMap icons

`export/grapplemap_icons_export.py` — broadened `_resolve_node_positions`'s matching
from `cv.vocab_map.POSITION_TYPES` (guard/control only, a CV-classifier constraint) to
all curated technique types (GrappleMap's 588 positions already tag submission/takedown
finishes, e.g. `"arm lock"`, `"basic omoplata finish"`). Coverage: **73/211** curated
techniques matched (up from 28/62 when restricted to guard/control).

Re-adds the App require-index removed 2026-09-09 (`d4fcc2f`, had zero importers at the
time) at a new path/shape for the detail-modal consumer: keyed by node_key (not the old
underscored alias), living at `src/data/` (not `src/assets/`).

```bash
uv run python -m export.grapplemap_icons_export            # render PNGs + write index
uv run python -m export.grapplemap_icons_export --check     # index diff only, no PNG render
```

Outputs:
- `data/grapplemap_icons/` — full CV set (Analytics-only, 588 PNGs).
- `GrapplingArcApp/src/assets/grapplemap_icons/` — 73 PNGs, one per matched technique.
- `GrapplingArcApp/src/data/grapplemapIconIndex.ts` — `GRAPPLEMAP_ICONS: Record<string, number>`.

Tests: `tests/test_grapplemap.py` (`test_export_icons_writes_subset`,
`test_export_icons_check_mode_writes_nothing_and_detects_drift`) exercise matching,
the index shape, and that every referenced PNG exists — no DB, sibling-repo-gated
(`_DEFAULT_NODES_PATH.exists()`, same guard as the rest of the file).
