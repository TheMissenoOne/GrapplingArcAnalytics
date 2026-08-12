"""CLI: dump raw frames from a remote match video for blind human audit.

Generalizes the old `scratch_audit_frames.py` (hardcoded switch list, 3
frames/switch) into a reusable tool: match id + time window + sample rate +
output dir. Filenames carry ONLY the video timestamp — no model prediction
(role/position/state) is allowed in the name, so a human judging the images
is not primed by what the classifier thinks it saw.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
from dotenv import load_dotenv

from decision_vision.build_role_timeline import _load_match
from decision_vision.common import find_analytics_root
from decision_vision.frame_stream import FrameStream
from decision_vision.remote_frame_sequence import iter_bgr_frames


def frame_filename(ts: float) -> str:
    """`frame_<timestamp>s.png`, zero-padded so lexical sort == chronological.

    5 integer digits (covers matches up to ~27h) + 2 decimal places.
    """
    return f"frame_{ts:08.2f}s.png"


def dump_frames(
    *,
    match_id: str,
    start: float,
    end: float,
    sample_every: float,
    output_dir: Path,
    output_size: int = 640,
    analytics_root: str | None = None,
) -> list[float]:
    """Stream+write frames for one window. Returns the actual timestamps written."""
    find_analytics_root(analytics_root)
    match = _load_match(match_id)
    url = str(match["video_url"])
    if not url:
        raise SystemExit(f"Match {match_id} has no video_url")

    output_dir.mkdir(parents=True, exist_ok=True)
    stream = FrameStream(output_size=output_size)

    written: list[float] = []
    for ts, frame in iter_bgr_frames(
        stream,
        url,
        start_seconds=start,
        end_seconds=end,
        sample_every_seconds=sample_every,
        output_size=output_size,
    ):
        if frame is None or not frame.size:
            continue
        path = output_dir / frame_filename(float(ts))
        cv2.imwrite(str(path), frame)
        written.append(float(ts))

    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump raw (unlabeled) frames from a match video for audit."
    )
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--start", type=float, required=True, help="seconds")
    parser.add_argument("--end", type=float, required=True, help="seconds")
    parser.add_argument("--sample-every", type=float, default=1.0, help="seconds")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-size", type=int, default=640)
    parser.add_argument("--analytics-root")
    args = parser.parse_args()

    load_dotenv(".env")

    written = dump_frames(
        match_id=args.match_id,
        start=args.start,
        end=args.end,
        sample_every=args.sample_every,
        output_dir=args.output_dir,
        output_size=args.output_size,
        analytics_root=args.analytics_root,
    )

    if not written:
        raise SystemExit("No frames were extracted for this window.")

    written.sort()
    print(
        f"wrote {len(written)} frames -> {args.output_dir} "
        f"[{written[0]:.2f}s .. {written[-1]:.2f}s]"
    )


if __name__ == "__main__":
    main()
