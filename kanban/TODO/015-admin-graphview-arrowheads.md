---
id: "015"
slug: admin-graphview-arrowheads
phase: 6
lane: H
priority: P2
status: todo
depends: []
branch: feature/015-admin-graphview-arrowheads
created: 2026-07-07
tags: [kanban, phase-6, P2, admin]
---

# 015 — Admin graphview.js Directed Edges

## Goal
`admin/static/graphview.js` draws SVG arrowheads at each edge's target end. Zoom/pan/drag already
exist and stay untouched.

## Context
See `docs/deepseek/I-directed-graphs-and-review-mode.md` §1–§2. **CORRECTION to the first draft:**
`graphview.js` already has wheel-zoom (`[0.3, 6]`), drag-pan, and node-drag inside `<g id="graph-root">`
— the only missing piece is arrowheads. Single-athlete graph → no fighter palette, no fighter
filter. Analytics-only, disjoint file. Blocks [[016-admin-match-review|card 016]], which embeds this
upgraded graph.

## Execution Plan
1. Add one `<marker id="gv-arrow" orient="auto" markerUnits="userSpaceOnUse" ...>` (a `<path>`
   triangle in the edge stroke color `#3a4250`) to the SVG `<defs>` (create a `<defs>` under `svg`).
   `markerUnits="userSpaceOnUse"` keeps arrow size stable under the `<g>` scale transform; `refX`
   offset by the target node radius so it clears the circle.
2. Set `marker-end="url(#gv-arrow)"` on each edge `<line>` in `edgeEls`. Endpoints already update in
   `redrawNode()` — the marker follows for free.
3. Manual QA in the admin (an athlete detail graph): arrows point source→target, stay clear of nodes,
   size stable across wheel-zoom, still track node-drag.

## Acceptance Criteria
- [ ] Arrowheads at target ends on the admin athlete graph
- [ ] Arrow size stable under zoom; clears the node circle
- [ ] Existing zoom/pan/drag unchanged
- [ ] No change to the `data-graph` JSON shape

## Test Plan
Manual: load an athlete with a known career graph in the admin, confirm arrow directions match the
transitions and behave under zoom + node-drag. No unit harness for the DOM renderer.
</content>
