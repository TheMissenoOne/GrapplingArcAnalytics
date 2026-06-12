"""
Grappling Techniques pipeline (Kaggle: liiucbs/grappling-techniques).

76 techniques from BJJ, Judo, and Wrestling — used to enrich the app's
technique library and build position classifiers.
"""

from __future__ import annotations

import pandas as pd

from pipelines.etl import Pipeline
from pipelines.registry import DATASETS


class GrapplingTechniquesPipeline(Pipeline):
    spec = DATASETS["grappling_techniques"]

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.dropna(subset=["Name", "Position"])
        df.columns = [c.strip() for c in df.columns]
        if "Origin" in df.columns:
            df["Origin"] = df["Origin"].str.strip()
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        df["technique_type"] = df.get("Type", df.get("type", "")).str.lower().str.strip()
        df["position"] = df["Position"].str.strip()
        df["name"] = df["Name"].str.strip()
        df["origin"] = df.get("Origin", "").str.strip()
        return df.rename(columns={
            "name": "technique_name",
            "position": "bjj_position",
            "origin": "martial_art",
        })
