---
id: "003"
slug: bjjheroes-pipeline
phase: 3.5
lane: B
priority: P1
status: done
depends: []
branch: feature/003-bjjheroes-pipeline
created: 2026-06-12
tags: [kanban, phase-3-5, P1, pipelines]
---

# 003 — BJJ Heroes Scraper Pipeline

## Goal
`pipelines/bjjheroes.py` scrapes BJJ Heroes fighter pages (~400 athletes) into `data/processed/bjjheroes.parquet` with belt, team, lineage, achievements.

## Context
Port of `bjagrelli/bjj_data_scrapping` (cite). Only non-Kaggle pipeline — needs `download()` override. Deps already in pyproject: aiohttp, beautifulsoup4, nest-asyncio. Provides belt/team data that ADCC datasets lack → unblocks [[005-belt-analysis|card 005]].

## Execution Plan
1. `pipelines/registry.py` — add `bjjheroes` DatasetSpec. Kaggle-specific fields don't fit; add optional `source: Literal["kaggle","scrape"] = "kaggle"` + `url: str = ""` to `DatasetSpec` (defaults keep existing specs valid).
2. **Pre-flight:** check `bjjheroes.com/robots.txt` + site ToS; honor disallow rules and set a descriptive User-Agent. If scraping disallowed → stop, note on card, propose alternative source.
3. `pipelines/bjjheroes.py` — `BJJHeroesPipeline(Pipeline)`:
   - Override `download()`: fetch `https://www.bjjheroes.com/a-z-bjj-fighters-list` table → per-fighter pages via aiohttp, concurrency ≤ 4, 1s delay between batches (politeness), retry 3× exponential backoff.
   - Cache raw HTML in `data/raw/bjjheroes/` (gitignored) — re-run parses cache, no re-fetch unless `force=True`.
   - Parse → single CSV in raw dir → existing `_load_raw` path works unchanged.
   - `clean()`: drop rows w/o name; `normalize()`: `fighter_name, nickname, belt, team, weight_class, achievements_raw`.
4. Update `FighterStats` schema: belt/team fields already exist — no change expected; verify.
5. `tests/test_bjjheroes.py` — parse fixture HTML (2 saved fighter-page snippets in `tests/fixtures/`), no network. Test clean/normalize on synthetic frame.
6. CLAUDE.md dataset registry table: add row. AGENTS.md registry table too.
7. Gates clean.

## Acceptance Criteria
- [ ] Full scrape completes locally, parquet ≥ 350 rows
- [ ] Second run hits HTML cache (0 HTTP requests — assert via log)
- [ ] Fixture-based parser tests, no-network
- [ ] Registry tables in CLAUDE.md + AGENTS.md updated
- [ ] Gates clean

## Test Plan
Fixture HTML → parser yields expected name/belt/team. Synthetic frame round-trip. Spec defaults: existing 3 Kaggle specs unaffected (`source == "kaggle"`).
