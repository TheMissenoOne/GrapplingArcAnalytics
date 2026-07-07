# I — Directed Graphs + Stats Visualization + Match Review Mode

> **Status: refined against the live code (2026-07).** The first draft got several
> renderer facts wrong; corrections are inline and flagged `CORRECTION:`. Ponytail
> cuts (deferred / dropped scope) are flagged `DEFER:` / `CUT:` with a one-line reason.
> This is a design doc, not a spec — the kanban cards (012–017) are the shippable units.

## 1. Current State

All three layers store **directed edges** (`source→target` / `from→to`) but render them
**undirected** (straight line, no arrowhead). The direction is already in the data everywhere —
only the render throws it away.

| Layer | Renderer (real file) | Tech | Edge shape | Node shape | Zoom range |
|-------|----------------------|------|------------|------------|------------|
| Analytics export | `export/site_data.py:_to_graphview` | Python | `{from, to, fighter, weight}` | `{id, label, cat, size(1-3), fighter}` | N/A (data) |
| Site | `GrapplingArc/site/graph.js` — `GAGraph.mount` | Canvas 2D, custom force-sim | `{from, to, fighter?(a/b/x), weight?(1-3)}` | `{id, label, cat, size?(1-3), fighter?, color?}` | `[0.25, 4]` (wheel + pinch) |
| Admin | `admin/static/graphview.js` (single `<svg id="athlete-graph">`) | SVG, Fruchterman–Reingold | `{source, target, data:{elo}}` | `{id, label, data:{type, usageCount, computedElo}}` | **`[0.3, 6]` — already has wheel-zoom + drag-pan + node-drag** |
| App | `GrapplingArcApp/.../TreeGraph.tsx` → `GraphRenderer.tsx` → `EdgeRenderer.tsx` | d3-force + react-native-svg + reanimated | `{id, source, target, data:{elo, type, actor}}` | `{id, x, y, data:{label, type, elo, computedElo}}` | `[0.25, 6]` (pinch) |

**CORRECTION vs first draft:**
- The admin `graphview.js` is **not** feature-less. It already implements wheel-zoom (`[0.3, 6]`),
  drag-to-pan, and node-drag inside a `<g id="graph-root">` transform. The only thing it lacks for
  this epic is **arrowheads**. "Add zoom + pan to graphview.js" was a phantom task — drop it.
- The **admin/app graphs are single-actor**. Admin renders one athlete's career graph; the app
  renders the user's own game (or one pro athlete's). There is **no fighter `a`/`b`/`x`** in those
  two — that palette exists **only** on the site (two-sided match/dossier graphs). Any "toggle
  fighter A/B" idea does not apply to admin or app.
- Site node objects carry **no stats** — `_to_graphview` emits only `{id, label, cat, size, fighter}`
  (`size` = `usageCount` clamped to 1–3). `computedElo`/`PageRank`/`trend` are **not** on the node.
  Surfacing them on the site graph = a data-shape change in `site_data.py` **and** a read in
  `graph.js` = a two-repo contract (see §4).

Stats that exist but are not on the graph node payload:
- Node (server-side, e.g. `analysis/network_metrics`, `ocean.py`): usageCount, computedElo, PageRank,
  centrality, bridging. On the site these already surface in **the Ocean sidebar**, not on the node.
- Edge: `elo`, `weight`/`count`, setup string.

---

## 2. Directed Edge Rendering

**Decision: arrowheads only.** One direction cue, drawn at the target end. Best
recognition-to-compute ratio and no per-zoom mode juggling.

### Site (Canvas) — `graph.js`
In the existing link loop (`draw()`), after `moveTo/lineTo`, draw a filled triangle at the target:
```
v = target - source (normalized); tip = target - v * (target.r + 2)
base = tip - v * arrowSize; two base corners = base ± perp(v) * arrowSize*0.6
ctx.fill() the triangle in the same stroke color
```
- `arrowSize` ~6–10px, scaled by `weight`. Colors reuse `FIG[a/b/x]` (already the link color).
- Reciprocal pairs (A→B **and** B→A) overlap as straight lines → offset both to a shallow quadratic
  bezier so the two arrows are visible. Offset ∝ node distance; keep it small.

