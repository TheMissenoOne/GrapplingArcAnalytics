"""
ADCC Historical Matches pipeline (Kaggle: bjagrelli/adcc-historical-dataset).

1,028 matches from 1998–2022 with winner/loser, win type, stage, submission, weight class.
Used for ELO calibration and technique frequency analysis.
"""

from __future__ import annotations

import pandas as pd

from pipelines.etl import Pipeline
from pipelines.registry import DATASETS


class ADCCHistoricalPipeline(Pipeline):
    spec = DATASETS["adcc_historical"]

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.dropna(subset=["winner_name", "loser_name"])
        df["year"] = pd.to_numeric(df["year"], errors="coerce")
        df["win_type"] = df["win_type"].str.upper().str.strip()
        df["submission"] = df.get("submission", pd.Series(dtype=str)).str.strip().replace("", None)
        df["stage"] = df["stage"].str.strip().str.upper()
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        df["win_type"] = df["win_type"].apply(self._classify_win_type)
        return df.rename(columns={
            "match_id": "match_id",
            "winner_name": "winner",
            "loser_name": "loser",
            "win_type": "win_type",
            "stage": "stage",
            "submission": "submission",
            "weight_class": "weight_class",
            "sex": "sex",
            "year": "year",
        })

    @staticmethod
    def _classify_win_type(x: str) -> str:
        if "SUB" in x:
            return "SUBMISSION"
        if "DEC" in x or "REF" in x:
            return "DECISION"
        if "DQ" in x:
            return "DQ"
        if "INJ" in x:
            return "INJURY"
        return "POINTS"
