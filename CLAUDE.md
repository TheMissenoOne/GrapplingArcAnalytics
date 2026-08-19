# CLAUDE.md — GrapplingArcAnalytics

## Identity

**GrapplingArcAnalytics** — Python data analytics module for BJJ competition analysis. Separate from GrapplingArcApp (RN/Expo). Runs locally, eventually cloud.

## Stack

- Python 3.12+, uv package manager
- pandas/numpy (core data), pyarrow (parquet cache)
- scikit-learn + xgboost (ML)
- matplotlib + seaborn (viz)
- kagglehub (Kaggle integration)
- aiohttp + beautifulsoup4 (BJJ Heroes scraping)
- jupyter (notebooks)
- opencv-python + pillow (CV)
- pytest + ruff + mypy (quality)

## Repo Structure

```
.
├── pyproject.toml                  # uv-managed deps
├── AGENTS.md                        # agent instructions
├── CLAUDE.md                        # this file
├── .env.example                     # env template (Kaggle keys)
├── data/
│   ├── raw/                         # kaggle downloads (symlinked, gitignored)
│   └── processed/                   # cleaned parquet (gitignored)
├── pipelines/
│   ├── __init__.py                  # exports Pipeline, DATASETS, DatasetSpec
│   ├── etl.py                       # base Pipeline class (download → clean → normalize → cache)
│   ├── registry.py                  # DATASETS dict — all specs
│   ├── grappling_techniques.py      # liiucbs dataset
│   ├── adcc_historical.py           # bjagrelli dataset
│   ├── adcc_fighters.py             # albucathecoder dataset
│   └── bjjheroes.py                 # [TODO] fork of bjagrelli scraper
├── schemas/
│   ├── __init__.py                  # Technique, ADCCMatch, FighterStats, AppGraphNode, etc.
│   └── app_types.py                 # UserBundle parser — mirrors TS types
├── analysis/
│   ├── __init__.py                  # module exports
│   ├── elo_calibration.py           # [TODO] ADCC ELO + K-factor calibration
│   ├── technique_freq.py            # [TODO] position heatmaps, transition matrices
│   ├── belt_analysis.py             # [TODO] belt-level stats
│   ├── benchmark.py                 # [TODO] user vs pro benchmarking
│   └── similarity.py                # [TODO] cosine-sim fighter matching
├── cv/
│   ├── __init__.py                  # module exports
│   ├── vicos_download.py            # [TODO] ViCoS downloader
│   ├── vicos_explore.py             # [TODO] dataset exploration
│   ├── pose_features.py             # [TODO] keypoint feature engineering
│   └── baseline_classifier.py       # [TODO] RF/XGBoost on pose features
├── export/
│   ├── __init__.py                  # module exports
│   ├── tech_library.py              # ✅ → app technique library JSON + effectiveness scores
│   ├── adcc_elo_table.py            # [TODO] → app ELO benchmark
│   └── benchmark_results.py         # [TODO] → user benchmark JSON
├── harvest/                         # ✅ native YouTube fight-transcript harvester (replaced
│   │                                #   the bjj-match-analyzer sibling repo — now scrapped)
│   ├── transcripts.py               # youtube-transcript-api + oEmbed title + feedparser playlist
│   ├── harvester.py                 # write transcript + harvest/prompt.txt → data/harvest/inbox/
│   ├── prompt.txt                   # processing prompt (→ analytics match JSON contract)
│   └── __main__.py                  # CLI: uv run python -m harvest --url/--playlist/--urls-file
│   # Flow: harvest → human runs file through ChatGPT/Copilot/Deepseek → save JSON to
│   # data/harvest/processed/ → db/scraped_import imports as drafts. All via admin /admin/harvest.
├── admin/                           # ✅ local FastAPI dashboard: athlete entry, ontology authoring,
│   │                                #   harvest control, analytics overview (local-only, binds 127.0.0.1)
│   └── __main__.py                  #   run: uv run --extra web python -m admin → http://127.0.0.1:8765
├── docs/                            # design docs, audit reports
├── kanban/                          # agent task board + Obsidian vault: TODO/ DOING/ DONE/, lanes A–E for parallel agents (kanban/README.md)
├── notebooks/                       # Jupyter notebooks (exploratory)
├── tests/                           # pytest (pipelines, tech_library)
└── .claude/skills/                  # agent skills
```

