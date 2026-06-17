"""Roboflow object-detection backend — frame → position-class probabilities.

Calls a Roboflow bjj3 detection model over Roboflow's **hosted serverless HTTP API**
(plain ``requests`` — no heavy ``inference`` SDK, which lacks Python 3.14 wheels) and
turns a frame into the same ``{class: prob}`` shape as
``cv.inference.classify_pose_pair_probs``, so it drops into the existing
``vocab_map → rerank → segment → export`` pipeline as a ``/classify`` backend — no pose
estimation, no sklearn model.

Both athletes' detections are aggregated per ViCoS class (the paper's two-person finding),
and classes are converted to the ViCoS ``"{position}_{role}"`` form via
:func:`cv.roboflow_labels.roboflow_to_vicos` so role flows through to ``actor``.

Tests inject ``predict_fn`` so no network is needed in CI. (A self-hosted ``inference``
server can be used later by pointing ``api_url`` at it — same JSON contract.)
"""

from __future__ import annotations

import base64
import logging
import os
from collections.abc import Callable
from typing import Any

import numpy as np

from cv.roboflow_labels import roboflow_to_vicos

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://serverless.roboflow.com"

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
        api_url: str = DEFAULT_API_URL,
        timeout: float = 30.0,
        predict_fn: Predictor | None = None,
    ) -> None:
        """
        Parameters
        ----------
        model_id : str
            Roboflow model id, e.g. ``"bjj3/1"``.
        api_key : str or None
            Defaults to ``$ROBOFLOW_API_KEY``.
        conf : float
            Drop detections below this confidence.
        api_url : str
            Inference host. Defaults to Roboflow serverless; point at a self-hosted
            ``inference`` server for offline use (same JSON contract).
        timeout : float
            HTTP timeout (seconds).
        predict_fn : callable or None
            ``frame -> [(class_name, confidence)]`` override for tests / alt backends.
        """
        self.model_id = model_id
        self.api_key = api_key
        self.conf = conf
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._predict_fn = predict_fn

    def _predict(self, frame: np.ndarray) -> list[tuple[str, float]]:
        if self._predict_fn is not None:
            return self._predict_fn(frame)

        import cv2
        import requests

        key = self.api_key or os.environ.get("ROBOFLOW_API_KEY")
        if not key:
            raise RuntimeError("ROBOFLOW_API_KEY not set")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to JPEG-encode frame")
        payload = base64.b64encode(buf.tobytes()).decode("ascii")
        resp = requests.post(
            f"{self.api_url}/{self.model_id}",
            params={"api_key": key},
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return _extract(resp.json())

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
