"""Belt-level comparison analysis — join BJJ Heroes with ADCC outcomes."""

from __future__ import annotations

import logging

import pandas as pd

from analysis.names import _normalize_name

logger = logging.getLogger(__name__)


def join_fighters(adcc_df: pd.DataFrame, heroes_df: pd.DataFrame) -> pd.DataFrame:
    """Join ADCC matches with BJJ Heroes belt/team data on normalized name.

    Creates name_norm columns on both sides, left-joins adcc -> heroes.
    Reports hit-rate via logger.info.
    """
    if adcc_df.empty or heroes_df.empty:
        logger.warning("Empty DataFrame - returning empty join")
        return pd.DataFrame()

    adcc = adcc_df.copy()
    heroes = heroes_df.copy()

    adcc["name_norm"] = adcc["winner"].apply(_normalize_name)
    heroes["name_norm"] = heroes["fighter_name"].apply(_normalize_name)

    joined = adcc.merge(
        heroes[["name_norm", "belt", "team"]], on="name_norm", how="left"
    )

    total = len(joined)
    hits = joined["belt"].notna().sum()
    hit_rate = hits / total * 100 if total else 0
    logger.info("Belt join hit-rate: %d/%d (%.1f%%)", hits, total, hit_rate)

    return joined


def team_dominance(joined: pd.DataFrame) -> pd.DataFrame:
    """Aggregate wins and medals per team per year."""
    required = {"year", "team", "stage", "winner"}
    if not required.issubset(joined.columns) or joined.empty:
        return pd.DataFrame(columns=["year", "team", "wins", "medals"])

    medal_stages = {"SF", "F", "SPF", "3RD"}
    df = joined.copy()
    df = df[df["team"].notna() & (df["team"] != "")]

    if df.empty:
        return pd.DataFrame(columns=["year", "team", "wins", "medals"])

    result = df.groupby(["year", "team"]).agg(
        wins=("year", "count"),
        medals=("stage", lambda s: s.isin(medal_stages).sum()),
    ).reset_index()

    return result.sort_values(
        ["wins", "medals"], ascending=False
    ).reset_index(drop=True)


def win_type_by_team(joined: pd.DataFrame) -> pd.DataFrame:
    """Submission vs points vs decision mix per team."""
    required = {"team", "win_type"}
    if not required.issubset(joined.columns) or joined.empty:
        return pd.DataFrame(columns=["team", "win_type", "count"])

    df = joined.copy()
    df = df[df["team"].notna() & (df["team"] != "")]

    if df.empty:
        return pd.DataFrame(columns=["team", "win_type", "count"])

    result = df.groupby(["team", "win_type"]).size().reset_index(name="count")
    return result.sort_values(
        ["team", "count"], ascending=[True, False]
    ).reset_index(drop=True)
