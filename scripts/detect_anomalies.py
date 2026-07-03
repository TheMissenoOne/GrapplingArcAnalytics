"""Detect anomalies in batch-generated data modules: names, dupes, missing results."""
from __future__ import annotations
import importlib, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parent))  # GrapplingArcAnalytics/
ANOMALIES: list[str] = []

def check_module(mod_name: str):
    try:
        raw = importlib.import_module(mod_name).RAW
    except Exception as e:
        ANOMALIES.append(f"  IMPORT FAIL {mod_name}: {e}")
        return
    for entry in raw:
        for (name, year), info in entry.items():
            # 1. Name contains description text (not just a name)
            if len(name) > 40:
                ANOMALIES.append(f"  LONG NAME [{mod_name}] {name[:60]}... — likely description in name field")
            # 2. Name contains "(" or ":" — likely metadata
            if '(' in name or ':' in name or 'This' in name or 'Encore' in name:
                ANOMALIES.append(f"  META NAME [{mod_name}] {name[:60]} — name contains metadata")
            # 3. Opponent contains description
            opp = info.get("opponent", "")
            if len(opp) > 40 or '(' in opp or ':' in opp or 'This' in opp:
                ANOMALIES.append(f"  META OPP [{mod_name}] {name[:30]} vs {opp[:60]} — opponent has metadata")
            # 4. Missing winner
            if not info.get("winner"):
                ANOMALIES.append(f"  NO WINNER [{mod_name}] {name[:30]} vs {info.get('opponent','?')[:30]} ({year})")
            # 5. Unknown method
            method = info.get("method", "")
            if method == "Unknown" or not method:
                ANOMALIES.append(f"  UNKNOWN METHOD [{mod_name}] {name[:30]} vs {info.get('opponent','?')[:30]}")
            # 6. Unknown win_type
            wt = info.get("win_type")
            if not wt:
                ANOMALIES.append(f"  NO WIN_TYPE [{mod_name}] {name[:30]} vs {info.get('opponent','?')[:30]}")

# Check all modules in DATASETS from reprocess_all
reprocess = importlib.import_module("scripts.reprocess_all")
for mod_path, event, label in reprocess.DATASETS:
    check_module(mod_path)

print(f"=== ANOMALY REPORT: {len(ANOMALIES)} issues ===\n")
for a in ANOMALIES:
    print(a)
print(f"\n=== {len(ANOMALIES)} TOTAL ===")
