"""Tests for confidence weighting (wave 9, docs/rating_v2/07_PONDERACAO_POR_CONFIANCA.md)."""

from __future__ import annotations

from analysis.confidence_weight import (
    RD_FLOOR_DEFAULT,
    athlete_weights,
    bounded_weight,
    precision_weight,
)


def test_precision_weight_positive_and_decreasing() -> None:
    assert precision_weight(50.0) > 0
    assert precision_weight(50.0) > precision_weight(100.0) > precision_weight(250.0)


def test_bounded_weight_positive_decreasing_and_bounded() -> None:
    lo, mid, hi = bounded_weight(1.0), bounded_weight(200.0), bounded_weight(2000.0)
    assert 0 < hi < mid < lo <= 1.0
    assert lo <= 1.0


def test_uniform_scheme_is_all_ones() -> None:
    w = athlete_weights(["a", "b"], {"a": 58.0, "b": 900.0}, "uniform")
    assert w == {"a": 1.0, "b": 1.0}


def test_missing_rd_falls_to_floor_never_zero() -> None:
    w_precision = athlete_weights(["known", "missing"], {"known": RD_FLOOR_DEFAULT}, "precision")
    w_bounded = athlete_weights(["known", "missing"], {"known": RD_FLOOR_DEFAULT}, "bounded")
    # same RD (floor) for both athletes -> same weight, and both strictly positive
    assert w_precision["known"] == w_precision["missing"] > 0
    assert w_bounded["known"] == w_bounded["missing"] > 0


def test_precision_scheme_normalizes_to_mean_one() -> None:
    rd_by_athlete = {"a": 58.0, "b": 250.0, "c": 900.0}
    w = athlete_weights(list(rd_by_athlete), rd_by_athlete, "precision")
    assert abs(sum(w.values()) / len(w) - 1.0) < 1e-9


def test_low_rd_outweighs_high_rd_within_each_scheme() -> None:
    rd_by_athlete = {"confident": 58.0, "unsure": 900.0}
    for scheme in ("precision", "bounded"):
        w = athlete_weights(list(rd_by_athlete), rd_by_athlete, scheme)
        assert w["confident"] > w["unsure"] > 0


def test_unknown_scheme_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        athlete_weights(["a"], {}, "nonsense")
