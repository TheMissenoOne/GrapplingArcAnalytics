"""One theme, three renderers. Without this Manim, Vizzu and MoviePy look like three products.

Every value a renderer needs is here, and the adapters translate rather than redefine. A
renderer that reaches for its own colour is a bug: the athlete mapping in particular must
never flip between scenes, because colour is how a silent viewer tracks who is who.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

THEME_VERSION = "1"


@dataclass(frozen=True)
class VideoTheme:
    # 1080x1920 at 30fps, H.264/yuv420p. Instagram overlays the bottom of the frame, so
    # nothing that must be read is placed inside safe_bottom.
    width: int = 1080
    height: int = 1920
    fps: int = 30
    safe_top: int = 240
    safe_bottom: int = 384          # ~20% — Reels controls live here

    background: str = "#0E1212"
    surface: str = "#171D1B"
    ink: str = "#E9EEEC"
    ink_muted: str = "#939E9A"
    accent: str = "#D2A34B"
    success: str = "#5FBF8F"
    failure: str = "#D8707E"

    # Fixed for the life of a video. Never derived from who is winning.
    athlete_a: str = "#7FB2D8"
    athlete_b: str = "#D98FA8"

    node_colors: dict[str, str] = field(default_factory=lambda: {
        "control": "#7FB2D8", "guard": "#8FC7A8", "pass": "#D2A34B",
        "submission": "#D8707E", "takedown": "#B79BD6", "sweep": "#7FD8CE",
        "escape": "#D89A7F", "transition": "#939E9A"})

    font_title: str = "Archivo"
    font_body: str = "Archivo"
    font_mono: str = "JetBrains Mono"
    title_px: int = 76
    body_px: int = 46
    label_px: int = 34
    line_width: int = 6

    enter_s: float = 0.45
    hold_s: float = 0.9
    exit_s: float = 0.35
    easing: str = "ease_out_quint"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "theme_version": THEME_VERSION}


THEME = VideoTheme()
