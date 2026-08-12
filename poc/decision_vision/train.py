"""Train the hierarchical Decision Criterion model from ephemeral FFmpeg frames.

The manifest contains only DB context, URL and timestamp. This script resolves
each source, asks FFmpeg for exactly one frame at each timestamp, keeps JPEG
bytes only in process RAM, then trains for multiple epochs from that RAM cache.

No extracted frame is written to disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import io
import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from PIL import Image
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, Dataset
from torchvision import models, transforms

from decision_vision.frame_stream import FrameStream

logger = logging.getLogger("decision_vision.train")

HEADS = ("leaf", "family", "category")
COLUMN_BY_HEAD = {
    "leaf": "leaf_label",
    "family": "family_label",
    "category": "category_label",
}
LOSS_WEIGHT = {
    "leaf": 1.0,
    "family": 0.65,
    "category": 0.35,
}


@dataclass(frozen=True)
class LabelSpace:
    classes: tuple[str, ...]
    index: dict[str, int]


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def choose_device(
    explicit: str | None = None,
) -> torch.device:
    if explicit:
        return torch.device(explicit)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _ytdlp_extra_args(
    cookies_from_browser: str | None,
) -> list[str]:
    if not cookies_from_browser:
        return []
    return [
        "--cookies-from-browser",
        cookies_from_browser,
    ]


def preload_frames(
    frame: pd.DataFrame,
    *,
    fetch_workers: int,
    ffmpeg_timeout: float,
    output_size: int,
    cookies_from_browser: str | None,
) -> tuple[pd.DataFrame, dict[str, bytes]]:
    """Fetch all manifest frames once and keep JPEG bytes only in RAM."""
    stream = FrameStream(
        output_size=output_size,
        timeout_seconds=ffmpeg_timeout,
        ytdlp_extra_args=_ytdlp_extra_args(
            cookies_from_browser
        ),
    )

    cache: dict[str, bytes] = {}
    failed: dict[str, str] = {}

    def fetch(row_index: int) -> tuple[int, str, bytes]:
        row = frame.iloc[row_index]
        sample_id = str(row["sample_id"])
        data = stream.fetch_jpeg(
            str(row["source_url"]),
            float(row["frame_ts"]),
        )
        return row_index, sample_id, data

    total = len(frame)
    logger.info(
        "Fetching %d frames with FFmpeg "
        "(RAM only, %d workers)",
        total,
        fetch_workers,
    )

    with ThreadPoolExecutor(
        max_workers=max(1, fetch_workers)
    ) as executor:
        futures = {
            executor.submit(fetch, index): index
            for index in range(total)
        }

        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            sample_id = str(
                frame.iloc[index]["sample_id"]
            )
            try:
                _, sample_id, data = future.result()
                cache[sample_id] = data
            except Exception as exc:
                failed[sample_id] = str(exc)
            completed += 1

            if (
                completed == total
                or completed % 25 == 0
            ):
                logger.info(
                    "FFmpeg frames: %d/%d ok=%d failed=%d",
                    completed,
                    total,
                    len(cache),
                    len(failed),
                )

    usable = frame[
        frame["sample_id"].astype(str).isin(cache)
    ].reset_index(drop=True)

    if failed:
        logger.warning(
            "%d frame(s) failed. First failures: %s",
            len(failed),
            list(failed.items())[:5],
        )

    if len(usable) < 4:
        raise RuntimeError(
            "Fewer than 4 frames could be fetched; "
            "cannot run training."
        )

    total_bytes = sum(
        len(value)
        for value in cache.values()
    )
    logger.info(
        "Ephemeral JPEG cache: %.1f MiB in RAM; "
        "no frames persisted",
        total_bytes / 1024 / 1024,
    )
    return usable, cache


def build_label_spaces(
    frame: pd.DataFrame,
    *,
    min_samples: int,
    min_matches: int,
) -> dict[str, LabelSpace]:
    spaces: dict[str, LabelSpace] = {}

    for head in HEADS:
        column = COLUMN_BY_HEAD[head]
        valid = frame.dropna(
            subset=[column]
        ).copy()
        valid[column] = valid[column].astype(str)

        sample_counts = valid[column].value_counts()
        match_counts = valid.groupby(
            column
        )["match_id"].nunique()

        eligible = sorted(
            label
            for label in sample_counts.index
            if int(sample_counts[label])
            >= min_samples
            and int(
                match_counts.get(label, 0)
            )
            >= min_matches
        )

        if len(eligible) >= 2:
            spaces[head] = LabelSpace(
                classes=tuple(eligible),
                index={
                    label: idx
                    for idx, label
                    in enumerate(eligible)
                },
            )
        else:
            logger.warning(
                "Head %s disabled: %d eligible classes",
                head,
                len(eligible),
            )

    if not spaces:
        raise RuntimeError(
            "No trainable heads. Lower support thresholds "
            "or add more independent matches."
        )
    return spaces


class MemoryFrameDataset(Dataset):
    """Decode ephemeral JPEG bytes from RAM, never from disk."""

    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        jpeg_cache: dict[str, bytes],
        spaces: dict[str, LabelSpace],
        transform,
    ) -> None:
        self.frame = frame.reset_index(drop=True)
        self.jpeg_cache = jpeg_cache
        self.spaces = spaces
        self.transform = transform

    def __len__(self) -> int:
        return len(self.frame)

    def __getitem__(self, index: int):
        row = self.frame.iloc[index]
        sample_id = str(row["sample_id"])
        data = self.jpeg_cache[sample_id]

        with Image.open(io.BytesIO(data)) as image:
            tensor = self.transform(
                image.convert("RGB")
            )

        labels = {}
        for head, space in self.spaces.items():
            raw = str(
                row[COLUMN_BY_HEAD[head]]
            )
            labels[head] = space.index.get(
                raw,
                -1,
            )

        return tensor, labels


class HierarchicalCriterionModel(nn.Module):
    def __init__(
        self,
        head_sizes: dict[str, int],
        *,
        pretrained: bool = True,
    ) -> None:
        super().__init__()

        weights = (
            models.ResNet18_Weights.DEFAULT
            if pretrained
            else None
        )
        backbone = models.resnet18(
            weights=weights
        )
        feature_dim = int(
            backbone.fc.in_features
        )
        backbone.fc = nn.Identity()

        self.backbone = backbone
        self.heads = nn.ModuleDict(
            {
                head: nn.Linear(
                    feature_dim,
                    size,
                )
                for head, size
                in head_sizes.items()
            }
        )

    def forward(self, x):
        features = self.backbone(x)
        return {
            head: layer(features)
            for head, layer
            in self.heads.items()
        }


def transforms_for_training():
    train_transform = transforms.Compose(
        [
            transforms.Resize(288),
            transforms.RandomResizedCrop(
                224,
                scale=(0.82, 1.0),
                ratio=(0.9, 1.1),
            ),
            transforms.RandomHorizontalFlip(
                p=0.5
            ),
            transforms.ColorJitter(
                brightness=0.15,
                contrast=0.15,
                saturation=0.08,
            ),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[
                    0.485,
                    0.456,
                    0.406,
                ],
                std=[
                    0.229,
                    0.224,
                    0.225,
                ],
            ),
        ]
    )
    val_transform = transforms.Compose(
        [
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[
                    0.485,
                    0.456,
                    0.406,
                ],
                std=[
                    0.229,
                    0.224,
                    0.225,
                ],
            ),
        ]
    )
    return train_transform, val_transform


def grouped_split(
    frame: pd.DataFrame,
    *,
    val_fraction: float,
    seed: int,
    allow_frame_split: bool,
):
    groups = frame["match_id"].astype(str)
    unique_matches = groups.nunique()

    if unique_matches >= 2:
        splitter = GroupShuffleSplit(
            n_splits=1,
            test_size=val_fraction,
            random_state=seed,
        )
        train_idx, val_idx = next(
            splitter.split(
                frame,
                groups=groups,
            )
        )
    else:
        if not allow_frame_split:
            raise RuntimeError(
                "Only one match in usable frames. "
                "Add more matches or pass --allow-frame-split "
                "for a smoke test only."
            )

        logger.warning(
            "LEAKY SMOKE TEST: one-match frame split enabled."
        )
        indices = np.arange(len(frame))
        rng = np.random.default_rng(seed)
        rng.shuffle(indices)
        split = max(
            1,
            int(
                len(indices)
                * (1.0 - val_fraction)
            ),
        )
        train_idx = indices[:split]
        val_idx = indices[split:]

    return (
        frame.iloc[
            train_idx
        ].reset_index(drop=True),
        frame.iloc[
            val_idx
        ].reset_index(drop=True),
    )


def trim_spaces_to_train(
    spaces: dict[str, LabelSpace],
    train_frame: pd.DataFrame,
) -> dict[str, LabelSpace]:
    trimmed: dict[str, LabelSpace] = {}

    for head, space in spaces.items():
        present = set(
            train_frame[
                COLUMN_BY_HEAD[head]
            ].astype(str)
        )
        classes = tuple(
            label
            for label in space.classes
            if label in present
        )

        if len(classes) >= 2:
            trimmed[head] = LabelSpace(
                classes=classes,
                index={
                    label: idx
                    for idx, label
                    in enumerate(classes)
                },
            )
        else:
            logger.warning(
                "Head %s disabled after grouped split: "
                "%d train classes",
                head,
                len(classes),
            )

    if not trimmed:
        raise RuntimeError(
            "Grouped split left no head with >=2 train classes."
        )
    return trimmed


def class_weights(
    frame: pd.DataFrame,
    *,
    head: str,
    space: LabelSpace,
    device: torch.device,
) -> torch.Tensor:
    labels = frame[
        COLUMN_BY_HEAD[head]
    ].astype(str)
    counts = labels.value_counts()

    weights = [
        1.0
        / np.sqrt(
            max(
                1,
                int(
                    counts.get(label, 0)
                ),
            )
        )
        for label in space.classes
    ]
    tensor = torch.tensor(
        weights,
        dtype=torch.float32,
        device=device,
    )
    return tensor / tensor.mean()


def collate_batch(batch):
    images = torch.stack(
        [item[0] for item in batch]
    )
    heads = batch[0][1].keys()
    labels = {
        head: torch.tensor(
            [
                item[1][head]
                for item in batch
            ],
            dtype=torch.long,
        )
        for head in heads
    }
    return images, labels


def evaluate(
    model,
    loader,
    *,
    device,
    spaces,
):
    model.eval()
    truth = {
        head: []
        for head in spaces
    }
    pred = {
        head: []
        for head in spaces
    }

    with torch.no_grad():
        for images, labels in loader:
            images = images.to(device)
            logits = model(images)

            for head in spaces:
                y = labels[head].numpy()
                p = (
                    logits[head]
                    .argmax(dim=1)
                    .cpu()
                    .numpy()
                )
                mask = y >= 0
                truth[head].extend(
                    y[mask].tolist()
                )
                pred[head].extend(
                    p[mask].tolist()
                )

    metrics = {}
    for head in spaces:
        if not truth[head]:
            continue

        metrics[head] = {
            "accuracy": round(
                float(
                    accuracy_score(
                        truth[head],
                        pred[head],
                    )
                ),
                4,
            ),
            "macro_f1": round(
                float(
                    f1_score(
                        truth[head],
                        pred[head],
                        average="macro",
                        zero_division=0,
                    )
                ),
                4,
            ),
            "n": len(truth[head]),
        }

    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "data/cv_decision_poc/manifest.csv"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/cv_decision_poc/model"
        ),
    )
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument(
        "--weight-decay",
        type=float,
        default=1e-4,
    )
    parser.add_argument(
        "--val-fraction",
        type=float,
        default=0.2,
    )
    parser.add_argument("--min-samples", type=int, default=8)
    parser.add_argument("--min-matches", type=int, default=2)
    parser.add_argument(
        "--freeze-backbone-epochs",
        type=int,
        default=1,
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device")
    parser.add_argument(
        "--no-pretrained",
        action="store_true",
    )
    parser.add_argument(
        "--allow-frame-split",
        action="store_true",
    )

    # Remote-frame options.
    parser.add_argument(
        "--fetch-workers",
        type=int,
        default=4,
    )
    parser.add_argument(
        "--ffmpeg-timeout",
        type=float,
        default=45.0,
    )
    parser.add_argument(
        "--frame-size",
        type=int,
        default=320,
    )
    parser.add_argument(
        "--cookies-from-browser",
        help=(
            "Optional yt-dlp browser name, e.g. firefox/chrome. "
            "Only used to resolve media URL."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(
            logging,
            args.log_level.upper(),
        ),
        format="%(levelname)s %(message)s",
    )
    seed_everything(args.seed)

    manifest = args.manifest.resolve()
    frame = pd.read_csv(manifest)

    required = {
        "sample_id",
        "source_url",
        "frame_ts",
        "match_id",
        "leaf_label",
        "family_label",
        "category_label",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            f"Manifest missing columns: {sorted(missing)}"
        )

    # Fetch once. No image file is produced.
    frame, jpeg_cache = preload_frames(
        frame,
        fetch_workers=max(
            1,
            args.fetch_workers,
        ),
        ffmpeg_timeout=max(
            5.0,
            args.ffmpeg_timeout,
        ),
        output_size=max(
            224,
            args.frame_size,
        ),
        cookies_from_browser=args.cookies_from_browser,
    )

    spaces = build_label_spaces(
        frame,
        min_samples=max(
            1,
            args.min_samples,
        ),
        min_matches=max(
            1,
            args.min_matches,
        ),
    )

    train_frame, val_frame = grouped_split(
        frame,
        val_fraction=args.val_fraction,
        seed=args.seed,
        allow_frame_split=args.allow_frame_split,
    )
    spaces = trim_spaces_to_train(
        spaces,
        train_frame,
    )

    logger.info(
        "samples=%d train=%d val=%d matches=%d heads=%s",
        len(frame),
        len(train_frame),
        len(val_frame),
        frame["match_id"].nunique(),
        {
            head: len(space.classes)
            for head, space
            in spaces.items()
        },
    )

    train_tf, val_tf = transforms_for_training()

    train_ds = MemoryFrameDataset(
        train_frame,
        jpeg_cache=jpeg_cache,
        spaces=spaces,
        transform=train_tf,
    )
    val_ds = MemoryFrameDataset(
        val_frame,
        jpeg_cache=jpeg_cache,
        spaces=spaces,
        transform=val_tf,
    )

    # num_workers=0 is deliberate: the JPEG cache lives in this process'
    # memory and must not be copied into worker processes.
    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=0,
        pin_memory=torch.cuda.is_available(),
        collate_fn=collate_batch,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=torch.cuda.is_available(),
        collate_fn=collate_batch,
    )

    device = choose_device(args.device)
    model = HierarchicalCriterionModel(
        {
            head: len(space.classes)
            for head, space
            in spaces.items()
        },
        pretrained=not args.no_pretrained,
    ).to(device)

    criteria = {
        head: nn.CrossEntropyLoss(
            weight=class_weights(
                train_frame,
                head=head,
                space=space,
                device=device,
            ),
            ignore_index=-1,
        )
        for head, space
        in spaces.items()
    }

    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=args.lr,
        weight_decay=args.weight_decay,
    )

    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )
    best_score = -1.0
    history = []

    for epoch in range(
        1,
        args.epochs + 1,
    ):
        freeze = (
            epoch
            <= args.freeze_backbone_epochs
        )
        for parameter in model.backbone.parameters():
            parameter.requires_grad = not freeze

        model.train()
        epoch_loss = 0.0
        seen_batches = 0

        for images, labels in train_loader:
            images = images.to(device)
            labels = {
                head: value.to(device)
                for head, value
                in labels.items()
            }

            optimizer.zero_grad(
                set_to_none=True
            )
            logits = model(images)

            loss = torch.zeros(
                (),
                device=device,
            )
            active_heads = 0

            for head in spaces:
                target = labels[head]
                if not torch.any(
                    target >= 0
                ):
                    continue
                loss = (
                    loss
                    + LOSS_WEIGHT[head]
                    * criteria[head](
                        logits[head],
                        target,
                    )
                )
                active_heads += 1

            if active_heads == 0:
                continue

            loss.backward()
            optimizer.step()

            epoch_loss += float(
                loss.item()
            )
            seen_batches += 1

        metrics = evaluate(
            model,
            val_loader,
            device=device,
            spaces=spaces,
        )
        mean_f1 = float(
            np.mean(
                [
                    value["macro_f1"]
                    for value
                    in metrics.values()
                ]
            )
            if metrics
            else 0.0
        )

        record = {
            "epoch": epoch,
            "train_loss": round(
                epoch_loss
                / max(
                    1,
                    seen_batches,
                ),
                5,
            ),
            "val": metrics,
            "mean_macro_f1": round(
                mean_f1,
                4,
            ),
            "backbone_frozen": freeze,
        }
        history.append(record)
        logger.info(
            "%s",
            json.dumps(
                record,
                ensure_ascii=False,
            ),
        )

        if mean_f1 > best_score:
            best_score = mean_f1
            checkpoint = {
                "architecture": "resnet18",
                "state_dict": model.state_dict(),
                "head_classes": {
                    head: list(space.classes)
                    for head, space
                    in spaces.items()
                },
                "input_size": 224,
                "normalization": {
                    "mean": [
                        0.485,
                        0.456,
                        0.406,
                    ],
                    "std": [
                        0.229,
                        0.224,
                        0.225,
                    ],
                },
                "best_mean_macro_f1": best_score,
                "seed": args.seed,
            }
            torch.save(
                checkpoint,
                args.output
                / "criterion_resnet18.pt",
            )

    report = {
        "manifest": str(manifest),
        "samples": len(frame),
        "matches": int(
            frame["match_id"].nunique()
        ),
        "train_samples": len(train_frame),
        "val_samples": len(val_frame),
        "heads": {
            head: list(space.classes)
            for head, space
            in spaces.items()
        },
        "best_mean_macro_f1": round(
            best_score,
            4,
        ),
        "frames_persisted": False,
        "frame_transport": "ffmpeg-image2pipe-jpeg",
        "history": history,
    }
    (
        args.output
        / "training_report.json"
    ).write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Best checkpoint -> %s",
        args.output
        / "criterion_resnet18.pt",
    )
    logger.info(
        "Training exiting: ephemeral frame bytes "
        "will be released from RAM."
    )


if __name__ == "__main__":
    main()
