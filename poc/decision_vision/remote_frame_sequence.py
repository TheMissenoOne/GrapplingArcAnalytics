"""Stream regularly sampled remote video frames through one FFmpeg process.

Unlike ``FrameStream.fetch_jpeg()``, which is optimized for sparse event-aligned
timestamps, this helper is for dense timelines. It resolves the remote media URL
once, launches one FFmpeg process, and yields fixed-size BGR frames from stdout.

No video or frame file is written to disk.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import numpy as np

from decision_vision.frame_stream import FrameStream, FrameStreamError


def _read_exact(stream, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def iter_bgr_frames(
    frame_stream: FrameStream,
    source_url: str,
    *,
    start_seconds: float,
    end_seconds: float,
    sample_every_seconds: float = 1.0,
    output_size: int = 320,
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield ``(video_timestamp_seconds, BGR frame)`` without media persistence."""
    start = max(0.0, float(start_seconds))
    end = max(start, float(end_seconds))
    sample_every = max(0.05, float(sample_every_seconds))
    size = max(64, int(output_size))

    if end <= start:
        return

    resolved = frame_stream.resolve(source_url)
    fps = 1.0 / sample_every
    duration = end - start

    vf = (
        f"fps={fps:.8f},"
        f"scale={size}:{size}:force_original_aspect_ratio=decrease,"
        f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:black"
    )

    command = [
        frame_stream.ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-ss",
        f"{start:.4f}",
    ]

    if resolved.headers:
        header_blob = "".join(
            f"{key}: {value}\r\n"
            for key, value in resolved.headers.items()
        )
        command.extend(["-headers", header_blob])

    command.extend(
        [
            "-i",
            resolved.media_url,
            "-t",
            f"{duration:.4f}",
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            vf,
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
    )

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None

    frame_bytes = size * size * 3
    index = 0

    try:
        while True:
            raw = _read_exact(process.stdout, frame_bytes)
            if len(raw) == 0:
                break
            if len(raw) != frame_bytes:
                raise FrameStreamError(
                    "FFmpeg ended with a partial raw frame "
                    f"({len(raw)}/{frame_bytes} bytes)"
                )

            frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                (size, size, 3)
            ).copy()
            timestamp = start + index * sample_every
            if timestamp > end + sample_every * 0.51:
                break

            yield timestamp, frame
            index += 1
    finally:
        if process.stdout:
            process.stdout.close()
        return_code = process.wait()
        stderr = (
            process.stderr.read().decode("utf-8", "replace")
            if process.stderr
            else ""
        )
        if process.stderr:
            process.stderr.close()

        if return_code != 0:
            raise FrameStreamError(
                "FFmpeg timeline stream failed:\n"
                + stderr.strip()[-2500:]
            )
