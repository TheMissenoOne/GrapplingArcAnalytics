"""Tests for runtime inference — feature assembly, label decode, model loading.

Pure: synthetic poses + an in-memory classifier. No model download or network.
"""

from __future__ import annotations

import joblib
import numpy as np
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

from cv import baseline_classifier, inference
from cv.inference import (
    ClassifierBundle,
    classify_pose_pair,
    classify_pose_pair_probs,
    load_classifier,
)
from cv.pose_features import (
    L_ANKLE,
    L_HIP,
    L_KNEE,
    L_SHOULDER,
    NOSE,
    R_ANKLE,
    R_HIP,
    R_KNEE,
    R_SHOULDER,
    pair_to_features,
)


def _pose(y_offset: float) -> np.ndarray:
    """A simple confident pose, shifted vertically by ``y_offset``."""
    kp = np.zeros((17, 3))
    kp[NOSE, :2] = [100, 100 + y_offset]
    kp[L_SHOULDER, :2] = [80, 130 + y_offset]
    kp[R_SHOULDER, :2] = [120, 130 + y_offset]
    kp[L_HIP, :2] = [85, 230 + y_offset]
    kp[R_HIP, :2] = [115, 230 + y_offset]
    kp[L_KNEE, :2] = [85, 280 + y_offset]
    kp[R_KNEE, :2] = [115, 280 + y_offset]
    kp[L_ANKLE, :2] = [85, 330 + y_offset]
    kp[R_ANKLE, :2] = [115, 330 + y_offset]
    kp[:, 2] = 0.9
    return kp


def test_pair_to_features_shape() -> None:
    feats = pair_to_features(_pose(0), _pose(50))
    assert feats.shape == (68,)
    assert feats.dtype == np.float64


def _toy_bundle() -> tuple[ClassifierBundle, np.ndarray, np.ndarray, str]:
    """Train a tiny classifier on two separable pose-pair clusters.

    Mirrors production: fit on LabelEncoder-encoded ints so ``predict`` returns
    indices that ``ClassifierBundle.decode`` maps back to labels.
    """
    rng = np.random.default_rng(0)
    # Cluster A: tightly stacked pair; Cluster B: far-apart pair.
    pair_a = (_pose(0), _pose(20))
    pair_b = (_pose(0), _pose(200))
    rows: list[np.ndarray] = []
    labels: list[str] = []
    for _ in range(30):
        jitter = rng.normal(0, 1.0, (17, 3))
        rows.append(pair_to_features(pair_a[0] + jitter, pair_a[1] + jitter))
        labels.append("mount_top")
        rows.append(pair_to_features(pair_b[0] + jitter, pair_b[1] + jitter))
        labels.append("guard_bottom")

    x = np.stack(rows)
    le = LabelEncoder()
    y = le.fit_transform(labels)
    model = RandomForestClassifier(n_estimators=20, random_state=0)
    model.fit(x, y)
    bundle = ClassifierBundle(
        model=model,
        classes=le.classes_.tolist(),
        feature_names=[f"f{i}" for i in range(68)],
        model_type="rf",
    )
    return bundle, pair_a[0], pair_a[1], "mount_top"


def test_classify_pose_pair_roundtrip() -> None:
    bundle, kp0, kp1, expected = _toy_bundle()
    label, conf = classify_pose_pair(kp0, kp1, bundle)
    assert label == expected
    assert 0.0 <= conf <= 1.0


def test_classify_pose_pair_probs() -> None:
    bundle, kp0, kp1, expected = _toy_bundle()
    probs = classify_pose_pair_probs(kp0, kp1, bundle)
    # one entry per class, keyed by label, summing to ~1, and consistent with the
    # hard prediction.
    assert set(probs) == set(bundle.classes)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert max(probs, key=lambda k: probs[k]) == expected


def test_load_classifier_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(baseline_classifier, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(inference, "MODEL_DIR", tmp_path)

    bundle, *_ = _toy_bundle()
    joblib.dump(bundle.model, tmp_path / "position_clf_rf.joblib")
    baseline_classifier.write_classifier_meta("rf", bundle.classes, bundle.feature_names)

    loaded = load_classifier("rf")
    assert loaded.classes == bundle.classes
    assert loaded.feature_names == bundle.feature_names
    assert loaded.model_type == "rf"


def test_load_classifier_missing_meta(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(baseline_classifier, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(inference, "MODEL_DIR", tmp_path)

    bundle, *_ = _toy_bundle()
    joblib.dump(bundle.model, tmp_path / "position_clf_rf.joblib")  # no meta

    with pytest.raises(FileNotFoundError):
        load_classifier("rf")
