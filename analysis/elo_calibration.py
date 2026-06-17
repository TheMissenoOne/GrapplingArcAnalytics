"""ADCC ELO calibration — compute ELO ratings and calibrate K-factors against app."""

from __future__ import annotations

import logging

import pandas as pd

WIN_TYPE_MULT: dict[str, float] = {
    "SUBMISSION": 1.15,
    "DECISION": 0.85,
    "POINTS": 1.0,
    "DQ": 1.0,
    "INJURY": 1.0,
}

STAGE_MULT: dict[str, float] = {
    "SPF": 1.4,
    "F": 1.3,
    "SF": 1.2,
    "3RD": 1.15,
    "3PLC": 1.15,
    "R2": 1.0,
    "R1": 1.0,
    "E1": 1.0,
    "8F": 1.0,
    "4F": 1.0,
}

INITIAL_ELO: float = 1000.0


def _k_factor(base_k: float, win_type: str, stage: str) -> float:
    win_mult = WIN_TYPE_MULT.get(win_type, 1.0)
    if win_type not in WIN_TYPE_MULT:
        logging.warning("Unknown win_type '%s', defaulting to 1.0", win_type)
    stage_mult = STAGE_MULT.get(stage, 1.0)
    if stage not in STAGE_MULT:
        logging.warning("Unknown stage '%s', defaulting to 1.0", stage)
    return base_k * win_mult * stage_mult


def _expected(elo_a: float, elo_b: float) -> float:
    result: float = 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))
    return result


def compute_adcc_elo(df: pd.DataFrame, base_k: float = 40.0) -> pd.DataFrame:
    """Compute ELO ratings for ADCC fighters.

    Parameters
    ----------
    df : pd.DataFrame
        Normalized ADCC historical frame with columns:
        match_id, year, winner, loser, win_type, stage, submission, weight_class, sex.
    base_k : float, default 40.0
        Base K-factor before win-type and stage multipliers.

    Returns
    -------
    pd.DataFrame
        Columns: fighter, elo, matches, wins, losses, last_year.
    """
    df_sorted = df.sort_values(["year", "match_id"]).reset_index(drop=True)

    elo: dict[str, float] = {}
    matches: dict[str, int] = {}
    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    last_year: dict[str, int] = {}

    for _, row in df_sorted.iterrows():
        winner: str = row["winner"]
        loser: str = row["loser"]
        win_type: str = row["win_type"] if row["win_type"] is not None else "POINTS"
        stage: str = row["stage"] if row["stage"] is not None else "R1"
        year: int = int(row["year"])

        k = _k_factor(base_k, win_type, stage)

        if winner not in elo:
            elo[winner] = INITIAL_ELO
            matches[winner] = 0
            wins[winner] = 0
            losses[winner] = 0
            last_year[winner] = 0
        if loser not in elo:
            elo[loser] = INITIAL_ELO
            matches[loser] = 0
            wins[loser] = 0
            losses[loser] = 0
            last_year[loser] = 0

        exp_w = _expected(elo[winner], elo[loser])
        delta = k * (1.0 - exp_w)

        elo[winner] += delta
        elo[loser] -= delta
        matches[winner] += 1
        matches[loser] += 1
        wins[winner] += 1
        losses[loser] += 1
        last_year[winner] = max(last_year[winner], year)
        last_year[loser] = max(last_year[loser], year)

    return pd.DataFrame(
        {
            "fighter": list(elo.keys()),
            "elo": list(elo.values()),
            "matches": [matches[f] for f in elo],
            "wins": [wins[f] for f in elo],
            "losses": [losses[f] for f in elo],
            "last_year": [last_year[f] for f in elo],
        },
    )


def calibrate_k_factor(
    df: pd.DataFrame,
    target_std: float,
    k_grid: list[float] | None = None,
) -> float:
    """Grid-search base K minimizing |std(elo) - target_std|.

    Parameters
    ----------
    df : pd.DataFrame
        Normalized ADCC historical frame.
    target_std : float
        Target standard deviation of ELO ratings to match.
    k_grid : list[float] | None, default None
        Candidate base-K values. If None or empty, returns 40.0.
        Default grid when None: range(10, 81, 5).

    Returns
    -------
    float
        Grid member closest to target.
    """
    if k_grid is None or len(k_grid) == 0:
        return 40.0

    best_k = k_grid[0]
    best_err = abs(compute_adcc_elo(df, base_k=best_k)["elo"].std() - target_std)

    for k in k_grid[1:]:
        err = abs(compute_adcc_elo(df, base_k=k)["elo"].std() - target_std)
        if err < best_err:
            best_err = err
            best_k = k

    return best_k
