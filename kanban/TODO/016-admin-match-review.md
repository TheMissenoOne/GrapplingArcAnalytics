---
id: "016"
slug: admin-match-review
phase: 6
lane: H
priority: P2
status: todo
depends: ["[[015-admin-graphview-arrowheads]]"]
branch: feature/016-admin-match-review
created: 2026-07-07
tags: [kanban, phase-6, P2, admin]
---

# 016 — Admin Match List + Read-Only Review Pane

## Goal
`GET /admin/matches` lists all matches (paginated), and a per-match review view shows the breakdown
graph (via the arrowhead-upgraded `graphview.js`) + the rendered `match_narrative` sections beside
the existing event editor — all read-only.

## Context
See `docs/deepseek/I-directed-graphs-and-review-mode.md` §5. **Scoped DOWN from the first draft:**
`build_match_breakdown` (`export/match_breakdown.py`), `match_narrative` → `list[Section]`
(`export/narrative.py`), and a per-match edit page (`/admin/athletes/{aid}/matches/{mid}/edit` +
`admin/templates/edit_match.html`) already exist. This card adds a **matches list** + a **read-only**
graph/prose pane; it does NOT add contenteditable prose, a `narrative_overrides` schema column, event
drag-reorder, or a reprocess endpoint (the `export.site_data` CLI already re-exports). Depends on
[[015-admin-graphview-arrowheads|card 015]] (embeds the upgraded graph). Analytics-only.

## Execution Plan
1. `admin/server.py`: `GET /admin/matches` (auth-gated like the other admin routes) — paginated query
   over the `Match` model (event, fighters, year, win_type, status, seq_len), each row linking to the
   review view. New `admin/templates/matches.html` extending `base.html`.
2. Review view — reuse `edit_match.html` or add `admin/templates/match_review.html` extending
   `base.html`. In the route, call `build_match_breakdown(match, session)` and
   `match_narrative(breakdown)`; render the graph JSON into the `<svg id="athlete-graph" data-graph=...>`
   that `graphview.js` reads, and the `Section` list to server-rendered HTML (heading + paragraphs,
   no client markdown lib). If a graph is needed via `athlete_graph_to_app_json`, pass a **`graph_id`**
   (not an athlete_id).
3. Link in: a "Review" link from `athlete_detail.html`'s match rows and from the new matches list.
4. Manual QA: list paginates; review view shows arrowed graph + prose for a known bout; no write paths.
5. Gates: `uv run pytest && uv run ruff check . && uv run mypy .` (route smoke-testable with the app's
   test client if one exists; otherwise assert the breakdown/narrative calls in a small unit test).

## Acceptance Criteria
- [ ] `GET /admin/matches` paginated list, auth-gated, links to review
- [ ] Review view renders the directed breakdown graph + rendered narrative sections, read-only
- [ ] Uses `build_match_breakdown` + `match_narrative`; `athlete_graph_to_app_json` called with graph_id
- [ ] No new schema, no prose editing, no reprocess endpoint (deferred by design)
- [ ] Gates clean

## Test Plan
Unit: feed a fixture match through `build_match_breakdown` + `match_narrative`, assert the review
context dict has non-empty graph JSON + sections. Manual: paginate the list, open a review page.
</content>
