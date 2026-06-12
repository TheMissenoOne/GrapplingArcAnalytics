"""
ADCC Fighter Stats pipeline (Kaggle: albucathecoder/adcc-fighter-stats).

614 fighters, 25 columns: career stats, win ratios, submission preferences, titles.
"""

from __future__ import annotations

import pandas as pd

from pipelines.etl import Pipeline
from pipelines.registry import DATASETS


class ADCCFightersPipeline(Pipeline):
    spec = DATASETS["adcc_fighters"]

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        cols = {c.lower().strip().replace(" ", "_"): c for c in df.columns}
        df = df.rename(columns={v: k for k, v in cols.items()})
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        col = df.columns.tolist()

        result = pd.DataFrame({"fighter_name": df["name"]})

        # wins → total_wins or total_fights
        if "total_wins" in col:
            result["wins"] = df["total_wins"].fillna(0).astype(int)
        elif "total_fights" in col:
            result["wins"] = (df["total_fights"] * df.get("win_ratio", 0)).fillna(0).astype(int)
        else:
            result["wins"] = 0

        result["losses"] = 0  # derived later if total_fights present
        if "total_fights" in col:
            result["losses"] = (df["total_fights"] - result["wins"]).clip(lower=0).astype(int)

        result["titles"] = df.get("n_titles", 0).fillna(0).astype(int)
        result["sub_ratio"] = df.get("sub_win_ratio", df.get("sub_ratio", 0.0)).fillna(0.0)
        result["debut_year"] = df.get("debut_year", 0).fillna(0).astype(int)

        if "win_ratio" in col:
            result["win_ratio"] = df["win_ratio"].fillna(0.0)
        elif "total_fights" in col:
            result["win_ratio"] = (result["wins"] / df["total_fights"].replace(0, 1)).round(3)
        else:
            result["win_ratio"] = 0.0

        if "favorite_target" in col:
            result["favorite_target"] = df["favorite_target"].fillna("").astype(str)

        if "main_weight_class" in col:
            result["weight_class"] = df["main_weight_class"].fillna(0).astype(int).astype(str)
            result["weight_class"] = result["weight_class"].replace("0", "")

        # extra rich fields kept for advanced analysis
        for extra in [
            "n_editions_competed", "scored_points_per_fight",
            "suffered_points_per_fight", "fights_per_edition",
            "avg_match_importance", "highest_match_importance",
            "open_weight_ratio", "custom_score", "n_different_subs",
            "fought_superfight", "champion", "female", "point_win_ratio",
            "decision_win_ratio", "most_vulnerable",
        ]:
            if extra in col:
                result[extra] = df[extra]

        return result