### App (SVG) — `EdgeRenderer.tsx` + `GraphRenderer.tsx`
- Add one `<Defs><Marker>` block in `GraphRenderer.tsx` (it owns the `<Svg>`), `markerEnd` on each
  `<AnimatedLine>` in `EdgeRenderer.tsx`.
- **CORRECTION:** the app has no fighter colors. Edges are one belt-themed accent with opacity/width
  by `edge.data.type` (`weak_link`/`sequence`/other). So **one** marker (accent-colored), not
  "one per fighter a/b/x". `refX` offset by node radius so the arrow clears the node.
- **Open risk (keep):** `react-native-svg` `<Marker>` + `AnimatedLine` (reanimated animated endpoints)
  may not repaint the marker orientation on the UI thread. Verify on device; fallback is a small
  `AnimatedPolygon` positioned at the target end.

### CUT
- **Tapered lines (old Option B):** a second, weaker direction encoding for a "medium zoom" band.
  Two cues for one fact. Arrowheads alone read fine. Dropped.
- **Animated particles (old Option C):** the first draft already called it "expensive, hero delight
  only." YAGNI. Dropped.

---

## 3. Zoom Behavior + the real perf win

**CORRECTION on the perf premise.** The first draft claimed the big win is "skip the edge loop at
low zoom." The actual hot path is different:

- **Site (`graph.js`):** `draw()` runs **every rAF frame while the canvas is on-screen**, redrawing
  all nodes + links — *even after the physics has frozen* (`alpha=0`, `step()` early-returns but
  `draw()` still runs). Off-screen graphs are already paused by an `IntersectionObserver`. So the
  cheapest, broadest win is a **dirty-flag: skip `draw()` when settled and nothing changed**
  (no `camTo`, no hover change, `alpha < ALPHA_MIN`). That saves redraw cost at *every* zoom, not
  just when zoomed out. Skipping edges at low zoom is a marginal add on top.
- The site **already** declutters labels by zoom (`mapLabel = cam.k >= 1 || size>=2 || inFocus`).
  A separate three-band scheme is redundant with that.
- **App:** there is **no per-frame canvas redraw** — it's reanimated shared values + SVG elements,
  updated on gesture/sim only. `GraphRenderer` already **viewport-culls** nodes/edges above 120
  nodes (`cullVisibleNodes`/`cullVisibleEdges`). So "skip edges at low zoom for fps" is largely
  already handled by culling; don't re-implement it.

**Kept, simplified — two states, not three bands:**
- Site: draw arrowheads only when zoomed in enough to read them (reuse the existing `cam.k` threshold
  used for labels, e.g. arrows when `cam.k >= 1`). Below that, plain lines. Plus the dirty-flag above.
- CUT the "constellation super-node" band entirely (it was tangled with the deferred taxonomy work, §9).

---

## 4. Visual Node/Edge Stats

**CORRECTION / ponytail:** most of this already exists and is in the **wrong layer to duplicate**.

- The site's **Ocean sidebar** already renders frequency, centrality, bridging, favorability, and
  effectiveness bars on node select. An on-canvas hover tooltip repeating those = duplicate work.
- The site node is **already sized** by `size` (usageCount 1–3) via the node radius `5 + size*4`.
- The **app already sizes nodes by a blend of ELO + degree centrality** (`enrichedNodes`, multiplier
  0.88–1.12) and does so *deliberately subtly* — the code comment says "calm even constellation
  rather than a bubble chart." Driving app node size hard off usageCount **fights an explicit design
  decision.** Don't.

**What's actually worth adding (small):**
- Site: a **trend dot** (rising/falling/stable → green/red/gray) at the node center. But `trend` is
  not currently on the emitted node payload → needs a `site_data.py` field add + `graph.js` read.
  **This is a two-repo site-bundle contract change** (Analytics `site_data.py` ↔ public `graph.js`).
  Given the sidebar already carries the numbers, this is **low value — mark it optional** inside the
  site-arrows card, not its own card.

### DEFER
- App edge-tap → stats bottom-sheet: node stats already reachable via long-press `NodeModal`; edge
  stats are low-demand. Defer until asked.
- Concentric rings / multi-metric badge stacks: over-designed for a node that's ~10px. Dropped.

