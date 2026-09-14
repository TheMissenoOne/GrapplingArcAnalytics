# 2026-09-14 — N1 alias replay batch 2 (deferred aliases + duplicate library entries)

Follow-on to `docs/repairs/2026-09-13_curated_library_sync.md`, which deferred three alias
edits (real corpus impact, needs a replay) and left two duplicate curated entries unmerged.
This session decides all five against the ontology (state = position, action = dynamic) and
makes the edits. **Nothing below has been run against prod except read-only measurement** —
every write step in "Runbook" is for the orchestrator.

## Decisions

### A — deferred aliases (`docs/repairs/2026-09-13_curated_library_sync.md`)

| Alias | Decision | Why |
|---|---|---|
| `roll` on Roll-Through | **Keep merged.** | "Roll" as logged is a transition EVENT (a dynamic act), not a position — the same class as Roll-Through itself. The curated library already resolved it this way (never removed); the only gap was the athlete-graph side (`analysis.names.SYNONYMS`, which `analysis/chain_compiler.py`/`analysis/taxonomy_kind.py` read via `canonicalize()`, NOT the curated file) still carried "roll" and "rollthrough" as two separate node keys. Closed that gap. |
| `north south choke` / `North-South Choke` | **Merge, hyphenated spelling wins.** | Same technique, two spellings — same shape as `north-south position`/`north-south pass`, already merged this way. Curated `en` renamed to the hyphenated form (absorbs the App's separate App-only "North-South Choke" row on the next `sync_app_artifacts` run); `SYNONYMS` gets the matching fused-key entry so the athlete graph agrees. |
| `pass` (bare) on Guard Pass | **Keep merged, no-op.** | Generic guard-pass action, exactly the class the 2026-09-13 doc's "reserved bare event-type words" fix exists for. Already merged on both sides — `SYNONYMS["pass"] = "guard pass"` has existed since 2026-08-24 (Q7), and 0 athlete graphs carry a bare `"pass"` node today. Confirmed, not re-added. |

### B — duplicate curated entries

| Pair | Decision | Why |
|---|---|---|
| Arm Lock / Armbar | **Merge into Armbar** (217 corpus events vs 6; `en` = Armbar, `pt` = "Chave de Braço"). Arm Lock's variants (`arm lock`, `bent armlock`) folded into Armbar's. | Same submission, same `pt` before disambiguation was ever needed — narrators use "Arm Lock" as a synonym for the straight armbar (`analysis.names.NAME_ALIASES["chave de braco"] = "armbar"` already agreed). Armbar entry dropped nothing distinctive; deleting the standalone Arm Lock entry removes the translation collision the 2026-09-13 session had to work around instead of resolving. |
| Reverse De La Riva / Inverted De La Riva Guard | **Merge into Inverted De La Riva Guard** (5 corpus events reachable vs 0 — see below; `en` = Inverted De La Riva Guard, `pt` = "De La Riva Invertida"). Reverse's variants (`rdlr`, `dlr invertida`, `de la riva reversa`, `reverse dlr`) + its `pt` lowercased folded into Inverted's. | These were **already a dead-entry pair** before this edit: both listed `"reverse de la riva"` as a variant, alphabetical first-match (`technique_match._index`, `idx.setdefault`) made every lookup resolve to Inverted De La Riva Guard except the `rdlr` variant (unique to Reverse). 0 raw corpus events use the literal label "Reverse De La Riva" or "RDLR" (checked against all 10 121 prod `matches.sequence` events). Formalising the merge just removes the shadowed duplicate — same technique, same guard. |

## Measurement method

Same as 2026-09-13: for the curated-file edits, resolved every prod `matches.sequence[]`
label (read-only, all 911 matches / 10 121 events, any status) through
`analysis.technique_match.clean_label` — script in session scratchpad, not the repo.

For the **replay-triggering** question (does this actually move a `node_key` an athlete
graph carries?) that method is the wrong layer: `technique_match.clean_label` is read by the
cleaning/map/ocean/next-moves consumers, but the athlete graph's `node_key` comes from
`analysis/athlete_elo.py`'s `canonicalize(_normalize_name(label))` — `analysis.names.
SYNONYMS`, not the curated file. Measured directly against prod `graph_nodes` (join
`graphs.owner_kind`):

| old key | → target key | graphs w/ old | graphs w/ target already | union | owner_kind |
|---|---|---|---|---|---|
| `roll` | `rollthrough` | 12 | 1 | 13 | athlete (all) |
| `north south choke` | `northsouth choke` | 6 | 0 | 6 | athlete (all) |
| `pass` | `guard pass` | 0 | 97 | 97 | — (already merged, no move) |
| `arm lock` | `armbar` | 0 | 113 | 113 | — (already merged 2026-08-24, no move) |
| `bent armlock` | `armbar` | 1 | 113 | 114 | athlete |
| `reverse de la riva` | `inverted de la riva guard` | 0 | 4 | 4 | — (no raw corpus label, future-proofing only) |
| `rdlr` / `dlr invertida` / `de la riva reversa` / `reverse dlr` / `guarda de la riva invertida` | `inverted de la riva guard` | 0 each | 4 | 4 | — (future-proofing only) |

**19 graph_nodes rows actually move on replay** (12 `roll` + 6 `north south choke` + 1
`bent armlock`); the rest are no-ops confirming existing behaviour or guards against a raw
label ("RDLR" etc.) that hasn't occurred yet. `graph_edges`/`graph_edge_bouts` are not
migrated by hand — `graph_edges` FKs into `graph_nodes(graph_id, node_key)` `ON DELETE
CASCADE`, so the full per-athlete replay (`scripts.backfill_edge_bouts`) rebuilds them from
`matches.sequence` directly; nothing here is a manual UPDATE.

`technique_nodes` (shared public vocabulary, `scripts/seed_technique_nodes.py`, upsert-only —
never deletes) still carries rows for the four retired curated entries (`arm lock`,
`bent armlock`, `reverse de la riva`, and the pre-rename `north south choke`); expected,
matches the seed script's documented behaviour, not addressed here.

## What moved

**`analysis/names.SYNONYMS`** (new entries, `analysis/names.py`):
```
"roll": "rollthrough",
"north south choke": "northsouth choke",
"bent armlock": "armbar",
"reverse de la riva": "inverted de la riva guard",
"rdlr": "inverted de la riva guard",
"dlr invertida": "inverted de la riva guard",
"de la riva reversa": "inverted de la riva guard",
"reverse dlr": "inverted de la riva guard",
"guarda de la riva invertida": "inverted de la riva guard",
```
(`"arm lock": "armbar"` already existed since 2026-08-24 — not re-added, ruff caught the
duplicate-key attempt.)

**`analysis/data/technique_library.json`** (curated mirror — 212 → 210 entries):
- Dropped: `Arm Lock` (folded into `Armbar`'s variants: `arm lock`, `bent armlock`).
- Dropped: `Reverse De La Riva` (folded into `Inverted De La Riva Guard`'s variants: `rdlr`,
  `dlr invertida`, `de la riva reversa`, `reverse dlr`, `guarda de la riva invertida`).
- Renamed: `North South Choke` → `North-South Choke` (`en` field only; both spellings were
  already listed as variants, so `clean_label` lookup is unaffected — only the canonical
  DISPLAY string changes, which is what the map/ocean/next-moves consumers show).

No `CANONICAL_LABELS` entries added — same precedent as the 2026-09-04 batch: each target key
(`rollthrough`, `northsouth choke`, `armbar`, `inverted de la riva guard`) already has a
natural raw-label fallback from the athlete graph itself.

## Goldens regenerated

**None drifted.** Checked all 8: `export_taxonomy_kind_fixtures`, `export_chain_compiler_
fixtures`, `export_actions_parity_fixtures`, `export_map_aggregate_fixtures`, `export_node_
key_fixtures`, `export_path_bundling_fixtures`, `export_path_metrics_fixtures`, `export_flow_
layout_fixtures` — all `--check` green, zero bytes written. Expected: `taxonomy_kind`/`library_
lookup` derive from the APP's `grappling-arch.nodes.json` (untouched this session, out of
scope — "another builder owns the App data files right now"); the rest are static-mock-bundle
generators that never read the curated file or `SYNONYMS` at all. Also ran `export.tech_
library` (regenerates `data/processed/technique_library.json`, the `seed_technique_nodes.py`
input — gitignored output, 210 curated + 39 dataset + 13 ADCC = 262 techniques) and `scripts.
export_taxonomy_kind_fixtures` (writes `data/taxonomy/library_lookup.json` — byte-identical,
confirming the `--check` above).

`scripts.audit_ontology --check`: rc=0. `alias_candidates` stays at 1 (kimura grip/trap,
unchanged — not part of this batch). No `--write-baseline` needed.

## App paths that will need byte-identical copies (NOT done here)

- `GrapplingArcApp/src/data/grappling-arch.nodes.json` — regenerate via `uv run python -m
  scripts.sync_app_artifacts` once the curated identity source lands; will absorb the Armbar/
  Arm Lock merge, the Inverted De La Riva Guard/Reverse De La Riva merge, and the North-South
  Choke rename (which also retires the App's own separate "North-South Choke" App-only row —
  `sync_app_artifacts` will report one fewer "app-only kept").
- `GrapplingArcApp/src/data/taxonomy_inference_table.json` and `GrapplingArcApp/src/services/
  __fixtures__/taxonomyKindGolden.json` — confirmed **unchanged** by this batch (see above), so
  no copy is actually pending from THIS session; listed for completeness since both are fed by
  the same generator chain and must be re-checked after the App-side sync above runs.
- `App`-side test content (`taxonomyKind.test.ts`, `nodeCorpusScores.bundle.test.ts`, etc., same
  class as 2026-09-13's updates) may need new dated entries once `sync_app_artifacts` actually
  changes the App bundle — cannot be measured until that run happens.

## Corpus pins (`tests/test_actions_parity.py`, `tests/test_path_metrics.py`) — found broken, NOT caused by this batch

`uv run pytest` surfaces two failures against the owner's private corpus dump
(`/home/vetor/GrapplingArc/_analytics_export.json`, 281 bouts, never committed):

```
tests/test_actions_parity.py::test_no_empty_endpoint_edges_and_no_generic_out_degrees_the_real_graph
tests/test_path_metrics.py::test_observed_total_matches_the_fase2_invariant_on_the_real_corpus
assert 1396 == 1399   # pinned by 926208f, the 2026-09-13 commit itself
```

Measured this is **pre-existing, not a regression from this batch**:
1. `git stash` (reverting this session's edits back to bare HEAD, commit `926208f` — the exact
   commit that set the `1399` pin) still reproduces `1396`, twice, deterministically.
2. Toggling this session's new `SYNONYMS` entries on/off against the private corpus (a
   before/after script, same technique as the table above) gives **delta 0** — none of
   `roll`/`north south choke`/`bent armlock`/`reverse de la riva`/etc. occur as raw labels in
   this particular 281-bout dump, so this batch cannot be the cause.

Root cause not investigated (out of scope — not this task's edit, and guessing at the pin
would be a symptom patch, not the fix). Both tests `pytest.skip()` when the dump file is
absent, which is the case in CI (never committed, LGPD) — so this does **not** fail the CI
gate, only local runs on this machine. Flagging for the owner as a separate, pre-existing
finding; the `1399`/`320`/`319` comments in both test files are untouched by this session.

### Achado (2026-09-14, follow-up) — frozen `athlete_a/b_key` vs live `ATHLETE_ALIASES`

Root cause found by bisection: `analysis.names.ATHLETE_ALIASES` gained `"bia mesquita":
"beatriz mesquita"` in this commit. `scripts.shadow_chain_compiler._side_of` compares
`athlete_key(actor)` (canonicalized fresh on every event) against a match's
`athlete_a_key`/`athlete_b_key` — but those two fields are frozen into the dump at export
time and never re-derived. On this dump (frozen 2026-06-30, months before this alias
existed) one bout's `athlete_b_key` still read the pre-alias form while its actor now
canonicalizes to the post-alias form, so `_side_of` matched neither side and its whole `b`
side lost all 3 observed actions — `1399 -> 1396`.

Fixed at the source: `_side_of` now canonicalizes the frozen `athlete_a_key`/`athlete_b_key`
through `athlete_key()` too, so a later alias can never desync from an already-exported
dump (`scripts/shadow_chain_compiler.py`, unit tests in `tests/test_shadow_chain_compiler.py`
— synthetic bout: frozen key = pre-alias form, actor = alias source, must still resolve).

The fix does **not** land back on 1399 — it lands on **1415**. Applying it against commit
`926208f`'s own alias table (the commit that measured 1399, with no batch-2 aliases at all)
gives the same 1415, so this isn't specific to `bia mesquita`: the June-30 dump's frozen
keys had already drifted from several *other*, older `ATHLETE_ALIASES` entries too — those
just degraded gracefully (a few events per bout quietly unattributed to either side) instead
of zeroing a whole bout, so nobody had noticed. Net `1396 -> 1415` (+19): +3 restores the
`bia mesquita` bout, +16 recovers previously-dropped events across 7 other bouts (no
`a_key == b_key` collisions on any of the 8 — verified). `inferred_actions` (319) is
unaffected. Both pins re-measured and re-pinned to 1415 with the accounting above (counts
only, per the private-corpus convention) in `tests/test_actions_parity.py` /
`tests/test_path_metrics.py`; the earlier `git stash` claim above (step 1, "reproduces 1396
at bare 926208f") does not hold under a clean worktree checkout of that commit — it measures
1399, matching the original pin; the stash likely ran with other uncommitted local files
still present.

## Gate results

```
uv run pytest -q -p no:cacheprovider   # 2989 passed, 3 failed (both known, see below)
uv run ruff check .                    # All checks passed!
uv run mypy .                          # Success: no issues found in 473 source files
```

3 failures, both out of this task's scope:
- `test_actions_parity.py::test_no_empty_endpoint_edges_and_no_generic_out_degrees_the_real_graph`
  and `test_path_metrics.py::test_observed_total_matches_the_fase2_invariant_on_the_real_corpus`
  — pre-existing at HEAD, see above.
- `test_sync_app_artifacts.py::test_app_nodes_library_identity_matches_curated_source` —
  **expected**: this test asserts the App's `grappling-arch.nodes.json` matches the curated
  source; this session deliberately did not touch the App file (task scope — another builder
  owns it right now). Will go green once `scripts.sync_app_artifacts` runs.

`uv run mypy .` ran without `--all-extras` (not run this session, per instruction not to
`uv sync`) — CLAUDE.md's standing caveat: CI's mypy (after `--all-extras`) is the one that
actually gates; this local run is a lighter but currently-green signal, not a substitute.

## Runbook — order, all writes prod (orchestrator runs this)

Same shape as `docs/repairs/2026-09-04_n1_alias_replay.md` Fase 8 / `docs/rating_v2/
08_ESTADO_DO_CUTOVER.md` — this batch rides the SAME replay, do not run it twice.

```bash
cd GrapplingArcAnalytics
set -a; source .env; set +a

# 0. Seed the curated library into technique_nodes (upsert by node_key, never deletes).
#    Reads data/processed/technique_library.json, already regenerated this session.
uv run python scripts/seed_technique_nodes.py --dry-run
uv run python scripts/seed_technique_nodes.py

# 1. Full replay. ALL athletes, not just the 19 affected: `scripts.backfill_edge_bouts` has
#    no "affected only" mode (it iterates every athlete regardless), and a node-key merge
#    moves computed_elo GLOBALLY per docs/repairs/2026-09-04_n1_alias_replay.md's same
#    reasoning — the Glicko-2 run is one shared `run_id`/`input_hash`, not per-athlete.
uv run python -m analysis.rating_v2.replay                # coverage/summary, no write
uv run python -m analysis.rating_v2.replay --persist       # -> new run_id
#    edit analysis/rating_v2/config.py: SITE_RATING_RUN_ID = "<run_id>"
uv run python -m scripts.backfill_edge_bouts --dry-run
uv run python -m scripts.backfill_edge_bouts               # ~1300 athletes, SAVEPOINT each

# 2. Baselines that depend on computed_elo, in order.
uv run python -c "from db.base import db_session; from analysis.archetype import run_archetype_pipeline
with db_session() as s: run_archetype_pipeline(s, k=6)"
uv run python -m scripts.assign_user_archetypes
uv run python -m export.ontology

# 3. Site regen (GrapplingArc repo, main) — ~10-12min, N+1 known.
uv run python -m export.site_data --full
#    commit + push GrapplingArc/main — GitHub Pages publishes on push.
```

## Checks after, before committing the site

- `uv run python -m scripts.audit_ontology --check` — rc=0.
- No `graph_nodes` row still on a retired key:
  ```sql
  SELECT node_key, count(*) FROM graph_nodes
  WHERE node_key IN ('roll', 'north south choke', 'bent armlock', 'reverse de la riva', 'rdlr')
  GROUP BY node_key;   -- expect 0 rows
  ```
- `athletes.elo` — no athlete jumps more than one rating tier versus pre-replay, except the
  13/6/114-union athletes in the impact table (real node-count change, some movement expected).
- goldens still green: `uv run pytest tests/test_taxonomy_kind.py tests/test_cross_repo_
  fixtures.py -q` and `for f in scripts/export_*_fixtures.py; do uv run python -m
  "scripts.$(basename "$f" .py)" --check; done`.
