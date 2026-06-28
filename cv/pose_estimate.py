"""Pose estimation for live/recorded frames — YOLOv8-pose -> COCO-17 keypoints.

Turns an image frame into per-athlete ``(17, 3)`` COCO keypoints in **pixel**
coordinates, matching the pixel-space the baseline classifier was trained on
(``cv.pose_features.pair_features`` encodes raw pixel distances/ordering).

Runtime is pluggable: by default it lazily loads an Ultralytics YOLOv8-pose model
(which handles detection + NMS + keypoint decode and emits COCO-17 order directly),
but a ``runtime`` callable can be injected — used by tests to avoid a real model
download or any network access.

The model weights are not committed; place ``yolov8*-pose.pt`` (or an exported
``.onnx``) under ``data/`` (gitignored) or pass an explicit ``model_path``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .pose_features import L_HIP, R_HIP

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_MODEL = DATA_DIR / "yolov8n-pose.pt"

# A runtime turns one frame into a list of (17, 3) pixel-coord keypoint arrays.
PoseRuntime = Callable[[np.ndarray], list[np.ndarray]]


def _hip_y(kp: np.ndarray) -> float:
    """Mean y of the hips (image coords; smaller y = higher in frame)."""
    return float((kp[L_HIP, 1] + kp[R_HIP, 1]) / 2.0)


def _bbox_area(kp: np.ndarray, conf_thresh: float = 0.3) -> float:
    """Area of the axis-aligned bbox over confident keypoints (0 if too few)."""
    vis = kp[kp[:, 2] > conf_thresh]
    if len(vis) < 2:
        return 0.0
    w = float(vis[:, 0].max() - vis[:, 0].min())
    h = float(vis[:, 1].max() - vis[:, 1].min())
    return w * h


class PoseEstimator:
    """Estimate COCO-17 poses per frame via YOLOv8-pose (or an injected runtime)."""

    def __init__(
        self,
        model_path: str | Path | None = None,
        conf: float = 0.25,
        runtime: PoseRuntime | None = None,
    ) -> None:
        """
        Parameters
        ----------
        model_path : str or Path or None
            Path to YOLOv8-pose weights. Defaults to ``data/yolov8n-pose.pt``.
            Ignored when ``runtime`` is supplied.
        conf : float
            Detection confidence threshold passed to the model.
        runtime : callable or None
            Optional ``frame -> list[(17,3)]`` override. When given, no model is
            loaded (used in tests and for alternate backends, e.g. onnxruntime).
        """
        self.conf = conf
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL
        self._runtime = runtime
        self._model: Any | None = None

    def _ultralytics_runtime(self) -> PoseRuntime:
        """Lazily build a runtime backed by an Ultralytics YOLO model."""
        if self._model is None:
            from ultralytics import YOLO  # type: ignore[attr-defined]  # heavy, deferred

            if not self.model_path.exists():
                # Ultralytics auto-downloads known names (e.g. "yolov8n-pose.pt").
                logger.info(
                    "Weights %s not found; loading by name (Ultralytics may download).",
                    self.model_path,
                )
                self._model = YOLO(self.model_path.name)
            else:
                self._model = YOLO(str(self.model_path))

        def run(frame: np.ndarray) -> list[np.ndarray]:
            assert self._model is not None
            results = self._model.predict(frame, conf=self.conf, verbose=False)
            if not results:
                return []
            kps = results[0].keypoints
            if kps is None or kps.data is None:
                return []
            data = kps.data.cpu().numpy()  # (n, 17, 3) pixel coords [x, y, conf]
            return [np.asarray(p, dtype=np.float64) for p in data]

        return run

    def estimate(self, frame: np.ndarray) -> list[np.ndarray]:
        """Return one ``(17, 3)`` keypoint array per detected person (pixel coords)."""
        runtime = self._runtime or self._ultralytics_runtime()
        poses = runtime(frame)
        out: list[np.ndarray] = []
        for p in poses:
            arr = np.asarray(p, dtype=np.float64)
            if arr.shape != (17, 3):
                logger.warning("Skipping pose with shape %s (expected (17,3))", arr.shape)
                continue
            out.append(arr)
        return out

    def select_grappler_pair(
        self,
        poses: list[np.ndarray],
        order_by: str = "hip_y",
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Pick the two grapplers and order them to match training's athlete_idx.

        Strategy: take the two largest-bbox detections (the grapplers fill the most
        frame), then order them.

        Parameters
        ----------
        poses : list[np.ndarray]
            Candidate ``(17, 3)`` poses from :meth:`estimate`.
        order_by : str
            ``"hip_y"`` (default): athlete 0 = the higher athlete in the frame
            (smaller mean hip-y, i.e. the "top" player). ``"none"``: keep input
            order.

        Returns
        -------
        tuple[np.ndarray, np.ndarray] or None
            ``(kp0, kp1)``, or ``None`` if fewer than two poses.

        Notes
        -----
        ViCoS ``athlete_idx`` is taken verbatim from the dataset annotations and is
        not geometry-derived, so this ordering rule is a best-effort match. Because
        ``pair_features`` encodes directional cues (head-to-head vector, vertical
        ordering), a wrong order degrades accuracy — the post-match review step is
        the safety net, and the rule should be re-validated against the dataset's
        actual ``athlete_idx`` semantics once full ViCoS data is available.
        """
        if len(poses) < 2:
            return None

        top_two = sorted(poses, key=_bbox_area, reverse=True)[:2]

        if order_by == "hip_y":
            kp0, kp1 = sorted(top_two, key=_hip_y)  # smaller hip-y (higher) first
        elif order_by == "none":
            kp0, kp1 = top_two[0], top_two[1]
        else:
            raise ValueError(f"Unknown order_by: {order_by!r}")

        return kp0, kp1
