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

# A detector turns one frame into a list of raw detection dicts
# ``{class/raw_class, confidence, x, y, width, height}`` (x,y = bbox centre).
DetectFn = Callable[[np.ndarray], list[dict[str, Any]]]


def _get(obj: Any, *keys: str) -> Any:
    """Read the first present key from a dict or attribute from an object."""
    for k in keys:
        if isinstance(obj, dict):
            if k in obj:
                return obj[k]
        elif hasattr(obj, k):
            return getattr(obj, k)
    return None


def _extract_detections(resp: Any) -> list[dict[str, Any]]:
    """Adapt an inference result (object or dict) to raw detection dicts with boxes."""
    if isinstance(resp, list):
        resp = resp[0] if resp else {}
    preds = _get(resp, "predictions") or []
    out: list[dict[str, Any]] = []
    for p in preds:
        name = _get(p, "class", "class_name", "class_")
        conf = _get(p, "confidence")
        if name is None or conf is None:
            continue
        out.append(
            {
                "raw_class": str(name),
                "confidence": float(conf),
                "x": float(_get(p, "x") or 0.0),
                "y": float(_get(p, "y") or 0.0),
                "width": float(_get(p, "width") or 0.0),
                "height": float(_get(p, "height") or 0.0),
            }
        )
    return out


def _image_dims(resp: Any) -> tuple[int, int] | None:
    """Read (width, height) of the inferred image from the response, if present."""
    if isinstance(resp, list):
        resp = resp[0] if resp else {}
    img = _get(resp, "image")
    if img is None:
        return None
    w, h = _get(img, "width"), _get(img, "height")
    return (int(w), int(h)) if w and h else None


class RoboflowClassifier:
    """Classify a frame into position-class probabilities via a Roboflow model."""

    def __init__(
        self,
        model_id: str,
        api_key: str | None = None,
        conf: float = 0.4,
        api_url: str = DEFAULT_API_URL,
        timeout: float = 30.0,
        detect_fn: DetectFn | None = None,
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
        detect_fn : callable or None
            ``frame -> [{raw_class, confidence, x, y, width, height}]`` override for
            tests / alt backends (skips the HTTP call).
        """
        self.model_id = model_id
        self.api_key = api_key
        self.conf = conf
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._detect_fn = detect_fn

    def _raw(self, frame: np.ndarray) -> tuple[list[dict[str, Any]], tuple[int, int] | None]:
        """Return (raw detections, image dims or None) for a frame."""
        if self._detect_fn is not None:
            return self._detect_fn(frame), None

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
        body = resp.json()
        return _extract_detections(body), _image_dims(body)

    def detect(
        self, frame: np.ndarray, role_map: dict[int, str] | None = None
    ) -> dict[str, Any]:
        """Frame → ``{detections: [...], image_w, image_h}`` with boxes (for the overlay).

        Each detection: ``{raw_class, vicos_class, confidence, x, y, width, height}``
        (``x``/``y`` = bbox centre). Image dims come from the model response, falling back
        to the frame shape (for injected ``detect_fn``).
        """
        raw, dims = self._raw(frame)
        image_w = dims[0] if dims else int(frame.shape[1])
        image_h = dims[1] if dims else int(frame.shape[0])
        detections = [
            {
                "raw_class": d["raw_class"],
                "vicos_class": roboflow_to_vicos(d["raw_class"], role_map),
                "confidence": float(d["confidence"]),
                "x": float(d["x"]),
                "y": float(d["y"]),
                "width": float(d["width"]),
                "height": float(d["height"]),
            }
            for d in raw
            if float(d.get("confidence", 0.0)) >= self.conf
        ]
        return {"detections": detections, "image_w": image_w, "image_h": image_h}

    def classify_frame_probs(
        self, frame: np.ndarray, role_map: dict[int, str] | None = None
    ) -> dict[str, float]:
        """Frame → ``{vicos_class: probability}`` (L1-normalized).

        Aggregates ``detect()`` confidences per ViCoS class (so both athletes' boxes
        count), then L1-normalizes. Empty when nothing is detected.
        """
        agg: dict[str, float] = {}
        for det in self.detect(frame, role_map)["detections"]:
            label = det["vicos_class"]
            agg[label] = agg.get(label, 0.0) + det["confidence"]
        total = sum(agg.values())
        return {k: v / total for k, v in agg.items()} if total > 0 else {}
