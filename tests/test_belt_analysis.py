"""Belt-level analysis tests — synthetic frames, no network."""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from analysis.belt_analysis import join_fighters, team_dominance, win_type_by_team


def test_join_fighters_hit_miss(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    adcc = pd.DataFrame({
        "winner": ["Gordon Ryan", "Rodolfo Vieira", "John Danaher Jr"],
        "year": [2019, 2017, 2022],
        "stage": ["F", "SF", "R1"],
        "win_type": ["SUBMISSION", "POINTS", "DECISION"],
    })
    heroes = pd.DataFrame({
        "fighter_name": ["Gordon Ryan", "Rodolfo Vieira"],
        "belt": ["black", "black"],
        "team": ["Danaher Death Squad", "Alliance"],
    })

    joined = join_fighters(adcc, heroes)

    assert len(joined) == 3
    belt_hits = joined["belt"].notna().sum()
    assert belt_hits == 2
    assert joined["belt"].iloc[0] == "black"
    assert joined["belt"].iloc[1] == "black"
    assert pd.isna(joined["belt"].iloc[2])
    assert joined["team"].iloc[0] == "Danaher Death Squad"
    assert joined["team"].iloc[1] == "Alliance"
    assert pd.isna(joined["team"].iloc[2])
    assert "66.7%" in caplog.text


def test_join_empty_adcc() -> None:
    adcc = pd.DataFrame(columns=["winner", "year", "stage", "win_type"])
    heroes = pd.DataFrame({"fighter_name": ["Gordon Ryan"], "belt": ["black"], "team": ["DDS"]})
    joined = join_fighters(adcc, heroes)
    assert joined.empty


def test_join_empty_heroes() -> None:
    adcc = pd.DataFrame({
        "winner": ["Gordon Ryan"], "year": [2019],
        "stage": ["F"], "win_type": ["SUB"],
    })
    heroes = pd.DataFrame(columns=["fighter_name", "belt", "team"])
    joined = join_fighters(adcc, heroes)
    assert joined.empty


def test_team_dominance() -> None:
    joined = pd.DataFrame({
        "winner": ["A", "B", "C", "D", "E"] * 3,
        "year": [2019] * 8 + [2022] * 7,
        "team": ["Alpha"] * 5 + ["Beta"] * 5 + ["Alpha"] * 5,
        "stage": ["F", "SF", "R1", "R2", "SPF", "F", "SF", "3RD", "R1", "R2",
                   "SF", "F", "R1", "4F", "8F"],
        "win_type": ["SUB"] * 15,
    })

    result = team_dominance(joined)

    assert list(result.columns) == ["year", "team", "wins", "medals"]
    assert result["wins"].sum() == 15

    alpha_2019 = result[(result["year"] == 2019) & (result["team"] == "Alpha")]
    assert len(alpha_2019) == 1
    assert alpha_2019["wins"].iloc[0] == 5
    assert alpha_2019["medals"].iloc[0] == 3  # F, SF, SPF

    beta_2019 = result[(result["year"] == 2019) & (result["team"] == "Beta")]
    assert len(beta_2019) == 1
    assert beta_2019["wins"].iloc[0] == 3
    assert beta_2019["medals"].iloc[0] == 3  # F, SF, 3RD


def test_team_dominance_empty_team() -> None:
    joined = pd.DataFrame({
        "winner": ["A", "B"],
        "year": [2019, 2019],
        "team": ["", "Alpha"],
        "stage": ["F", "R1"],
        "win_type": ["SUB", "POINTS"],
    })

    result = team_dominance(joined)

    assert len(result) == 1
    assert result["team"].iloc[0] == "Alpha"
    assert result["wins"].iloc[0] == 1


def test_team_dominance_no_team_col() -> None:
    df = pd.DataFrame({"winner": ["A"], "year": [2019], "stage": ["F"]})
    result = team_dominance(df)
    assert result.empty
    assert list(result.columns) == ["year", "team", "wins", "medals"]


def test_team_dominance_empty_df() -> None:
    result = team_dominance(pd.DataFrame())
    assert result.empty
    assert list(result.columns) == ["year", "team", "wins", "medals"]


def test_win_type_by_team() -> None:
    joined = pd.DataFrame({
        "winner": ["A", "B", "C", "D", "E"],
        "team": ["Alpha"] * 2 + ["Beta"] * 3,
        "win_type": ["SUBMISSION", "POINTS", "SUBMISSION", "DECISION", "POINTS"],
    })

    result = win_type_by_team(joined)

    assert list(result.columns) == ["team", "win_type", "count"]

    alpha_sub = result[(result["team"] == "Alpha") & (result["win_type"] == "SUBMISSION")]
    assert alpha_sub["count"].iloc[0] == 1

    alpha_pts = result[(result["team"] == "Alpha") & (result["win_type"] == "POINTS")]
    assert alpha_pts["count"].iloc[0] == 1

    beta_sub = result[(result["team"] == "Beta") & (result["win_type"] == "SUBMISSION")]
    assert beta_sub["count"].iloc[0] == 1

    beta_dec = result[(result["team"] == "Beta") & (result["win_type"] == "DECISION")]
    assert beta_dec["count"].iloc[0] == 1

    beta_pts = result[(result["team"] == "Beta") & (result["win_type"] == "POINTS")]
    assert beta_pts["count"].iloc[0] == 1

    assert result["count"].sum() == 5


def test_win_type_empty_team() -> None:
    joined = pd.DataFrame({
        "winner": ["A", "B", "C"],
        "team": ["", "Alpha", ""],
        "win_type": ["SUBMISSION", "POINTS", "DECISION"],
    })

    result = win_type_by_team(joined)

    assert len(result) == 1
    assert result["team"].iloc[0] == "Alpha"
    assert result["win_type"].iloc[0] == "POINTS"
    assert result["count"].iloc[0] == 1


def test_win_type_by_team_no_cols() -> None:
    result = win_type_by_team(pd.DataFrame({"winner": ["A"]}))
    assert result.empty
    assert list(result.columns) == ["team", "win_type", "count"]


def test_win_type_by_team_empty_df() -> None:
    result = win_type_by_team(pd.DataFrame())
    assert result.empty
    assert list(result.columns) == ["team", "win_type", "count"]
