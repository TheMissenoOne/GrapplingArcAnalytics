"""Build derived temporal features from ephemeral Decision Vision frames.

No media is persisted. The existing POC manifest supplies URL/timestamp/labels.
Frames are fetched via ``decision_vision.frame_stream.FrameStream`` into RAM,
then transformed into:
  * generic COCO pose dynamics using existing ``cv.pose_estimate`` +
    ``cv.pose_features.pair_to_features``;
  * externally-trained BJJ position priors using existing
    ``cv.roboflow_classifier.RoboflowClassifier`` (default ``bjj3/1``).

The output contains numeric features only:
  samples.csv
  features.npz
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from decision_vision.frame_stream import FrameStream
from decision_vision.progress import ProgressReporter

logger = logging.getLogger("decision_vision.temporal_features")

POSITION_CLASSES = (
    "standing",
    "5050 guard",
    "back",
    "closed guard",
    "half guard",
    "mount",
    "open guard",
    "side control",
    "takedown",
    "turtle",
)

POSE_DIM = 68


@dataclass(frozen=True)
class CriterionGroup:
    key: str
    rows: pd.DataFrame


def _criterion_key(row: pd.Series) -> str:
    return ":".join(
        [
            str(row["match_id"]),
            str(row["source_event_index"]),
            str(row["criterion_event_index"]),
            str(row["response_event_index"]),
        ]
    )


def _decode_bgr(jpeg: bytes) -> np.ndarray:
    array = np.frombuffer(jpeg, dtype=np.uint8)
    frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("OpenCV could not decode FFmpeg JPEG bytes")
    return frame


def _collapse_position(label: str) -> str:
    value = str(label or "").strip().lower()
    if value.endswith("_top"):
        value = value[:-4]
    elif value.endswith("_bottom"):
        value = value[:-7]
    return value.strip().replace("_", " ")


def _position_vector(probs: dict[str, float]) -> np.ndarray:
    out = np.zeros(len(POSITION_CLASSES), dtype=np.float32)
    index = {label: idx for idx, label in enumerate(POSITION_CLASSES)}

    for label, probability in probs.items():
        collapsed = _collapse_position(label)
        if collapsed in index:
            out[index[collapsed]] += float(probability)

    total = float(out.sum())
    if total > 0:
        out /= total
    return out


def _pose_vector(frame: np.ndarray, estimator) -> tuple[np.ndarray, float]:
    poses = estimator.estimate(frame)
    pair = estimator.select_grappler_pair(poses, order_by="hip_y")
    if pair is None:
        return np.zeros(POSE_DIM, dtype=np.float32), 0.0

    from cv.pose_features import pair_to_features

    features = pair_to_features(pair[0], pair[1]).astype(np.float32)
    if features.shape != (POSE_DIM,):
        raise RuntimeError(f"Unexpected pose feature shape: {features.shape}")
    return features, 1.0


def _summary(matrix: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """Summarize a T×D temporal signal without raw RGB identity features."""
    if matrix.ndim != 2:
        raise ValueError(f"Expected 2D temporal matrix, got {matrix.shape}")

    center_index = int(np.argmin(np.abs(offsets)))
    center = matrix[center_index]

    pre = matrix[offsets < 0]
    post = matrix[offsets > 0]

    pre_mean = pre.mean(axis=0) if len(pre) else center
    post_mean = post.mean(axis=0) if len(post) else center
    delta = post_mean - pre_mean
    std = matrix.std(axis=0)

    if len(matrix) >= 2:
        signed_motion = matrix[-1] - matrix[0]
        abs_motion = np.abs(np.diff(matrix, axis=0)).mean(axis=0)
    else:
        signed_motion = np.zeros_like(center)
        abs_motion = np.zeros_like(center)

    return np.concatenate(
        [
            center,
            pre_mean,
            post_mean,
            delta,
            std,
            signed_motion,
            abs_motion,
        ]
    ).astype(np.float32)


def _load_manifest(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "sample_id",
        "match_id",
        "source_url",
        "frame_ts",
        "frame_offset",
        "source_event_index",
        "criterion_event_index",
        "response_event_index",
        "leaf_label",
        "family_label",
        "category_label",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Manifest missing columns: {sorted(missing)}")

    frame["criterion_key"] = frame.apply(_criterion_key, axis=1)
    return frame


def build_features(
    manifest: pd.DataFrame,
    *,
    frame_stream: FrameStream,
    pose_estimator,
    position_classifier,
    use_pose: bool,
    use_position: bool,
    progress: ProgressReporter | None = None,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    samples: list[dict[str, object]] = []
    pose_rows: list[np.ndarray] = []
    position_rows: list[np.ndarray] = []

    grouped = manifest.groupby("criterion_key", sort=False)
    total = grouped.ngroups

    for group_index, (criterion_key, rows) in enumerate(grouped, start=1):
        rows = rows.sort_values(["frame_offset", "frame_ts"]).reset_index(drop=True)
        offsets = rows["frame_offset"].astype(float).to_numpy(dtype=np.float32)

        pose_seq: list[np.ndarray] = []
        pose_ok: list[float] = []
        position_seq: list[np.ndarray] = []

        for _, row in rows.iterrows():
            jpeg = frame_stream.fetch_jpeg(
                str(row["source_url"]),
                float(row["frame_ts"]),
            )
            frame = _decode_bgr(jpeg)

            if use_pose:
                pose, ok = _pose_vector(frame, pose_estimator)
            else:
                pose, ok = np.zeros(POSE_DIM, dtype=np.float32), 0.0
            pose_seq.append(pose)
            pose_ok.append(ok)

            if use_position:
                probs = position_classifier.classify_frame_probs(frame)
                position = _position_vector(probs)
            else:
                position = np.zeros(len(POSITION_CLASSES), dtype=np.float32)
            position_seq.append(position)

        pose_matrix = np.stack(pose_seq)
        position_matrix = np.stack(position_seq)

        pose_summary = _summary(pose_matrix, offsets)
        # Append pose-detection quality separately so failure is visible signal.
        quality = np.asarray(
            [
                np.mean(pose_ok),
                np.min(pose_ok),
                np.max(pose_ok),
            ],
            dtype=np.float32,
        )
        pose_summary = np.concatenate([pose_summary, quality])

        position_summary = _summary(position_matrix, offsets)

        first = rows.iloc[0]

        def _pick(row: pd.Series, visual_col: str, fallback: str) -> str:
            value = row.get(visual_col)
            if pd.notna(value) and str(value).strip() not in ("", "nan", "None"):
                return str(value)
            return str(row[fallback])

        samples.append(
            {
                "criterion_key": criterion_key,
                "match_id": str(first["match_id"]),
                "source_key": str(first.get("source_key", "")),
                "criterion_label": str(first.get("criterion_label", "")),
                "response_key": str(first.get("response_key", "")),
                "leaf_label": _pick(first, "visual_leaf_label", "leaf_label"),
                "family_label": _pick(first, "visual_family_label", "family_label"),
                "category_label": _pick(first, "visual_category_label", "category_label"),
                "frame_count": len(rows),
                "pose_pair_rate": round(float(np.mean(pose_ok)), 4),
            }
        )
        pose_rows.append(pose_summary)
        position_rows.append(position_summary)

        if progress is not None and (
            group_index == total or group_index % 5 == 0
        ):
            progress.update(
                phase="features",
                current=group_index,
                total=total,
                message=f"Built temporal features for {group_index}/{total} criterion groups",
                metrics={
                    "criterion_groups": group_index,
                    "pose_pair_rate_mean": round(
                        float(np.mean([item["pose_pair_rate"] for item in samples])),
                        4,
                    ),
                },
            )

        if group_index == total or group_index % 10 == 0:
            logger.info("criterion groups %d/%d", group_index, total)

    return (
        pd.DataFrame(samples),
        np.stack(pose_rows).astype(np.float32),
        np.stack(position_rows).astype(np.float32),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/cv_decision_poc/manifest.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cv_decision_poc/temporal"),
    )
    parser.add_argument("--pose-model", type=Path)
    parser.add_argument("--position-model-id", default="bjj3/1")
    parser.add_argument("--position-api-url", default="https://serverless.roboflow.com")
    parser.add_argument("--disable-pose", action="store_true")
    parser.add_argument("--disable-position", action="store_true")
    parser.add_argument("--ffmpeg-timeout", type=float, default=45.0)
    parser.add_argument("--frame-size", type=int, default=320)
    parser.add_argument("--cookies-from-browser")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(message)s",
    )

    if args.disable_pose and args.disable_position:
        raise SystemExit("At least one feature family must be enabled")

    from cv.pose_estimate import PoseEstimator
    from cv.roboflow_classifier import RoboflowClassifier

    manifest = _load_manifest(args.manifest.resolve())

    ytdlp_extra = (
        ["--cookies-from-browser", args.cookies_from_browser]
        if args.cookies_from_browser
        else []
    )
    stream = FrameStream(
        output_size=max(224, args.frame_size),
        timeout_seconds=max(5.0, args.ffmpeg_timeout),
        ytdlp_extra_args=ytdlp_extra,
    )

    pose_estimator = PoseEstimator(model_path=args.pose_model)
    position_classifier = RoboflowClassifier(
        model_id=args.position_model_id,
        api_key=os.environ.get("ROBOFLOW_API_KEY"),
        api_url=args.position_api_url,
    )

    reporter = ProgressReporter(
        output_dir=args.output,
        run_id="temporal-features",
        pipeline="temporal_features",
    )
    reporter.update(
        phase="features",
        current=0,
        total=max(1, manifest["criterion_key"].nunique()),
        message="Starting temporal feature extraction",
    )

    samples, x_pose, x_position = build_features(
        manifest,
        frame_stream=stream,
        pose_estimator=pose_estimator,
        position_classifier=position_classifier,
        use_pose=not args.disable_pose,
        use_position=not args.disable_position,
        progress=reporter,
    )

    args.output.mkdir(parents=True, exist_ok=True)
    samples.to_csv(args.output / "samples.csv", index=False)
    np.savez_compressed(
        args.output / "features.npz",
        X_pose=x_pose,
        X_position=x_position,
        position_classes=np.asarray(POSITION_CLASSES, dtype=object),
    )

    reporter.complete(
        message="Temporal features complete",
        metrics={
            "samples": len(samples),
            "pose_pair_rate_mean": round(
                float(samples["pose_pair_rate"].mean()),
                4,
            ),
        },
    )
    logger.info(
        "saved derived features only: samples=%d pose=%s position=%s -> %s",
        len(samples),
        x_pose.shape,
        x_position.shape,
        args.output,
    )
    logger.info("No video/frame media persisted.")


if __name__ == "__main__":
    main()
