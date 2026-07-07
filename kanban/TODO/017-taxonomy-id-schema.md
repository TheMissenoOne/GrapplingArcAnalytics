---
id: "017"
slug: taxonomy-id-schema
phase: 6
lane: I
priority: P3
status: todo
depends: ["[[012-merge-attempt-nodes]]"]
branch: feature/017-taxonomy-id-schema
created: 2026-07-07
tags: [kanban, phase-6, P3, schemas]
---

# 017 — taxonomy_id Column + Export Carry-Through (BLOCKED ON DATA)

## Goal
`technique_nodes` gains a nullable `taxonomy_id`, and the export node payload carries it — **only
once a technique→subcategory mapping actually exists**. This is the minimal data step; all taxonomy
*render* work (super-nodes, hulls, two-axis palette, auto-classify) is CUT per the design doc.

## Context
See `docs/deepseek/I-directed-graphs-and-review-mode.md` §7 (deferred). **Blocked:** `docs/taxonomy.json`
exists but is git-ignored / do-not-commit (root CLAUDE.md) and has categories/subcategories but **no
technique→subcategory mapping** — the source data this column would hold does not exist yet. This is
a **schema + export contract change = TWO PRs**: (1) Analytics — alembic migration on `technique_nodes`
+ carry `taxonomy_id` through `athlete_graph_export.py`/`site_data.py`; (2) App — read `data.taxonomy_id`
if/when it consumes it. Depends on [[012-merge-attempt-nodes|card 012]] (canonical nodes before
tagging them). **Do NOT start until the mapping is authored;** if it never lands, this card stays
parked — that is the correct outcome (ponytail: no schema for data that doesn't exist).

## Execution Plan
1. **Precondition gate:** confirm a committed, non-ignored technique→subcategory mapping source exists.
   If not, leave this card in TODO with a `**Blocked:**` note. Do not add `docs/taxonomy.json` to git.
2. Alembic migration (next free rev after `0009`/`0010`): add nullable `taxonomy_id` (Text/UUID) to
   `technique_nodes` in `db/models.py` + the migration. **Do not mutate prod** — hand the migration +
   apply steps to the orchestrator.
3. Backfill script populates `taxonomy_id` from the mapping keyed by `node_key` (`_normalize_name`).
4. Export: add `taxonomy_id` to the node dict in `export/athlete_graph_export.py` and
   `export/site_data.py:_to_graphview` (guarded — omit when null so the site-bundle stays backward-compatible).
5. **Second PR (App repo):** document the field for `TreeGraph` consumers; wire only if a render card
   is later un-deferred.
6. Gates: `uv run pytest && uv run ruff check . && uv run mypy .`

## Acceptance Criteria
- [ ] Precondition met (mapping data exists) — else card stays Blocked
- [ ] Nullable `taxonomy_id` on `technique_nodes` via alembic (not applied to prod by this card)
- [ ] Export node payload carries `taxonomy_id` when present, omits when null
- [ ] Contract named on both sides (Analytics schema/export ↔ App read); two PRs referenced
- [ ] No git-committed `docs/taxonomy.json` / `docs/TAXONOMY_PLAN.md`
- [ ] Gates clean

## Test Plan
Unit: migration up/down on a scratch DB; backfill maps a known node_key → its subcategory; export
emits `taxonomy_id` for a tagged node and omits it for an untagged one.
</content>
