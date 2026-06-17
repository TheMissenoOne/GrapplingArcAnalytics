"""Export ADCC ELO table in app-compatible JSON format."""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from analysis.elo_calibration import compute_adcc_elo
from analysis.names import _normalize_name
from pipelines.adcc_fighters import ADCCFightersPipeline
from pipelines.adcc_historical import ADCCHistoricalPipeline
from pipelines.etl import PROCESSED_DIR

logger = logging.getLogger(__name__)


def export_adcc_elo_table(base_k: float = 40.0) -> dict[str, Any]:
    """Run full export: load, compute, join, write JSON, return summary."""
    adcc_df = ADCCHistoricalPipeline().run()
    elo_df = compute_adcc_elo(adcc_df, base_k=base_k)

    try:
        fighters_df = ADCCFightersPipeline().run()
        fighters_df["name_norm"] = fighters_df["fighter_name"].apply(_normalize_name)
    except Exception as e:
        logger.warning("Fighters data unavailable: %s", e)
        fighters_df = pd.DataFrame()

    elo_df["name_norm"] = elo_df["fighter"].apply(_normalize_name)

    total = len(elo_df)
    hits = 0
    records: list[dict[str, Any]] = []

    for _, row in elo_df.iterrows():
        rec: dict[str, Any] = {
            "fighter": row["fighter"],
            "elo": int(round(row["elo"])),
            "matches": int(row["matches"]),
            "wins": int(row["wins"]),
            "losses": int(row["losses"]),
        }
        if not fighters_df.empty:
            match = fighters_df[fighters_df["name_norm"] == row["name_norm"]]
            if not match.empty:
                frow = match.iloc[0]
                rec["titles"] = int(frow.get("titles", 0))
                rec["sub_ratio"] = float(frow.get("sub_ratio", 0.0))
                rec["weight_class"] = str(frow.get("weight_class", ""))
                hits += 1
            else:
                logger.debug("No fighters enrichment for %s", row["fighter"])
        records.append(rec)

    records.sort(key=lambda x: x["elo"], reverse=True)

    output_path = PROCESSED_DIR / "adcc_elo_table.json"
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(records, f, indent=2)

    logger.info("Exported %d ADCC ELO entries to %s", len(records), output_path)

    miss_count = total - hits
    return {
        "total_fighters": total,
        "enriched": hits,
        "missed": miss_count,
        "hit_rate": round(hits / total * 100, 1) if total else 0.0,
        "output_path": str(output_path),
    }