## Skill Library

Local `.claude/skills/` (repo tree above) = this module's own skills (analysis/cv/pipeline) — separate
from the workspace-root cross-module library one level up at `/home/vetor/GrapplingArc/.claude/skills`;
check root `CLAUDE.md`'s "Skill Library" table before cross-cutting or unfamiliar work. Most relevant here:

| Skill | Load when |
|---|---|
| ingest-to-publish | new event transcript needs to become live site pages |
| run-and-operate | starting app/dashboard/realtime locally, change needs to reach the public site, replay decision |
| supabase-schema-migration | adding/altering table/column/RLS/view, new alembic revision, suspected schema drift |
| site-export-perf-campaign | public-site export slow/hangs ~10-12min, speeding up build_* functions |
| research-methodology | experimental ELO/metric/embedding work, "is this predictive" |
| add-ci | adding or reviewing a GitHub Actions workflow, deciding what should gate a PR |
| diagnostics-and-tooling | need read-only current state — prod DB check, site bundle validation, run one test |
| debugging-playbook | symptom matches a known failure mode (export stuck, sync errors, stale offline data) |
| failure-archaeology | about to retry something that smells like a past dead end |
| architecture-contract | change crosses a module boundary — storage key, Supabase schema, ELO export, site global |

## Dataset Registry

| key | source / slug | rows | delimiter | columns |
|---|---|---|---|---|
| grappling_techniques | liiucbs/grappling-techniques | 76 | `,` | Name, Position, Origin, Type, … |
| adcc_historical | bjagrelli/adcc-historical-dataset | 1028 | `;` | match_id, year, winner_name, loser_name, win_type, stage, submission, weight_class, sex |
| adcc_fighters | albucathecoder/adcc-fighter-stats | ~600 | `,` | fighter_name, wins, losses, titles, sub_ratio, win_ratio, debut_year, favorite_target |
| bjjheroes | (scrape) bjjheroes.com | ~400 | N/A | fighter_name, nickname, belt, team, weight_class, achievements_raw |

## Data Flow

```
kagglehub → data/raw/{key}/ → Pipeline.clean() → Pipeline.normalize() → data/processed/{key}.parquet
                                                                              ↓
                                                              analysis/*.py (read parquet)
                                                                              ↓
                                                              export/*.py (produce app JSON)
```

## Public vs Private Data (ethics + LGPD — read before writing any query)

**Dados alimentados diretamente pelo App como usuário são PRIVADOS, anonimizados para
processamento, e NUNCA usados diretamente para análises competitivas.**

- **Public** — competition footage this module ingests: `matches`, `athletes`, athlete
  graphs (`owner_kind='athlete'`). Feeds the site, ELO, archetypes, the map, scouting.
- **Private** — anything the App user fed in: `user_sessions`, user graphs
  (`owner_kind='user'`) and their `graph_edges`, `profiles`, gyms, classes. Serves that
  user only.

One-way: public may inform a user's own dossier; **private never flows into a public or
competitive artefact** — not as an average, centroid, embedding, ranking or export.

**Vectorising app data is allowed** (LGPD permitting) when the output goes back to that
same user — their own dossier, their grapple-like / archetype match. The line is purpose,
not technique: the same user embedding is legitimate for the owner's dossier and forbidden
inside an archetype centroid or anything the public site renders.

Every query building a public/competitive artefact **filters `owner_kind` explicitly**.
An unfiltered `select(Graph)` is a defect. `export/*` (`site_data`, `match_breakdown`,
`ontology`, `athlete_graph_export`) and `analysis/archetype.py` already filter; the guards
in `analysis/embeddings.py` are `backfill_archetype_embeddings` (athlete-only centroids —
`scripts/assign_user_archetypes` stamps `archetype_id` on user graphs too, so the filter is
load-bearing) and `nearest_graphs` (athlete-only by default). `backfill_graph_embeddings`
deliberately covers user graphs — a user needs a vector to be placed against the archetypes,
and that result is shown only to them.

## Match Event Model

