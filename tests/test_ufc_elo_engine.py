"""Tests for the extracted UFC Elo engine (pure, deterministic)."""

from __future__ import annotations

import pytest

from analysis.ufc_elo_engine import (
    BASE_K,
    FINISH_K_MULT,
    Fight,
    compute_ratings,
    expected_score,
    k_factor,
    update_elo,
)


def test_expected_score_symmetry() -> None:
    assert expected_score(1000, 1000) == pytest.approx(0.5)
    assert expected_score(1200, 1000) + expected_score(1000, 1200) == pytest.approx(1.0)


def test_k_factor_finish_bump() -> None:
    assert k_factor("Decision") == BASE_K
    assert k_factor("KO/TKO") == pytest.approx(BASE_K * FINISH_K_MULT)
    assert k_factor("Submission (RNC)") == pytest.approx(BASE_K * FINISH_K_MULT)


def test_update_elo_zero_sum_on_win() -> None:
    a, b = update_elo(1000, 1000, BASE_K)
    assert a > 1000 > b
    assert (a - 1000) == pytest.approx(1000 - b)  # equal-rated → symmetric swing


def test_no_contest_unchanged() -> None:
    assert update_elo(1100, 900, BASE_K, result="nc") == (1100.0, 900.0)


def test_compute_ratings_winner_climbs_and_peak_tracked() -> None:
    fights = [
        Fight("A", "B", "Decision", "win"),
        Fight("A", "C", "KO/TKO", "win"),
    ]
    current, peak = compute_ratings(fights)
    assert current["A"] > 1000 and current["B"] < 1000 and current["C"] < 1000
    assert peak["A"] >= current["A"]  # peak is the high-water mark
