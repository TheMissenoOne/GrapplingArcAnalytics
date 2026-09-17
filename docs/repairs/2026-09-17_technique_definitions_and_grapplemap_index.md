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

`export/grapplemap_icons_export.py` — two matching passes:

1. Broadened `_resolve_node_positions`'s type filter from `cv.vocab_map.POSITION_TYPES`
   (guard/control only, a CV-classifier constraint) to all curated technique types
   (GrappleMap's 588 positions already tag submission/takedown finishes, e.g.
   `"arm lock"`, `"basic omoplata finish"`).
2. Ordered, auditable rule chain per node (first hit wins, rule recorded):
   `tag` (unchanged) → `canonical_en` → `variant` → `synonym` → `token_set`. All four
   name-based rules compare on the **canonical key**
   (`analysis.names.canonicalize(_normalize_name(label))`, applied to BOTH the curated
   label and the GrappleMap position name — the same key the corpus resolves event
   labels through) rather than the earlier bare `_normalize_name` exact match, which is
   why obvious pairs like "north south" (GrappleMap) / "North-South Position" (curated,
   via its `north-south` variant) were slipping through before. `token_set` (rule 4) is
   the deliberately conservative fallback: every token of one side's canonical key is a
   subset of the other's (e.g. `ashi ⊆ inside ashi`; `open guard ⊆ wide open guard`) —
   no fuzzy edit-distance guessing, ties broken by fewest extra tokens then
   plainest-then-alphabetical name.

**Coverage: 96/211** curated techniques matched (up from 73/211 pre-rules, 28/62 when
still restricted to guard/control). Rule breakdown: `tag` 62, `canonical_en` 6,
`variant` 6, `synonym` 0 (structurally subsumed by `canonical_en`/`variant` once both
sides are canonicalized — kept as a distinct, order-respecting rule per spec, not dead
code removed, in case the main index is ever built without canonicalizing GrappleMap's
side), `token_set` 22.

Of the 8 names flagged as suspicious misses, 6 now match (Controle Norte-Sul, Guarda
Aberta, De La Riva Invertida, Emaranhado de Pernas, Controle da Tartaruga, all 3 Ashi
Garami variants). The remaining 2 are genuine absences under conservative matching, not
matcher bugs: **Ganchos Encaixados** (Hooks In) — no GrappleMap position combines
"hooks" and "in" as its own canonical tokens; **Abraço por Trás** (Rear Body Lock) — the
closest GrappleMap position, `"standing behind w/ body lock"`, shares the concept but
not the token set (`rear` absent from the GrappleMap name, `standing`/`behind` absent
from the curated one) — a human would fold these, `token_set`'s subset rule correctly
won't. Both are editorial calls (add a `variants` entry / `names.SYNONYMS` mapping), not
a matching-algorithm fix — left as unmatched.

First 15 of the 115 still-unmatched (mostly submissions/throws GrappleMap has no
tagged/named terminal pose for, or judo-only takedowns with no BJJ-corpus alias):
Abertura de Guarda, Abraço por Trás, Americana, Anaconda, Arremesso de Quadril,
Berimbolo, Body Lock das Costas, Buggy Choke, Calf Slicer, Chave de Bíceps, Chave de
Ombro, Chave de Panturrilha, Chave de Polícia, Chave de Pulmão, Chave de Pulso.

Re-adds the App require-index removed 2026-09-09 (`d4fcc2f`, had zero importers at the
time) at a new path/shape for the detail-modal consumer: keyed by node_key (not the old
underscored alias), living at `src/data/` (not `src/assets/`).

```bash
uv run python -m export.grapplemap_icons_export            # render PNGs + write index
uv run python -m export.grapplemap_icons_export --check     # index diff only, no PNG render
```

Outputs:
- `data/grapplemap_icons/` — full CV set (Analytics-only, 588 PNGs).
- `GrapplingArcApp/src/assets/grapplemap_icons/` — 96 PNGs, one per matched technique.
- `GrapplingArcApp/src/data/grapplemapIconIndex.ts` — `GRAPPLEMAP_ICONS: Record<string, number>`.

Tests: `tests/test_grapplemap.py` (`test_export_icons_writes_subset`,
`test_export_icons_check_mode_writes_nothing_and_detects_drift`) exercise matching,
the index shape, and that every referenced PNG exists — no DB, sibling-repo-gated
(`_DEFAULT_NODES_PATH.exists()`, same guard as the rest of the file).
