---
id: "000"
slug: short-kebab-slug
phase: 0
lane: X
priority: P2
status: todo
depends: []
branch: feature/000-short-kebab-slug
created: 2026-06-12
tags: [kanban, phase-0, P2, area]
---

# 000 — Title

## Goal
One sentence. What exists when this card is done.

## Context
Why now. Wikilink blocking/blocked cards: [[001-adcc-elo-calibration|card 001]]. Link docs/, reference repos, datasets involved.

## Execution Plan
1. Step — file(s) touched, approach. Include pre-flight checks (licenses, robots.txt, disk) where relevant.
2. Step.
3. Tests — synthetic/fixture inputs, no network.
4. Quality gates: `uv run pytest && uv run ruff check . && uv run mypy .`

## Acceptance Criteria
- [ ] Observable outcome 1
- [ ] Tests cover X
- [ ] Gates clean

## Test Plan
What synthetic inputs, what assertions. No-network rule for unit tests.

<!--
Frontmatter rules:
- id: zero-padded string, unique across all columns
- lane: concurrency lane (A–E existing, new letter = new disjoint file set; see README)
- depends: wikilinks to card files, e.g. ["[[001-adcc-elo-calibration]]"]
- tags: always [kanban, phase-X, P0–P3, area] — area ∈ pipelines|analysis|cv|export|schemas
- status mirrors the column dir; the dir is source of truth
-->
