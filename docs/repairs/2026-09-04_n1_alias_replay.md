# N1 alias merge + library split — replay runbook (owner/orchestrator-runnable)

Code is done and merged into `map/actions-paths` (this file, `analysis/names.py`,
`analysis/data/technique_library.json`, goldens). Nothing below has been run — every command
here writes to prod. Read-only measurement that justified it is in "Impact, measured" below.

## What moved

**`analysis/names.SYNONYMS`** (7 new entries, all `node_key`-level merges — every one requires
a full replay because `computed_elo`/`graph_edges.elo`/`graphs.user_elo`/`athletes.elo`/
`elo_series` are keyed by `node_key`):

| old | → target | corpus events (old / target) |
|---|---|---|
| `close guard` | `closed guard` | 1 / 145 |
| `take down` | `takedown` | 3 / 131 |
| `snap down` | `snapdown` | 40 / 47 |
| `shin on shin guard` | `shin to shin guard` | 1 / 12 |
| `nearfall` | `near fall` | 2 / 5 |
| `north south` | `northsouth position` | 3 / 104 |
| `north south control` | `northsouth position` | 13 / 104 |
| `north south pass` | `northsouth pass` | 0 / 1 (forward-compat, spelling not seen yet) |
| `leg lock entanglement` | `leg entanglement` | 1 / 35 |

**Reviewed, not merged** (documented in `analysis/names.py`, no code change):
- `kimura grip` (4 events, `control`) vs `kimura trap` (3 events, mostly `submission`) — the
  `alias_candidates` family still flags this pair (edit distance 2), but the type split says
  different techniques.
- `Leg Entry (50/50)` (1 event) already resolves to `5050 guard` via a pre-existing
  `"leg entry 5050"` entry, not to `leg entry` or `backside 50/50 entry` — ambiguous at n=1,
  left as-is rather than re-litigated.

**`analysis/data/technique_library.json`** (curated mirror, consumed by `technique_match.
clean_label` / `grappling_map` / `next_moves*` / `refine_pbp` / `vocabulary_review` /
`link_graph_node_canonicals` — NOT the same file as `data/processed/technique_library.json`,
see "Two libraries" below):
- `Back Control` variants: dropped `"saddle"` and `"back take"`.
- New entry `Back Take` (`type: transition`, pt "Pegada de Costas", variants `["take the
  back", "back-take"]`) — was silently losing the `_resolution_index` collision to `Back
  Control` before this (alphabetical first-match rule), which is the exact bug
  `docs/taxonomy/04_ONTOLOGIA_CANONICA.md` §3.1 names as the temporary
  `_LIBRARY_VARIANTS_THAT_ARE_ACTIONS = {"back take"}` bridge — that bridge can be deleted
  once this ships (not done here — `taxonomy_kind.py` untouched by this task).
- `Saddle (Inside Sankaku)` already carried `"saddle"` as a variant; no change needed there.

**Goldens regenerated** (`scripts/export_*_fixtures.py`, write mode then `--check`, all green):
only `data/rating/taxonomy_kind_golden.json` (Analytics) and
`GrapplingArcApp/src/services/__fixtures__/taxonomyKindGolden.json` (App) moved — 2 probes
(`guard|close guard`, `control|north south control`) flip `source: derived → declared`, value
unchanged (`bottom`/`top`). `node_key`, `chain_compiler`, `actions_parity`, `map_aggregate`,
`path_bundling`, `path_metrics`, `flow_layout`, `markov_weight`, `glicko2`, `ring_layout`,
`constellation` goldens: byte-identical, nothing to commit.
`tests/test_orientation_for_inference_covers_every_curated_label` updated (52 → 50 blind
labels — the same two probes stop being blind before the three-level reading even runs).

**`data/taxonomy/audit_baseline.json`**: `alias_candidates` 6 → 1 (only kimura grip/trap left),
`states_without_orientation` 59 → 53 (the 10 merged states inherit whichever side already had
a curated orientation). Written by `scripts.audit_ontology --write-baseline`, never by hand.
`--check` rc=0.

## Two libraries — don't conflate them

- `analysis/data/technique_library.json` (edited here) — Analytics-internal, used at
  ingestion/matching time. No App counterpart file; the App mirror is `GrapplingArcApp/src/
  data/grappling-arch.nodes.json`, a SEPARATE file this task deliberately did not touch.
