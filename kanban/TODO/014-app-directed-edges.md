---
id: "014"
slug: app-directed-edges
phase: 6
lane: G
priority: P1
status: todo
depends: []
branch: feature/014-app-directed-edges
created: 2026-07-07
tags: [kanban, phase-6, P1, app]
---

# 014 — App TreeGraph Directed Edges (SVG arrowheads)

## Goal
The app graph (`GrapplingArcApp/src/components/dataVisualization/`) draws a directional arrowhead at
each edge's target end, verified to repaint correctly with animated (reanimated) endpoints.

## Context
See `docs/deepseek/I-directed-graphs-and-review-mode.md` §2. Edges are already directed
(`{source, target}`). The app graph is **single-actor** (user's own game / one pro athlete) — there
is **no fighter a/b/x palette**; edges are one belt-themed accent whose opacity/width encode
`edge.data.type` (`weak_link`/`sequence`/other). So **one** accent-colored marker, not one per
fighter. **Different repo (GrapplingArcApp), disjoint from site/admin.** No new node/edge fields →
single PR, no cross-module contract.

## Execution Plan
1. `GraphRenderer.tsx` (owns the `<Svg>`): add a `<Defs>` block with one `<Marker id="edge-arrow"
   orient="auto" markerWidth/Height ...>` containing a `<Path>` triangle in the belt accent. `refX`
   offset so the arrow clears the target node radius.
2. `EdgeRenderer.tsx`: set `markerEnd="url(#edge-arrow)"` on the `<AnimatedLine>`. Keep the existing
   type-based color/width/dash. (Dashed `weak_link` edges may omit the arrow — decide during QA.)
3. **Verify reanimated compatibility on device:** confirm the marker reorients as endpoints animate
   during pan/pinch and sim settle. If `<Marker>` + `AnimatedLine` doesn't repaint on the UI thread,
   fall back to a small `AnimatedPolygon`/`AnimatedPath` positioned + rotated at the target end
   (compute in the same worklet that reads `positionsShared`).
4. Optional (only if the primary path works cleanly): shallow curve for reciprocal pairs. Defer if it
   complicates the marker path.
5. `npm test` stays green (no logic change to graph building); manual QA on NetworkScreen.

## Acceptance Criteria
- [ ] Arrowheads render at target ends on the app graph, accent-colored
- [ ] Arrows reorient correctly during pan/pinch and after sim settle (device-verified)
- [ ] Fallback path documented/used if `<Marker>` + `AnimatedLine` fails to repaint
- [ ] No new node/edge data fields; `npm test` green

## Test Plan
Manual device/emulator QA on NetworkScreen: arrows follow `source→target`, survive pan/pinch and the
settle animation, and match a known sequence direction. Existing Jest suites unchanged.
</content>
