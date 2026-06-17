"""Runtime inference — keypoints -> position label.

Loads a trained baseline classifier (``cv.baseline_classifier``) plus its sidecar
label meta and exposes a clean ``keypoints -> (label, confidence)`` path for the
realtime backend. Produces a feature vector via :func:`cv.pose_features.pair_to_features`,
so the served vector is byte-identical to the trained one.

The pose estimator (``cv.pose_estimate``) is imported lazily, so this module stays
usable — and unit-testable — without the heavy estimator dependencies.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import joblib
import numpy as np

from .baseline_classifier import MODEL_DIR, _meta_path
from .pose_features import pair_to_features

if TYPE_CHECKING:
    from .pose_estimate import PoseEstimator

logger = logging.getLogger(__name__)


@dataclass
class ClassifierBundle:
    """A loaded classifier plus the metadata needed to decode its predictions."""

    model: Any
    classes: list[str]
    feature_names: list[str]
    model_type: str

    def decode(self, index: int) -> str:
        """Map a predicted class index back to its label."""
        return self.classes[index]


def load_classifier(model_type: str = "rf") -> ClassifierBundle:
    """Load a trained classifier and its sidecar label meta.

    Parameters
    ----------
    model_type : str
        ``"rf"`` or ``"xgb"``.

    Returns
    -------
    ClassifierBundle

    Raises
    ------
    FileNotFoundError
        If the model artifact or its sidecar meta is missing. The meta is written
        by ``train_baseline`` (or backfilled via ``scripts/backfill_clf_meta.py``);
        without it predictions cannot be decoded to labels.
    """
    model_path = MODEL_DIR / f"position_clf_{model_type}.joblib"
    meta_path = _meta_path(model_type)
    if not model_path.exists():
        raise FileNotFoundError(f"Model artifact not found: {model_path}")
    if not meta_path.exists():
        raise FileNotFoundError(
            f"Classifier meta not found: {meta_path}. Run "
            f"`python scripts/backfill_clf_meta.py --model-type {model_type}` "
            "or retrain via train_baseline."
        )

    model = joblib.load(model_path)
    meta = json.loads(meta_path.read_text())
    classes = list(meta["classes"])

    n_model = len(getattr(model, "classes_", classes))
    if n_model != len(classes):
        raise ValueError(
            f"Meta has {len(classes)} classes but model has {n_model}; "
            f"meta and artifact are out of sync ({meta_path})."
        )

    return ClassifierBundle(
        model=model,
        classes=classes,
        feature_names=list(meta["feature_names"]),
        model_type=model_type,
    )


def classify_pose_pair(
    kp0: np.ndarray,
    kp1: np.ndarray,
    bundle: ClassifierBundle,
) -> tuple[str, float]:
    """Classify a single pair of athletes into a position label.

    Parameters
    ----------
    kp0, kp1 : np.ndarray
        ``(17, 3)`` COCO keypoints ``[x, y, confidence]`` (pixel coords) for
        athlete 0 and athlete 1 — ordering must match training's ``athlete_idx``.
    bundle : ClassifierBundle

    Returns
    -------
    tuple[str, float]
        ``(label, confidence)`` where confidence is the model's probability for the
        predicted class (``1.0`` if the model exposes no ``predict_proba``).
    """
    probs = classify_pose_pair_probs(kp0, kp1, bundle)
    label = max(probs, key=lambda k: probs[k])
    return label, probs[label]


def classify_pose_pair_probs(
    kp0: np.ndarray,
    kp1: np.ndarray,
    bundle: ClassifierBundle,
) -> dict[str, float]:
    """Full per-class probability map for a pair of athletes.

    Used by the prediction loop (``analysis.priors``) to re-rank against athlete
    priors. Keyed by the human-readable class label (``bundle.classes``).

    Parameters
    ----------
    kp0, kp1 : np.ndarray
        ``(17, 3)`` COCO keypoints (pixel coords); ordering must match training's
        ``athlete_idx``.
    bundle : ClassifierBundle

    Returns
    -------
    dict[str, float]
        ``{class_label: probability}``. If the model exposes no ``predict_proba``,
        returns ``{predicted_label: 1.0}``.
    """
    features = pair_to_features(kp0, kp1).reshape(1, -1)
    proba = getattr(bundle.model, "predict_proba", None)
    if callable(proba):
        probs = proba(features)[0]
        # predict_proba columns are ordered by model.classes_ (encoded ints);
        # map each back to its label via bundle.classes.
        return {
            bundle.decode(int(cls)): float(p)
            for cls, p in zip(bundle.model.classes_, probs)
        }
    pred_idx = int(bundle.model.predict(features)[0])
    return {bundle.decode(pred_idx): 1.0}


def classify_frame(
    frame: np.ndarray,
    bundle: ClassifierBundle,
    estimator: PoseEstimator,
) -> tuple[str, float] | None:
    """Estimate poses in a frame, select the grappler pair, and classify.

    Parameters
    ----------
    frame : np.ndarray
        ``(H, W, 3)`` BGR/RGB image.
    bundle : ClassifierBundle
    estimator : PoseEstimator
        A ``cv.pose_estimate.PoseEstimator`` (imported by the caller to avoid
        pulling heavy deps into this module).

    Returns
    -------
    tuple[str, float] or None
        ``(label, confidence)``, or ``None`` if fewer than two athletes were detected.
    """
    poses = estimator.estimate(frame)
    pair = estimator.select_grappler_pair(poses)
    if pair is None:
        return None
    kp0, kp1 = pair
    return classify_pose_pair(kp0, kp1, bundle)
