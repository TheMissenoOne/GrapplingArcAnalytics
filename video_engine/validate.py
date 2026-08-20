"""The gate between an agent's output and the renderer. Nothing unvalidated gets drawn.

Every check here exists because of a specific way a generated video can lie or break, and
each returns a message a person can act on rather than a boolean.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from video_engine.contracts import BeatKind, ClaimKind, EditorialBreakdown, VideoBrief
from video_engine.facts import UnresolvedClaimError, resolve

# Silent viewing is the design constraint, so these are not style preferences -- a headline
# that overflows or holds for 0.6s is unreadable on a muted phone, which is every viewer.
MAX_HEADLINE_CHARS = 55
MIN_HEADLINE_CHARS = 8
MIN_BEAT_SECONDS = 1.5
MAX_BEAT_SECONDS = 12.0
MAX_BODY_LINES = 2
DURATION_TOLERANCE = 0.20        # ±20% of the brief's own target

# A beat that names data must say WHICH data. Without this an agent can emit a
# `transition_graph` beat with no focus and the renderer has to invent the story.
REQUIRES_VISUAL: dict[BeatKind, tuple[str, ...]] = {
    BeatKind.SEQUENCE: ("events",),
    BeatKind.TRANSITION_GRAPH: ("focus_nodes",),
    BeatKind.STAT_COMPARE: ("metric",),
    BeatKind.DECISION_SPACE: ("reduction",),
}
# Beats that assert something about the match must carry a claim. A hook or a CTA is
# editorial framing and may stand on its own words.
FREE_TEXT_BEATS = {BeatKind.HOOK, BeatKind.CTA, BeatKind.CONCLUSION}


def validate(brief: VideoBrief, breakdown: Mapping[str, Any],
             editorial: EditorialBreakdown | None = None) -> list[str]:
    """Every problem with this brief, in reading order. Empty means renderable."""
    problems: list[str] = []
    if not brief.beats:
        return ["brief has no beats"]

    target, actual = brief.duration_target, brief.duration
    if target > 0 and abs(actual - target) > target * DURATION_TOLERANCE:
        problems.append(
            f"beats sum to {actual:.1f}s against a target of {target:.1f}s "
            f"(outside ±{DURATION_TOLERANCE:.0%})")

    n_events = len(breakdown.get("sequence") or [])
    for i, beat in enumerate(brief.beats):
        at = f"beat[{i}] {beat.kind.value}"
        if not MIN_BEAT_SECONDS <= beat.duration <= MAX_BEAT_SECONDS:
            problems.append(f"{at}: {beat.duration}s outside "
                            f"{MIN_BEAT_SECONDS}-{MAX_BEAT_SECONDS}s")
        if beat.headline is not None:
            n = len(beat.headline)
            if n > MAX_HEADLINE_CHARS:
                problems.append(f"{at}: headline is {n} chars, over {MAX_HEADLINE_CHARS} "
                                "— it will not fit or will not be readable at a glance")
            elif n < MIN_HEADLINE_CHARS:
                problems.append(f"{at}: headline is {n} chars, too short to carry a beat")
        if beat.body and beat.body.count("\n") + 1 > MAX_BODY_LINES:
            problems.append(f"{at}: body is over {MAX_BODY_LINES} lines")

        for field_name in REQUIRES_VISUAL.get(beat.kind, ()):
            if not getattr(beat.visual, field_name, None):
                problems.append(f"{at}: needs visual.{field_name} and has none")

        for idx in beat.visual.events:
            if not 0 <= idx < n_events:
                problems.append(f"{at}: cites event {idx}, outside 0..{n_events - 1}")

        objective = [c for c in beat.claims if c.kind is not ClaimKind.ANALYST]
        if beat.kind not in FREE_TEXT_BEATS and not beat.claims:
            problems.append(f"{at}: asserts something about the match with no claim behind it")
        if beat.kind is BeatKind.STAT_COMPARE and not objective:
            problems.append(f"{at}: a stat comparison backed only by analyst opinion")

        for j, claim in enumerate(beat.claims):
            try:
                resolve(claim, breakdown, editorial)
            except UnresolvedClaimError as exc:
                problems.append(f"{at} claim[{j}]: {exc}")
    return problems


def resolved_brief(brief: VideoBrief, breakdown: Mapping[str, Any],
                   editorial: EditorialBreakdown | None = None) -> VideoBrief:
    """The brief with every claim resolved. Raises if it does not validate -- the renderer is
    never handed a brief that only mostly checks out."""
    problems = validate(brief, breakdown, editorial)
    if problems:
        raise UnresolvedClaimError("brief does not validate:\n  " + "\n  ".join(problems))
    from dataclasses import replace
    return replace(brief, beats=[
        replace(b, claims=[resolve(c, breakdown, editorial) for c in b.claims])
        for b in brief.beats])


def contact_sheet_marks(brief: VideoBrief) -> Sequence[float]:
    """Timestamps a human should eyeball before a reel is called done: start, quarters, end."""
    total = brief.duration
    return [0.0, total * 0.25, total * 0.5, total * 0.75, max(0.0, total - 0.1)]
