"""Technique frequency analysis tests — synthetic frames, no network."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.names import _normalize_adcc_sub
from analysis.technique_freq import (
    position_distribution,
    submission_frequency,
    submission_trend,
)


def test_normalize_adcc_sub() -> None:
    assert _normalize_adcc_sub("inside heel hook") == "heel hook"
    assert _normalize_adcc_sub("outside heel hook") == "heel hook"
    assert _normalize_adcc_sub("rnc") == "rear naked choke"
    assert _normalize_adcc_sub("RNC") == "rear naked choke"
    assert _normalize_adcc_sub("Heel Hook") == "heel hook"
    assert _normalize_adcc_sub("rear naked choke") == "rear naked choke"
    assert _normalize_adcc_sub("armbar") == "armbar"


def _synthetic_techniques() -> pd.DataFrame:
    return pd.DataFrame({
        "technique_name": ["Armbar", "Triangle", "Kimura", "Scissor Sweep", "Bridge Escape"],
        "bjj_position": ["guard", "guard", "side_control", "guard", "mount"],
        "technique_type": ["submission", "submission", "submission", "sweep", "escape"],
        "martial_art": ["BJJ", "BJJ", "BJJ", "BJJ", "BJJ"],
    })


def test_position_distribution_shape() -> None:
    tech_df = _synthetic_techniques()
    result = position_distribution(tech_df)
    assert list(result.index) == ["guard", "mount", "side_control"]
    assert list(result.columns) == ["escape", "submission", "sweep"]
    assert result.loc["guard", "submission"] == 2
    assert result.loc["mount", "escape"] == 1
    assert result.loc["side_control", "submission"] == 1
    assert result.values.sum() == 5


def test_submission_frequency_merges_variants() -> None:
    df = pd.DataFrame({
        "submission": [
            "inside heel hook", "outside heel hook", "RNC",
            "rear naked choke", "armbar", "inside heel hook",
        ],
        "year": [2017, 2017, 2019, 2019, 2019, 2022],
    })
    result = submission_frequency(df, by="year")
    # inside + outside heel hook → heel hook (2 in 2017, 0 in 2019, 1 in 2022)
    assert "heel hook" in result.index
    assert "rear naked choke" in result.index
    # Shares sum to 1 per year
    for col in result.columns:
        assert abs(result[col].sum() - 1.0) < 1e-10
    # 2017: 2 heel hook → 1.0 share
    assert result.loc["heel hook", 2017] == 1.0
    # 2019: RNC + rear naked choke → 2 merged, + 1 armbar → 2/3, 1/3
    assert result.loc["rear naked choke", 2019] == pytest.approx(2 / 3)
    assert result.loc["armbar", 2019] == pytest.approx(1 / 3)
    # 2022: 1 heel hook → 1.0 share
    assert result.loc["heel hook", 2022] == 1.0


def test_submission_frequency_by_weight_class() -> None:
    df = pd.DataFrame({
        "submission": ["armbar", "armbar", "RNC", "guillotine"],
        "weight_class": ["77", "77", "88", "88"],
    })
    result = submission_frequency(df, by="weight_class")
    assert list(result.columns) == ["77", "88"]
    assert result["77"].sum() == pytest.approx(1.0)
    assert result["88"].sum() == pytest.approx(1.0)


def test_submission_trend_top_n() -> None:
    df = pd.DataFrame({
        "submission": [
            "armbar", "armbar", "armbar",
            "RNC", "RNC",
            "guillotine",
            "kimura",
        ],
        "year": [2017, 2019, 2022, 2017, 2019, 2017, 2022],
    })
    result = submission_trend(df, top_n=2)
    # Top 2 overall: armbar (3), RNC (2)
    assert list(result.index) == ["armbar", "rear naked choke"]
    assert list(result.columns) == [2017, 2019, 2022]
    # 2017: 2 total → armbar=0.5, rnc=0.5
    assert result.loc["armbar", 2017] == 0.5
    assert result.loc["rear naked choke", 2017] == 0.5
    # 2019: 2 total → armbar=0.5, rnc=0.5
    assert result.loc["armbar", 2019] == 0.5
    assert result.loc["rear naked choke", 2019] == 0.5
    # 2022: armbar=1.0, rnc=0.0
    assert result.loc["armbar", 2022] == 1.0
    assert result.loc["rear naked choke", 2022] == 0.0
