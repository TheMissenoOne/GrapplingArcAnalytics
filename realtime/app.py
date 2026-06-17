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
from typing import TYPE_CHECKING, Any

import numpy as np
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from analysis.athlete_graph import AthleteGraph, build_athlete_graph
from analysis.priors import (
    DEFAULT_ALPHA,
    label_map_from_index,
    next_move_prior,
    rerank_classification,
    suggest_next,
)
from cv.inference import ClassifierBundle, classify_pose_pair_probs, load_classifier
from cv.segmenter import segment
from cv.vocab_map import NodeRef, build_vocab_index, load_app_nodes, map_vicos_class
from realtime.export import TimelineEvent, build_session_payload

if TYPE_CHECKING:
    from analysis.vector_store import AthleteVectorStore
    from cv.roboflow_classifier import RoboflowClassifier

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
    reranked: bool = False


class NodeOption(BaseModel):
    name: str
    type: str
    en: str | None = None


class ExportEventIn(BaseModel):
    label: str
    type: str
    role: str = ""
    successful: bool = True
    setup: str | None = None


class ExportRequest(BaseModel):
    events: list[ExportEventIn]
    you_role: str = "top"
    difficulty: int = Field(default=3, ge=1, le=5)
    intensity: int = Field(default=3, ge=1, le=5)
    notes: str = ""
    timestamp: int | None = None
    outcome: str | None = None
    # When set, the produced session is ingested into this athlete's graph (+ store).
    athlete: str | None = None


class PriorsRequest(BaseModel):
    athlete: str
    prev_label: str
    k: int = Field(default=5, ge=1)


