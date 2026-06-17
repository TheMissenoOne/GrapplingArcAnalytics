"""ViCoS download tests — synthetic fixtures, no network."""

from __future__ import annotations

import json
from pathlib import Path

from cv.vicos_download import VicosSample, download_annotations, download_images, verify


def test_verify_returns_counts(tmp_path: Path) -> None:
    vicos_data = tmp_path / "vicos_data"
    annotations_dir = vicos_data / "annotations"
    images_dir = vicos_data / "images"
    annotations_dir.mkdir(parents=True)
    images_dir.mkdir()

    annotations = {
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1},
            {"id": 2, "image_id": 2, "category_id": 2},
            {"id": 3, "image_id": 3, "category_id": 1},
        ]
    }
    (annotations_dir / "annotations.json").write_text(json.dumps(annotations))

    (images_dir / "img_0001.jpg").touch()
    (images_dir / "img_0002.jpg").touch()

    result = verify(dest=vicos_data)

    assert result["images_found"] == 2
    assert result["annotation_entries"] == 3
    assert result["missing_files"] == ["img_0003.jpg"]


def test_verify_empty_directories(tmp_path: Path) -> None:
    vicos_data = tmp_path / "vicos_data"
    vicos_data.mkdir()

    result = verify(dest=vicos_data)

    assert result["images_found"] == 0
    assert result["annotation_entries"] == 0
    assert result["missing_files"] == []


def test_verify_no_annotations_file(tmp_path: Path) -> None:
    vicos_data = tmp_path / "vicos_data"
    images_dir = vicos_data / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "img_0001.jpg").touch()

    result = verify(dest=vicos_data)

    assert result["images_found"] == 1
    assert result["annotation_entries"] == 0
    assert result["missing_files"] == []


def test_download_annotations_raises_when_url_empty() -> None:
    import pytest

    with pytest.raises(ValueError, match="ViCoS download URL not configured"):
        download_annotations()


def test_download_images_raises_when_url_empty() -> None:
    import pytest

    with pytest.raises(ValueError, match="ViCoS download URL not configured"):
        download_images()


def test_vicos_sample_fields() -> None:
    sample = VicosSample(
        image_path="img_0001.jpg",
        keypoints=[[100.0, 200.0, 1.0]] * 17,
        class_label="mount",
    )

    assert sample.image_path == "img_0001.jpg"
    assert len(sample.keypoints) == 17
    assert sample.keypoints[0] == [100.0, 200.0, 1.0]
    assert sample.class_label == "mount"


def test_verify_handles_dest_does_not_exist(tmp_path: Path) -> None:
    vicos_data = tmp_path / "nonexistent"

    result = verify(dest=vicos_data)

    assert result["images_found"] == 0
    assert result["annotation_entries"] == 0
    assert result["missing_files"] == []
