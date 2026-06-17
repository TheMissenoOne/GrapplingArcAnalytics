"""Tests for the Roboflow classifier wrapper (injected detector — no network)."""

from __future__ import annotations

import numpy as np
import pytest

from cv.roboflow_classifier import RoboflowClassifier, _extract_detections, _image_dims


def _det(raw_class: str, conf: float, x: float = 50, y: float = 50) -> dict:
    return {"raw_class": raw_class, "confidence": conf, "x": x, "y": y, "width": 20, "height": 20}


def _clf(detections: list[dict], conf: float = 0.4) -> RoboflowClassifier:
    return RoboflowClassifier("bjj3/1", conf=conf, detect_fn=lambda _frame: detections)


def test_classify_probs_aggregates_both_athletes() -> None:
    clf = _clf([_det("mount1", 0.9, x=30), _det("mount2", 0.8, x=70)])
    probs = clf.classify_frame_probs(np.zeros((100, 100, 3), dtype=np.uint8))
    assert set(probs) == {"mount_top", "mount_bottom"}
    assert probs["mount_top"] == pytest.approx(0.9 / 1.7)
    assert sum(probs.values()) == pytest.approx(1.0)


def test_detect_returns_boxes_and_dims() -> None:
    clf = _clf([_det("mount1", 0.9, x=30)])
    out = clf.detect(np.zeros((480, 640, 3), dtype=np.uint8))
    assert out["image_w"] == 640 and out["image_h"] == 480  # from frame shape
    d = out["detections"][0]
    assert d["raw_class"] == "mount1" and d["vicos_class"] == "mount_top"
    assert d["x"] == 30 and d["width"] == 20


def test_detect_drops_low_confidence() -> None:
    clf = _clf([_det("mount1", 0.9), _det("standing", 0.1)], conf=0.4)
    out = clf.detect(np.zeros((100, 100, 3), dtype=np.uint8))
    assert [d["raw_class"] for d in out["detections"]] == ["mount1"]


def test_classify_empty_when_nothing_detected() -> None:
    assert _clf([]).classify_frame_probs(np.zeros((10, 10, 3), dtype=np.uint8)) == {}


def test_http_path(monkeypatch) -> None:
    import requests

    class _FakeResp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {
                "image": {"width": 1280, "height": 720},
                "predictions": [
                    {
                        "class": "mount1",
                        "confidence": 0.9,
                        "x": 100,
                        "y": 200,
                        "width": 50,
                        "height": 60,
                    }
                ],
            }

    captured: dict = {}

    def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _FakeResp()

    monkeypatch.setattr(requests, "post", _fake_post)
    clf = RoboflowClassifier("bjj3/1", api_key="k")
    out = clf.detect(np.zeros((8, 8, 3), dtype=np.uint8))
    assert out["image_w"] == 1280 and out["image_h"] == 720  # from response, not frame
    assert out["detections"][0]["vicos_class"] == "mount_top"
    assert captured["url"].endswith("/bjj3/1")
    assert captured["params"]["api_key"] == "k"


def test_extract_and_dims_handle_dict_and_object() -> None:
    body = {
        "image": {"width": 10, "height": 20},
        "predictions": [
            {"class": "back2", "confidence": 0.7, "x": 1, "y": 2, "width": 3, "height": 4}
        ],
    }
    dets = _extract_detections(body)
    assert dets[0]["raw_class"] == "back2" and dets[0]["width"] == 3.0
    assert _image_dims(body) == (10, 20)
    assert _image_dims({"predictions": []}) is None
