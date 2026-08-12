"""Pure manifest-quality metrics for the Decision Vision POC.

No IO, no DB access. Input: the manifest DataFrame produced by
``extract_frames.py`` (original columns plus ``visual_*``).
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd


def manifest_quality(frame: pd.DataFrame) -> dict[str, int | float]:
    """Summarize label/taxonomy quality of a manifest.

    ``visual_*`` columns are preferred when present (vision-only normalization),
    falling back to the original ``leaf/family/category`` columns.
    """
    if frame.empty:
        return {
            "rows": 0,
            "criterion_events": 0,
            "taxonomy_resolved_rate": 0.0,
            "leaf_eq_family_rate": 0.0,
            "attempt_collapsed_rate": 0.0,
            "matches": 0,
            "classes_leaf": 0,
            "classes_family": 0,
            "classes_category": 0,
        }

    def preferred(base: str, visual: str) -> pd.Series:
        column = visual if visual in frame.columns else base
        return frame[column].fillna("").astype(str)

    def resolved(row: Any) -> bool:
        path = row.get("taxonomy_path")
        if not path:
            return False
        try:
            parsed = json.loads(str(path))
        except ValueError:
            return False
        return bool(parsed)

    rows = len(frame)
    resolved_count = sum(
        1 for row in frame.to_dict("records") if resolved(row)
    )
    strata_key = (
        ["match_id", "criterion_event_index"]
        if "criterion_event_index" in frame.columns
        else ["match_id"]
    )
    criterion_events = int(
        frame.drop_duplicates(strata_key).shape[0]
    )

    leaf = preferred("leaf_label", "visual_leaf_label")
    family = preferred("family_label", "visual_family_label")
    category = preferred("category_label", "visual_category_label")

    return {
        "rows": rows,
        "criterion_events": criterion_events,
        "taxonomy_resolved_rate": round(
            resolved_count / rows,
            4,
        ),
        "leaf_eq_family_rate": round(
            float((leaf == family).mean()),
            4,
        ),
        "attempt_collapsed_rate": (
            round(
                float(
                    frame["visual_label_collapsed"].astype(
                        bool
                    ).mean()
                ),
                4,
            )
            if "visual_label_collapsed" in frame.columns
            else 0.0
        ),
        "matches": int(
            frame["match_id"].nunique()
        ),
        "classes_leaf": int(leaf.nunique()),
        "classes_family": int(family.nunique()),
        "classes_category": int(category.nunique()),
    }
