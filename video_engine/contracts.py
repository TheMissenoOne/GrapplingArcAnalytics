"""The video DSL, and the rule that keeps an agent out of the renderer.

The agent chooses WHAT STORY to tell. It never chooses how pixels get drawn. That boundary is
this module: a `VideoBrief` names beats, and each beat names a `BeatKind` the renderer already
knows how to draw. An agent that wants a new visual has to get one built, not emit arbitrary
scene code -- which is what keeps this maintainable past the first impressive one-off.

**Three claim classes, and no fourth.**

    FACT     the value is present in match_breakdown.json at `path`
    DERIVED  a deterministic formula over facts, evaluated here, never by the agent
    ANALYST  stated in the human's own editorial recording, cited by segment

There is deliberately no "model inference" class. A sentence like "she was dominating the
wrestling" is an ANALYST claim if the analyst said it and is nothing at all otherwise. The
validator refuses a brief whose objective text has no claim behind it, so the failure mode
where a plausible-sounding number gets invented cannot reach a rendered frame.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class BeatKind(StrEnum):
    """What a beat is allowed to be. Adding one means adding a renderer for it."""

    HOOK = "hook"
    MATCHUP = "matchup"
    RESULT = "result"
    STAT_COMPARE = "stat_compare"
    MOMENTUM = "momentum"
    SEQUENCE = "sequence"
    TRANSITION_GRAPH = "transition_graph"
    DECISION_SPACE = "decision_space"
    SYSTEM = "system"
    ELO_CHANGE = "elo_change"
    CONCLUSION = "conclusion"
    CTA = "cta"


class ClaimKind(StrEnum):
    FACT = "fact"
    DERIVED = "derived"
    ANALYST = "analyst"


@dataclass(frozen=True)
class Claim:
    """A sentence that will be burned into a frame, and where it came from.

    ``text`` is what the viewer reads. Everything else is what makes it checkable after the
    fact -- which matters because a rendered video is the least auditable artefact this
    project produces, and the only defence is that every claim in it was resolvable at build
    time.
    """

    text: str
    kind: ClaimKind
    path: str | None = None            # FACT: dotted path into the breakdown
    formula: str | None = None         # DERIVED: expression over fact paths
    segment: str | None = None         # ANALYST: editorial transcript segment id
    resolved: Any = None               # filled by facts.resolve(); never authored

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VisualSpec:
    """Renderer inputs for a beat. Data selection only -- never geometry, timing or styling.

    An agent may say "show events 18 through 23"; it may not say where a node lands on screen
    or how long a fade takes. Those come from the storyboard compiler and the theme, so two
    videos built a month apart still look like the same product.
    """

    events: list[int] = field(default_factory=list)
    focus_nodes: list[str] = field(default_factory=list)
    focus_side: str | None = None      # "a" | "b"
    metric: str | None = None
    reduction: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VideoBeat:
    kind: BeatKind
    duration: float
    headline: str | None = None
    body: str | None = None
    claims: list[Claim] = field(default_factory=list)
    visual: VisualSpec = field(default_factory=VisualSpec)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["claims"] = [c.to_dict() for c in self.claims]
        return d


@dataclass(frozen=True)
class VideoBrief:
    match_slug: str
    title: str
    thesis: str
    duration_target: float
    beats: list[VideoBeat]

    def to_dict(self) -> dict[str, Any]:
        return {"match_slug": self.match_slug, "title": self.title, "thesis": self.thesis,
                "duration_target": self.duration_target,
                "beats": [b.to_dict() for b in self.beats]}

    @property
    def duration(self) -> float:
        return round(sum(b.duration for b in self.beats), 3)


@dataclass(frozen=True)
class EditorialBreakdown:
    """The human's second recording, after the data exists. Intent, not observation.

    Kept apart from the match data on purpose: the first recording says what happened, this
    one says what mattered, and only the first is evidence.
    """

    thesis: str
    turning_points: list[dict[str, Any]] = field(default_factory=list)
    emphasis: list[str] = field(default_factory=list)
    deemphasize: list[str] = field(default_factory=list)
    segments: dict[str, str] = field(default_factory=dict)   # "editorial:14" -> text

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