---

## 5. Match Review Mode (Analytics Admin)

**CORRECTION / ponytail — most of the machinery already exists; scope this DOWN.**

What's already live:
- `export/match_breakdown.py:build_match_breakdown(match, session)` → timeline + graph + stat cards.
- `export/narrative.py:match_narrative(bd)` → **`list[Section]`** (heading + paragraphs), *not* a
  markdown string. Render server-side.
- `export/athlete_graph_export.py:athlete_graph_to_app_json(graph_id, session)` — **takes `graph_id`,
  not `athlete_id`** (first draft called it with `match.athlete_a_id` — wrong).
- A **per-match edit page already exists**: `GET/POST /admin/athletes/{athlete_id}/matches/{match_id}/edit`
  + `admin/templates/edit_match.html`, plus `approve`/`delete` routes.

So a full new three-column app with its own routes, `contenteditable` prose, a `narrative_overrides`
JSON **schema column**, and a reprocess/re-export endpoint is a big lift that mostly re-treads
existing surface. **Scope v1 to read-only enrichment:**

**Card 016 (v1):**
1. `GET /admin/matches` — a paginated matches list (event, fighters, year, win_type, status, seq_len,
   link to review). Net-new but small; reuses `Match` model + `base.html`.
2. On the **existing** `edit_match.html` (or a sibling `match_review.html` extending `base.html`),
   add two **read-only** panes beside the current event editor:
   - the breakdown graph via the **upgraded `graphview.js`** (arrowheads from Card 015), and
   - the **rendered `match_narrative` sections** (server-rendered HTML, no client markdown lib).

**DEFER (explicit, one line each):**
- Inline `contenteditable` prose editing + `match.narrative_overrides` JSON column — new schema +
  merge-conflict handling for a workflow nobody's asked to run yet. Regenerate prose from data instead.
- "Reprocess + re-export" button — the CLI `uv run python -m export.site_data` already does the
  re-export after any DB edit (the documented publish flow). A button is convenience, not need.
- Drag-reorder events / add-event-from-library in the review page — the edit page already edits the
  sequence. Don't duplicate the editor.
- Iframing GAGraph from the site into admin — nice someday; for v1 the upgraded `graphview.js` is enough.

---

## 6. Attempt Node Canonicalization (real quick win — §8 renamed)

### Problem
`technique_nodes` may hold rows whose label contains "Attempt" (e.g. "Heel Hook Attempt"), created
before label-canonicalization was applied to a given ingest path. They show up as duplicate nodes in
athlete graphs and the site export.

**CORRECTION on the mechanism:**
- The guard that canonicalizes labels is **`clean_label` / `clean_sequence` in
  `analysis/technique_match.py`** (matches against the library + variant aliases, rejects
  cross-type matches). It is **not** `_clean_events`.
- `_clean_events` is a **different** function in `scripts/insert_ufc_matches.py` — it drops
  `strike`/`reset`/`referee` events and resolves actors to sides. It does **not** touch "attempt"
  labels. The first draft conflated the two.
- There is **no `canonicalize.py`** in the repo. The `--check` verifier is net-new (put it in the
  merge script).

