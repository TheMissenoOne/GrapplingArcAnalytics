---
id: "012"
slug: merge-attempt-nodes
phase: 6
lane: J
priority: P1
status: todo
depends: []
branch: feature/012-merge-attempt-nodes
created: 2026-07-07
tags: [kanban, phase-6, P1, analysis]
---

# 012 — Merge Attempt Nodes into Canonical Parents

## Goal
A one-time `scripts/merge_attempt_nodes.py` that folds any `technique_nodes` row whose label
contains "attempt" into its canonical library node, remaps all incident edges, and a `--check`
mode that verifies none remain. Prereq for accurate exports and per-node stats.

## Context
See `docs/deepseek/I-directed-graphs-and-review-mode.md` §6. Attempt-labelled nodes (e.g. "Heel
Hook Attempt") duplicate their canonical parent in athlete graphs + the site export. The
canonicalizer is `clean_label`/`clean_sequence` in `analysis/technique_match.py` (NOT `_clean_events`
— that lives in `scripts/insert_ufc_matches.py` and only drops strike/reset/referee). Prereq for
[[017-taxonomy-id-schema|card 017]]. Cross-module: Analytics-only (script + DB); the orchestrator
runs it against prod after review — this card must NOT mutate prod.

## Execution Plan
1. **Preflight (read-only, orchestrator/db-prober):** run
   `SELECT node_key, label FROM technique_nodes WHERE label ~* '\battempt(s|ed|ing)?\b';`. If empty,
   the problem doesn't exist — close this card as a no-op. Record the count in `## Findings`.
2. `scripts/merge_attempt_nodes.py` (uses `db/models.py` + `db/base.py` session, `analysis.names._normalize_name`,
   `analysis.technique_match.clean_label`):
   - find attempt-labelled `TechniqueNode`s;
   - `canonical_key = _normalize_name(clean_label(label))`; upsert the canonical node from the
     library (`source='library'`) if absent;
   - remap `graph_edges.source_key/target_key/edge_key` and `map_edges.source_key/target_key`
     attempt_key → canonical_key, deduping any row collision against the unique constraints;
   - delete the attempt node.
   - `--check`: exit non-zero if any attempt-labelled node_key remains.
   - `--dry-run` default; `--apply` to write. No hardcoded prod DSN — read from env like the rest of `db/`.
3. `tests/test_merge_attempt_nodes.py` — build a synthetic in-memory/SQLite-or-fixture session with
   one attempt node + a canonical node + edges through the attempt node; run merge; assert edges
   remapped, attempt node gone, no duplicate edge rows, `--check` passes after.
4. Gates: `uv run pytest && uv run ruff check . && uv run mypy .`

## Acceptance Criteria
- [ ] Preflight count recorded (may close the card if 0)
- [ ] Script remaps `graph_edges` + `map_edges`, dedupes collisions, deletes attempt nodes
- [ ] `--check` mode asserts zero attempt node_keys remain
- [ ] `--dry-run` is the default; no prod mutation from this card
- [ ] Test covers remap + dedupe + check on a synthetic session
- [ ] Gates clean

## Test Plan
Synthetic session: canonical "Heel Hook" node + "Heel Hook Attempt" node + two edges routing through
the attempt node (one that would collide with an existing canonical edge). Assert post-merge edge
count, key values, single row per unique constraint, attempt node deleted, `--check` green.
</content>
