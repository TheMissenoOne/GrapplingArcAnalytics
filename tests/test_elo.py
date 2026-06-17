"""Tests for ADCC ELO calibration — known inputs, zero-sum, K-factor grid search."""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from analysis.elo_calibration import STAGE_MULT, WIN_TYPE_MULT, calibrate_k_factor, compute_adcc_elo


@pytest.fixture
def three_fighter_data() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "match_id": ["1", "2", "3", "4"],
            "year": [2019, 2019, 2020, 2021],
            "winner": ["A", "C", "B", "A"],
            "loser": ["B", "A", "C", "B"],
            "win_type": ["POINTS", "SUBMISSION", "DECISION", "POINTS"],
            "stage": ["R1", "SF", "F", "R2"],
            "submission": [None, "RNC", None, None],
            "weight_class": ["77", "77", "77", "77"],
            "sex": ["M", "M", "M", "M"],
        },
    )


def test_initial_elo_default() -> None:
    """Single match — winner +20, loser -20 (base_k=40, POINTS/R1)."""
    df = pd.DataFrame(
        {
            "match_id": ["1"],
            "year": [2019],
            "winner": ["A"],
            "loser": ["B"],
            "win_type": ["POINTS"],
            "stage": ["R1"],
            "submission": [None],
            "weight_class": ["77"],
            "sex": ["M"],
        },
    )
    result = compute_adcc_elo(df).set_index("fighter")
    assert result.loc["A", "elo"] == pytest.approx(1020.0)
    assert result.loc["B", "elo"] == pytest.approx(980.0)
    assert result.loc["A", "matches"] == 1
    assert result.loc["B", "matches"] == 1
    assert result.loc["A", "wins"] == 1
    assert result.loc["A", "losses"] == 0
    assert result.loc["B", "losses"] == 1
    assert result.loc["A", "last_year"] == 2019


def test_three_fighter_sequence(three_fighter_data: pd.DataFrame) -> None:
    """Hand-computed ELO values for A/B/C with 4 matches."""
    result = compute_adcc_elo(three_fighter_data).set_index("fighter")

    assert result.loc["A", "elo"] == pytest.approx(1011.64, abs=0.01)
    assert result.loc["B", "elo"] == pytest.approx(984.38, abs=0.01)
    assert result.loc["C", "elo"] == pytest.approx(1003.98, abs=0.01)

    assert result.loc["A", "matches"] == 3
    assert result.loc["A", "wins"] == 2
    assert result.loc["A", "losses"] == 1
    assert result.loc["A", "last_year"] == 2021

    assert result.loc["B", "matches"] == 3
    assert result.loc["B", "wins"] == 1
    assert result.loc["B", "losses"] == 2
    assert result.loc["B", "last_year"] == 2021

    assert result.loc["C", "matches"] == 2
    assert result.loc["C", "wins"] == 1
    assert result.loc["C", "losses"] == 1
    assert result.loc["C", "last_year"] == 2020


def test_zero_sum_per_match() -> None:
    """Sum of ELO deltas per match is zero (symmetric update)."""
    df = pd.DataFrame(
        {
            "match_id": ["1", "2", "3"],
            "year": [2020, 2020, 2021],
            "winner": ["X", "Y", "Z"],
            "loser": ["Y", "Z", "X"],
            "win_type": ["POINTS", "SUBMISSION", "DECISION"],
            "stage": ["R1", "SF", "F"],
            "submission": [None, "RNC", None],
            "weight_class": ["77", "77", "77"],
            "sex": ["M", "M", "M"],
        },
    )
    result = compute_adcc_elo(df)
    n = len(result)
    assert result["elo"].sum() == pytest.approx(n * 1000.0)


def test_dq_injury_same_as_points() -> None:
    """DQ and INJURY use multiplier 1.0 (same as POINTS)."""
    base = {
        "match_id": "1",
        "year": 2019,
        "winner": "A",
        "loser": "B",
        "stage": "R1",
        "submission": None,
        "weight_class": "77",
        "sex": "M",
    }
    df_dq = pd.DataFrame({**base, "win_type": "DQ"}, index=[0])
    df_injury = pd.DataFrame({**base, "win_type": "INJURY"}, index=[0])
    df_points = pd.DataFrame({**base, "win_type": "POINTS"}, index=[0])

    r_dq = compute_adcc_elo(df_dq, base_k=1.0).set_index("fighter")
    r_inj = compute_adcc_elo(df_injury, base_k=1.0).set_index("fighter")
    r_pts = compute_adcc_elo(df_points, base_k=1.0).set_index("fighter")

    assert r_dq.loc["A", "elo"] == r_pts.loc["A", "elo"] == 1000.5
    assert r_inj.loc["A", "elo"] == r_pts.loc["A", "elo"] == 1000.5


