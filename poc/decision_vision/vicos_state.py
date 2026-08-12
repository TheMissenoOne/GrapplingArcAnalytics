"""ViCoS BJJ state reconstruction — pose -> (position, top/bottom role).

Research/benchmark only: ViCoS is CC BY-NC-SA 4.0 (non-commercial).

Source + citation (Hudovernik & Skocaj, ACM MMSports'22):
  https://vicos.si/resources/jiujitsu/
Annotations: http://data.vicos.si/datasets/JuiJuitsu/annotations.json (~200 MB)

Questions this answers:
  1. Can body geometry alone (two COCO-17 poses, pair_to_features) predict
     the combat state: position (10) / role-encoded state (18) / top role?
  2. Under leave-one-sequence-out — the leak-safe split (adjacent frames of
     the same 6 sparring sequences share appearance/mat/lighting; a random
     frame split would be the RGB-POC failure mode again).
  3. Providing the ground-truth analogue for the live-video tracker:
     athlete1/athlete2 + top/bottom + position continuity.

Protocol mirrors poc/decision_vision/train_temporal.py (leave-one-group-out,
unseen-class skip, uniform chance per fold, StandardScaler + balanced LR).
Only numeric artifacts (.npz features, JSON report) are persisted.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROLE_NEUTRAL = {"standing", "5050_guard"}
POSITION_SUFFIX = {"1", "2"}

DEFAULT_ANNOTATIONS = Path("data/external/decision_vision/vicos_bjj/annotations.json")
DEFAULT_OUTPUT = Path("data/cv_decision_poc/vicos_state")

HEAD_STATE = "state"
HEAD_POSITION = "position"
HEAD_ROLE = "role"
HEADS = (HEAD_STATE, HEAD_POSITION, HEAD_ROLE)


def load_annotations(path: Path) -> pd.DataFrame:
    """Load ViCoS annotations JSON into a frame with sequence/pose columns.

    Columns: sequence_id (int, first 2 digits of image id), frame (int),
    position (str, 18-class label), pose1, pose2 (np.ndarray (17, 3)).
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    rows: list[dict[str, Any]] = []
    for ann in raw:
        image = str(ann.get("Image") or ann.get("image") or "")
        sequence_id = int(image[:2]) if len(image) >= 2 and image[:2].isdigit() else -1
        pose1_raw = ann.get("Pose1", ann.get("pose1"))
        pose2_raw = ann.get("Pose2", ann.get("pose2"))
        if pose1_raw is None or pose2_raw is None or len(pose1_raw) != 17 or len(pose2_raw) != 17:
            continue  # single-pose frames are not usable for pair features
        pose1 = np.asarray(pose1_raw, dtype=np.float64).reshape(17, 3)
        pose2 = np.asarray(pose2_raw, dtype=np.float64).reshape(17, 3)
        rows.append(
            {
                "sequence_id": sequence_id,
                "frame": int(ann.get("Frame") or ann.get("frame") or 0),
                "position": str(ann.get("Position") or ann.get("position") or ""),
                "pose1": pose1,
                "pose2": pose2,
            }
        )
    return pd.DataFrame(rows)


def position_head(position: str) -> str:
    """Strip the athlete-role suffix (mount2 -> mount; standing stays standing)."""
    if position[-1:] in POSITION_SUFFIX:
        return position[:-1]
    return position


def role_head(position: str) -> str:
    """Which athlete is top, per the role-aware label; neutral for symmetric ones."""
    if position in ROLE_NEUTRAL:
        return "none"
    if position.endswith("1"):
        return "athlete1"
    if position.endswith("2"):
        return "athlete2"
    return "none"


