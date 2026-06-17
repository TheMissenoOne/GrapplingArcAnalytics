#!/usr/bin/env bash
set -euo pipefail

# sync_to_app.sh — run ETL + exports, copy processed data to GrapplingArcApp/analytics
#
# Usage: ./scripts/sync_to_app.sh [--force] [--benchmark <bundle.json>]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
APP_ANALYTICS_DIR="/home/vetor/projetos/GrapplingArcApp/analytics"

FORCE=""
BENCHMARK_BUNDLE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE="--force"; shift ;;
        --benchmark) BENCHMARK_BUNDLE="$2"; shift 2 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

cd "$PROJECT_DIR"

echo "=== Sync GrapplingArcAnalytics → GrapplingArcApp/analytics ==="
echo ""

# ── 1. ETL pipelines ────────────────────────────────────────────
echo "[1/5] Running ETL pipelines..."
uv run python -c "
import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
from pipelines.adcc_historical import ADCCHistoricalPipeline
from pipelines.grappling_techniques import GrapplingTechniquesPipeline
from pipelines.adcc_fighters import ADCCFightersPipeline
for p_cls in [ADCCHistoricalPipeline, GrapplingTechniquesPipeline, ADCCFightersPipeline]:
    p = p_cls()
    df = p.run($([[ "$FORCE" == "--force" ]] && echo "force_download=True" || echo ""))
    print(f'  {p.spec.key}: {len(df)} rows')
"

# ── 2. BJJ Heroes (skip if no force and cache exists) ───────────
echo "[2/5] BJJ Heroes pipeline..."
if [[ -s data/raw/bjjheroes/bjjheroes.csv && -z "$FORCE" ]]; then
    uv run python -c "
import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
from pipelines.bjjheroes import BJJHeroesPipeline
p = BJJHeroesPipeline()
df = p.run()
print(f'  bjjheroes: {len(df)} rows')
" 2>&1 || echo "  bjjheroes: pipeline error"
else
    echo "  bjjheroes: no cache, skipping (run manually with network)"
fi

# ── 3. Exports ──────────────────────────────────────────────────
echo "[3/5] Exporting technique library..."
uv run python -m export.tech_library 2>/dev/null
echo "  technique_library.json + technique_effectiveness.json"

echo "[4/5] Exporting ADCC ELO table..."
uv run python -c "
from export.adcc_elo_table import export_adcc_elo_table
r = export_adcc_elo_table()
print(f'  {r[\"total_fighters\"]} fighters, {r[\"enriched\"]} enriched')
"

# ── 5. Benchmark (optional) ─────────────────────────────────────
if [[ -n "$BENCHMARK_BUNDLE" ]]; then
    echo "[5/5] Exporting benchmark results..."
    uv run python -c "
from export.benchmark_results import export_benchmark_results
r = export_benchmark_results('$BENCHMARK_BUNDLE')
print(f'  {r[\"total_techniques\"]} techniques, {r[\"matched\"]} matched with ADCC')
" || echo "  benchmark: failed (bad bundle path?)"
else
    echo "[5/5] Benchmark: skipped (pass --benchmark <bundle.json> to run)"
fi

# ── Copy to GrapplingArcApp/analytics ───────────────────────────
echo ""
echo "Copying processed data → $APP_ANALYTICS_DIR"
mkdir -p "$APP_ANALYTICS_DIR"

# App-facing JSON exports
for f in technique_library.json technique_effectiveness.json adcc_elo_table.json benchmark_results.json; do
    src="data/processed/$f"
    if [[ -f "$src" ]]; then
        cp "$src" "$APP_ANALYTICS_DIR/"
        echo "  ✓ $f"
    fi
done

# Parquet datasets (for future app-side analysis)
for f in adcc_historical.parquet adcc_fighters.parquet grappling_techniques.parquet bjjheroes.parquet vicos_keypoints.parquet; do
    src="data/processed/$f"
    if [[ -f "$src" ]]; then
        cp "$src" "$APP_ANALYTICS_DIR/"
        echo "  ✓ $f"
    fi
done

# Trained classifiers
for f in position_clf_rf.joblib position_clf_xgb.joblib; do
    src="data/processed/$f"
    if [[ -f "$src" ]]; then
        cp "$src" "$APP_ANALYTICS_DIR/"
        echo "  ✓ $f"
    fi
done

echo ""
echo "=== Done ==="
echo "App analytics at: $APP_ANALYTICS_DIR"
ls -lh "$APP_ANALYTICS_DIR" 2>/dev/null | awk 'NR>1 {print "  " $NF " (" $5 ")"}'
