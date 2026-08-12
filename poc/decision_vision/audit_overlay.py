"""Read-only audit overlay: draw what the live-transfer pipeline saw.

For each raw switch_audit frame, runs the SAME detection path the pipeline
already uses (``cv.pose_estimate.PoseEstimator`` + ``select_grappler_pair``,
same model/config as ``poc/decision_vision/live_state.py``) and draws:

- every detected person's bbox + COCO-17 skeleton,
- which two (if any) ``select_grappler_pair`` chose as the athlete pair, and
  in what order (``sel0``/``sel1`` — the index fed to the role/position probe),
- the role/role_conf/position/position_conf the pipeline actually recorded for
  that timestamp, read back from the run's own ``state_samples_raw.csv``
  (not re-inferred — avoids drawing a number this script invented).

Pure visualization. No retraining, no threshold/config change, no second
inference route: detection + pair selection are the real ``cv.pose_estimate``
code, imported unmodified.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FRAME_NAME_RE = re.compile(r"frame_(\d+\.\d+)s\.png$")

# COCO-17 skeleton edges, cheap to draw from the same keypoints the pipeline used.
SKELETON: list[tuple[int, int]] = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]

SEL_COLORS = {"sel0": (0, 220, 0), "sel1": (0, 140, 255)}
UNSELECTED_COLOR = (180, 180, 180)


def frame_timestamp(path: Path) -> float | None:
    """Parse the video timestamp out of ``frame_05313.50s.png``."""
    match = FRAME_NAME_RE.search(path.name)
    return float(match.group(1)) if match else None


def label_detections(
    poses: list[np.ndarray],
    pair: tuple[np.ndarray, np.ndarray] | None,
) -> list[str]:
    """Label each detection by its role in the pipeline's chosen pair.

    Matches by object identity, not value: ``select_grappler_pair`` only
    ``sorted()``s the ``poses`` list, it never copies the arrays, so the
    selected pair members are literally the same objects as their source
    entries in ``poses``.
    """
    if pair is None:
        return ["unselected" for _ in poses]
    sel0, sel1 = pair
    labels = []
    for pose in poses:
        if pose is sel0:
            labels.append("sel0")
        elif pose is sel1:
            labels.append("sel1")
        else:
            labels.append("unselected")
    return labels


def lookup_recorded(
    df: pd.DataFrame,
    timestamp: float,
    tolerance: float = 0.05,
) -> dict[str, object] | None:
    """Row of ``state_samples_raw.csv`` for this timestamp, or None if absent."""
    matches = df.loc[(df["timestamp"] - timestamp).abs() <= tolerance]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def _draw_pose(
    img: np.ndarray,
    kp: np.ndarray,
    color: tuple[int, int, int],
    label: str,
    kp_conf_min: float = 0.3,
) -> None:
    xs, ys, conf = kp[:, 0], kp[:, 1], kp[:, 2]
    visible = conf > kp_conf_min

    if visible.sum() >= 2:
        x1, y1 = int(xs[visible].min()), int(ys[visible].min())
        x2, y2 = int(xs[visible].max()), int(ys[visible].max())
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            img, label, (x1, max(15, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA,
        )

    for a, b in SKELETON:
        if conf[a] > kp_conf_min and conf[b] > kp_conf_min:
            cv2.line(img, (int(xs[a]), int(ys[a])), (int(xs[b]), int(ys[b])), color, 2)
    for i in range(17):
        if conf[i] > kp_conf_min:
            cv2.circle(img, (int(xs[i]), int(ys[i])), 3, color, -1)


def _draw_corner_text(img: np.ndarray, lines: list[str]) -> None:
    y = 20
    for line in lines:
        cv2.putText(img, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(
            img, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA
        )
        y += 20


def annotate_frame(
    img: np.ndarray,
    poses: list[np.ndarray],
    pair: tuple[np.ndarray, np.ndarray] | None,
    recorded: dict[str, object] | None,
) -> np.ndarray:
    out = img.copy()
    labels = label_detections(poses, pair)
    for pose, label in zip(poses, labels):
        color = SEL_COLORS.get(label, UNSELECTED_COLOR)
        _draw_pose(out, pose, color, label)

    lines = [f"detections: {len(poses)}"]
    if recorded is not None:
        lines.append(f"role={recorded['role']} ({float(recorded['role_conf']):.2f})")
        lines.append(f"position={recorded['position']} ({float(recorded['position_conf']):.2f})")
    else:
        lines.append("role/position: not recorded at this timestamp")
    _draw_corner_text(out, lines)
    return out


def run(
    *,
    frames_dir: Path,
    output_dir: Path,
    state_csv: Path | None,
) -> list[tuple[Path, int]]:
    """Annotate every PNG in frames_dir -> output_dir. Returns (path, n_detections)."""
    from decision_vision.common import find_analytics_root

    find_analytics_root(None)  # repo root onto sys.path so top-level `cv` package resolves
    from cv.pose_estimate import PoseEstimator

    frame_paths = sorted(frames_dir.glob("*.png"))
    if not frame_paths:
        raise SystemExit(f"No PNGs found in {frames_dir}")

    recorded_df = None
    if state_csv is not None and state_csv.exists():
        recorded_df = pd.read_csv(state_csv)
    else:
        logger.warning(
            "No state_samples_raw.csv at %s — role/position overlay will be blank", state_csv
        )

    estimator = PoseEstimator()
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[Path, int]] = []
    for path in frame_paths:
        img = cv2.imread(str(path))
        if img is None:
            logger.warning("could not read %s, skipping", path)
            continue

        poses = estimator.estimate(img)
        pair = estimator.select_grappler_pair(poses)

        ts = frame_timestamp(path)
        recorded = (
            lookup_recorded(recorded_df, ts) if recorded_df is not None and ts is not None else None
        )

        out_img = annotate_frame(img, poses, pair, recorded)
        out_path = output_dir / path.name
        cv2.imwrite(str(out_path), out_img)
        results.append((path, len(poses)))
        logger.info("%s -> %d detections", path.name, len(poses))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path("data/cv_decision_poc/vicos_transfer_window3")
    parser.add_argument(
        "--frames-dir", type=Path, default=default_root / "switch_audit" / "frames"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=default_root / "switch_audit" / "frames_overlay"
    )
    parser.add_argument("--state-csv", type=Path, default=default_root / "state_samples_raw.csv")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()), format="%(levelname)s %(message)s"
    )

    results = run(frames_dir=args.frames_dir, output_dir=args.output_dir, state_csv=args.state_csv)
    counts = [n for _, n in results]
    mean = sum(counts) / len(counts)
    print(
        f"annotated {len(results)} frames -> {args.output_dir} "
        f"[detections/frame min={min(counts)} max={max(counts)} mean={mean:.2f}]"
    )


if __name__ == "__main__":
    main()