def build_features(frame: pd.DataFrame) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Pairwise pose features (cv.pose_features.pair_to_features) + head targets.

    Returns (X (n, 68), {head: y (n,)}). Pure; no IO.
    """
    from cv.pose_features import pair_to_features  # lazy: heavier imports

    x_rows = []
    for _, row in frame.iterrows():
        x_rows.append(pair_to_features(row["pose1"], row["pose2"]))
    x = np.stack(x_rows).astype(np.float32)

    y: dict[str, np.ndarray] = {
        HEAD_STATE: frame["position"].to_numpy(),
        HEAD_POSITION: frame["position"].map(position_head).to_numpy(),
        HEAD_ROLE: frame["position"].map(role_head).to_numpy(),
    }
    return x, y


def evaluate_head(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    *,
    c: float = 1.0,
    max_iter: int = 4000,
) -> dict[str, Any]:
    """Leave-one-sequence-out probe for one head. Mirrors train_temporal."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    unique_groups = np.unique(groups)
    unique_classes = np.unique(y)

    truth: list[str] = []
    pred: list[str] = []
    skipped_unseen = 0
    eligible_total = 0
    folds: list[dict[str, Any]] = []

    for fold_index, held_out in enumerate(unique_groups, start=1):
        train_mask = groups != held_out
        test_mask = groups == held_out
        if not np.any(test_mask):
            continue

        trains = np.unique(y[train_mask])
        test_rows = y[test_mask]
        known_mask = np.isin(test_rows, trains)
        eligible_total += int(np.sum(known_mask))
        skipped_unseen += int(np.sum(~known_mask))
        if not np.any(known_mask):
            folds.append(
                {
                    "fold": fold_index,
                    "held_out_sequence": str(held_out),
                    "status": "skipped_no_seen_test_classes",
                }
            )
            continue

        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=c,
                max_iter=max_iter,
                class_weight="balanced",
                solver="lbfgs",
            ),
        )
        model.fit(x[train_mask], y[train_mask])

        fold_truth = y[test_mask][known_mask]
        fold_pred = model.predict(x[test_mask][known_mask])
        truth.extend(fold_truth.tolist())
        pred.extend(fold_pred.tolist())

        folds.append(
            {
                "fold": fold_index,
                "held_out_sequence": str(held_out),
                "status": "ok",
                "train_classes": int(len(trains)),
                "eval_samples": int(len(fold_truth)),
                "accuracy": round(float(accuracy_score(fold_truth, fold_pred)), 4),
                "macro_f1": round(
                    float(
                        f1_score(
                            fold_truth,
                            fold_pred,
                            average="macro",
                            zero_division=0,
                        )
                    ),
                    4,
                ),
                "uniform_chance_accuracy": round(1.0 / len(trains), 4),
            }
        )

    if not truth:
        return {
            "status": "no_evaluable_samples",
            "folds": folds,
            "eligible_samples": eligible_total,
            "skipped_unseen_class_samples": skipped_unseen,
            "n_classes": int(len(unique_classes)),
        }

    return {
        "status": "ok",
        "accuracy": round(float(accuracy_score(truth, pred)), 4),
        "macro_f1": round(
            float(
                f1_score(
                    truth,
                    pred,
                    average="macro",
                    zero_division=0,
                )
            ),
            4,
        ),
        "evaluated_samples": len(truth),
        "eligible_samples": eligible_total,
        "skipped_unseen_class_samples": skipped_unseen,
        "n_classes": int(len(unique_classes)),
        "folds": folds,
    }


def swap_role(label: str) -> str:
    """athlete1 <-> athlete2 for role-equivariant augmentation."""
    if label == "athlete1":
        return "athlete2"
    if label == "athlete2":
        return "athlete1"
    return label


def swap_state(label: str) -> str:
    """mount1 <-> mount2 for state-equivariant augmentation."""
    if label[-1:] in POSITION_SUFFIX:
        return label[:-1] + ("2" if label.endswith("1") else "1")
    return label


