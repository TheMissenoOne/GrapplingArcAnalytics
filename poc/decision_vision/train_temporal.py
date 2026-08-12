"""Leave-one-match-out evaluation of temporal Decision Vision features.

This is intentionally a probe, not a deep fine-tune. The previous static RGB
experiment memorized venue/identity under fine-tuning and showed chance-level
transfer under a frozen backbone. The next POC asks whether BJJ-specific,
identity-reduced features transfer across held-out matches.

Experiments:
  pose
  position
  fused

Model:
  StandardScaler -> multinomial LogisticRegression(class_weight="balanced")

Evaluation:
  LeaveOneGroupOut by match_id.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from decision_vision.progress import ProgressReporter

logger = logging.getLogger("decision_vision.train_temporal")

HEADS = {
    "leaf": "leaf_label",
    "family": "family_label",
    "category": "category_label",
}


def _load(data_dir: Path) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    samples = pd.read_csv(data_dir / "samples.csv")
    payload = np.load(data_dir / "features.npz", allow_pickle=True)
    x_pose = np.asarray(payload["X_pose"], dtype=np.float32)
    x_position = np.asarray(payload["X_position"], dtype=np.float32)

    if len(samples) != len(x_pose) or len(samples) != len(x_position):
        raise ValueError("samples/features row count mismatch")

    return samples, {
        "pose": x_pose,
        "position": x_position,
        "fused": np.concatenate([x_pose, x_position], axis=1),
    }


def _evaluate_one(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    *,
    c: float,
    max_iter: int,
) -> dict[str, object]:
    logo = LeaveOneGroupOut()

    truth: list[str] = []
    pred: list[str] = []
    folds: list[dict[str, object]] = []
    eligible_total = 0
    skipped_unseen = 0

    for fold_index, (train_idx, test_idx) in enumerate(
        logo.split(x, y, groups),
        start=1,
    ):
        y_train = y[train_idx]
        y_test = y[test_idx]

        train_classes = sorted(set(y_train.tolist()))
        if len(train_classes) < 2:
            folds.append(
                {
                    "fold": fold_index,
                    "held_out_match": str(groups[test_idx][0]),
                    "status": "skipped_less_than_2_train_classes",
                }
            )
            continue

        supported = np.isin(y_test, train_classes)
        eligible_total += int(supported.sum())
        skipped_unseen += int((~supported).sum())

        if not np.any(supported):
            folds.append(
                {
                    "fold": fold_index,
                    "held_out_match": str(groups[test_idx][0]),
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
        model.fit(x[train_idx], y_train)

        fold_truth = y_test[supported]
        fold_pred = model.predict(x[test_idx][supported])

        truth.extend(fold_truth.tolist())
        pred.extend(fold_pred.tolist())

        folds.append(
            {
                "fold": fold_index,
                "held_out_match": str(groups[test_idx][0]),
                "status": "ok",
                "train_classes": len(train_classes),
                "eval_samples": len(fold_truth),
                "accuracy": round(
                    float(accuracy_score(fold_truth, fold_pred)),
                    4,
                ),
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
                "uniform_chance_accuracy": round(1.0 / len(train_classes), 4),
            }
        )

    if not truth:
        return {
            "status": "no_evaluable_samples",
            "folds": folds,
            "eligible_samples": eligible_total,
            "skipped_unseen_class_samples": skipped_unseen,
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
        "skipped_unseen_class_samples": skipped_unseen,
        "folds": folds,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/cv_decision_poc/temporal"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cv_decision_poc/temporal/report.json"),
    )
    parser.add_argument(
        "--feature-sets",
        nargs="+",
        choices=["pose", "position", "fused"],
        default=["pose", "position", "fused"],
    )
    parser.add_argument("--heads", nargs="+", choices=list(HEADS), default=list(HEADS))
    parser.add_argument("--c", type=float, default=1.0)
    parser.add_argument("--max-iter", type=int, default=4000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s %(message)s",
    )

    samples, features = _load(args.data.resolve())
    groups = samples["match_id"].astype(str).to_numpy()

    if len(set(groups.tolist())) < 3:
        raise RuntimeError(
            "Need at least 3 independent matches for a useful leave-one-match-out probe."
        )

    reporter = ProgressReporter(
        output_dir=args.output.parent,
        run_id="temporal-probe",
        pipeline="temporal_probe",
    )
    total_experiments = max(1, len(args.feature_sets) * len(args.heads))
    reporter.update(
        phase="evaluation",
        current=0,
        total=total_experiments,
        message="Starting leave-one-match-out probes",
    )

    report: dict[str, object] = {
        "protocol": "leave_one_match_out",
        "model": "standard_scaler+balanced_logistic_regression",
        "samples": len(samples),
        "matches": len(set(groups.tolist())),
        "experiments": {},
    }

    completed_experiments = 0
    for feature_name in args.feature_sets:
        x = features[feature_name]
        feature_report: dict[str, object] = {}

        for head in args.heads:
            column = HEADS[head]
            y = samples[column].astype(str).to_numpy()

            result = _evaluate_one(
                x,
                y,
                groups,
                c=args.c,
                max_iter=args.max_iter,
            )
            feature_report[head] = result
            completed_experiments += 1
            reporter.update(
                phase="evaluation",
                current=completed_experiments,
                total=total_experiments,
                message=f"Evaluated {feature_name}/{head}",
                metrics={
                    f"{feature_name}.{head}.macro_f1": result.get("macro_f1"),
                    f"{feature_name}.{head}.accuracy": result.get("accuracy"),
                },
            )
            logger.info(
                "%s/%s -> %s",
                feature_name,
                head,
                json.dumps(
                    {
                        key: value
                        for key, value in result.items()
                        if key != "folds"
                    },
                    ensure_ascii=False,
                ),
            )

        report["experiments"][feature_name] = feature_report

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    reporter.complete(
        message="Temporal probe complete",
        metrics={"experiments": completed_experiments},
    )
    logger.info("report -> %s", args.output)


if __name__ == "__main__":
    main()
