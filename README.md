# GrapplingArcAnalytics

BJJ competition data analytics module for GrapplingArc. Runs locally, Python 3.12+, uv.

## Quick Start

```bash
uv venv
source .venv/bin/activate
uv sync
cp .env.example .env  # add Kaggle API keys
uv run pytest
```

## Datasets

| Dataset | Source | Rows | Status |
|---|---|---|---|
| Grappling Techniques | [Kaggle](https://kaggle.com/datasets/liiucbs/grappling-techniques) | 76 | ✅ Pipeline |
| ADCC Historical Matches | [Kaggle](https://kaggle.com/datasets/bjagrelli/adcc-historical-dataset) | 1,028 | ✅ Pipeline |
| ADCC Fighter Stats | [Kaggle](https://kaggle.com/datasets/albucathecoder/adcc-fighter-stats) | ~600 | ✅ Pipeline |
| BJJ Heroes | [GitHub](https://github.com/bjagrelli/bjj_data_scrapping) | ~400 | 🚧 TODO |

## Structure

```
pipelines/    ETL: download → clean → normalize → cache
schemas/      Unified dataclasses + App TS type mirrors
analysis/     ELO calibration, technique freq, benchmarking, similarity
cv/           ViCoS dataset exploration, pose features, baseline classifier
export/       JSON generators for app consumption
notebooks/    Jupyter exploration
```

## Repos Referenced

- [adcc_elo_engine](https://github.com/felixgnwn/adcc_elo_engine) — ELO/Glicko-2 for ADCC
- [Grappling-Technique-Analytics](https://github.com/omeedtavakoli/Grappling-Technique-Analytics) — ML classifier
- [bjj_cnn_position_detector](https://github.com/waizbart/bjj_cnn_position_detector) — pose → position
- [bjj_data_scrapping](https://github.com/bjagrelli/bjj_data_scrapping) — BJJ Heroes scraper
