"""Tests for the Roboflow classifier wrapper (injected predictor — no network)."""

from __future__ import annotations

import numpy as np
import pytest

from cv.roboflow_classifier import RoboflowClassifier, _extract


def _clf(detections: list[tuple[str, float]], conf: float = 0.4) -> RoboflowClassifier:
    return RoboflowClassifier("bjj3/1", conf=conf, predict_fn=lambda _frame: detections)


def test_aggregates_both_athletes_per_class() -> None:
    clf = _clf([("mount1", 0.9), ("mount2", 0.8)])
    probs = clf.classify_frame_probs(np.zeros((4, 4, 3)))
    # both roles kept as distinct ViCoS classes, L1-normalized
    assert set(probs) == {"mount_top", "mount_bottom"}
    assert probs["mount_top"] == pytest.approx(0.9 / 1.7)
    assert sum(probs.values()) == pytest.approx(1.0)


def test_drops_low_confidence() -> None:
    clf = _clf([("mount1", 0.9), ("standing", 0.1)], conf=0.4)
    probs = clf.classify_frame_probs(np.zeros((4, 4, 3)))
    assert "standing" not in probs
    assert probs == {"mount_top": pytest.approx(1.0)}


def test_empty_when_nothing_detected() -> None:
    assert _clf([]).classify_frame_probs(np.zeros((4, 4, 3))) == {}


def test_variant_role_map() -> None:
    clf = _clf([("mount1", 0.9)])
    probs = clf.classify_frame_probs(np.zeros((4, 4, 3)), role_map={})
    assert probs == {"mount": pytest.approx(1.0)}


def test_http_predict_path(monkeypatch) -> None:
    import requests

    class _FakeResp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {"predictions": [{"class": "mount1", "confidence": 0.9}]}

    captured: dict = {}

    def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _FakeResp()

    monkeypatch.setattr(requests, "post", _fake_post)
    clf = RoboflowClassifier("bjj3/1", api_key="k")
    probs = clf.classify_frame_probs(np.zeros((8, 8, 3), dtype=np.uint8))
    assert probs == {"mount_top": pytest.approx(1.0)}
    assert captured["url"].endswith("/bjj3/1")
    assert captured["params"]["api_key"] == "k"


def test_extract_handles_dict_and_object_results() -> None:
    dict_resp = {"predictions": [{"class": "mount1", "confidence": 0.9}]}
    assert _extract(dict_resp) == [("mount1", 0.9)]
    assert _extract([dict_resp]) == [("mount1", 0.9)]

    class _P:
        class_name = "back2"
        confidence = 0.7

    class _R:
        predictions = [_P()]

    assert _extract(_R()) == [("back2", 0.7)]
