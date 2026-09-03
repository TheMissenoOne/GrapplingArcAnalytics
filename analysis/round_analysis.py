"""Pure derivation over ONE Gemini video-round reading: sequences, difficulty, highlights.

Video-pro plan, Fase 3 (``/tmp/.../video_pro_plan.md``). Inputs are the ``events``/``resets``
Gemini returns for a single round -- actors are ``"you"``/``"partner"``, never a name (the
round-reading prompt, ``docs/PROMPT_gemini_round_reading.md``, fixes that vocabulary). No I/O
here (no DB, no network, no filesystem) -- ``scripts/video_jobs.py`` owns every side effect and
every ``owner_id`` filter.

Privacy class: **PRIVATE** (root ``CLAUDE.md`` / this repo's ``CLAUDE.md``, "Public vs Private
Data"). This module reads one user's own round, produced for that same user's own review. The
output of every function here must never reach ``data/finetune``, a CV/vision dataset, the
public corpus, an archetype centroid, an athlete's ELO, or the ``site/`` export -- there is no
code path from here to any of those, and none should ever be added.
"""
from __future__ import annotations

import bisect
from collections.abc import Mapping, Sequence
from typing import Any

from analysis.lamas_chain import lamas_state
from analysis.markov_weights import block_for_family, load_markov_weights, weight_of
from analysis.taxonomy_kind import kind_of_entry

Event = Mapping[str, Any]

# ponytail: not measured against real round data yet -- a gap this long between two reported
# events, with no explicit reset, is treated as "the pair separated and restarted" (the fallback
# the round-reading prompt's `reset: true` marker is meant to make unnecessary). Revisit once a
# real batch of rounds gives this a number to fit against, same as the difficulty coefficients
# below.
RESET_GAP_S = 25.0

#: The three positional-exchange types that count toward "who is winning the exchanges", same
#: split `docs/video_pro_plan.md` §Fase 3 names (submission is scored separately, it dominates
#: the read on its own).
_POSITIONAL_TYPES = frozenset({"sweep", "pass", "takedown"})

_CONFIDENCE_WEIGHT = {"high": 1.0, "low": 0.4}
_DEFAULT_CONFIDENCE_WEIGHT = 0.4

#: Highlight clip window around the scored event's timestamp -- matches the plan's own
#: "[ts-3, ts+4] clamped" (lower bound only; there is no total-duration input to clamp the
#: upper bound against here, `scripts/video_jobs.py` clamps to the video's own length).
HIGHLIGHT_WINDOW_BEFORE_S = 3.0
HIGHLIGHT_WINDOW_AFTER_S = 4.0

#: Window the motion peak is read from, centred a little ahead of the event's own timestamp --
#: a technique's peak of motion usually precedes the moment it gets NAMED by a couple of
#: seconds. Matches the plan's "[ts-2, ts+3]".
MOTION_WINDOW_BEFORE_S = 2.0
MOTION_WINDOW_AFTER_S = 3.0


def _ordered(events: Sequence[Event]) -> list[Event]:
    return sorted(events, key=lambda e: float(e.get("ts", 0.0)))


# ── sequences ─────────────────────────────────────────────────────────────────────────────────
def build_sequences(events: Sequence[Event], resets: Sequence[float]) -> list[list[Event]]:
    """Group ``events`` into sequences (one ``sequenceId`` each downstream) split at ``resets``.

    ``resets`` (video-absolute seconds, the model's own reset marks) is the primary signal --
    ``n`` resets always produce ``n + 1`` groups, even an empty one, so a caller can zip
    ``sequenceId`` onto them positionally without re-deriving the count. With no resets at all,
    falls back to splitting on any inter-event gap wider than :data:`RESET_GAP_S` -- no gap, no
    split, one sequence for the whole round.
    """
    ordered = _ordered(events)
    if resets:
        boundaries = sorted(float(r) for r in resets)
        groups: list[list[Event]] = [[] for _ in range(len(boundaries) + 1)]
        for ev in ordered:
            idx = bisect.bisect_right(boundaries, float(ev.get("ts", 0.0)))
            groups[idx].append(ev)
        return groups

    if not ordered:
        return [[]]
    groups = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        gap = float(cur.get("ts", 0.0)) - float(prev.get("ts", 0.0))
        if gap > RESET_GAP_S:
            groups.append([])
        groups[-1].append(cur)
    return groups


# ── difficulty ────────────────────────────────────────────────────────────────────────────────
def _motion_records(motion: Any) -> list[Mapping[str, Any]]:
    """Accepts either a full ``motion.json`` document (``{"records": [...]}``,
    ``scripts.video_frames``'s own output shape) or a bare records list. ``None``/empty -> []."""
    if isinstance(motion, Mapping):
        records = motion.get("records")
        return list(records) if records else []
    return list(motion) if motion else []


