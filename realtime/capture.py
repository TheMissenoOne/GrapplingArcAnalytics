"""Persist hand-labeled annotation frames (bjj3 class + bbox + actor).

The keyboard annotation studio assigns *which athlete is you* (the signal bjj3 lacks).
This module turns the model's detections + the user's left/right choice into a record
and writes the frame image + a COCO-ish ``annotations.jsonl`` line — a dataset that can
later train an identity-aware model.

Kept dependency-free (stdlib only): role is read straight off a trailing ``1``/``2``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROLE = {"1": "top", "2": "bottom"}


def actor_for(center_x: float, image_w: int, you_side: str) -> str:
    """Map a box's horizontal centre to ``"you"``/``"opponent"`` given the you-side."""
    side = "left" if center_x < image_w / 2 else "right"
    return "you" if side == you_side else "opponent"


def _role_of(raw_class: str) -> str:
    """Top/bottom from a trailing 1/2 (only when preceded by a letter)."""
    if len(raw_class) >= 2 and raw_class[-1] in _ROLE and raw_class[-2].isalpha():
        return _ROLE[raw_class[-1]]
    return ""


def build_capture_record(
    detections: list[dict[str, Any]],
    you_side: str,
    image_w: int,
    image_h: int,
    manual_position: str | None = None,
) -> dict[str, Any]:
    """Build a per-frame capture record (annotations not yet persisted).

    Parameters
    ----------
    detections : list[dict]
        Each ``{raw_class, confidence, x, y, width, height}`` where ``x``/``y`` are the
        bbox **centre** (Roboflow convention).
    you_side : str
        ``"left"`` or ``"right"`` — which side of the frame is *you*.
    image_w, image_h : int
        Frame dimensions.
    manual_position : str or None
        When set (the no-detection path), the record holds a single full-frame
        annotation with this typed class, attributed to *you*; ``detections`` ignored.

    Returns
    -------
    dict
        ``{width, height, annotations: [...], manual_position}``. The image filename and
        timestamp are added later by :func:`save_capture`.
    """
    annotations: list[dict[str, Any]] = []
    if manual_position is not None:
        annotations.append(
            {
                "class": manual_position,
                "role": "",
                "actor": "you",
                "bbox": [0, 0, image_w, image_h],
                "confidence": 1.0,
            }
        )
    else:
        for det in detections:
            raw_class = str(det["raw_class"])
            cx, cy = float(det["x"]), float(det["y"])
            w, h = float(det["width"]), float(det["height"])
            annotations.append(
                {
                    "class": raw_class,
                    "role": _role_of(raw_class),
                    "actor": actor_for(cx, image_w, you_side),
                    "bbox": [cx - w / 2, cy - h / 2, w, h],
                    "confidence": float(det.get("confidence", 0.0)),
                }
            )

    return {
        "width": image_w,
        "height": image_h,
        "annotations": annotations,
        "manual_position": manual_position,
    }


def save_capture(capture_dir: Path, image_bytes: bytes, record: dict[str, Any]) -> Path:
    """Write the frame JPG and append the record (with image name + ts) to JSONL.

    Returns the written image path.
    """
    images_dir = capture_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time() * 1000)
    filename = f"frame_{ts}.jpg"
    image_path = images_dir / filename
    image_path.write_bytes(image_bytes)

    line = {"image": filename, "ts": ts, **record}
    with (capture_dir / "annotations.jsonl").open("a") as f:
        f.write(json.dumps(line) + "\n")

    n = len(record.get("annotations", []))
    logger.info("Captured frame → %s (%d annotations)", image_path, n)
    return image_path
