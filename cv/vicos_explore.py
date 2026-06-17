"""ViCoS dataset exploration — parse JSON, visualize, class distribution."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

KEYPOINT_COLS = [f"kp_{i}_{c}" for i in range(17) for c in ("x", "y", "c")]


def load_annotations(path: str | Path) -> pd.DataFrame:
    """Flatten ViCoS JSON annotation file into a tidy DataFrame.

    One row per athlete-instance with 51 keypoint columns (kp_{0..16}_{x|y|c})
    plus metadata: image_id, athlete_idx, position, role.

    Caches to data/processed/vicos_keypoints.parquet.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Annotations not found: {path}")

    with open(path) as f:
        data: dict[str, Any] = json.load(f)

    rows: list[dict[str, Any]] = []

    for img in data.get("images", []):
        img_id = img.get("id", "")
        for ann in data.get("annotations", []):
            if ann.get("image_id") == img_id:
                rows.append(_flatten_annotation(img_id, ann))

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    for col in KEYPOINT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

    cache_dir = Path(__file__).resolve().parent / ".." / "data" / "processed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "vicos_keypoints.parquet"
    df.to_parquet(cache_path, index=False)
    logger.info("Cached %d ViCoS instances to %s", len(df), cache_path)

    return df


def _flatten_annotation(image_id: str, ann: dict[str, Any]) -> dict[str, Any]:
    """Flatten a single annotation into a row dict."""
    row: dict[str, Any] = {
        "image_id": image_id,
        "athlete_idx": ann.get("athlete_idx", 0),
        "position": ann.get("position", ""),
        "role": ann.get("role", ""),
        "class_label": f"{ann.get('position', 'unknown')}_{ann.get('role', 'unknown')}",
    }

    keypoints = ann.get("keypoints", [])
    for i in range(17):
        if i < len(keypoints):
            row[f"kp_{i}_x"] = keypoints[i][0]
            row[f"kp_{i}_y"] = keypoints[i][1]
            row[f"kp_{i}_c"] = keypoints[i][2]
        else:
            row[f"kp_{i}_x"] = 0.0
            row[f"kp_{i}_y"] = 0.0
            row[f"kp_{i}_c"] = 0.0

    return row


def class_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """Counts + share per class_label."""
    if df.empty:
        return pd.DataFrame(columns=["class_label", "count", "share"])

    counts = df["class_label"].value_counts().reset_index()
    counts.columns = ["class_label", "count"]
    counts["share"] = (counts["count"] / len(df)).round(4)
    return counts.sort_values("count", ascending=False).reset_index(drop=True)


def keypoint_quality(df: pd.DataFrame) -> pd.DataFrame:
    """Per-class mean keypoint confidence + % keypoints below 0.3 threshold.

    Returns DataFrame with: class_label, mean_confidence, low_conf_pct.
    """
    if df.empty:
        return pd.DataFrame(columns=["class_label", "mean_confidence", "low_conf_pct"])

    conf_cols = [f"kp_{i}_c" for i in range(17) if f"kp_{i}_c" in df.columns]
    if not conf_cols:
        return pd.DataFrame(columns=["class_label", "mean_confidence", "low_conf_pct"])

    df = df.copy()
    df["_mean_conf"] = df[conf_cols].mean(axis=1)
    df["_low_conf_count"] = (df[conf_cols] < 0.3).sum(axis=1)

    result = df.groupby("class_label").agg(
        mean_confidence=("_mean_conf", "mean"),
        low_conf_pct=("_low_conf_count", lambda x: x.mean() / len(conf_cols)),
    ).reset_index()

    result["mean_confidence"] = result["mean_confidence"].round(4)
    result["low_conf_pct"] = result["low_conf_pct"].round(4)

    return result.sort_values("class_label").reset_index(drop=True)


def explore_vicos(annotations_path: str | Path) -> dict[str, Any]:
    """Load annotations + compute distribution + quality. Return summary dict."""
    df = load_annotations(annotations_path)
    dist = class_distribution(df)
    qual = keypoint_quality(df)

    logger.info("ViCoS: %d instances, %d classes", len(df), len(dist))

    return {
        "total_instances": len(df),
        "num_classes": len(dist),
        "class_distribution": dist.to_dict(orient="records"),
        "keypoint_quality": qual.to_dict(orient="records"),
    }


def plot_class_distribution() -> None:
    """Plot class distribution. TODO"""


def plot_pose_skeleton() -> None:
    """Overlay pose skeleton on sample. TODO"""
