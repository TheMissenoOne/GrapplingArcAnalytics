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

## 2026-09-18 — curated position map (rule 0)

Owner: "siga com as figuras, adicione as já existentes" — raise coverage using
positions GrappleMap already has, without inventing anything or touching
`technique_library.json` node identity.

Added `analysis/data/grapplemap_position_map.json` — a hand-curated
`node_key -> {position, note}` override, loaded by `load_position_map()` and
consulted as **rule 0 (`curated`)**, before `tag`/`canonical_en`/`variant`/`synonym`/
`token_set`. Built by loading every GrappleMap position's name+tags through the
parser and manually searching each of the 115 then-unmatched curated techniques for
a genuinely matching pose (BJJ synonyms: kneebar=knee bar — already auto-matched,
americana=keylock/ude garami, anaconda, calf slicer/crush, berimbolo, wrist lock,
etc.) — most of those specific submissions turned out to have **no** distinct
GrappleMap position (see rejected list below); 10 genuine matches were found.

**Coverage: 106/211** (up from 96/211). Rule breakdown: `curated` 10, `tag` 62,
`canonical_en` 6, `variant` 6, `synonym` 0, `token_set` 22.

### The 10 curated matches

| curated technique | GrappleMap position | why |
|---|---|---|
| Abraço por Trás (Rear Body Lock) | `standing behind w/ body lock` | owner's own example — exact pairing |
| Kesa-Gatame | `kuzure kesa gatame` | only kesa-gatame-family position (modified variant, no plain one exists) |
| Kimura Trap | `single leg vs kimura` | kimura grip as a counter/trap vs a single-leg — matches the curated "kimura counter" variant |
| Controle de Punho Dois-contra-Um (Two-on-One Wrist Control) | `2-on-1 to chest` | plainest `two_on_one`-tagged position, no arm-drag/pass in progress |
| Ganchos Encaixados (Hooks In) | `leg ride` | tags `back`+`leg_ride` — same concept as "hooks in", just not the same tokens (doc's earlier "genuine absence" call revisited: same idea, different name) |
| Meia-Guarda Esmagada (Smash Half Guard) | `smashed traditional half` | plainest `smash`+`half_guard` position |
| Meia-Guarda com Escudo (Knee Shield Half Guard) | `half guard shell w/ leg grabbed` | half guard with the knee-shield/shell frame up; distinct target from Guarda Z's own match (`quarter z`) |
| Guarda De La Riva | `standing vs de la riva` | plainest of the three `de la riva`-tagged positions (bottom player playing DLR vs a standing passer) |
| De La Riva Invertida | `standing vs reverse dlr` | GrappleMap's own name for the reverse/inverted DLR variant, distinct target from the base DLR mapping above |
| Emaranhado de Pernas (Leg Entanglement) | `ashi` | GrappleMap's generic leg-entanglement position — already reused by 4 other Ashi Garami-family curated nodes, `Emaranhado de Pernas` genuinely is the same broad concept |

Rejected during curation (checked, deliberately NOT mapped — precision over
coverage, per the module's own stated matching philosophy):

- **Americana, Chave de Ombro (generic Shoulder Lock)** — the only `shoulder_lock`-tagged
  position is `perfect kimura`, already claimed by the `Kimura` node (tag rule). Reusing
  it here would show the Kimura icon for a different, visually distinguishable technique
  — worse than no icon.
- **Anaconda** — exists only as a GrappleMap *transition* (`anaconda`, endpoints both
  `parallel jiu-claw`), not a position; that position is already `Omoplata`'s icon.
  Mapping-file entries must be positions only (rule spec), and reusing Omoplata's icon
  would misrepresent a completely different submission.
- **Body Lock das Costas** — near-duplicate concept of `Abraço por Trás` (already
  mapped) and the pre-existing `Body Lock` node (already `turtle body lock`); no third
  distinct body-lock position exists.
- **Leg Lace, Leg Hug, Gift Wrap, Chave de Pulso (Wrist Lock), Chave de Virilha,
  Chave de Panturrilha, Chave de Bíceps, Chave de Polícia, Chave de Pulmão, Chave de
  Pé (generic Foot Lock), Chave de Tornozelo, Shoulder Crunch, Triângulo de Corpo,
  Triângulo pelas Costas, Choi Bar, Buggy Choke, Calf Slicer, Gravata Peruana,
  Gogoplata, Ezequiel** and the choke family (Cruzado/Rodado/Taco de Beisebol/Von
  Flue/Relógio/Guilhotina Lateral) — checked by name, tag, and `SYNONYMS` against the
  full 588-position + 145-tag vocabulary; genuinely absent, not a matcher gap.
- **Puxada Dupla (Double Guard Pull)** — `guard_pull` tag exists but both positions
  carrying it are single-limb variants (`jumping guard w/ overhook`,
  `pulling guard from single-leg`), neither depicts a *double* (two-handed) pull.
- All **pass** (18), **takedown** (17), **sweep** (6), **escape** (10), **transition**
  (9) and **concept** (4) type nodes were left alone as a category: GrappleMap
  positions are static end-poses; these curated types name a *movement*
  (Toreando, Berimbolo, Snapdown, Scramble, Mudança de Nível, …), and GrappleMap
  models movement as multi-frame transitions, not positions — there is no
  single-frame pose that depicts a pass or a takedown without borrowing the
  resulting control position from a different, already-matched curated node.

Still unmatched: 105/211 (was 115). By curated type: submission 25, pass 18,
takedown 17, escape 10, transition 9, control 9, guard 7, sweep 6, concept 4.

Regen command unchanged (see above). `--check` clean at 106 entries after this
change. Validated by two new tests: `test_position_map_entries_are_valid`
(every key a real curated node_key, every target position exists, no duplicate
targets within the curated file) and `test_curated_rule_wins_before_automatic_rules`.
