"""Tests for the annotation capture record + persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from realtime.capture import actor_for, build_capture_record, save_capture


@pytest.mark.parametrize(
    ("center_x", "you_side", "expected"),
    [
        (10, "left", "you"),
        (10, "right", "opponent"),
        (90, "left", "opponent"),
        (90, "right", "you"),
    ],
)
def test_actor_for(center_x: float, you_side: str, expected: str) -> None:
    assert actor_for(center_x, image_w=100, you_side=you_side) == expected


def test_build_record_two_detections() -> None:
    dets = [
        {"raw_class": "mount1", "confidence": 0.9, "x": 30, "y": 50, "width": 20, "height": 40},
        {"raw_class": "mount2", "confidence": 0.8, "x": 80, "y": 50, "width": 20, "height": 40},
    ]
    rec = build_capture_record(dets, you_side="left", image_w=100, image_h=100)
    a = rec["annotations"]
    assert rec["manual_position"] is None
    assert a[0]["class"] == "mount1" and a[0]["role"] == "top" and a[0]["actor"] == "you"
    assert a[1]["class"] == "mount2" and a[1]["role"] == "bottom" and a[1]["actor"] == "opponent"
    # bbox center (30,50) w20 h40 → top-left (20, 30, 20, 40)
    assert a[0]["bbox"] == [20.0, 30.0, 20.0, 40.0]


def test_build_record_manual_position() -> None:
    rec = build_capture_record(
        [], you_side="left", image_w=64, image_h=48, manual_position="Berimbolo"
    )
    assert rec["manual_position"] == "Berimbolo"
    assert len(rec["annotations"]) == 1
    ann = rec["annotations"][0]
    assert ann["class"] == "Berimbolo" and ann["actor"] == "you"
    assert ann["bbox"] == [0, 0, 64, 48]


def test_save_capture_writes_image_and_jsonl(tmp_path: Path) -> None:
    rec = build_capture_record([], you_side="left", image_w=8, image_h=8, manual_position="Mount")
    p1 = save_capture(tmp_path, b"jpgbytes", rec)
    assert p1.exists() and p1.parent.name == "images" and p1.suffix == ".jpg"

    save_capture(tmp_path, b"jpgbytes2", rec)  # second frame
    lines = (tmp_path / "annotations.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert "image" in first and "ts" in first and first["manual_position"] == "Mount"
