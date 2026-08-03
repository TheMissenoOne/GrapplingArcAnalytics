"""Shared perspective conversion: one match viewed from one athlete's side.

The single definition of "you" / "opponent" / "neutral" that BOTH the athlete
execution graph (``build_athlete_graph``) and the decision-flow extractor
(``analysis.decision_flow``) consume. Keeps every event — both actors, in
order, with tri-state success and best-effort timestamps — so opponent
information is never dropped here; callers filter what they need.

    >>> perspective_events(match, athlete_id)  # -> list[PerspectiveEvent]

Duck-typed input: ``match`` needs ``.sequence`` (clean events:
``{label, type, actor_id, successful?, ts?}``) and optionally ``.timeline``
(the full superset with ``ts`` — used only for timestamps and window
boundaries, never for scoring). The input is never mutated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from analysis.names import _normalize_name

Actor = Literal["you", "opponent", "neutral"]

# Stable-state event types: (re)define the shared position, whoever owns them.
STABLE_STATE_TYPES = frozenset({"guard", "control"})
# Timeline event types that force an extraction boundary (never join a window).
BOUNDARY_TIMELINE_TYPES = frozenset({"reset", "referee", "strike"})
# Timestamp gap (seconds) between consecutive events that counts as a boundary.
WINDOW_GAP_SECONDS = 120

# Timeline actor values are 'a'/'b' (models.py: Match.timeline), mapped to athlete ids.
_TIMELINE_SIDES = {"a", "b"}


@dataclass(frozen=True)
class PerspectiveEvent:
    """One sequence event, remapped to ``athlete_id``'s perspective."""

    index: int  # index into the match.sequence list
    actor: Actor
    label: str
    node_key: str  # _normalize_name(label) — canonical key space
    event_type: str
    successful: bool | None  # None = key absent (unknown) — never coerced to False
    timestamp_seconds: int | None
    raw: dict[str, Any]


def _timeline_ts_by_sequence_index(match: Any, athlete_id: str) -> dict[int, int]:
    """Best-effort ``sequence_index -> ts`` map from the timeline superset.

    Timeline events carry ``ts``; sequence events may not. Each sequence event
    is matched to the first *unconsumed* timeline event with the same label and
    side ('a'/'b' → athlete ids), order-preserving and deterministic. No
    matching timeline event → no timestamp (never fabricated).
    """
    timeline = getattr(match, "timeline", None) or []
    pool: list[tuple[str, str, int]] = []  # (label, side_id, ts)
    for t in timeline:
        if not isinstance(t, dict):
            continue
        ts = t.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            continue
        actor_raw = t.get("actor")
        side_id = None
        if actor_raw in _TIMELINE_SIDES:
            side_id = match.athlete_a_id if actor_raw == "a" else match.athlete_b_id
        pool.append((str(t.get("label", "")), str(side_id), int(ts)))
    out: dict[int, int] = {}
    consumed = set()
    for i, e in enumerate(match.sequence or []):
        if not isinstance(e, dict):
            continue
        label = str(e.get("label", ""))
        aid = e.get("actor_id")
        for j, (tlabel, tside, ts) in enumerate(pool):
            if j in consumed:
                continue
            if tlabel == label and tside == str(aid):
                out[i] = ts
                consumed.add(j)
                break
    return out


def perspective_events(match: Any, athlete_id: str) -> list[PerspectiveEvent]:
    """One match from ``athlete_id``'s side — BOTH actors kept, in order.

    ``actor == 'you'`` for the athlete's own events, ``'opponent'`` for the
    other athlete's, ``'neutral'`` for unattributed events (referee, noise).
    ``successful`` is tri-state: None when the key is absent — an unknown
    outcome is never treated as a failure. ``ts`` is taken from the sequence
    event when present, else best-effort from ``match.timeline``.
    """
    seq = match.sequence or []
    ts_map = _timeline_ts_by_sequence_index(match, athlete_id)
    out: list[PerspectiveEvent] = []
    for i, e in enumerate(seq):
        if not isinstance(e, dict):
            continue
        aid = e.get("actor_id")
        if aid is not None and aid == athlete_id:
            actor: Actor = "you"
        elif aid is not None:
            actor = "opponent"
        else:
            actor = "neutral"
        ts = e.get("ts")
        ts = int(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
        out.append(PerspectiveEvent(
            index=i,
            actor=actor,
            label=str(e.get("label", "")),
            node_key=_normalize_name(str(e.get("label", ""))),
            event_type=str(e.get("type", "")),
            successful=None if "successful" not in e else e.get("successful"),
            timestamp_seconds=ts if ts is not None else ts_map.get(i),
            raw=dict(e),
        ))
    return out


def sequence_boundaries(
    match: Any,
    athlete_id: str,
    *,
    gap_seconds: int = WINDOW_GAP_SECONDS,
) -> set[int]:
    """Sequence indexes at which a new extraction window must start.

    Boundary sources (deterministic, never fabricated):
    * neutral (unattributed) events — they break the exchange;
    * the first sequence event at/after a timeline reset/referee/strike;
    * an event whose timestamp is more than ``gap_seconds`` after the
      previous event's (round restart / long pause).
    """
    events = perspective_events(match, athlete_id)
    boundaries = {e.index for e in events if e.actor == "neutral"}
    timeline = getattr(match, "timeline", None) or []
    boundary_ts = [
        int(t["ts"])
        for t in timeline
        if isinstance(t, dict)
        and isinstance(t.get("ts"), (int, float))
        and not isinstance(t.get("ts"), bool)
        and t.get("type") in BOUNDARY_TIMELINE_TYPES
    ]
    ts_indexes = {e.index: e.timestamp_seconds for e in events if e.timestamp_seconds is not None}
    for b in boundary_ts:
        for idx, ts in sorted(ts_indexes.items()):
            if ts >= b:
                boundaries.add(idx)
                break
    prev_ts: int | None = None
    for e in events:
        if e.timestamp_seconds is None:
            prev_ts = None
            continue
        if prev_ts is not None and e.timestamp_seconds - prev_ts > gap_seconds:
            boundaries.add(e.index)
        prev_ts = e.timestamp_seconds
    return boundaries
