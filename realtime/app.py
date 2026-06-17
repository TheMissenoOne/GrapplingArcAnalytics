"""FastAPI app for the realtime CV backend.

Routes (Phase 4):
- ``GET  /health``   — liveness.
- ``POST /segment``  — a per-frame classification stream → discrete position events,
  each mapped to a GrapplingArc node. Pure; needs no model.
- ``POST /classify`` — a single image frame → pose estimation → position class → node.
  Needs the trained classifier + pose estimator (loaded lazily; 503 if unavailable).

Dependencies (pose estimator, classifier bundle, vocab index) live on ``app.state`` and are
created lazily on first use, so tests can inject fakes via :func:`create_app` and the pure
``/segment`` path runs with no model or heavy deps.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import BaseModel, Field

from cv.inference import ClassifierBundle, classify_pose_pair, load_classifier
from cv.segmenter import segment
from cv.vocab_map import NodeRef, build_vocab_index, load_app_nodes, map_vicos_class

logger = logging.getLogger(__name__)


# ─── Schemas ────────────────────────────────────────────────────────────────
class FrameClassification(BaseModel):
    """One frame's classifier output."""

    frame_index: int
    label: str
    confidence: float = 1.0


class SegmentRequest(BaseModel):
    frames: list[FrameClassification]
    window: int = Field(default=5, ge=1)
    min_frames: int = Field(default=1, ge=1)


class EventOut(BaseModel):
    label: str
    start: int
    end: int
    n_frames: int
    mean_conf: float
    role: str
    node_name: str | None
    node_type: str | None
    ok: bool


class SegmentResponse(BaseModel):
    events: list[EventOut]


class ClassifyResponse(BaseModel):
    vicos_class: str
    confidence: float
    role: str
    node_name: str | None
    node_type: str | None
    ok: bool


# ─── Helpers ──────────────────────────────────────────────────────────────--
def _event_to_out(event: Any, index: dict[str, NodeRef]) -> EventOut:
    """Map a segmenter PositionEvent to the response shape via the vocab index."""
    match = map_vicos_class(event.label, index)
    return EventOut(
        label=event.label,
        start=event.start,
        end=event.end,
        n_frames=event.n_frames,
        mean_conf=event.mean_conf,
        role=match.role,
        node_name=match.node_name,
        node_type=match.node_type,
        ok=match.ok,
    )


def _decode_image(raw: bytes) -> np.ndarray:
    """Decode raw image bytes to a BGR ndarray."""
    import cv2

    arr = np.frombuffer(raw, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="Could not decode image")
    return frame


# ─── App factory ─────────────────────────────────────────────────────────--
def create_app(
    estimator: Any | None = None,
    classifier: ClassifierBundle | None = None,
    vocab_index: dict[str, NodeRef] | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    Parameters
    ----------
    estimator : PoseEstimator-like or None
        Injected for tests; lazily constructed (real YOLOv8-pose) when None.
    classifier : ClassifierBundle or None
        Injected for tests; lazily loaded via ``load_classifier`` when None.
    vocab_index : dict or None
        Injected for tests; built from ``load_app_nodes`` at startup when None.
    """
    app = FastAPI(title="GrapplingArc Realtime CV", version="0.1.0")
    app.state.estimator = estimator
    app.state.classifier = classifier
    app.state.vocab_index = (
        vocab_index if vocab_index is not None else build_vocab_index(load_app_nodes())
    )

    def get_estimator() -> Any:
        if app.state.estimator is None:
            from cv.pose_estimate import PoseEstimator

            app.state.estimator = PoseEstimator()
        return app.state.estimator

    def get_classifier() -> ClassifierBundle:
        if app.state.classifier is None:
            try:
                bundle = load_classifier("rf")
            except (FileNotFoundError, ValueError) as exc:
                raise HTTPException(
                    status_code=503, detail=f"Classifier unavailable: {exc}"
                ) from exc
            app.state.classifier = bundle
        bundle = app.state.classifier
        assert isinstance(bundle, ClassifierBundle)
        return bundle

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "vocab_index_size": len(app.state.vocab_index)}

    @app.post("/segment", response_model=SegmentResponse)
    def segment_stream(req: SegmentRequest) -> SegmentResponse:
        frames = [(f.frame_index, f.label, f.confidence) for f in req.frames]
        events = segment(frames, window=req.window, min_frames=req.min_frames)
        return SegmentResponse(events=[_event_to_out(e, app.state.vocab_index) for e in events])

    @app.post("/classify", response_model=ClassifyResponse)
    def classify(file: UploadFile) -> ClassifyResponse:
        # Sync route (reads the underlying file directly) so the endpoint needs no
        # async portal — TestClient's portal task-naming breaks once a sibling test
        # has applied nest_asyncio to the loop.
        frame = _decode_image(file.file.read())
        est = get_estimator()
        bundle = get_classifier()

        poses = est.estimate(frame)
        pair = est.select_grappler_pair(poses)
        if pair is None:
            raise HTTPException(status_code=422, detail="Fewer than two athletes detected")

        kp0, kp1 = pair
        vicos_label, conf = classify_pose_pair(kp0, kp1, bundle)
        match = map_vicos_class(vicos_label, app.state.vocab_index)
        return ClassifyResponse(
            vicos_class=vicos_label,
            confidence=conf,
            role=match.role,
            node_name=match.node_name,
            node_type=match.node_type,
            ok=match.ok,
        )

    return app