def _train_full_models(
    x: np.ndarray,
    y: dict[str, np.ndarray],
    output: Path,
    *,
    c: float = 1.0,
    max_iter: int = 4000,
    x_alt: np.ndarray | None = None,
    y_alt: dict[str, np.ndarray] | None = None,
) -> None:
    """Fit one probe per head on ALL frames; persist for live transfer use.

    x_alt/y_alt (optional): the same samples with swapped pair ordering —
    features recomputed as pair_to_features(kp1, kp0) and labels mirrored
    (athlete1<->athlete2, mount1<->mount2). ViCoS athlete_idx is persistent
    identity, not geometry (~0.47 hip-y correlation), so any live ordering
    that does not reproduce ViCoS's exact identity numbering would otherwise
    destroy the role/state heads. Symmetrical augmentation makes the probe
    predict labels relative to the INPUT order — the live contract is the
    tracker's stable track_0/track_1 identity.
    """
    from joblib import dump
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    models_dir = output / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    classes_out: dict[str, list[str]] = {}
    for head in HEADS:
        if x_alt is not None and y_alt is not None:
            x_fit = np.concatenate([x, x_alt], axis=0)
            y_fit = np.concatenate([y[head], y_alt[head]], axis=0)
            suffix = "_eq"
        else:
            x_fit, y_fit = x, y[head]
            suffix = ""
        model = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=c,
                max_iter=max_iter,
                class_weight="balanced",
                solver="lbfgs",
            ),
        )
        model.fit(x_fit, y_fit)
        dump(model, models_dir / f"probe_{head}{suffix}.joblib")
        classes_out[f"{head}{suffix}"] = sorted(model.classes_.tolist())
        logger.info(
            "trained full probe %s -> %s",
            head,
            models_dir / f"probe_{head}{suffix}.joblib",
        )
    (output / "classes.json").write_text(
        json.dumps(classes_out, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-features", action="store_true")
    parser.add_argument("--train-full", action="store_true")
    parser.add_argument("--equivariant", action="store_true")
    parser.add_argument("--c", type=float, default=1.0)
    parser.add_argument("--max-iter", type=int, default=4000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(message)s",
    )

    from decision_vision.progress import ProgressReporter

    reporter = ProgressReporter(
        output_dir=args.output,
        run_id="vicos-state",
        pipeline="vicos_state",
    )

    reporter.update(
        phase="parse",
        current=0,
        total=1,
        message=f"Loading ViCoS annotations from {args.annotations}",
    )
    frame = load_annotations(args.annotations)
    if len(frame) < 1000:
        raise SystemExit(
            f"Annotations look wrong: only {len(frame)} samples. "
            f"Expected ~120k. Check {args.annotations}"
        )
    reporter.update(
        phase="features",
        current=1,
        total=2,
        message=f"Built pose pair features for {len(frame)} frames",
    )

    x, y = build_features(frame)
    if args.cache_features:
        args.output.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            args.output / "features.npz",
            X=x,
            **{f"y_{head}": y[head] for head in HEADS},
        )

    groups = frame["sequence_id"].to_numpy()
    report: dict[str, object] = {
        "protocol": "leave-one-sequence-out",
        "model": "StandardScaler + LogisticRegression(class_weight=balanced)",
        "samples": int(len(frame)),
        "sequences": int(len(np.unique(groups))),
        "features": "cv.pose_features.pair_to_features (68-dim)",
        "experiments": {},
    }

    for head in HEADS:
        reporter.update(
            phase=f"eval/{head}",
            current=2,
            total=3,
            message=f"Leave-one-sequence-out probe on {head}",
        )
        result = evaluate_head(
            x,
            y[head],
            groups,
            c=args.c,
            max_iter=args.max_iter,
        )
        report["experiments"][head] = result
        logger.info("%s -> %s", head, json.dumps(result, ensure_ascii=False))

    if args.train_full:
        x_alt: np.ndarray | None = None
        y_alt: dict[str, np.ndarray] | None = None
        if args.equivariant:
            from cv.pose_features import pair_to_features

            swapped_rows = []
            for _, row in frame.iterrows():
                swapped_rows.append(
                    pair_to_features(row["pose2"], row["pose1"])
                )
            x_alt = np.stack(swapped_rows).astype(np.float32)
            y_alt = {
                HEAD_STATE: np.vectorize(swap_state)(y[HEAD_STATE]),
                HEAD_POSITION: y[HEAD_POSITION],
                HEAD_ROLE: np.vectorize(swap_role)(y[HEAD_ROLE]),
            }
        _train_full_models(
            x,
            y,
            args.output,
            c=args.c,
            max_iter=args.max_iter,
            x_alt=x_alt,
            y_alt=y_alt,
        )

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    reporter.complete(
        message=f"ViCoS state reconstruction complete ({len(frame)} frames)",
        metrics={
            "samples": len(frame),
            "sequences": int(len(np.unique(groups))),
            "state_macro_f1": report["experiments"][HEAD_STATE]["macro_f1"],
        },
    )
    logger.info("report -> %s", args.output / "report.json")


if __name__ == "__main__":
    main()
