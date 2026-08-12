"""Inference without stored frames: fetch timestamps with FFmpeg, aggregate heads."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import io
import json
from pathlib import Path

import pandas as pd
import torch
from PIL import Image
from torch import nn
from torchvision import models, transforms

from decision_vision.frame_stream import FrameStream


class HierarchicalCriterionModel(nn.Module):
    def __init__(
        self,
        head_sizes: dict[str, int],
    ) -> None:
        super().__init__()
        backbone = models.resnet18(weights=None)
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


def choose_device(
    explicit: str | None,
) -> torch.device:
    if explicit:
        return torch.device(explicit)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def image_transform():
    return transforms.Compose(
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
    )
    selector = parser.add_mutually_exclusive_group(
        required=True
    )
    selector.add_argument(
        "--sample-id",
        nargs="+",
    )
    selector.add_argument(
        "--bundle-id",
    )
    selector.add_argument(
        "--row",
        type=int,
        nargs="+",
    )

    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--device")
    parser.add_argument(
        "--frame-size",
        type=int,
        default=320,
    )
    parser.add_argument(
        "--ffmpeg-timeout",
        type=float,
        default=45.0,
    )
    parser.add_argument(
        "--cookies-from-browser",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
    )
    args = parser.parse_args()

    manifest = pd.read_csv(
        args.manifest
    )

    if args.sample_id:
        selected = manifest[
            manifest["sample_id"]
            .astype(str)
            .isin(args.sample_id)
        ]
    elif args.bundle_id:
        selected = manifest[
            manifest["bundle_id"]
            .astype(str)
            == str(args.bundle_id)
        ]
    else:
        selected = manifest.iloc[
            args.row
        ]

    if selected.empty:
        raise SystemExit(
            "No manifest rows matched selection"
        )

    source_count = selected[
        "source_url"
    ].nunique()
    if source_count != 1:
        raise SystemExit(
            "Selected rows must belong to one video/source."
        )

    ytdlp_extra = (
        [
            "--cookies-from-browser",
            args.cookies_from_browser,
        ]
        if args.cookies_from_browser
        else []
    )
    stream = FrameStream(
        output_size=max(
            224,
            args.frame_size,
        ),
        timeout_seconds=max(
            5.0,
            args.ffmpeg_timeout,
        ),
        ytdlp_extra_args=ytdlp_extra,
    )

    device = choose_device(args.device)
    checkpoint = torch.load(
        args.model,
        map_location=device,
        weights_only=False,
    )
    classes = checkpoint["head_classes"]

    model = HierarchicalCriterionModel(
        {
            head: len(labels)
            for head, labels
            in classes.items()
        }
    ).to(device)
    model.load_state_dict(
        checkpoint["state_dict"]
    )
    model.eval()

    tf = image_transform()
    tensors = []
    sample_meta = []

    for _, row in selected.iterrows():
        jpeg = stream.fetch_jpeg(
            str(row["source_url"]),
            float(row["frame_ts"]),
        )
        with Image.open(
            io.BytesIO(jpeg)
        ) as image:
            tensors.append(
                tf(image.convert("RGB"))
            )
        sample_meta.append(
            {
                "sample_id": str(
                    row["sample_id"]
                ),
                "frame_ts": float(
                    row["frame_ts"]
                ),
            }
        )

    batch = torch.stack(
        tensors
    ).to(device)

    with torch.no_grad():
        logits = model(batch)

    output = {
        "samples": sample_meta,
        "heads": {},
        "frames_persisted": False,
    }

    for head, head_logits in logits.items():
        probs = torch.softmax(
            head_logits,
            dim=1,
        ).mean(dim=0)
        k = min(
            args.top_k,
            len(classes[head]),
        )
        values, indices = torch.topk(
            probs,
            k=k,
        )

        output["heads"][head] = [
            {
                "label": classes[head][
                    int(index)
                ],
                "probability": round(
                    float(value),
                    6,
                ),
            }
            for value, index
            in zip(
                values.cpu(),
                indices.cpu(),
            )
        ]

    text = json.dumps(
        output,
        indent=2,
        ensure_ascii=False,
    )
    print(text)

    if args.json_out:
        args.json_out.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        args.json_out.write_text(
            text + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
