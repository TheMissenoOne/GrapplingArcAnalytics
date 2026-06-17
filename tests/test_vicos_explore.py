from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cv.vicos_explore import class_distribution, keypoint_quality, load_annotations

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "vicos_annotations_mini.json"


def test_load_annotations_shape() -> None:
    df = load_annotations(FIXTURE)
    assert len(df) == 3
    expected_cols = {"image_id", "athlete_idx", "position", "role", "class_label"} | set(
        [f"kp_{i}_{c}" for i in range(17) for c in ("x", "y", "c")]
    )
    assert expected_cols.issubset(set(df.columns))
    assert df["kp_0_x"].dtype == float


def test_class_distribution() -> None:
    df = load_annotations(FIXTURE)
    dist = class_distribution(df)
    assert "share" in dist.columns
    assert dist["share"].sum() == pytest.approx(1.0, abs=1e-3)


def test_keypoint_quality() -> None:
    df = load_annotations(FIXTURE)
    qual = keypoint_quality(df)
    assert "mean_confidence" in qual.columns
    assert "low_conf_pct" in qual.columns


def test_parquet_cache() -> None:
    df1 = load_annotations(FIXTURE)
    cache = (
        Path(__file__).resolve().parent.parent
        / "data" / "processed" / "vicos_keypoints.parquet"
    )
    assert cache.exists()
    df2 = pd.read_parquet(cache)
    assert len(df2) == len(df1)


def test_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_annotations("/nonexistent/path.json")


def test_empty_annotation_handling() -> None:
    df = load_annotations(FIXTURE)
    missing_row = df[df["image_id"] == "img_002"].iloc[0]
    assert missing_row["kp_1_x"] == 0.0


def test_keypoint_quality_empty_df() -> None:
    empty = pd.DataFrame()
    qual = keypoint_quality(empty)
    assert qual.empty
