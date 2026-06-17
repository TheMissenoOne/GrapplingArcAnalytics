#!/usr/bin/env python
"""Backfill the sidecar class-label meta for an existing position classifier.

The trained model (``data/processed/position_clf_{type}.joblib``) is fit on
``LabelEncoder``-encoded integers, so it has no human-readable label map on its
own. ``LabelEncoder`` orders classes alphabetically, so the index→label map is
exactly ``sorted(unique(class_label))`` derived from the training keypoint cache
(``data/processed/vicos_keypoints.parquet``).

This script reconstructs that map and writes the sidecar meta — **without
retraining** — but only when the cache's class count matches the model's, so we
never write a map that doesn't line up with the artifact on disk.

Usage:
    uv run python scripts/backfill_clf_meta.py [--model-type rf|xgb] [--all]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import joblib
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cv.baseline_classifier import MODEL_DIR, write_classifier_meta  # noqa: E402

logger = logging.getLogger("backfill_clf_meta")

PARQUET = MODEL_DIR / "vicos_keypoints.parquet"


def backfill(model_type: str) -> bool:
    """Write meta for one model artifact. Returns True on success."""
    model_path = MODEL_DIR / f"position_clf_{model_type}.joblib"
    if not model_path.exists():
        logger.warning("No model artifact at %s — skipping", model_path)
        return False
    if not PARQUET.exists():
        logger.error("Keypoint cache missing: %s", PARQUET)
        return False

    df = pd.read_parquet(PARQUET)
    if "class_label" not in df.columns:
        logger.error("Parquet %s has no 'class_label' column", PARQUET)
        return False
    classes = sorted(df["class_label"].astype(str).unique())

    model = joblib.load(model_path)
    n_model = len(getattr(model, "classes_", []))
    if n_model != len(classes):
        logger.error(
            "Class-count mismatch: %s has %d classes but the keypoint cache yields "
            "%d (%s). The cache is a different/subset dataset than the model was "
            "trained on — regenerate the full ViCoS keypoint parquet and retrain "
            "(train_baseline now emits meta automatically), rather than backfilling "
            "a mismatched map.",
            model_path.name,
            n_model,
            len(classes),
            classes,
        )
        return False

    write_classifier_meta(model_type, classes)
    logger.info("Backfilled meta for %s: %s", model_type, classes)
    return True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-type", choices=["rf", "xgb"], default="rf")
    parser.add_argument("--all", action="store_true", help="backfill both rf and xgb")
    args = parser.parse_args(argv)

    targets = ["rf", "xgb"] if args.all else [args.model_type]
    ok = all(backfill(mt) for mt in targets)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