A bout's `sequence` = events `{label, type, actor, successful?, ts?}` → transition graph. **Any**
entry path (DeepSeek refiner, `convert_dump.py`, `insert_*.py`, admin paste) must follow one
convention — **`actor` = the fighter whose game the node belongs to**, not who's winning: a `guard`
node is owned by the **guard player** (bottom), the `pass` by the passer. Full model + the per-type
ownership table: **`docs/match_event_model.md`**. The refiner prompt + rules: **`docs/PROMPT_events_sidecar.md`**
(replaced `docs/deepseek/E-refine-events.md`, which teaches a wrong bout-relative timestamp).

**End-to-end ingestion** (transcript → dump → refine → import → embeddings → site → validate),
with the command + owner for each step: **`docs/ingestion_pipeline.md`**. QA a fresh batch with
`analysis/match_deviance.py` (per-bout deviance from each athlete's usual game → recheck list).

## ELO Engine

From `felixgnwn/adcc_elo_engine/elo_engine.py`:
- K = 40 × win_type_mult × stage_mult
- win_type_mult: SUB=1.15, DECISION=0.85, POINTS=1.0
- stage_mult: SPF=1.4, F=1.3, SF=1.2, 3RD=1.15, R2/R1/E1/8F=1.0
- Expected score: 1/(1 + 10^((elo_b - elo_a)/400))
- Update: elo += K × (score - expected)
- Initial ELO: 1000

## ViCoS Dataset

- **Location:** https://vicos.si/resources/jiujitsu/
- **Size:** 120,279 labeled images, ~14 GB
- **Format:** JSON annotations, COCO 17-keypoint poses
- **Classes:** 10 positions × top/bottom → 18 classes
- **Keypoints per athlete:** 17×[x, y, confidence]
- **Reference accuracy:** 90%+ (waizbart), 92% 3-view (ValterH)
- **Our target (phase 1):** ~80% with sklearn on keypoint features

## Reference Repos

| Repo | What to Borrow | Where Applied |
|---|---|---|
| felixgnwn/adcc_elo_engine | ELO math, K-factor weighting | analysis/elo_calibration.py |
| omeedtavakoli/Grappling-Technique-Analytics | sklearn pipeline structure | Reference only |
| waizbart/bjj_cnn_position_detector | ViCoS → pose → position approach | cv/ |
| ValterH/automatic-positions-detection-and-scoring-in-jiu-jitsu | ViTPose integration, scoring | cv/ (future) |
| bjagrelli/bjj_data_scrapping | BJJ Heroes scraper code | pipelines/bjjheroes.py |

## Commands

```bash
uv venv                  # create virtualenv
uv sync --all-extras     # install deps — SEE BELOW, plain `uv sync` is not enough for mypy
uv run pytest            # run tests
uv run ruff check .      # lint
uv run mypy .            # typecheck
uv run jupyter lab       # start notebooks
```

⚠️ **`uv run mypy .` is only equivalent to CI's after `uv sync --all-extras`.** CI installs every
extra (`.github/workflows/ci.yml`); a plain `uv sync` leaves optional deps like `ultralytics` out,
and mypy then resolves those imports to `Any` instead of checking them. A local run can be green
while CI fails on a module that touches an optional dep — and can also report a needed
`type: ignore` as unused, which is how one got deleted and turned CI red (see the docstring on
`cv/pose_estimate._ultralytics_runtime`). Ignores that only apply with an extra installed carry
`unused-ignore` alongside the real code so both environments agree.

## App Integration

Export layer produces JSON the App consumes:
- `export/tech_library.py` → matches `@grapplingarch:nodes_library` format
- `export/benchmark_results.py:export_pro_baseline_db` → `src/data/pro_baseline.json`
  (`ProBaselineV1`, bundled with the App) — style-mix + submission-family p25/median/p75.
  This is the real App-facing data export.

⚠️ `export/adcc_elo_table.py` is **not** an App export, despite what this file used to claim.
It computes ELO from the Kaggle ADCC historical corpus (not the `matches` table) and writes
`data/processed/adcc_elo_table.json`, which **nothing consumes** — not the App, not the site.
And `@grapplingarch:elo_stats` is the App's own on-device `StoredEloStats` (mean/stdDev/
signatures/weakLinks off the user's graph), never imported from here. The two were never
connected; verified 2026-08-18.

User bundle import: `schemas/app_types.UserBundle.from_json()` parses GrapplingArc `mock_user_bundle.json`.

