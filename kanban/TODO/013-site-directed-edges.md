---
id: "013"
slug: site-directed-edges
phase: 6
lane: F
priority: P1
status: todo
depends: []
branch: feature/013-site-directed-edges
created: 2026-07-07
tags: [kanban, phase-6, P1, site]
---

# 013 — Site GAGraph Directed Edges + Settled-Frame Redraw Skip

> **2026-07-08 update (supersedes the direction/dash part below):** implemented per the
> user-approved data-directed-edges design, which changed two things vs. the original plan:
> - **No split / no curve.** A reciprocal pair collapses to ONE link. A genuine two-way
>   exchange (`m >= two_way_ratio*M`, `two_way_ratio=0.34`) → `arrow:false`, undirected —
>   never two arrows, no bezier offset.
> - **This IS a two-repo contract change for the aggregate slice** (ocean + career dossiers):
>   Analytics now emits `arrow`/`dashed` booleans per link (`ocean_from_map` in
>   `analysis/ocean.py`; `_career_graphview`/`_direct_career_links` in `export/site_data.py`),
>   `graph.js` consumes them. Breakdown links (`_to_graphview`) stayed a single-repo concern as
>   originally planned — always `arrow:true, dashed:false` (timeline fact, no gating).
> - **New: dashed = low-success, data-driven.** A corpus-wide bottom-quartile-success gate
>   (target-type gated, weight-floored, `analysis/network_metrics.success_threshold`) marks an
>   edge `dashed:true`; `graph.js` draws it `[5,5]` (distinct from the existing `[3,4]`
>   "contested handover" dash).
> - Item 4 (settled-frame redraw-skip) is UNTOUCHED by this update — still open.

## Goal
`GrapplingArc/site/graph.js` renders arrowheads at each edge's target end (with a small curve for
reciprocal pairs) and stops redrawing the canvas when the layout is settled and nothing changed.

## Context
See `docs/deepseek/I-directed-graphs-and-review-mode.md` §2–§3. Edges are already directed in the
data (`{from, to}`); only the render is undirected. `draw()` currently runs every rAF frame even
after physics freezes — a dirty-flag skip is the broad perf win. **Different repo (GrapplingArc
public site), so disjoint from the app/admin cards** — this PR lands in the GrapplingArc repo.
Direction is already in `from/to`, so **no data-shape change, single PR.** (Optional trend-dot
stretch would add a node field = a two-repo site-bundle contract — split it out if taken.)

## Execution Plan
1. In `draw()`'s link loop, after the line stroke, draw a filled triangle at the target: unit vector
   `target-source`, tip clamped to `target.r + 2` off the node, base back by `arrowSize` (~6–10px,
   scaled by `weight`), corners ± perpendicular. Fill in the link color (`FIG[...]` / `#3a3a45`).
2. Draw arrowheads only when readable — reuse the existing label zoom gate (`cam.k >= 1`); below
   that, plain lines.
3. Reciprocal pairs (both `from→to` and `to→from` exist): offset each to a shallow quadratic bezier
   so the two arrows don't overlap. Offset ∝ node distance; tune against the densest dossier graph.
4. **Dirty-flag:** in `loop()`, skip `draw()` when `alpha < ALPHA_MIN` AND no `camTo` AND hover/selected
   unchanged since last frame AND no active pointer. Reset the flag on hover/select/resize/pinch/wheel/pan.
   Keep the rAF alive (cheap) so interaction still wakes it.
5. Manual QA on a live dossier + a breakdown page: arrows point source→target, reciprocal pairs
   readable, CPU drops to ~idle once settled (check devtools performance), interaction still smooth.

## Acceptance Criteria
- [ ] Arrowheads render at the target end, colored by the existing link palette
- [ ] Arrows appear only when zoomed in (`cam.k >= 1`); plain lines below
- [ ] Reciprocal A↔B pairs shown as two distinguishable curved arrows
- [ ] `draw()` skipped when settled + idle; interaction re-wakes it
- [ ] No change to the `{nodes,links}` data shape (single-repo PR)

## Test Plan
No unit harness for canvas — manual QA on a generated `breakdown-*.html` and a `grapple-*.html`.
Verify against a known bout that arrow directions match the sequence (e.g. guard-pull → pass).
Confirm settled CPU via browser performance panel before/after.
</content>