class RankedItem(BaseModel):
    label: str
    score: float


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
def _node_options(nodes: list[dict[str, Any]]) -> list[NodeOption]:
    """Distinct {name, type, en} for the manual annotation picker."""
    seen: set[str] = set()
    out: list[NodeOption] = []
    for n in nodes:
        name = str(n.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        translations = n.get("translations", {}) or {}
        out.append(NodeOption(name=name, type=str(n.get("type", "")), en=translations.get("en")))
    return out


def create_app(
    estimator: Any | None = None,
    classifier: ClassifierBundle | None = None,
    vocab_index: dict[str, NodeRef] | None = None,
    nodes: list[dict[str, Any]] | None = None,
    cors_origins: list[str] | None = None,
    store: AthleteVectorStore | None = None,
    roboflow: RoboflowClassifier | None = None,
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
    nodes : list[dict] or None
        Raw app nodes for the ``/nodes`` picker. Defaults to ``load_app_nodes()``.
    cors_origins : list[str] or None
        Allowed CORS origins. Defaults to ``["*"]`` (dev).
    store : AthleteVectorStore or None
        Optional Qdrant store for the similar-athlete prior blend. ``None`` ⇒ priors
        use own-history only (graceful).
    """
    raw_nodes = nodes if nodes is not None else load_app_nodes()
    app = FastAPI(title="GrapplingArc Realtime CV", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.estimator = estimator
    app.state.classifier = classifier
    app.state.store = store
    app.state.roboflow = roboflow
    app.state.node_options = _node_options(raw_nodes)
    app.state.vocab_index = (
        vocab_index if vocab_index is not None else build_vocab_index(raw_nodes)
    )
    app.state.label_map = label_map_from_index(app.state.vocab_index)
    app.state.node_types = {ref.name: ref.type for ref in app.state.vocab_index.values()}
    # In-memory per-athlete state: accumulated sessions + the derived graph.
    app.state.athlete_sessions = {}
    app.state.athlete_graphs = {}

    def get_estimator() -> Any:
        if app.state.estimator is None:
            from cv.pose_estimate import PoseEstimator

            app.state.estimator = PoseEstimator()
        return app.state.estimator

    def get_classifier() -> ClassifierBundle:
        bundle = app.state.classifier
        if bundle is None:
            try:
                bundle = load_classifier("rf")
            except (FileNotFoundError, ValueError) as exc:
                raise HTTPException(
                    status_code=503, detail=f"Classifier unavailable: {exc}"
                ) from exc
            app.state.classifier = bundle
        assert isinstance(bundle, ClassifierBundle)
        return bundle

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "vocab_index_size": len(app.state.vocab_index)}

    @app.get("/nodes", response_model=list[NodeOption])
    def nodes_route() -> list[NodeOption]:
        options: list[NodeOption] = app.state.node_options
        return options

    @app.post("/segment", response_model=SegmentResponse)
    def segment_stream(req: SegmentRequest) -> SegmentResponse:
        frames = [(f.frame_index, f.label, f.confidence) for f in req.frames]
        events = segment(frames, window=req.window, min_frames=req.min_frames)
        return SegmentResponse(events=[_event_to_out(e, app.state.vocab_index) for e in events])

    def _graph_for(athlete: str) -> AthleteGraph:
        graph = app.state.athlete_graphs.get(athlete)
        return graph if graph is not None else AthleteGraph(athlete=athlete)

    @app.post("/classify", response_model=ClassifyResponse)
    def classify(
        file: UploadFile,
        athlete: str | None = Form(None),
        prev_label: str | None = Form(None),
        alpha: float = Form(DEFAULT_ALPHA),
    ) -> ClassifyResponse:
        # Sync route (reads the underlying file directly) so the endpoint needs no
        # async portal — TestClient's portal task-naming breaks once a sibling test
        # has applied nest_asyncio to the loop.
        frame = _decode_image(file.file.read())

        if app.state.roboflow is not None:
            # Roboflow object-detection backend: frame → position classes directly.
            probs = app.state.roboflow.classify_frame_probs(frame)
            if not probs:
                raise HTTPException(status_code=422, detail="No position detected")
        else:
            # Pose-estimation + sklearn fallback backend.
            est = get_estimator()
            bundle = get_classifier()
            poses = est.estimate(frame)
            pair = est.select_grappler_pair(poses)
            if pair is None:
                raise HTTPException(status_code=422, detail="Fewer than two athletes detected")
            kp0, kp1 = pair
            probs = classify_pose_pair_probs(kp0, kp1, bundle)

        vicos_label = max(probs, key=lambda k: probs[k])
        match = map_vicos_class(vicos_label, app.state.vocab_index)
        raw_name = match.node_name if match.ok else match.position

        # Prior-aware re-rank when an athlete context is supplied and known.
        if athlete and prev_label and athlete in app.state.athlete_graphs:
            top, score, _ = rerank_classification(
                probs,
                prev_label,
                _graph_for(athlete),
                app.state.vocab_index,
                alpha=alpha,
                store=app.state.store,
                athlete=athlete,
            )
            if top:
                if top != raw_name:
                    logger.info(
                        "prior flipped argmax: %s -> %s (athlete=%s, prev=%s)",
                        raw_name, top, athlete, prev_label,
                    )
                return ClassifyResponse(
                    vicos_class=vicos_label,
                    confidence=score,
                    role=match.role,
                    node_name=top,
                    node_type=app.state.node_types.get(top),
                    ok=True,
                    reranked=True,
                )

        return ClassifyResponse(
            vicos_class=vicos_label,
            confidence=probs[vicos_label],
            role=match.role,
            node_name=match.node_name,
            node_type=match.node_type,
            ok=match.ok,
        )

    @app.post("/export")
    def export(req: ExportRequest) -> dict[str, Any]:
        events = [
            TimelineEvent(
                label=e.label, type=e.type, role=e.role, successful=e.successful, setup=e.setup
            )
            for e in req.events
        ]
        payload = build_session_payload(
            events,
            you_role=req.you_role,
            difficulty=req.difficulty,
            intensity=req.intensity,
            notes=req.notes,
            timestamp=req.timestamp,
            outcome=req.outcome,
        )
        # Close the loop: ingest the session into the athlete's graph (+ store).
        if req.athlete:
            sessions = app.state.athlete_sessions.setdefault(req.athlete, [])
            sessions.append(payload)
            graph = build_athlete_graph(req.athlete, sessions)
            app.state.athlete_graphs[req.athlete] = graph
            if app.state.store is not None:
                app.state.store.upsert_athlete(graph)
        return payload

    @app.post("/priors", response_model=list[RankedItem])
    def priors_route(req: PriorsRequest) -> list[RankedItem]:
        prior = next_move_prior(
            _graph_for(req.athlete),
            req.prev_label,
            store=app.state.store,
            athlete=req.athlete,
            k=req.k,
            label_map=app.state.label_map,
        )
        ranked = sorted(prior.items(), key=lambda kv: kv[1], reverse=True)
        return [RankedItem(label=label, score=score) for label, score in ranked]

    @app.post("/suggest", response_model=list[RankedItem])
    def suggest_route(req: PriorsRequest) -> list[RankedItem]:
        ranked = suggest_next(
            _graph_for(req.athlete),
            req.prev_label,
            k=req.k,
            store=app.state.store,
            athlete=req.athlete,
            label_map=app.state.label_map,
        )
        return [RankedItem(label=label, score=score) for label, score in ranked]

    return app