### Preflight (do this before writing the script)
Confirm attempt nodes actually exist in prod via a **read-only** query (orchestrator / db-prober):
`SELECT node_key, label FROM technique_nodes WHERE label ~* '\battempt(s|ed|ing)?\b';`
If the set is empty, this card is a no-op — close it. (Ponytail: don't build a migration for a
problem you haven't confirmed exists.)

### Fix — one-time script `scripts/merge_attempt_nodes.py`
```
For each TechniqueNode whose label matches /\battempt(s|ed|ing)?\b/:
  1. canonical_label = clean_label(label)  # strips/aliases "attempt" via the library
  2. canonical = TechniqueNode where node_key == _normalize_name(canonical_label)
       - upsert from the library if missing (source='library')
  3. remap graph_edges: source_key/target_key/edge_key attempt_key → canonical_key (dedupe collisions)
  4. remap map_edges the same way (source_key/target_key)
  5. delete the attempt TechniqueNode
  6. `--check` mode: assert zero attempt-labelled node_keys remain; exit non-zero otherwise
```
- **Do NOT run against prod** — the script is the deliverable; the orchestrator runs it after review
  (per workspace rules). Test on a synthetic session (fixture DB), not the live one.
- Prereq for accurate exports and for anything that reads per-node stats (§4) and taxonomy (§7) —
  so cards 017 depend on this.

---

## 7. Taxonomy-Driven Readability — **DEFERRED**

**Ponytail verdict: defer the whole render side; the data it needs does not exist.**

- `docs/taxonomy.json` and `docs/TAXONOMY_PLAN.md` **exist but are git-ignored / do-not-commit**
  (root CLAUDE.md). They carry category→subcategory but **no technique→subcategory mapping**.
- `technique_nodes` has **no `taxonomy_id` column** today. Adding it is:
  - an **alembic migration** on `technique_nodes` (Analytics),
  - a change to the node payload in `athlete_graph_export.py` / `site_data.py` (export),
  - and a read in the app `TreeGraph`/site `graph.js` if used for coloring/filtering.
  That is a **schema + export contract change = TWO PRs** (Analytics DB/export ↔ App), for a feature
  whose *source data (the mapping) isn't populated.*

**CUT outright (speculative, high effort, low confirmed value):**
- Subcategory super-node aggregation + convex-hull cluster rendering at low zoom.
- Two-axis hue/saturation/border palette. The current 8-type `node_type` coloring already works and
  needs no `taxonomy_id`.
- Embedding-similarity **auto-classify** of techniques into subcategories — a whole ML step to
  generate data of unproven need.

**Kept as a single P3, blocked-on-data card (017):** add the `taxonomy_id` column + carry it through
export, *only once the technique→subcategory mapping is actually authored*. No render work depends on
it until then. If the mapping never lands, this card stays parked — that's the correct outcome.

---

## 8. Cross-Module Contract Implications (read before coding)

| Change | Repos touched | PRs |
|--------|---------------|-----|
| Site arrowheads (`graph.js`) | GrapplingArc (public) only — direction already in `from/to` | 1 |
| App arrowheads (`EdgeRenderer`/`GraphRenderer`) | GrapplingArcApp only — no new node/edge fields | 1 |
| Admin arrowheads (`graphview.js`) | GrapplingArcAnalytics only | 1 |
| Admin match list + read-only review | GrapplingArcAnalytics only (`admin/server.py` + templates) | 1 |
| Surface node `trend` on the site graph | Analytics `site_data.py` node payload ↔ public `graph.js` read | **2** (site-bundle contract) |
| Merge attempt nodes | GrapplingArcAnalytics only (script + DB) | 1 (+ orchestrator runs it) |
| `taxonomy_id` column | Analytics migration + export ↔ App read | **2** (schema + export contract) |

The three arrowhead cards are contract-free and can ship independently in parallel. Anything that
adds a **field to a node payload** (trend, taxonomy_id) is a two-side contract — name both sides in
the PR and split per the table.

---

## 9. Implementation Order (maps to kanban 012–017)

1. **012** — merge attempt nodes (Analytics script). Preflight-gated; prereq for clean exports/stats.
2. **013** — site directed edges: arrowheads + reciprocal-edge curve + the settled-frame dirty-flag
   (the real perf win). Optional stretch: trend dot (needs the 2-repo payload add — split out if taken).
3. **014** — app directed edges: SVG marker defs + `markerEnd`; verify reanimated-marker repaint.
4. **015** — admin `graphview.js` arrowheads (it already has zoom/pan/drag).
5. **016** — admin `/admin/matches` list + read-only review pane (reuses 015's graph + `match_narrative`).
6. **017** — `taxonomy_id` column + export carry-through. **P3, blocked on the technique→subcategory
   mapping.** No render work depends on it; parked until the data exists.

## 10. Open Questions (trimmed)

- **Reanimated + SVG `<Marker>`:** does the arrowhead reorient on the UI thread as endpoints animate?
  Verify on device (Card 014). Fallback: `AnimatedPolygon` at the target end.
- **Reciprocal-edge curve offset:** proportional to node distance; tune the constant against the
  densest real dossier graph (Card 013).
- **Attempt nodes — do any exist in prod?** Read-only query first (Card 012 preflight). If none, close.
</content>
</invoke>
