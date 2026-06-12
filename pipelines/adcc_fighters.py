"""
ADCC Fighter Stats pipeline (Kaggle: albucathecoder/adcc-fighter-stats).

Career stats per ADCC fighter: wins, losses, titles, submission ratios, favorite targets.
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

        num_cols = ["wins", "losses", "titles"]
        for c in num_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

        if "sub_ratio" in df.columns:
            df["sub_ratio"] = pd.to_numeric(df["sub_ratio"], errors="coerce").fillna(0.0)

        if "debut_year" in df.columns:
            df["debut_year"] = (
                pd.to_numeric(df["debut_year"], errors="coerce").fillna(0).astype(int)
            )

        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        name_col = next((c for c in df.columns if "fighter" in c or "name" in c), "name")
        target_col = next(
            (c for c in df.columns if "target" in c or "submission_target" in c),
            "favorite_target",
        )
        belt_col = next((c for c in df.columns if "belt" in c), "")
        team_col = next((c for c in df.columns if "team" in c or "association" in c), "")
        weight_col = next((c for c in df.columns if "weight" in c or "division" in c), "")
        win_ratio_col = next((c for c in df.columns if "win_ratio" in c or "win_rate" in c), "")

        result = pd.DataFrame({
            "fighter_name": df[name_col].str.strip(),
            "wins": df.get("wins", 0),
            "losses": df.get("losses", 0),
            "titles": df.get("titles", 0),
            "sub_ratio": df.get("sub_ratio", 0.0),
        })

        if win_ratio_col and win_ratio_col in df.columns:
            result["win_ratio"] = pd.to_numeric(df[win_ratio_col], errors="coerce")
        else:
            total = result["wins"] + result["losses"]
            result["win_ratio"] = (result["wins"] / total.replace(0, 1)).round(3)

        if target_col:
            result["favorite_target"] = df[target_col].str.strip()
        if belt_col:
            result["belt"] = df[belt_col].str.strip()
        if team_col:
            result["team"] = df[team_col].str.strip()
        if weight_col:
            result["weight_class"] = df[weight_col].str.strip()

        debut = df.get("debut_year", 0)
        result["debut_year"] = debut

        return result