- `data/processed/technique_library.json` (regenerated here via `export.tech_library`, output
  only, gitignored) — built from the Kaggle dataset + ADCC history + the APP's node file, feeds
  `scripts/seed_technique_nodes.py` → prod `technique_nodes` table. It reads the App's node
  file, not the file this task edited, so it still encodes the OLD `Back Control` (with
  `saddle`/`back take` variants) until the App side is fixed separately.

`scripts/sync_app_artifacts` (docstring-verified, not run to completion here — `load_fresh_
scores` recomputes node scores over the whole corpus and exceeded a 180s budget, orthogonal to
this task) only (a) byte-copies markov weights, (b) byte-copies `ontology_seed.json`, (c)
injects `rrb`/`eloPercentile` onto EXISTING App node entries by normalized name. It never adds/
removes/restructures node entries — it will **not** carry the Back Take split or the Saddle
variant removal to the App. That's a separate, App-side edit to `grappling-arch.nodes.json`
(same three edits as above), out of this Analytics-only task's scope.

## Impact, measured (read-only, prod, 2026-09-04)

1340 athlete graphs total (`owner_kind='athlete'`). Every one needs the full replay regardless
(node-key merges move `computed_elo` globally) — the counts below are graphs where the OLD key
literally collides with an existing node in the SAME graph (the rest just gain a rename):

| merge | graphs w/ old key | graphs w/ target key already | union |
|---|---|---|---|
| close guard → closed guard | 1 | 89 | 90 |
| take down → takedown | 3 | 91 | 93 |
| snap down → snapdown | 20 | 40 | 58 |
| shin on shin guard → shin to shin guard | 1 | 10 | 11 |
| nearfall → near fall | 2 | 5 | 7 |
| north south → northsouth position | 3 | 43 | 46 |
| north south control → northsouth position | 6 | 43 | 47 |
| north south pass → northsouth pass | 0 | 1 | 1 |
| leg lock entanglement → leg entanglement | 1 | 29 | 29 |

## Runbook — order, all writes prod

```bash
cd GrapplingArcAnalytics
set -a; source .env; set +a

# 0. Seed the curated library into technique_nodes (upsert by node_key, never deletes).
#    Uses data/processed/technique_library.json (export.tech_library's output, already
#    regenerated in this task) — the App-side Back Take split needs its own App-repo edit
#    to grappling-arch.nodes.json BEFORE this step for the new node to seed with the right
#    type; without it, "back take" still upserts as whatever the App's node file says today.
uv run python scripts/seed_technique_nodes.py --dry-run
uv run python scripts/seed_technique_nodes.py

# 1. Full replay — same runbook as docs/rating_v2/08_ESTADO_DO_CUTOVER.md Fase 8, this alias
#    batch rides the SAME replay (don't run it twice for two reasons):
uv run python -m analysis.rating_v2.replay                # coverage/summary, no write
uv run python -m analysis.rating_v2.replay --persist       # -> new run_id
#    edit analysis/rating_v2/config.py: SITE_RATING_RUN_ID = "<run_id>"
uv run python -m scripts.backfill_edge_bouts --dry-run
uv run python -m scripts.backfill_edge_bouts               # ~1340 athletes, SAVEPOINT each

# 2. Baselines that depend on computed_elo, in order.
uv run python -m analysis.archetype
uv run python -m scripts.assign_user_archetypes
uv run python -m export.ontology

# 3. Site regen (GrapplingArc repo, main) — ~10-12min, N+1 known.
uv run python -m export.site_data --full
#    commit + push GrapplingArc/main — GitHub Pages publishes on push.
```

## Checks after, before committing the site

- `uv run python -m scripts.audit_ontology --check` — rc=0, `alias_candidates` stays at 1.
- No `node_key` in `graph_nodes` still reading an old alias:
  ```sql
  SELECT node_key, count(*) FROM graph_nodes
  WHERE node_key IN ('close guard','take down','snap down','shin on shin guard','nearfall',
                      'north south','north south control','leg lock entanglement')
  GROUP BY node_key;   -- expect 0 rows
  ```
- `athletes.elo` — no athlete jumps more than one rating tier (`analysis/rating_v2/
  presentation.py` bands) versus their pre-replay value, EXCEPT the union-column athletes in
  the impact table above (their node count changed, some tier movement there is expected —
  the merge is a real information change for graphs that had duplicate nodes).
- goldens still green: `uv run pytest tests/test_taxonomy_kind.py tests/test_cross_repo_
  fixtures.py -q` and `for f in scripts/export_*_fixtures.py; do uv run python -m
  "scripts.$(basename "$f" .py)" --check; done`.
