"""Ephemeral remote-frame acquisition with yt-dlp metadata resolution + FFmpeg.

No video file or extracted frame is written to disk. For page URLs such as
YouTube, yt-dlp is used only to resolve the currently valid media URL and HTTP
headers; FFmpeg seeks to one timestamp and returns one JPEG on stdout.

The caller decides how long the returned bytes live. The trainer keeps them in
RAM for the duration of one run so multiple epochs do not re-fetch the media.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class FrameStreamError(RuntimeError):
    """Remote media resolution / FFmpeg frame extraction failed."""


@dataclass(frozen=True)
class ResolvedMedia:
    source_url: str
    media_url: str
    headers: dict[str, str]


def _is_direct_media_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(
        (
            ".mp4",
            ".webm",
            ".mkv",
            ".mov",
            ".m4v",
            ".m3u8",
            ".mpd",
        )
    )


class FrameStream:
    """Resolve remote pages once, then seek individual frames with FFmpeg."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        ytdlp_bin: str = "yt-dlp",
        format_selector: str = (
            "bestvideo[height<=720]/best[height<=720]/bestvideo/best"
        ),
        output_size: int = 320,
        timeout_seconds: float = 45.0,
        ytdlp_extra_args: list[str] | None = None,
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.ytdlp_bin = ytdlp_bin
        self.format_selector = format_selector
        self.output_size = int(output_size)
        self.timeout_seconds = float(timeout_seconds)
        self.ytdlp_extra_args = list(ytdlp_extra_args or [])

        self._resolved: dict[str, ResolvedMedia] = {}
        self._lock = threading.Lock()

        if shutil.which(self.ffmpeg_bin) is None:
            raise FrameStreamError(
                f"FFmpeg executable not found: {self.ffmpeg_bin!r}"
            )

    def resolve(self, source_url: str) -> ResolvedMedia:
        source_url = str(source_url).strip()
        if not source_url:
            raise FrameStreamError("Empty source URL")

        with self._lock:
            cached = self._resolved.get(source_url)
        if cached is not None:
            return cached

        if _is_direct_media_url(source_url):
            resolved = ResolvedMedia(
                source_url=source_url,
                media_url=source_url,
                headers={},
            )
        else:
            resolved = self._resolve_with_ytdlp(source_url)

        with self._lock:
            self._resolved[source_url] = resolved
        return resolved

    def _resolve_with_ytdlp(self, source_url: str) -> ResolvedMedia:
        if shutil.which(self.ytdlp_bin) is None:
            raise FrameStreamError(
                "yt-dlp is required to resolve non-direct page URLs. "
                "Install it in the Analytics environment or pass a direct media URL."
            )

        command = [
            self.ytdlp_bin,
            "--no-playlist",
            "--skip-download",
            "--dump-single-json",
            "--format",
            self.format_selector,
            *self.ytdlp_extra_args,
            source_url,
        ]

        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise FrameStreamError(
                f"yt-dlp timed out resolving {source_url}"
            ) from exc

        if proc.returncode != 0:
            raise FrameStreamError(
                "yt-dlp could not resolve media URL:\n"
                + proc.stderr.strip()[-2500:]
            )

        try:
            payload: dict[str, Any] = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise FrameStreamError(
                "yt-dlp returned invalid JSON while resolving media"
            ) from exc

        media_url = payload.get("url")
        if not media_url:
            # Some extractors expose the selected format under requested_formats.
            requested = payload.get("requested_formats") or []
            selected = next(
                (
                    item
                    for item in requested
                    if isinstance(item, dict) and item.get("url")
                ),
                None,
            )
            media_url = selected.get("url") if selected else None
            headers = (
                selected.get("http_headers", {})
                if selected
                else payload.get("http_headers", {})
            )
        else:
            headers = payload.get("http_headers", {})

        if not media_url:
            raise FrameStreamError(
                "yt-dlp metadata did not contain a usable media URL"
            )

        clean_headers = {
            str(key): str(value)
            for key, value in dict(headers or {}).items()
            if value is not None
        }

        return ResolvedMedia(
            source_url=source_url,
            media_url=str(media_url),
            headers=clean_headers,
        )

    def fetch_jpeg(
        self,
        source_url: str,
        timestamp_seconds: float,
    ) -> bytes:
        """Return one 320×320 letterboxed JPEG from remote media.

        FFmpeg output goes to stdout (``image2pipe``); nothing is written to disk.
        """
        resolved = self.resolve(source_url)
        timestamp = max(0.0, float(timestamp_seconds))

        filter_expr = (
            f"scale={self.output_size}:{self.output_size}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={self.output_size}:{self.output_size}:"
            "(ow-iw)/2:(oh-ih)/2:black"
        )

        command = [
            self.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{timestamp:.4f}",
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
                "-map",
                "0:v:0",
                "-an",
                "-frames:v",
                "1",
                "-vf",
                filter_expr,
                "-q:v",
                "3",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "pipe:1",
            ]
        )

        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise FrameStreamError(
                f"FFmpeg timed out at {timestamp:.3f}s for {source_url}"
            ) from exc

        if proc.returncode != 0 or not proc.stdout:
            raise FrameStreamError(
                f"FFmpeg failed at {timestamp:.3f}s for {source_url}:\n"
                + proc.stderr.decode("utf-8", "replace").strip()[-2500:]
            )

        data = bytes(proc.stdout)
        if not (data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")):
            raise FrameStreamError(
                f"FFmpeg returned non-JPEG data at {timestamp:.3f}s"
            )
        return data
