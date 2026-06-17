"""Roboflow object-detection backend — frame → position-class probabilities.

Wraps a Roboflow bjj3 model (self-hosted via the ``inference`` package) and turns a
frame into the same ``{class: prob}`` shape as ``cv.inference.classify_pose_pair_probs``,
so it drops into the existing ``vocab_map → rerank → segment → export`` pipeline as a
``/classify`` backend — no pose estimation, no sklearn model.

Both athletes' detections are aggregated per ViCoS class (the paper's two-person finding),
and classes are converted to the ViCoS ``"{position}_{role}"`` form via
:func:`cv.roboflow_labels.roboflow_to_vicos` so role flows through to ``actor``.

The model is built lazily; tests inject ``predict_fn`` (or ``model``) so no network or the
heavy ``inference`` dependency is needed in CI.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

import numpy as np

from cv.roboflow_labels import roboflow_to_vicos

logger = logging.getLogger(__name__)

# A predictor turns one frame into a list of (class_name, confidence) detections.
Predictor = Callable[[np.ndarray], list[tuple[str, float]]]


def _extract(resp: Any) -> list[tuple[str, float]]:
    """Adapt an ``inference`` result (object or dict) to (class_name, confidence)."""
    if isinstance(resp, list):
        resp = resp[0] if resp else {}
    preds = getattr(resp, "predictions", None)
    if preds is None and isinstance(resp, dict):
        preds = resp.get("predictions", [])
    out: list[tuple[str, float]] = []
    for p in preds or []:
        if isinstance(p, dict):
            name = p.get("class") or p.get("class_name")
            conf = p.get("confidence")
        else:
            name = getattr(p, "class_name", None) or getattr(p, "class_", None)
            conf = getattr(p, "confidence", None)
        if name is not None and conf is not None:
            out.append((str(name), float(conf)))
    return out


class RoboflowClassifier:
    """Classify a frame into position-class probabilities via a Roboflow model."""

    def __init__(
        self,
        model_id: str,
        api_key: str | None = None,
        conf: float = 0.4,
        model: Any | None = None,
        predict_fn: Predictor | None = None,
    ) -> None:
        """
        Parameters
        ----------
        model_id : str
            Roboflow model id, e.g. ``"bjj3/1"`` or the full workspace slug.
        api_key : str or None
            Defaults to ``$ROBOFLOW_API_KEY`` (used once to fetch weights).
        conf : float
            Drop detections below this confidence.
        model : Any or None
            A pre-built inference model (skips lazy construction).
        predict_fn : callable or None
            ``frame -> [(class_name, confidence)]`` override for tests / alt backends.
        """
        self.model_id = model_id
        self.api_key = api_key
        self.conf = conf
        self._model = model
        self._predict_fn = predict_fn

    def _predict(self, frame: np.ndarray) -> list[tuple[str, float]]:
        if self._predict_fn is not None:
            return self._predict_fn(frame)
        if self._model is None:
            from inference import get_model  # heavy import, deferred

            key = self.api_key or os.environ.get("ROBOFLOW_API_KEY")
            self._model = get_model(self.model_id, api_key=key)
        return _extract(self._model.infer(frame))

    def classify_frame_probs(
        self, frame: np.ndarray, role_map: dict[int, str] | None = None
    ) -> dict[str, float]:
        """Frame → ``{vicos_class: probability}`` (L1-normalized).

        Detections below ``conf`` are dropped; the rest are converted to ViCoS classes
        and their confidences summed per class (so both athletes' boxes count), then
        L1-normalized. Empty when nothing is detected.
        """
        agg: dict[str, float] = {}
        for class_name, confidence in self._predict(frame):
            if confidence < self.conf:
                continue
            label = roboflow_to_vicos(class_name, role_map)
            agg[label] = agg.get(label, 0.0) + confidence
        total = sum(agg.values())
        return {k: v / total for k, v in agg.items()} if total > 0 else {}