def _round_duration(ordered_events: list[Event], motion: Any) -> float:
    records = _motion_records(motion)
    if records:
        return float(records[-1].get("t", 0.0))
    if ordered_events:
        return float(ordered_events[-1].get("ts", 0.0))
    return 0.0


def _count_successful(events: Sequence[Event], types: frozenset[str], actor: str) -> int:
    return sum(
        1
        for e in events
        if e.get("type") in types and e.get("successful") is True and str(e.get("actor")) == actor
    )


def difficulty_components(events: Sequence[Event], motion: Any) -> dict[str, float]:
    """The auditable inputs behind :func:`derive_difficulty` -- exactly what
    ``session_video_analysis.difficulty_inputs`` stores, so the formula can be re-fit later
    without re-reading the video (see the ``ponytail`` note on the coefficients below)."""
    ordered = _ordered(events)
    duration = _round_duration(ordered, motion)

    state_events = [
        e for e in ordered if kind_of_entry(str(e.get("label", "")), e.get("type")) == "state"
    ]
    you_time = 0.0
    total_time = 0.0
    for i, ev in enumerate(state_events):
        start = float(ev.get("ts", 0.0))
        end = float(state_events[i + 1].get("ts", 0.0)) if i + 1 < len(state_events) else duration
        span = max(0.0, end - start)
        total_time += span
        if str(ev.get("actor")) == "you":
            you_time += span
    control_share_you = you_time / total_time if total_time > 0 else 0.5

    return {
        "control_share_you": control_share_you,
        "sub_for": float(_count_successful(ordered, frozenset({"submission"}), "you")),
        "sub_against": float(_count_successful(ordered, frozenset({"submission"}), "partner")),
        "pos_for": float(_count_successful(ordered, _POSITIONAL_TYPES, "you")),
        "pos_against": float(_count_successful(ordered, _POSITIONAL_TYPES, "partner")),
        "duration_seconds": duration,
    }


def derive_difficulty(events: Sequence[Event], motion: Any) -> float:
    """0 (you dominated) .. 10 (partner dominated and finished you), clamped. 5 is even.

    # ponytail: the four coefficients below (5.0 / 3.0 / 1.5 / 0.5) are a first, transparent
    # guess -- NOT measured against real rounds. Knob lives here, at the top of the one function
    # that uses them; `difficulty_components` above is stored as `difficulty_inputs` precisely so
    # a re-fit against user-confirmed manual ratings never needs to re-read a video. See the
    # video-pro plan's "Decisões abertas #2".
    """
    c = difficulty_components(events, motion)
    value = (
        5.0
        + 3.0 * (1.0 - 2.0 * c["control_share_you"])
        + 1.5 * (c["sub_against"] - c["sub_for"])
        + 0.5 * (c["pos_against"] - c["pos_for"])
    )
    return max(0.0, min(10.0, value))


# ── highlights ────────────────────────────────────────────────────────────────────────────────
def _motion_value(record: Mapping[str, Any]) -> float:
    residual = record.get("diff_residual")
    return float(residual if residual is not None else record.get("diff_raw", 0.0))


def _peak_in_window(records: list[Mapping[str, Any]], lo: float, hi: float) -> float:
    values = [_motion_value(r) for r in records if lo <= float(r.get("t", 0.0)) <= hi]
    return max(values) if values else 0.0


def build_highlights(events: Sequence[Event], motion: Any, k: int = 5) -> list[dict[str, Any]]:
    """Top ``k`` events by ``score = successful + confidence + motion peak + Markov weight``
    (each term the plan's own additive rule, roughly O(1) so no single term dominates), each
    returned as a clip window ``{start, end, label, score}`` ready to hand to ffmpeg."""
    records = _motion_records(motion)
    peak_scale = max((_motion_value(r) for r in records), default=0.0)

    weights_doc = load_markov_weights()
    block = block_for_family(None, weights_doc)  # global block -- the App has no ruleset here

    scored: list[tuple[float, dict[str, Any]]] = []
    for e in events:
        ts = float(e.get("ts", 0.0))
        success_term = 1.0 if e.get("successful") is True else 0.3
        confidence_term = _CONFIDENCE_WEIGHT.get(
            str(e.get("confidence", "")).lower(), _DEFAULT_CONFIDENCE_WEIGHT
        )
        peak = _peak_in_window(records, ts - MOTION_WINDOW_BEFORE_S, ts + MOTION_WINDOW_AFTER_S)
        peak_term = (peak / peak_scale) if peak_scale > 0 else 0.0
        markov_term = weight_of(lamas_state(e), block) if block else 1.0
        score = success_term + confidence_term + peak_term + markov_term
        scored.append((
            score,
            {
                "start": round(max(0.0, ts - HIGHLIGHT_WINDOW_BEFORE_S), 2),
                "end": round(ts + HIGHLIGHT_WINDOW_AFTER_S, 2),
                "label": str(e.get("label", "")),
                "score": round(score, 3),
            },
        ))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:k]]
