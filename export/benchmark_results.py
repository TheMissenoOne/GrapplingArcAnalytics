"""Export benchmark results in app-compatible JSON format."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analysis.benchmark import compare, pro_baseline, user_submission_profile
from pipelines.adcc_historical import ADCCHistoricalPipeline
from pipelines.etl import PROCESSED_DIR
from schemas.app_types import UserBundle

logger = logging.getLogger(__name__)


def export_benchmark_results(bundle_path: str | Path) -> dict[str, Any]:
    """Run full benchmark export: load bundle, compare vs ADCC, write JSON.

    Output schema (data/processed/benchmark_results.json):
    {
        "generated_at": "ISO datetime",
        "total_user_techniques": int,
        "matched_with_adcc": int,
        "no_pro_data_count": int,
        "techniques": [
            {
                "technique": str,
                "user_share": float,
                "pro_share": float,
                "ratio": float,
                "emphasis": str,
                "no_pro_data": bool
            }
        ],
        "summary": {
            "top_technique": str,
            "most_overused": str | null,
            "most_underused": str | null
        }
    }
    """
    bundle_path = Path(bundle_path)
    with open(bundle_path) as f:
        data: dict[str, Any] = json.load(f)
    bundle = UserBundle.from_json(data)

    adcc_df = ADCCHistoricalPipeline().run()

    user_profile = user_submission_profile(bundle)
    baseline = pro_baseline(adcc_df)

    comparison = compare(user_profile, baseline)

    records = comparison.to_dict(orient="records")

    no_pro_data = (
        comparison[comparison["no_pro_data"]].shape[0]
        if not comparison.empty
        else 0
    )

    top_tech = records[0]["technique"] if records else ""

    valid = comparison[~comparison["no_pro_data"]]
    most_over = (
        valid.loc[valid["ratio"].idxmax(), "technique"]
        if not valid.empty and (valid["ratio"] > 1).any()
        else None
    )
    most_under = (
        valid.loc[valid["ratio"].idxmin(), "technique"]
        if not valid.empty and (valid["ratio"] < 1).any()
        else None
    )

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_user_techniques": len(records),
        "matched_with_adcc": len(records) - no_pro_data,
        "no_pro_data_count": no_pro_data,
        "techniques": [
            {
                "technique": r["technique"],
                "user_share": round(r["user_share"], 4),
                "pro_share": round(r["pro_share"], 4),
                "ratio": r["ratio"],
                "emphasis": r["emphasis"],
                "no_pro_data": bool(r["no_pro_data"]),
            }
            for r in records
        ],
        "summary": {
            "top_technique": top_tech,
            "most_overused": most_over,
            "most_underused": most_under,
        },
    }

    output_path = PROCESSED_DIR / "benchmark_results.json"
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    logger.info("Exported benchmark results to %s", output_path)

    return {
        "total_techniques": len(records),
        "matched": len(records) - no_pro_data,
        "no_pro_data": no_pro_data,
        "output_path": str(output_path),
    }
