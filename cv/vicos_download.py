"""ViCoS jiu-jitsu dataset downloader + verifier.

Source: https://vicos.si/resources/jiujitsu/ — 120,279 labeled images,
~14 GB, JSON COCO 17-keypoint annotations, 10 positions → 18 classes.

Usage:
    uv run python -m cv.vicos_download           # download annotations + images
    uv run python -m cv.vicos_download --annotations-only  # annotations only
    uv run python -m cv.vicos_download --force             # re-download

Download URLs:
    Actual download links are not hardcoded — check the ViCoS page above
    for current archive URLs. Until ANNOTATIONS_URL / IMAGES_URL are set
    the module operates in verify-only mode.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VICOS_DIR: Path = Path(__file__).resolve().parent / "vicos_data"
VICOS_URL: str = "https://vicos.si/resources/jiujitsu/"
ANNOTATIONS_URL: str = ""
IMAGES_URL: str = ""

_GB = 1_000_000_000


@dataclass
class VicosSample:
    """Single ViCoS annotation sample."""

    image_path: str
    keypoints: list[list[float]]
    class_label: str


def download_annotations(dest: Path | None = None, force: bool = False) -> Path:
    """Download and extract annotation JSON from ViCoS.

    Parameters
    ----------
    dest : Path or None
        Destination directory (defaults to ``VICOS_DIR / "annotations"``).
    force : bool
        Re-download even if target exists.

    Returns
    -------
    Path
        The destination directory.

    Raises
    ------
    ValueError
        If ``ANNOTATIONS_URL`` is not configured.
    """
    if not ANNOTATIONS_URL:
        raise ValueError(
            "ViCoS download URL not configured — "
            "check https://vicos.si/resources/jiujitsu/ for download links"
        )

    dest = dest or VICOS_DIR / "annotations"

    if dest.exists() and not force:
        return dest

    # TODO: implement actual HTTP download + extraction when URLs known
    dest.mkdir(parents=True, exist_ok=True)

    return dest


def download_images(
    dest: Path | None = None,
    subset: float | None = None,
    force: bool = False,
) -> Path:
    """Download ViCoS images (optional, ~14 GB).

    Parameters
    ----------
    dest : Path or None
        Destination directory (defaults to ``VICOS_DIR / "images"``).
    subset : float or None
        Fraction of images to download (0.0–1.0). ``None`` = all.
    force : bool
        Re-download even if target exists.

    Returns
    -------
    Path
        The destination directory.

    Raises
    ------
    ValueError
        If ``ANNOTATIONS_URL`` is not configured.
    RuntimeError
        If available disk space is below 20 GB.
    """
    if not ANNOTATIONS_URL:
        raise ValueError(
            "ViCoS download URL not configured — "
            "check https://vicos.si/resources/jiujitsu/ for download links"
        )

    dest = dest or VICOS_DIR / "images"

    parent = dest.parent if dest.exists() else VICOS_DIR
    parent.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(parent)
    if usage.free < 20 * _GB:
        raise RuntimeError(
            f"Insufficient disk space: {usage.free / _GB:.1f} GB available, "
            f"need >= 20 GB"
        )

    if dest.exists() and not force:
        return dest

    if subset is not None:
        if not 0.0 <= subset <= 1.0:
            raise ValueError(f"subset must be between 0.0 and 1.0, got {subset}")

    # TODO: implement actual HTTP download when URLs known
    dest.mkdir(parents=True, exist_ok=True)

    return dest


def verify(dest: Path | None = None) -> dict[str, Any]:
    """Verify downloaded ViCoS data integrity.

    Parameters
    ----------
    dest : Path or None
        Root of the ``vicos_data`` directory (defaults to ``VICOS_DIR``).

    Returns
    -------
    dict
        Keys:
            images_found : int
                Number of ``.jpg`` files in ``images/``.
            annotation_entries : int
                Number of entries in ``annotations/annotations.json``.
            missing_files : list[str]
                Image filenames referenced by annotations but not found on disk.
    """
    dest = dest or VICOS_DIR
    images_dir = dest / "images"
    annotations_file = dest / "annotations" / "annotations.json"

    images_found = 0
    if images_dir.is_dir():
        images_found = len(list(images_dir.glob("*.jpg")))

    annotation_entries = 0
    annotated_image_ids: list[int] = []
    if annotations_file.is_file():
        with open(annotations_file) as f:
            data = json.load(f)
        annotations = data.get("annotations", [])
        annotation_entries = len(annotations)
        for ann in annotations:
            img_id: int | None = ann.get("image_id") or ann.get("id")
            if img_id is not None:
                annotated_image_ids.append(img_id)

    missing_files: list[str] = []
    for img_id in annotated_image_ids:
        expected = f"img_{img_id:04d}.jpg"
        if not (images_dir / expected).is_file():
            missing_files.append(expected)

    return {
        "images_found": images_found,
        "annotation_entries": annotation_entries,
        "missing_files": missing_files,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ViCoS Dataset Downloader")
    parser.add_argument(
        "--annotations-only",
        action="store_true",
        help="Download only annotation files (skip images)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files exist",
    )
    args = parser.parse_args()

    print("ViCoS dataset downloader")
    print(f"  Page: {VICOS_URL}")
    print(f"  Local dir: {VICOS_DIR}")

    if args.annotations_only:
        print("  Mode: annotations only")
        try:
            download_annotations(force=args.force)
        except ValueError as e:
            print(f"  SKIP: {e}")
    else:
        try:
            download_annotations(force=args.force)
        except ValueError as e:
            print(f"  SKIP annotations: {e}")

        try:
            download_images(force=args.force)
        except (ValueError, RuntimeError) as e:
            print(f"  SKIP images: {e}")

    result = verify()
    print(f"  Verify: {result['images_found']} images, "
          f"{result['annotation_entries']} annotations, "
          f"{len(result['missing_files'])} missing")
    if result["missing_files"]:
        for fname in result["missing_files"][:5]:
            print(f"    missing: {fname}")
