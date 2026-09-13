# 2026-09-13 — curated technique library sync (71-entry expansion, tail end)

Follow-on to a previous session's `scripts/sync_app_artifacts.py` rewrite (`merge_curated_
identity`, `tests/test_sync_app_artifacts.py`): the curated file (`analysis/data/
technique_library.json`) had already grown from ~142 to 209 entries and been merged into the
App's `src/data/grappling-arch.nodes.json` (209 curated + 4 App-only = 213). This pass cleaned
up the alias collisions and App-only leftovers that merge surfaced, and regenerated every
cross-repo golden the library feeds.

## Measurement method (root CLAUDE.md decision, gate for every alias edit)

For each proposed curated variant removal, resolved every `matches.sequence[]` label in prod
(read-only, all matches, any status — 10 121 events) through `analysis.technique_match.
clean_label` before/after the edit and counted how many changed. Script kept in the session
scratchpad, not the repo (throwaway).

| Edit | Corpus events changed | Verdict |
|---|---|---|
| Arm Lock: drop `armlock`, `chave no braco` (dup of Armbar's) | 0 | Applied |
| Shoulder Lock: drop `shoulder crunch` | 0 | Applied |
| Hooks In: drop `ganchos` | 0 (already shadowed by Butterfly Guard, alphabetically first) | Applied |
| Inverted De La Riva Guard: drop `rdlr` | 0 | Applied |
| Knee Shield Half Guard: drop `escudo de joelho` | 0 | Applied |
| Roll-Through: drop `roll` | **14** | **Deferred** — real corpus events resolve "Roll" through this variant; removing it is a `node_key` move needing the N1 replay runbook. Curated file left as-is; `localizeTechniqueLabel.test.ts`'s `KNOWN_AMBIGUOUS_TERMS` documents it. |
| North South Choke: rename `en` to the hyphenated form (to absorb the App-only "North-South Choke" entry) | **6** | **Deferred** — same reason; would relabel every existing "North South Choke" corpus event's `clean_label`. Curated file (and its existing `north-south choke` variant + matching `pt`) left as-is; the App-only entry stays appended, `taxonomyKind.test.ts` unaffected (App-only entries aren't in the golden's scope check the same way — see below). |

Root cause: neither deferred alias is a case of a *stale* variant; both are corpus-load-bearing
today. Applying either without a replay would silently retitle live technique history.

## Additive: App-only entries folded into curated (no key moves)

Three App-only rows (present in `grappling-arch.nodes.json`, absent from the curated file)
copied into `analysis/data/technique_library.json` verbatim (`en`/`pt`/`type`/variants),
inserted alphabetically: **Electric Chair**, **Leg Hug**, **Shoulder Crunch**. All three
`en`/`pt`/type/variant sets match their existing App rows exactly, so the merge matched them
by key and preserved `_id`/`createdAt` — zero new-entry IDs minted for these three.

**North-South Choke** stays App-only (see deferred table above) — the only remaining app-only
row; `sync_app_artifacts` reports it every run (`1 app-only kept`), not silently dropped.

## Found in the process (not a named decision, root-caused and fixed)

**Bare event-type words leaking into node IDENTITY.** Three of the new curated entries name a
technique with the SAME word as its own event `type` and legitimately carry that bare word as
a `variants` entry for `technique_match.clean_label`'s benefit (real corpus events are logged
with the literal label "Pass"/"Sweep"/"Takedown"): `Guard Pass`/`pass`, `Sweep`/`sweep`,
`Takedown`/`takedown`. Corpus-count gate on removing `pass` from `Guard Pass`: **23 changed**
→ deferred, same class as the two aliases above; the bare word stays in `technique_library.json`
because cleanup needs it.

That reintroduced, via a different door, a bug the App had already fixed once
(`techniqueCategory.libraryNameVariants`'s docstring, 2026-08-27: "Sweep" resolving via
`getAllNames`'s `type`/`tipo` fields). Root-cause fix applied on **both** sides' IDENTITY
consumers only (never the cleaning path): `libraryKindIndex` (App, `techniqueCategory.ts`) and
`build_library_lookup` (Analytics, `scripts/export_taxonomy_kind_fixtures.py`) now both exclude
the ten reserved bare event-type words (`guard`, `control`, `pass`, `sweep`, `submission`,
`takedown`, `escape`, `transition`, `defensive`, `concept`) when building the alias→entry map.
`analysis.technique_match._index` (corpus cleaning) is untouched on purpose.

**"Arm Lock" vs "Armbar" `name`-field collision.** The curated `pt` disambiguation ("Arm
Lock" → "Chave de Braço (genérica)", Armbar keeps bare "Chave de Braço") fixed the
`translations.pt`/variant collision, but the App's pre-existing "Arm Lock" row's own `name`
field (untouched by `merge_curated_identity` by design — a real reader,
`SessionStartSheet.curatedFallbackTopics`, keys a **different**, unrelated hardcoded list by
`name`) still carried the stale legacy value `"Chave de Braço"`, so identity lookups (e.g.
`contentCards`' path localisation) still resolved the bare Portuguese term to "Arm Lock" over
"Armbar". Verified `curatedFallbackTopics`'s own list (`Guarda Fechada`/`Meia Guarda`/
`Toreando`/`Montada`/`Costas`/`Guilhotina`) never names either entry, so the field was free to
correct: `grappling-arch.nodes.json`'s Arm Lock row `name` → `"Chave de Braço (genérica)"` (a
one-time on-disk correction, not a `merge_curated_identity` behaviour change — the field stays
untouched by the generator going forward, same as before).

## Goldens regenerated

Two of the seven cross-repo generators the library feeds actually depend on
`grappling-arch.nodes.json`/`library_lookup.json`; five (`chain_compiler`, `path_bundling`,
`path_metrics`, `flow_layout`, `node_key`) don't touch it and reported no drift throughout.

| Generator | Change |
|---|---|
| `export_taxonomy_kind_fixtures.py` | `taxonomy_kind_golden.json` (`kinds`): **+71 keys, 0 removed, 0 EXISTING key changed kind** — exactly the expected "new curated variant now resolves" shape. `library_lookup.json` also regenerated (the bare-word exclusion above, plus the 71 new entries' aliases). |
| `export_actions_parity_fixtures.py` | Mock-bundle multiset: **+1 entry** (`takedown defense`/partner/observed) — the mock's "Sprawl" event now resolves through the new "Takedown Defense" entry (`sprawl` variant). Nothing removed/changed. |
| `export_map_aggregate_fixtures.py` | Mock-bundle aggregate: same "Takedown Defense" resolution ripples into the aggregate's states/edges/handovers (an `opp:sprawl` state disappears, a `takedown defense` action multiset entry appears). Byte-identical to the App's copy after regen. |

`data/taxonomy/library_lookup.json` sequencing note: it must be regenerated **after** every
edit that changes a node's `name`/`variations`/`translations` — the Arm Lock `name` fix above
was made after an earlier regen and required a second pass (`--check` caught the drift
immediately; no silent staleness).

## App-side test updates (content, not logic)

- `taxonomyKind.test.ts`: `CURATED_TABLE_OVERRIDES_THE_HEURISTIC` extended with 6 new labels
  (`collar tie`, `front headlock`, `guard recovery`, `hooks in`, `rear body lock`,
  `russian tie`, `twoonone wrist control`) — same documented class as the pre-existing `body
  lock` entry: the curated `attribution` table (Python-only, not ported to the App's live
  heuristic) answers these differently than `classifyKind`'s heuristic; `kindOf`'s golden
  lookup already carries the right answer, only the *heuristic-alone* test needed the escape
  hatch. Entry count comment corrected 142 → 213.
- `localizeTechniqueLabel.test.ts`: `KNOWN_AMBIGUOUS_TERMS` gains `roll` (the deferred
  Roll-Through/Roll alias above), dated comment naming the measured count and the reason.
- `nodeCorpusScores.bundle.test.ts`: gate-clearing count 9 → **21**, dated comment (measured
  against the regenerated 213-entry bundle).
- `ontologyStorage.test.ts`: `ONTOLOGY_VERSION` literal updated to match the bump below.

## Unrelated side effect, flagged for the owner

Running `scripts.sync_app_artifacts` (required to converge the node-score injection, decision
4) also byte-copies `data/processed/ontology_seed.json` → the App's bundled copy whenever they
differ — they did, by a **large** margin (49k-line diff, whole `archetypes`/`athlete_profiles`
sections reshuffled) that has nothing to do with the curated technique library. This predates
this session (the drift already existed in the repo at session start) and is a **pre-existing,
unrelated ontology-exporter drift** that this task's required full-tool run happened to pick
up as a side effect of running `sync_app_artifacts` for the technique-library work. Kept (the
sanity gate in `verify_ontology_seed` passed: 3 `position_decision_space` keys, 464
`athlete_profiles`, both non-degenerate) and `ONTOLOGY_VERSION` bumped
(`ontology_seed@2026-08-26-athlete-systems-refresh` → `ontology_seed@2026-09-13-curated-sync`)
so cold start re-seeds — but the CONTENT of that diff (why the archetype clustering changed)
was not reviewed here and is out of this task's scope. Owner should confirm this ontology
re-export was intentional.

## Open finding — RESOLVED (Option 1), 2026-09-13

Regenerating `library_lookup.json` against the now-213-entry App library made **"Hooks In"**
resolvable through `analysis.taxonomy_kind.resolve_library_entry`/`kind_of_entry` for the
first time (it was one of the 71 entries added in the prior session, not present in the
142-entry library `kind_of_entry`'s lookup was built from before). `kind_of_entry` exists
specifically to trust the LIBRARY's own type over a possibly-stale logged type — but "Hooks
In" is a **documented, still-open dual-identity case** (`tests/test_audit_ontology.py`'s
`SYNTHETIC` fixture comment: "hooks in segue aberta, N1"): logged as `control` it is the
STATE (already-secured position); logged as `transition` it is the ACTION (the moment of
landing the hooks) — Lamas' own `BACK_TAKE_TOKENS` reads it as an action absent a curated
override, and the curated `attribution` table only overrides the `control` case to `state`.
Left unfixed, the library's ONE fixed type (`control`) for "Hooks In" would resolve BOTH
loggings to `state`, silently closing that ambiguity in favour of "always a state" —
collapsing the exact case the audit test exists to prove is still open.

**Resolved by Option 1** — a library sync must be behaviour-preserving on the corpus; it is
not the ontology programme and must not silently decide a case N1 hasn't ruled on. New
constant `analysis.taxonomy_kind.OPEN_DUAL_IDENTITY = frozenset({"hooks in"})`: `kind_of_entry`
skips the library's type override for exactly these keys and classifies on the caller's LOGGED
`type` instead (the pre-library-resolution path). App mirror: `techniqueCategory.
OPEN_DUAL_IDENTITY` in `src/services/techniqueCategory.ts` (comment points back here), scoped
to the one case that actually needs it (a `FORCED_ACTION_TYPES` type wins outright; anything
else falls through to the golden, which already carries the curated `control` -> `state`
answer for this label — the App has no live port of the `attribution` curated table, so this
is narrower than the Python fix by construction, noted as a `ponytail:` in the code).

`tests/test_audit_ontology.py::test_dual_identity_needs_two_kinds_not_two_types` needed no
change — "hooks in" is flagged dual-identity again, as designed.

**Exact per-bout accounting on the owner's private corpus (`_analytics_export.json`, 281
bouts).** Excluding "Hooks In" does NOT return `observed_actions` to 1386: reverting one label
in isolation isn't a linear undo when TWO OTHER labels' classification legitimately moved in
the same regen (the OLD `kind_of_entry` lookup was built from the stale 142-entry App library
while `technique_match.clean_label` already read the 209-entry curated file — two libraries
disagreeing was the real, separate bug this sync fixes for `Roll`/`Front Headlock`). Per-bout
diff, OLD library_lookup vs NEW library_lookup + `OPEN_DUAL_IDENTITY`, all 281 bouts:

| label (bout count) | old kind | new kind | `observed_actions` Δ | mechanic |
|---|---|---|---|---|
| `transition/Roll` (14 bouts, 1 event each) | `transparent` (unresolved, old library had no "Roll-Through" variant for it) | `action` (resolves to "Roll-Through", forced by `transition` type) | **+14** (+1/bout) | direct: a `transparent` event was DROPPED by the compiler (`chain_compiler.compile_chain`); now it is a real observed action instead of being skipped |
| `control/Snap Down to Front Headlock` (1 bout) | `action` (type-only fallback, no curated entry existed) | `state` (resolves to the new "Front Headlock" curated entry) | **-1** | direct: a state lives on `ChainEdge`'s ENDPOINT, not in `edge.actions`, so it stops counting as observed |
| `transition/Hooks In` (16 bouts, 1 event each) | — | — | **0** | excluded via `OPEN_DUAL_IDENTITY`; classifies identically to the OLD library on both loggings |

Net: `1386 + 14 - 1 = 1399`, matching the measured total exactly — no D7 pending-buffer
cascade, no dropped-whole-run interaction between the two moved labels (each delta bout
carries exactly one reclassified event, confirmed by isolating `kind_of_entry`'s per-pair
output on both libraries before touching the compiler at all). `inferred_actions` moves by
one more, 320 -> 319: ONE of the 14 `Roll` bouts (actor Dayton Fix, 29:00) previously needed a
generic bridge to cross the gap the dropped `transparent` event left; now "Roll" fills that gap
itself as a real action, so the bridge is no longer inferred there. The other 13 `Roll` bouts
already had a real action/state adjacent, so dropping vs. keeping "Roll" made no difference to
inference in those. Both numbers, plus the mock-bundle's unrelated `observed 22 -> 23`
(`test_p1_observed_actions_are_the_invariant...`, the new "Takedown Defense" curated entry
resolving the mock's "Sprawl" event — already accepted in `actions_parity_golden.json`, this
hardcoded assertion was the one place left stale), are pinned with dated accounting comments
in `tests/test_actions_parity.py` / `tests/test_path_metrics.py`.

No golden regeneration was needed beyond what the prior session already did — none of
`export_{taxonomy_kind,actions_parity,map_aggregate,chain_compiler,path_bundling,
path_metrics,flow_layout}_fixtures.py` read `kind_of_entry`'s runtime exclusion (they build
off static library/table data, not the private corpus dump); all report `--check` clean, and
`tests/test_cross_repo_fixtures.py` is green.

Next step for N1: give "Hooks In" its own resolvable identity that doesn't force a `type`
choice (same shape as the `back take` bridge in `_LIBRARY_VARIANTS_THAT_ARE_ACTIONS`), at which
point `OPEN_DUAL_IDENTITY` loses this entry.

## Prod seed command (orchestrator runs it, prod DB write)

The 3 additive entries (Electric Chair, Leg Hug, Shoulder Crunch) need `technique_nodes` rows
on prod so Supabase-side graph sync/pro-graph publishing sees them. `scripts/seed_technique_
nodes.py` reads `data/processed/technique_library.json` (a SEPARATE, generated file —
`export/tech_library.py`'s pipeline output, itself built from `analysis/data/technique_
library.json` via `load_curated_library` plus the Kaggle/ADCC datasets), not the curated file
directly — regenerate that first:

```bash
uv run python -m export.tech_library          # regenerates data/processed/technique_library.json
                                               # from the curated file edited in this task
uv run python scripts/seed_technique_nodes.py --dry-run   # verify the diff first
uv run python scripts/seed_technique_nodes.py             # writes technique_nodes on prod
```
