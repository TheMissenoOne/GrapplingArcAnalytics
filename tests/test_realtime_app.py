"""Tests for the realtime FastAPI backend — /health, /segment, /classify.

Uses the real PoseEstimator with an injected runtime + a toy classifier bundle, so
no model download or sibling repo is needed.
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder

import realtime.app as app_module
from cv.inference import ClassifierBundle
from cv.pose_estimate import PoseEstimator
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
from cv.roboflow_classifier import RoboflowClassifier
from cv.vocab_map import build_vocab_index
from realtime.app import create_app

NODES = [
    {
        "name": "Montada",
        "type": "control",
        "translations": {"en": "Mount"},
        "variations": ["mount", "full mount"],
    },
    {
        "name": "Guarda Fechada",
        "type": "guard",
        "translations": {"en": "Closed Guard"},
        "variations": ["closed guard", "full guard"],
    },
]


def _pose(y_offset: float) -> np.ndarray:
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


def _toy_bundle() -> ClassifierBundle:
    rng = np.random.default_rng(0)
    rows, labels = [], []
    for _ in range(30):
        j = rng.normal(0, 1.0, (17, 3))
        rows.append(pair_to_features(_pose(0) + j, _pose(20) + j))
        labels.append("mount_top")
        rows.append(pair_to_features(_pose(0) + j, _pose(200) + j))
        labels.append("guard_bottom")
    x = np.stack(rows)
    le = LabelEncoder()
    y = le.fit_transform(labels)
    model = RandomForestClassifier(n_estimators=20, random_state=0)
    model.fit(x, y)
    return ClassifierBundle(
        model=model, classes=le.classes_.tolist(), feature_names=[], model_type="rf"
    )


def _png_bytes() -> bytes:
    ok, buf = cv2.imencode(".png", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture
def index() -> dict:
    return build_vocab_index(NODES)


def test_health(index: dict) -> None:
    client = TestClient(create_app(vocab_index=index))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_nodes_route(index: dict) -> None:
    client = TestClient(create_app(vocab_index=index, nodes=NODES))
    r = client.get("/nodes")
    assert r.status_code == 200
    body = r.json()
    names = {n["name"] for n in body}
    assert {"Montada", "Guarda Fechada"} <= names
    assert all("type" in n for n in body)


def test_cors_header(index: dict) -> None:
    client = TestClient(create_app(vocab_index=index, nodes=NODES))
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_segment_maps_to_nodes(index: dict) -> None:
    client = TestClient(create_app(vocab_index=index))
    frames = [{"frame_index": i, "label": "mount_top", "confidence": 0.9} for i in range(5)]
    frames += [{"frame_index": i, "label": "guard_bottom", "confidence": 0.8} for i in range(5, 10)]
    r = client.post("/segment", json={"frames": frames, "window": 3})
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 2
    assert events[0]["node_name"] == "Montada"
    assert events[0]["role"] == "top"
    assert events[1]["node_name"] == "Guarda Fechada"
    assert events[1]["ok"] is True


def test_classify_returns_node(index: dict) -> None:
    estimator = PoseEstimator(runtime=lambda _f: [_pose(0), _pose(20)])
    app = create_app(estimator=estimator, classifier=_toy_bundle(), vocab_index=index)
    client = TestClient(app)
    r = client.post("/classify", files={"file": ("f.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["vicos_class"] == "mount_top"
    assert body["node_name"] == "Montada"
    assert 0.0 <= body["confidence"] <= 1.0


def test_classify_422_when_one_pose(index: dict) -> None:
    estimator = PoseEstimator(runtime=lambda _f: [_pose(0)])
    app = create_app(estimator=estimator, classifier=_toy_bundle(), vocab_index=index)
    client = TestClient(app)
    r = client.post("/classify", files={"file": ("f.png", _png_bytes(), "image/png")})
    assert r.status_code == 422


def test_priors_and_suggest_after_export_ingest(index: dict) -> None:
    client = TestClient(create_app(vocab_index=index, nodes=NODES))
    # Ingest a session for an athlete who goes mount → (off-vocab) finish.
    export_body = {
        "events": [
            {"label": "Montada", "type": "control", "role": "top"},
            {"label": "Armlock", "type": "submission", "role": "top"},
        ],
        "athlete": "me",
        "you_role": "top",
    }
    assert client.post("/export", json=export_body).status_code == 200

    pr = client.post("/priors", json={"athlete": "me", "prev_label": "Montada"})
    assert pr.status_code == 200
    body = pr.json()
    assert body and body[0]["label"] == "Armlock"

    sg = client.post("/suggest", json={"athlete": "me", "prev_label": "Montada", "k": 3})
    assert sg.status_code == 200
    assert sg.json()[0]["label"] == "Armlock"


def test_priors_unknown_athlete_empty(index: dict) -> None:
    client = TestClient(create_app(vocab_index=index, nodes=NODES))
    r = client.post("/priors", json={"athlete": "ghost", "prev_label": "Montada"})
    assert r.status_code == 200
    assert r.json() == []


def test_classify_roboflow_backend(index: dict) -> None:
    dets = [
        {"raw_class": "mount1", "confidence": 0.9, "x": 30, "y": 50, "width": 20, "height": 20},
        {"raw_class": "mount2", "confidence": 0.8, "x": 70, "y": 50, "width": 20, "height": 20},
    ]
    rf = RoboflowClassifier("bjj3/1", detect_fn=lambda _f: dets)
    client = TestClient(create_app(vocab_index=index, nodes=NODES, roboflow=rf))
    r = client.post("/classify", files={"file": ("f.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["node_name"] == "Montada"  # mount → Montada
    assert body["role"] == "top"  # top class won the argmax (0.9 > 0.8)
    assert body["ok"] is True


def test_detect_returns_boxes(index: dict) -> None:
    dets = [{"raw_class": "mount1", "confidence": 0.9, "x": 30, "y": 50, "width": 20, "height": 20}]
    rf = RoboflowClassifier("bjj3/1", detect_fn=lambda _f: dets)
    client = TestClient(create_app(vocab_index=index, nodes=NODES, roboflow=rf))
    r = client.post("/detect", files={"file": ("f.png", _png_bytes(), "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["detections"][0]["vicos_class"] == "mount_top"
    assert body["image_w"] == 8 and body["image_h"] == 8  # from the 8x8 png


def test_detect_503_without_roboflow(index: dict) -> None:
    client = TestClient(create_app(vocab_index=index, nodes=NODES))
    r = client.post("/detect", files={"file": ("f.png", _png_bytes(), "image/png")})
    assert r.status_code == 503


def test_capture_writes_dataset(index: dict, tmp_path) -> None:
    client = TestClient(create_app(vocab_index=index, nodes=NODES, capture_dir=tmp_path))
    dets = [{"raw_class": "mount1", "confidence": 0.9, "x": 2, "y": 4, "width": 2, "height": 2}]
    r = client.post(
        "/capture",
        files={"file": ("f.png", _png_bytes(), "image/png")},
        data={"detections": json.dumps(dets), "you_side": "left", "image_w": 8, "image_h": 8},
    )
    assert r.status_code == 200
    rec = r.json()["record"]
    assert rec["annotations"][0]["actor"] == "you"  # x=2 < 4 → left → you
    assert (tmp_path / "annotations.jsonl").exists()


def test_classify_roboflow_422_when_empty(index: dict) -> None:
    from cv.roboflow_classifier import RoboflowClassifier

    rf = RoboflowClassifier("bjj3/1", detect_fn=lambda _f: [])
    client = TestClient(create_app(vocab_index=index, nodes=NODES, roboflow=rf))
    r = client.post("/classify", files={"file": ("f.png", _png_bytes(), "image/png")})
    assert r.status_code == 422


def test_classify_503_when_classifier_unavailable(index: dict, monkeypatch) -> None:
    def _boom(_model_type: str):
        raise FileNotFoundError("no meta")

    monkeypatch.setattr(app_module, "load_classifier", _boom)
    estimator = PoseEstimator(runtime=lambda _f: [_pose(0), _pose(20)])
    client = TestClient(create_app(estimator=estimator, vocab_index=index))  # classifier=None
    r = client.post("/classify", files={"file": ("f.png", _png_bytes(), "image/png")})
    assert r.status_code == 503
