"""Beats in, timed scenes out. Timing is computed here so no renderer ever hand-tunes a wait.

The compiler is deterministic: same brief plus same theme yields the same storyboard, cue for
cue. That is what makes the render cache safe -- a scene's hash can only mean one output.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from video_engine.contracts import BeatKind, VideoBrief
from video_engine.style.tokens import THEME_VERSION, VideoTheme

RENDERER_VERSION = "1"


@dataclass(frozen=True)
class Cue:
    at: float
    action: str
    target: str | None = None


@dataclass(frozen=True)
class Scene:
    index: int
    kind: str
    start: float
    duration: float
    headline: str | None
    body: str | None
    payload: dict[str, Any]
    cues: list[Cue] = field(default_factory=list)
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "cues": [asdict(c) for c in self.cues]}


def compile_storyboard(brief: VideoBrief, theme: VideoTheme) -> list[Scene]:
    scenes: list[Scene] = []
    clock = 0.0
    for i, beat in enumerate(brief.beats):
        cues = _cues(beat.kind, beat.duration, beat.visual.events, theme)
        payload = {"visual": beat.visual.to_dict(),
                   "claims": [c.to_dict() for c in beat.claims]}
        scene = Scene(index=i, kind=beat.kind.value, start=round(clock, 3),
                      duration=beat.duration, headline=beat.headline, body=beat.body,
                      payload=payload, cues=cues)
        scenes.append(_with_key(scene, theme))
        clock += beat.duration
    return scenes


def _cues(kind: BeatKind, duration: float, events: list[int], theme: VideoTheme) -> list[Cue]:
    """Where things happen inside a scene. Derived from the beat's own length, so shortening
    a beat compresses its cues instead of truncating them."""
    cues = [Cue(0.0, "title_in"), Cue(round(duration - theme.exit_s, 3), "scene_out")]
    if kind is BeatKind.SEQUENCE and events:
        # Nodes appear across the middle 70%, leaving room for the title and the exit.
        span = duration * 0.7
        step = span / max(1, len(events))
        for n, idx in enumerate(events):
            cues.append(Cue(round(theme.enter_s + n * step, 3), "node_in", str(idx)))
            if n:
                cues.append(Cue(round(theme.enter_s + n * step - step / 2, 3),
                                "edge_draw", f"{events[n - 1]}->{idx}"))
    if kind is BeatKind.DECISION_SPACE:
        for n in range(3):
            cues.append(Cue(round(theme.enter_s + n * (duration * 0.25), 3), "reduce", str(n)))
    return sorted(cues, key=lambda c: c.at)


def _with_key(scene: Scene, theme: VideoTheme) -> Scene:
    """Content hash for the render cache: renderer + kind + inputs + theme.

    A code-assisted loop re-renders constantly, and re-encoding a 45s reel because the outro
    changed is the difference between a usable loop and an unusable one.
    """
    blob = json.dumps({"r": RENDERER_VERSION, "t": THEME_VERSION, "k": scene.kind,
                       "d": scene.duration, "h": scene.headline, "b": scene.body,
                       "p": scene.payload, "c": [asdict(c) for c in scene.cues],
                       "w": theme.width, "hh": theme.height, "f": theme.fps},
                      sort_keys=True, default=str)
    return Scene(**{**asdict(scene), "cues": scene.cues,
                    "key": hashlib.sha256(blob.encode()).hexdigest()[:16]})
