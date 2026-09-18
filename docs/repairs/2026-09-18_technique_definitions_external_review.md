# 2026-09-18 — technique definitions: external (GPT) review applied

Owner had the 211 draft technique descriptions (`analysis/data/technique_definitions.json`,
see `docs/repairs/2026-09-17_technique_definitions_and_grapplemap_index.md`) reviewed
externally. Source files (`/home/vetor/Downloads/`, not in this repo):
`definicoes_grappling_revisadas.txt` (all 211, same format), `revisao_grappling_alteracoes.csv`
(the 80 changed rows, original × revised), `relatorio_revisao_grappling.md` (report +
taxonomic findings).

## Applied

- **80/80 CSV rows matched** by `_normalize_name(en_name)` (`analysis.names`, same key the
  file is already keyed by) — 0 unmatched. Each matched row's `original_en`/`original_pt`
  was asserted equal to what was on disk before writing (sanity that the CSV's "before" is
  this file's current state, not some other draft).
- For those 80: `en`/`pt` replaced with the revised text, `source: "draft"` →
  `"external-review"`. `reviewed` left `false` — the owner still confirms in the admin page
  (Auditoria → Definições). Key order preserved (in-place value update, no key
  insertion/reordering). All other 131 entries untouched, still `source: "draft"`.
- **21 entries got an added `review_note`** (new optional field, one-line, English,
  admin-only — never shown in the App UI, `services/techniqueDefinitions.ts` only reads
  `en`/`pt`):
  - **15** entries the report explicitly corrected for a factual/mechanical error (all
    among the 80, so `source: "external-review"` too): Buggy Choke, Electric Chair,
    Americana (Keylock), Double Guard Pull, Inverted De La Riva Guard, Shin to Shin Guard,
    Octopus Guard, Worm Guard, Shoulder Crunch, John Wayne Sweep, Wrestle-Up, Sumi Gaeshi,
    Tani Otoshi, False Reap, Leg Lock Escape.
  - **6** entries the report flags ambiguous/needing corpus examples — **not** revised (not
    in the 80 CSV rows, still `source: "draft"`), note added so the owner knows why the text
    didn't change: Leg Hug, Leg Lace, Lung Lock, Groin Lock, Inside Arm Stack Pass, Leg Drag
    to Straddle.

  Note: the CSV's own note column also flags **Choi Bar** and **Cop Lock** (`Chave de
  Polícia`) as specific corrections (both revised, both `source: "external-review"`) — not
  in the task's review_note list, so left without a note. Flagging here in case that was an
  oversight rather than a deliberate drop.

- Regenerated the App copy: `uv run python -m scripts.sync_app_artifacts` (writes
  `GrapplingArcApp/src/data/technique_definitions.json`), then
  `uv run python -m scripts.sync_app_artifacts --check` — clean, `technique_definitions.json
  (211 entries): unchanged`. No `NODE_LIBRARY_VERSION`/`ONTOLOGY_VERSION` bump — the App
  reads this file directly (`techniqueDefinitions.ts`, static import, no cold-start
  version gate), unlike the nodes library / ontology seed.
- `analysis/technique_definitions.py`: `TechniqueDefinition` TypedDict gained
  `review_note: NotRequired[str]`; docstring documents the 3-value `source` enum
  (`draft`/`external-review`/`human`).

## Not applied — node_key rename flagged, not touched

- **"Sassae Tsurikomi Ashi"** — report says the correct term is "Sasae Tsurikomi Ashi"
  (typo). Renaming the `en` display name would change `_normalize_name(en)`, i.e. the
  node_key itself — that's technique-library IDENTITY, not a description edit, and touches
  `technique_library.json` (curated identity source), the App nodes golden
  (`merge_curated_identity`), and any athlete graph/edge/embedding already keyed on
  `sassae tsurikomi ashi`. Left alone. CSV row 70 (`Sassae Tsurikomi Ashi` → same name) WAS
  applied as a normal description-only revision — the typo is in the display name, not the
  description, so this doesn't conflict with what was applied.

## Taxonomic pending items from the report (owner checklist)

None of these were touched — each is a node-identity / ontology decision, not a description
edit, so each needs a deliberate call before any code moves:

- [ ] **Sassae Tsurikomi Ashi → Sasae Tsurikomi Ashi** (spelling fix). Touches: curated
  `en` display name → node_key changes → `technique_library.json` identity →
  `merge_curated_identity`/App nodes golden → any athlete graph/edge/embedding/ELO already
  keyed on the old node_key needs a rename-and-replay, not a silent JSON edit.
- [ ] **Body Lock das Costas × Abraço por Trás** — report calls these near-duplicate (both
  "rear body lock"). Touches: possible merge = two node_keys collapse to one → every graph
  edge/node currently on the losing key needs remapping + replay; or keep both with an
  explicit distinguishing rule (e.g. standing vs. ground) written into both descriptions.
- [ ] **Controle Norte-Sul × Posição Norte-Sul** — report calls these near-duplicate
  (control vs. position/state). Same shape as above: merge = node-identity change +
  replay; keep-both needs an explicit ontological split (state vs. active control) in both
  descriptions, and in whatever taxonomy/event-typing code (if any) currently treats them
  as interchangeable.
- [ ] **Calf Lock × Calf Slicer** — possible alias; report can't confirm without corpus
  usage. Touches: if Calf Lock is actually always labeled Calf Slicer in the corpus, this
  is a `names.SYNONYMS` entry (derivation-layer collapse, no node_key rename) rather than a
  merge — cheaper fix, but needs the corpus check first (grep `matches`/sequence event
  labels for "calf lock" usage).
- [ ] **Cradle categorized as `takedown`** — report says it's a wrestling
  control/pin, not a takedown. Touches: `technique_library.json`'s `type` field for this
  node — changing `type` may move it in/out of type-filtered UI sections, GrappleMap
  icon-matching category rules (`export/grapplemap_icons_export.py` treats `pass`/
  `takedown`/`sweep`/`escape`/`transition`/`concept` as a category, per the 09-17 doc), and
  any analysis that buckets by type (e.g. `technique_freq.py`).
- [ ] **Clinch categorized as `transition`, False Reap categorized as `transition`** —
  report says both read more like states/positions than transitions. Touches: same `type`
  field concern as Cradle above; for False Reap specifically, also touches whatever event
  model treats `transition`-typed nodes differently from position nodes (check
  `docs/match_event_model.md`'s per-type ownership table before moving it).
- [ ] **Fuga para Guarda × Recomposição de Guarda** — report calls this an escape vs.
  transition/state-recovery overlap that "should be ontological, not mechanical." No
  specific fix proposed by the report; needs the owner's read on whether these are one
  concept or two before any node/description change.
- [ ] **Ashi Garami Cruzado / Ashi Garami Interno / Sela (411)** — report recommends
  defining an explicit canonical ashi-entanglement naming system (standard/outside/cross/
  saddle) before tightening any of these three descriptions further. Scoping call, not a
  data bug.

## Verification run

```
uv run python -m analysis.technique_definitions          # OK — 211 technique definitions loaded
uv run pytest tests/test_technique_definitions.py tests/test_sync_app_artifacts.py -q   # 35 passed
uv run pytest tests/test_admin_audit.py -q                # 28 passed (definitions-adjacent, unaffected)
uv run ruff check analysis/technique_definitions.py tests/test_technique_definitions.py  # clean
uv run mypy analysis/technique_definitions.py tests/test_technique_definitions.py        # clean
uv run python -m scripts.sync_app_artifacts --check       # clean, definitions artifact unchanged
```
