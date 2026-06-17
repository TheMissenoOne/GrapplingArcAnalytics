"""Tests for fighter similarity — synthetic vectors, cosine values, masking."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.similarity import (
    fighter_vectors,
    find_similar_fighters,
    top_similar,
    user_vector,
)


@pytest.fixture
def mini_adcc() -> pd.DataFrame:
    """4 fighters, mixed win types and submissions."""
    return pd.DataFrame({
        "winner": ["A", "A", "A", "B", "B", "B", "C", "C", "C", "D", "D", "D"],
        "win_type": [
            "SUBMISSION", "SUBMISSION", "POINTS",
            "SUBMISSION", "DECISION", "POINTS",
            "POINTS", "POINTS", "POINTS",
            "SUBMISSION", "SUBMISSION", "SUBMISSION",
        ],
        "submission": [
            "RNC", "armbar", None,
            "heel hook", None, None,
            None, None, None,
            "RNC", "triangle", "kimura",
        ],
        "year": [2019, 2021, 2022, 2019, 2021, 2022, 2019, 2021, 2022, 2019, 2021, 2022],
        "weight_class": ["77", "77", "77", "88", "88", "88", "99", "99", "99", "77", "77", "77"],
        "sex": ["M", "M", "M", "M", "M", "M", "M", "M", "M", "M", "M", "M"],
        "stage": ["F", "SF", "R1", "F", "SF", "R1", "F", "SF", "R1", "F", "SF", "R1"],
    })


@pytest.fixture
def mini_fighters() -> pd.DataFrame:
    return pd.DataFrame({
        "fighter_name": ["A", "B", "C", "D"],
        "sub_ratio": [0.8, 0.5, 0.1, 1.0],
        "win_ratio": [0.9, 0.7, 0.3, 1.0],
        "favorite_target": ["Neck", "Leg", "Other/Unknown", "Arm"],
    })


@pytest.fixture
def mini_elo() -> pd.DataFrame:
    return pd.DataFrame({
        "fighter": ["A", "B", "C", "D"],
        "elo": [1200, 1100, 900, 1300],
        "matches": [10, 8, 5, 12],
        "wins": [9, 6, 2, 12],
        "losses": [1, 2, 3, 0],
        "last_year": [2022, 2022, 2022, 2022],
    })


def test_fighter_vectors_shape(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    assert not vectors.empty
    assert "fighter" in vectors.columns
    assert vectors.shape[0] == 4


def test_fighter_vectors_normalized(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    feature_cols = [c for c in vectors.columns if c != "fighter"]
    norms = np.linalg.norm(vectors[feature_cols].values, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_fighter_vectors_min_match_filter(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    extra = pd.DataFrame({
        "winner": ["E"],
        "win_type": ["SUBMISSION"],
        "submission": ["RNC"],
        "year": [2022],
        "weight_class": ["77"],
        "sex": ["M"],
        "stage": ["F"],
    })
    adcc = pd.concat([mini_adcc, extra], ignore_index=True)
    fighters = pd.concat([mini_fighters, pd.DataFrame([{
        "fighter_name": "E", "sub_ratio": 0.0, "win_ratio": 0.0, "favorite_target": "",
    }])], ignore_index=True)
    vectors = fighter_vectors(adcc, fighters, mini_elo)
    assert "E" not in vectors["fighter"].values


def test_top_similar_identical(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    feature_cols = [c for c in vectors.columns if c != "fighter"]
    a_vec = vectors.loc[0, feature_cols].values.astype(np.float64)
    similar = top_similar(a_vec, vectors, k=3)
    assert len(similar) == 3
    assert similar.iloc[0]["fighter"] == vectors.iloc[0]["fighter"]
    assert similar.iloc[0]["similarity"] == pytest.approx(1.0, abs=1e-4)


def test_top_similar_orthogonal(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    feature_cols = [c for c in vectors.columns if c != "fighter"]
    first_vec = vectors.loc[0, feature_cols].values.astype(np.float64)
    opposite = -first_vec
    similar = top_similar(opposite, vectors, k=1)
    assert similar.iloc[0]["fighter"] != vectors.iloc[0]["fighter"]


def test_top_similar_k_greater_than_n(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    feature_cols = [c for c in vectors.columns if c != "fighter"]
    a_vec = vectors.loc[0, feature_cols].values.astype(np.float64)
    similar = top_similar(a_vec, vectors, k=100)
    assert len(similar) == len(vectors)


def test_masking_excludes_dims(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    feature_cols = [c for c in vectors.columns if c != "fighter"]
    a_vec = vectors.loc[0, feature_cols].values.astype(np.float64)

    full = top_similar(a_vec, vectors, k=3)

    sub_cols = [c for c in feature_cols if c.startswith("sub_")]
    masked = top_similar(a_vec, vectors, k=3, mask=sub_cols)
    assert not full.equals(masked)


def test_user_vector_returns_mask(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    feature_cols = [c for c in vectors.columns if c != "fighter"]

    user_profile = pd.DataFrame({
        "technique": ["rear naked choke", "armbar", "heel hook"],
        "attempts": [3, 2, 1],
        "successes": [2, 1, 1],
        "user_share": [0.5, 0.33, 0.17],
    })
    vec, mask = user_vector(user_profile, mini_adcc, feature_cols)
    assert len(vec) == len(feature_cols)
    assert len(mask) > 0
    assert all(col in feature_cols for col in mask)


def test_user_vector_unknown_technique_handling(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    vectors = fighter_vectors(mini_adcc, mini_fighters, mini_elo)
    feature_cols = [c for c in vectors.columns if c != "fighter"]

    user_profile = pd.DataFrame({
        "technique": ["completely made up technique"],
        "attempts": [1],
        "successes": [0],
        "user_share": [1.0],
    })
    vec, mask = user_vector(user_profile, mini_adcc, feature_cols)
    assert len(mask) == 0


def test_find_similar_fighters_self_match(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    similar = find_similar_fighters(mini_adcc, mini_fighters, mini_elo, "A", k=2)
    assert len(similar) == 2
    assert "A" not in similar["fighter"].values


def test_find_similar_fighters_unknown(
    mini_adcc: pd.DataFrame, mini_fighters: pd.DataFrame, mini_elo: pd.DataFrame,
) -> None:
    similar = find_similar_fighters(mini_adcc, mini_fighters, mini_elo, "UNKNOWN", k=2)
    assert similar.empty
