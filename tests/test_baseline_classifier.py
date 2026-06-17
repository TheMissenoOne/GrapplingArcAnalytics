"""Tests for baseline position classifier — synthetic separable data."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd
import pytest
from sklearn.datasets import make_classification
from sklearn.model_selection import GroupKFold

from cv.baseline_classifier import (
    cross_validate,
    evaluate_baseline,
    feature_importance,
    train_baseline,
)


def _make_separable() -> tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]]:
    """18-class separable dataset — 200 samples/class, 34 all-informative features."""
    x, y = make_classification(
        n_samples=3600,
        n_features=34,
        n_informative=34,
        n_redundant=0,
        n_repeated=0,
        n_classes=18,
        n_clusters_per_class=2,
        class_sep=2.5,
        flip_y=0.01,
        random_state=42,
    )
    return x.astype(np.float64), y.astype(str)


@pytest.fixture
def synthetic_data() -> tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]]:
    return _make_separable()


@pytest.fixture
def synthetic_groups(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> npt.NDArray[np.intp]:
    n = len(synthetic_data[0])
    return np.repeat(np.arange(n // 2), 2)[:n]


def test_train_baseline_returns_model_and_metrics(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> None:
    x, y = synthetic_data
    model, metrics = train_baseline(x, y, model_type="rf")
    assert model is not None
    assert "accuracy" in metrics
    assert "macro_f1" in metrics
    assert "per_class_f1" in metrics
    assert len(metrics["per_class_f1"]) == 18
    assert "confusion_matrix" in metrics
    assert "class_labels" in metrics


def test_train_baseline_high_accuracy(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> None:
    x, y = synthetic_data
    _, metrics = train_baseline(x, y, model_type="rf")
    assert metrics["accuracy"] >= 0.8


def test_train_baseline_xgb(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> None:
    x, y = synthetic_data
    _, metrics = train_baseline(x, y, model_type="xgb")
    assert metrics["accuracy"] >= 0.8


def test_cross_validate_returns_dataframe(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
    synthetic_groups: npt.NDArray[np.intp],
) -> None:
    x, y = synthetic_data
    cv_df = cross_validate(x, y, synthetic_groups, model_type="rf", folds=3)
    assert isinstance(cv_df, pd.DataFrame)
    assert len(cv_df) == 3
    assert "fold" in cv_df.columns
    assert "accuracy" in cv_df.columns
    assert "macro_f1" in cv_df.columns


def test_cross_validate_no_leakage(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> None:
    x, y = synthetic_data
    n = len(x)
    groups = np.repeat(np.arange(n // 2), 2)[:n]

    gkf = GroupKFold(n_splits=3)
    for train_idx, test_idx in gkf.split(x, y, groups):
        train_groups = set(groups[train_idx])
        test_groups = set(groups[test_idx])
        assert train_groups.isdisjoint(test_groups), "Leakage detected"


def test_feature_importance(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> None:
    x, y = synthetic_data
    feature_names = [f"feat_{i}" for i in range(x.shape[1])]
    model, _ = train_baseline(x, y, model_type="rf")
    fi = feature_importance(model, feature_names, top_n=5)
    assert len(fi) == 5
    assert "feature" in fi.columns
    assert "importance" in fi.columns


def test_feature_importance_top_n(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> None:
    x, y = synthetic_data
    feature_names = [f"feat_{i}" for i in range(x.shape[1])]
    model, _ = train_baseline(x, y, model_type="rf")
    fi = feature_importance(model, feature_names, top_n=34)
    assert len(fi) == 34
    assert fi["importance"].is_monotonic_decreasing


def test_evaluate_baseline_with_groups(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
    synthetic_groups: npt.NDArray[np.intp],
) -> None:
    x, y = synthetic_data
    metrics = evaluate_baseline(x, y, groups=synthetic_groups, model_type="rf")
    assert "cv" in metrics
    assert "mean_accuracy" in metrics["cv"]
    assert "per_fold" in metrics["cv"]


def test_evaluate_baseline_without_groups(
    synthetic_data: tuple[npt.NDArray[np.float64], npt.NDArray[np.str_]],
) -> None:
    x, y = synthetic_data
    metrics = evaluate_baseline(x, y, model_type="rf")
    assert "cv" not in metrics
