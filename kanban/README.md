# Kanban Board — Agent Task Tracking

File-based kanban, doubles as an **Obsidian vault** (open `kanban/` in Obsidian). One markdown card per task. Column = directory. Move card = `git mv`. Cards wikilink each other — graph view shows the dependency DAG (scoped to `tag:#kanban`, columns color-coded).

## Columns

```
kanban/
├── .obsidian/   # vault config (app.json + graph.json tracked; rest gitignored)
├── Board.md     # Dataview dashboard (optional plugin; dirs stay source of truth)
├── TODO/        # ready to pick up — execution plan written, deps noted
├── DOING/       # in progress — exactly one card per agent at a time
└── DONE/        # merged to main — keep for history
```

## Rules

1. **Pick:** take the lowest-`id` card in `TODO/` whose `depends` wikilinks all resolve to `DONE/`. `git mv kanban/TODO/<card> kanban/DOING/` and set `status: doing`.
2. **Branch:** use the card's `branch` field — `feature/<id>-<slug>` (matches AGENTS.md workflow). Worktree optional for quick cards.
3. **Work the plan:** execution plan steps are the contract. Deviations → edit the card in the same PR, note why.
4. **Done means:** acceptance criteria checked off, `uv run pytest && uv run ruff check . && uv run mypy .` clean, card moved to `DONE/` with `status: done` in the merge commit. Cards asking for findings ("record on completion") get a `## Findings` section appended.
5. **New work:** copy `_template.md`, next free `id`, write the execution plan *before* writing code.
6. **WIP limit:** 1 card in `DOING/` per agent. Blocked? Move back to `TODO/` with a `**Blocked:**` note + wikilink to the blocker.

## Card Format

Frontmatter: `id`, `slug`, `phase`, `lane` (concurrency lane A–E), `priority` (P0–P3), `status` (mirrors column; dir wins on conflict), `depends` (wikilinks to card files), `branch`, `created`, `tags` (`[kanban, phase-X, P0–P3, area]`).
Body: Goal → Context → Execution Plan (numbered) → Acceptance Criteria → Test Plan. Cross-reference cards with `[[file|card NNN]]` wikilinks.

## Concurrency Lanes

Cards in **different lanes touch disjoint files** → safe for parallel agents. Within a lane, cards are sequential (deps). Cross-lane deps exist only at lane starts/ends — see waves.

| Lane | Cards | Files owned |
|------|-------|-------------|
| A | 001 → 002 | `analysis/elo_calibration.py`, `export/adcc_elo_table.py` |
| B | 003 → 005 | `pipelines/bjjheroes.py`, `pipelines/registry.py`, `analysis/belt_analysis.py` |
| C | 004 | `analysis/technique_freq.py`, `analysis/names.py` (owns shared name-helper extraction from `export/tech_library.py`) |
| D | 006 → 007 → 008 → 009 | `cv/*` |
| E | 010 → 011 | `analysis/benchmark.py`, `export/benchmark_results.py`, `analysis/similarity.py` |

**Waves** (what can run simultaneously, assuming 1 card per agent):

| Wave | Parallel cards | Unblocked by |
|------|----------------|--------------|
| 1 | 001 (A), 003 (B), 004 (C), 006 (D) | — (start now, 4 agents) |
| 2 | 002 (A), 005 (B), 007 (D) | 001+004 / 003+004 / 006 |
| 3 | 008 (D), 010 (E) | 007 / 001+004 |
| 4 | 009 (D), 011 (E) | 008 / 010 |

Known shared-file touchpoints (sequenced via deps, no concurrent edits): `analysis/names.py` extraction owned by 004; consumed by 002, 005, 010. Doc tables (CLAUDE.md/AGENTS.md registry) edited by 003 only.

## Current Roadmap (from docs/tech_library_refinement.md §5 + CLAUDE.md TODOs)

| Phase | Cards | Theme |
|-------|-------|-------|
| 3 | [[001-adcc-elo-calibration\|001]], [[002-adcc-elo-export\|002]] | ADCC ELO: calibration + app export |
| 3.5 | [[003-bjjheroes-pipeline\|003]], [[004-technique-frequency\|004]], [[005-belt-analysis\|005]] | Data depth: BJJ Heroes scrape, technique freq, belt stats |
| 4 | [[006-vicos-download\|006]]–[[009-baseline-classifier\|009]] | CV: ViCoS download → explore → pose features → baseline classifier |
| 5 | [[010-user-benchmark\|010]], [[011-fighter-similarity\|011]] | User benchmarking + fighter similarity |

## Obsidian Notes

- Recommended community plugins: **Dataview** (powers `Board.md`). Not required — agents never need Obsidian; plain files + `git mv` are the workflow.
- `.obsidian/` is gitignored except `app.json`/`graph.json` (workspace state churns).
- `depends` uses wikilinks in frontmatter — Obsidian ≥1.4 renders them as link properties; agents parse the `[[stem]]` text.
