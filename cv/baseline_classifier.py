"""Baseline classifier — RF/XGBoost on keypoint features -> position prediction.

Target: ~80% accuracy with classic ML on pose features (phase 1).
Phase 2: ViTPose embeddings for 90%+ (future).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold, StratifiedShuffleSplit
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)


def train_baseline(
    x: np.ndarray,
    y: np.ndarray,
    model_type: Literal["rf", "xgb"] = "rf",
    seed: int = 42,
) -> tuple[Any, dict[str, Any]]:
    """Train baseline classifier with stratified 80/20 split + class weights.

    Returns (trained_model, metrics_dict) where metrics_dict contains:
      - accuracy, macro_f1, per_class_f1 (list), confusion_matrix, class_labels
    """
    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    sss = StratifiedShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
    train_idx, test_idx = next(sss.split(x, y_enc))

    x_train, x_test = x[train_idx], x[test_idx]
    y_train, y_test = y_enc[train_idx], y_enc[test_idx]

    classes, counts = np.unique(y_train, return_counts=True)
    n_samples = len(y_train)
    n_classes = len(classes)
    class_weight_dict = {
        c: n_samples / (n_classes * cnt) for c, cnt in zip(classes, counts)
    }

    if model_type == "rf":
        model: Any = RandomForestClassifier(
            n_estimators=300,
            max_depth=20,
            min_samples_leaf=4,
            class_weight=class_weight_dict,
            random_state=seed,
            n_jobs=-1,
        )
        model.fit(x_train, y_train)
    else:
        from xgboost import XGBClassifier

        sample_weights = np.array([class_weight_dict[c] for c in y_train])
        model = XGBClassifier(
            n_estimators=300,
            max_depth=10,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=seed,
            eval_metric="mlogloss",
            use_label_encoder=False,
        )
        model.fit(x_train, y_train, sample_weight=sample_weights)

    y_pred = model.predict(x_test)
    acc = accuracy_score(y_test, y_pred)
    macro_f1 = float(f1_score(y_test, y_pred, average="macro"))
    per_class = f1_score(y_test, y_pred, average=None).tolist()
    cm = confusion_matrix(y_test, y_pred).tolist()

    logger.info(
        "%s baseline: acc=%.4f, macro-F1=%.4f (%d classes)",
        model_type.upper(),
        acc,
        macro_f1,
        n_classes,
    )

    model_dir = Path(__file__).resolve().parent.parent / "data" / "processed"
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"position_clf_{model_type}.joblib"
    from joblib import dump

    dump(model, path)
    logger.info("Model saved to %s", path)

    metrics: dict[str, Any] = {
        "accuracy": round(float(acc), 4),
        "macro_f1": round(macro_f1, 4),
        "per_class_f1": [round(v, 4) for v in per_class],
        "class_labels": le.classes_.tolist(),
        "confusion_matrix": cm,
        "model_path": str(path),
    }

    return model, metrics


def cross_validate(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    model_type: Literal["rf", "xgb"] = "rf",
    folds: int = 5,
    seed: int = 42,
) -> pd.DataFrame:
    """Group-aware cross-validation — same image_id stays in same fold.

    Returns DataFrame: fold, accuracy, macro_f1.
    """
    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    gkf = GroupKFold(n_splits=folds)
    results: list[dict[str, Any]] = []

    for fold, (train_idx, test_idx) in enumerate(gkf.split(x, y_enc, groups)):
        x_train, x_test = x[train_idx], x[test_idx]
        y_train, y_test = y_enc[train_idx], y_enc[test_idx]

        classes, counts = np.unique(y_train, return_counts=True)
        n_samples = len(y_train)
        n_classes = len(classes)
        class_weight_dict = {
            c: n_samples / (n_classes * cnt) for c, cnt in zip(classes, counts)
        }

        if model_type == "rf":
            model: Any = RandomForestClassifier(
                n_estimators=200,
                max_depth=15,
                class_weight=class_weight_dict,
                random_state=seed,
                n_jobs=-1,
            )
        else:
            from xgboost import XGBClassifier

            model = XGBClassifier(
                n_estimators=200,
                max_depth=8,
                learning_rate=0.1,
                random_state=seed,
                eval_metric="mlogloss",
                use_label_encoder=False,
            )

        model.fit(x_train, y_train)
        y_pred = model.predict(x_test)

        acc = accuracy_score(y_test, y_pred)
        macro_f1 = float(f1_score(y_test, y_pred, average="macro"))

        results.append({
            "fold": fold + 1,
            "accuracy": round(float(acc), 4),
            "macro_f1": round(macro_f1, 4),
        })

    cv_df = pd.DataFrame(results)
    logger.info(
        "CV (%d-fold, %s): acc=%.4f \u00b1 %.4f",
        folds,
        model_type.upper(),
        cv_df["accuracy"].mean(),
        cv_df["accuracy"].std(),
    )

    return cv_df


def feature_importance(
    model: Any,
    feature_names: list[str],
    top_n: int = 20,
) -> pd.DataFrame:
    """Extract top-N feature importance from trained model.

    Works with RF (feature_importances_) and XGBoost.
    """
    if hasattr(model, "feature_importances_"):
        importances = model.feature_importances_
    else:
        logger.warning("Model has no feature_importances_ attribute")
        return pd.DataFrame(columns=["feature", "importance"])

    names = np.array(feature_names)
    indices = np.argsort(importances)[::-1][:top_n]

    df = pd.DataFrame({
        "feature": names[indices],
        "importance": importances[indices].round(4),
    })
    return df.reset_index(drop=True)


def evaluate_baseline(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray | None = None,
    model_type: Literal["rf", "xgb"] = "rf",
    seed: int = 42,
) -> dict[str, Any]:
    """Convenience: train + cross-validate + feature importance.

    Returns combined metrics dict.
    """
    model, metrics = train_baseline(x, y, model_type=model_type, seed=seed)

    if groups is not None and len(np.unique(groups)) > 1:
        cv_results = cross_validate(x, y, groups, model_type=model_type, seed=seed)
        metrics["cv"] = {
            "mean_accuracy": round(float(cv_results["accuracy"].mean()), 4),
            "std_accuracy": round(float(cv_results["accuracy"].std()), 4),
            "per_fold": cv_results.to_dict(orient="records"),
        }

    return metrics