def test_calibrate_k_factor_returns_grid_member() -> None:
    """calibrate_k_factor returns one of the grid values minimizing error."""
    df = pd.DataFrame(
        {
            "match_id": ["1", "2"],
            "year": [2019, 2020],
            "winner": ["A", "B"],
            "loser": ["B", "A"],
            "win_type": ["POINTS", "SUBMISSION"],
            "stage": ["R1", "F"],
            "submission": [None, "RNC"],
            "weight_class": ["77", "77"],
            "sex": ["M", "M"],
        },
    )
    grid = [10.0, 30.0, 50.0, 70.0]
    target = 30.0
    k = calibrate_k_factor(df, target, k_grid=grid)
    assert k in grid
    errors = {g: abs(compute_adcc_elo(df, base_k=g)["elo"].std() - target) for g in grid}
    best = min(grid, key=lambda g: errors[g])
    assert k == best


def test_calibrate_k_factor_empty_grid() -> None:
    """Empty or None grid returns 40.0."""
    df = pd.DataFrame(
        {
            "match_id": [],
            "year": [],
            "winner": [],
            "loser": [],
            "win_type": [],
            "stage": [],
            "submission": [],
            "weight_class": [],
            "sex": [],
        },
    )
    assert calibrate_k_factor(df, 30.0, k_grid=[]) == 40.0
    assert calibrate_k_factor(df, 30.0, k_grid=None) == 40.0


def test_unknown_stage_win_type_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown win_type and stage fall back to 1.0 multiplier and log warning."""
    df = pd.DataFrame(
        {
            "match_id": ["1"],
            "year": [2019],
            "winner": ["A"],
            "loser": ["B"],
            "win_type": ["BOGUS"],
            "stage": ["NONSENSE"],
            "submission": [None],
            "weight_class": ["77"],
            "sex": ["M"],
        },
    )
    with caplog.at_level(logging.WARNING):
        result = compute_adcc_elo(df)
    assert "Unknown win_type" in caplog.text
    assert "Unknown stage" in caplog.text
    assert result.set_index("fighter").loc["A", "elo"] == pytest.approx(1020.0)


def test_win_type_mult_dict_contains_expected_keys() -> None:
    """Verify the win_type multiplier dict has all required entries."""
    for k in ("SUBMISSION", "DECISION", "POINTS", "DQ", "INJURY"):
        assert k in WIN_TYPE_MULT
    assert WIN_TYPE_MULT["SUBMISSION"] == 1.15
    assert WIN_TYPE_MULT["DECISION"] == 0.85
    assert WIN_TYPE_MULT["POINTS"] == 1.0
    assert WIN_TYPE_MULT["DQ"] == 1.0
    assert WIN_TYPE_MULT["INJURY"] == 1.0


def test_stage_mult_dict_contains_expected_keys() -> None:
    """Verify the stage multiplier dict has all required entries."""
    for k in ("SPF", "F", "SF", "3RD", "R2", "R1", "E1", "8F", "4F"):
        assert k in STAGE_MULT
    assert STAGE_MULT["SPF"] == 1.4
    assert STAGE_MULT["F"] == 1.3
    assert STAGE_MULT["SF"] == 1.2
    assert STAGE_MULT["3RD"] == 1.15
    for k in ("R2", "R1", "E1", "8F", "4F"):
        assert STAGE_MULT[k] == 1.0


def test_default_grid_range() -> None:
    """Default grid when calibrate_k_factor called without k_grid is 10–80 step 5."""
    df = pd.DataFrame(
        {
            "match_id": ["1", "2"],
            "year": [2019, 2020],
            "winner": ["A", "B"],
            "loser": ["B", "A"],
            "win_type": ["POINTS", "POINTS"],
            "stage": ["R1", "R1"],
            "submission": [None, None],
            "weight_class": ["77", "77"],
            "sex": ["M", "M"],
        },
    )
    k = calibrate_k_factor(df, target_std=10.0)
    expected_grid = [float(x) for x in range(10, 85, 5)]
    assert k in expected_grid
