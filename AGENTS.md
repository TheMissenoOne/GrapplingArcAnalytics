# GrapplingArcAnalytics — Agent Instructions

Offline-first BJJ analytics module. Python pipelines, CV investigation, ELO calibration. Separate from GrapplingArcApp.

## Mode

ultra-caveman default. No filler. Fragments. Technical terms exact. Stop only on "normal mode".

## Non-Negotiable

1. **Read CLAUDE.md first** — contains project memory, stack, dataset registry, doc map
2. **Read AGENTS.md second** — you're reading it
3. **Pipelines in `pipelines/`** — one file per dataset. Inherit `Pipeline` base class
4. **Schemas in `schemas/`** — `__init__.py` for dataset schemas, `app_types.py` for app TS mirror
5. **Analysis in `analysis/`** — pure functions, no side effects, inputs are DataFrames
6. **CV in `cv/`** — ViCoS dataset investigation only. No raw image model training yet
7. **Export in `export/`** — produces JSON matching GrapplingArc AsyncStorage format
8. **Data never committed** — `data/raw/` and `data/processed/` are gitignored
9. **Kaggle via kagglehub** — never hardcode Kaggle paths. Use `Pipeline.download()`
10. **uv** — Python 3.12+, pyproject.toml, no requirements.txt

## Workflow

ticket → worktree → analyze → implement → test → pr

1. **Ticket:** Create/update kanban card in `kanban/TODO/`
2. **Worktree:** `git worktree add ../.worktrees/<id>-<slug> -b feature/<id>-<slug> origin/main`
3. **Analyze:** Read CLAUDE.md + relevant doc. Search patterns.
4. **Implement:** pipeline/schema → analysis/cv → tests → notebook. One layer at a time.
5. **Test:** `pytest tests/`. Data round-trip. Edge cases (empty, missing cols).
6. **PR:** Push. Summary + changes + tests.

## Quick Fix (1 file)
No ticket. No worktree. No git ops. Edit, ruff check, pytest, done.

## Dataset Registry

| key | slug | rows |
|---|---|---|
| grappling_techniques | liiucbs/grappling-techniques | 76 |
| adcc_historical | bjagrelli/adcc-historical-dataset | 1028 |
| adcc_fighters | albucathecoder/adcc-fighter-stats | ~600 |

Schema for each: `schemas/__init__.py`.

## Pipeline Template

```python
from pipelines.etl import Pipeline
from pipelines.registry import DATASETS

class MyPipeline(Pipeline):
    spec = DATASETS["my_key"]

    def clean(self, df):
        # handle NaN, fix types, normalize strings
        return df

    def normalize(self, df):
        # map to unified column names
        return df.rename(columns={...})
```

## ELO Engine Reference

Borrow math from `felixgnwn/adcc_elo_engine/elo_engine.py`:
- K-factor = BASE_K × WIN_TYPE_MULT × STAGE_MULT
- Expected score = 1 / (1 + 10^((elo_b - elo_a) / 400))
- Update: elo += K × (score - expected)

## CV Investigation

ViCoS dataset: 120,279 labeled images, COCO 17-keypoint poses, 10 positions → 18 classes.

Pipeline:
1. `cv/vicos_download.py` — download images + JSON annotations
2. `cv/vicos_explore.py` — parse, visualize, class distribution
3. `cv/pose_features.py` — engineer features from keypoints
4. `cv/baseline_classifier.py` — RF/XGBoost on pose features → position

Target: ~80% baseline (pose features only), ~90%+ with ViTPose embeddings (future).

## Testing

`pytest tests/`. Priority: pipelines (ETL round-trip) → analysis (known inputs) → CV (fixtures).

## Reference Repos (borrow, cite)

- `felixgnwn/adcc_elo_engine` — ELO/Glicko-2 ADCC engine
- `omeedtavakoli/Grappling-Technique-Analytics` — ML pipeline
- `waizbart/bjj_cnn_position_detector` — pose→position on ViCoS
- `ValterH/automatic-positions-detection-and-scoring-in-jiu-jitsu` — ViTPose integration
- `bjagrelli/bjj_data_scrapping` — BJJ Heroes scraper (port needed)
