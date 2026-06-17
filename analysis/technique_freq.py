"""Position frequency, submission rates, and trend analysis for BJJ techniques."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from analysis.names import _normalize_adcc_sub


def position_distribution(tech_df: pd.DataFrame) -> pd.DataFrame:
    """Counts per bjj_position × technique_type from normalized grappling_techniques frame.

    Input columns: technique_name, bjj_position, technique_type, martial_art
    Returns pivot table.
    """
    return pd.crosstab(tech_df["bjj_position"], tech_df["technique_type"])


def submission_frequency(
    adcc_df: pd.DataFrame,
    by: Literal["year", "weight_class", "sex", "stage"] = "year",
) -> pd.DataFrame:
    """Normalized submission counts per group — shares sum to 1 per group.

    Input: normalized ADCC historical frame.
    Normalizes submission names via _normalize_adcc_sub.
    Pivots submission × ``by`` with group-wise proportions.
    """
    subs = adcc_df.dropna(subset=["submission"]).copy()
    subs["sub_clean"] = subs["submission"].apply(_normalize_adcc_sub)
    pivot = pd.crosstab(subs["sub_clean"], subs[by])
    return pivot.div(pivot.sum(axis=0), axis=1)


def submission_trend(adcc_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Per-year share of the top-N most common submissions.

    Returns year × submission pivot with shares (sums to 1 per year).
    """
    subs = adcc_df.dropna(subset=["submission"]).copy()
    subs["sub_clean"] = subs["submission"].apply(_normalize_adcc_sub)
    top_subs = subs["sub_clean"].value_counts().head(top_n).index
    top = subs[subs["sub_clean"].isin(top_subs)]
    pivot = pd.crosstab(top["sub_clean"], top["year"])
    return pivot.div(pivot.sum(axis=0), axis=1)
