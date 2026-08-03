"""Decision-flow extractor — the second projection over match sequences.

The athlete execution graph (``build_athlete_graph``) keeps only ``actor ==
"you"`` events and edges consecutive OWN moves. This module derives the
parallel **decision-flow** representation that preserves what the opponent did
in between:

    latest stable position
        → athlete action
        → opponent condition bundle (zero or more opponent events)
        → athlete response (or None when the attempt fails)
        → resulting stable position

The extractor is pure (list of ``PerspectiveEvent`` in, list of
``DecisionPattern`` out) so it runs identically over DB matches and app
session rounds; ``aggregate_patterns`` folds repeated exchanges into one
pattern per (position, action, condition, response) with counts, a Bayesian
confidence, and capped evidence. The execution graph and ELO are untouched.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from analysis.network_metrics import _beta_ci
from analysis.opponent_conditions import OpponentCondition, classify_opponent_condition
from analysis.perspective_sequence import (
    STABLE_STATE_TYPES,
    PerspectiveEvent,
    sequence_boundaries,
)

logger = logging.getLogger(__name__)

PatternSource = Literal["observed", "expert", "hybrid"]

# Action-like types: events that can be an athlete action / response.
ACTION_TYPES = frozenset(
    {"pass", "takedown", "sweep", "submission", "escape", "transition"}
)
# Terminal outcome types: their success is only counted when explicitly
# supported (successful flag or the match's winning submission).
TERMINAL_OUTCOME_TYPES = frozenset({"submission", "sweep", "pass", "takedown"})

# Evidence entries embedded per pattern (payload cap is enforced later; keep
# extraction-side evidence slim already).
MAX_EVIDENCE = 8


@dataclass(frozen=True)
class PatternEvidence:
    match_id: str
    match_slug: str
    athlete_id: str
    action_index: int
    condition_indexes: tuple[int, ...]
    response_index: int | None
    timestamp_seconds: int | None


@dataclass
class DecisionPattern:
    source_position_key: str | None
    action_key: str
    condition_key: str | None
    response_key: str | None
    resulting_position_key: str | None

    count: int = 1
    success_count: int = 0
    failure_count: int = 0
    unknown_result_count: int = 0
    match_count: int = 1
    confidence: float = 0.0
    source: PatternSource = "observed"
    outcome_type: str | None = None  # response event type; "failure" when response is None
    evidence: list[PatternEvidence] = field(default_factory=list)


@dataclass
class _Window:
    action: PerspectiveEvent
    source_position: str | None
    conditions: list[PerspectiveEvent]
    position_after_conditions: str | None


def _is_successful(response: PerspectiveEvent, winning_submission_key: str | None) -> bool | None:
    """Tri-state outcome of a response event.

    True only when the data says so: explicit ``successful`` flag, or a
    submission event that IS the match's winning submission. Everything else
    is None (unknown) — a following event is never success.
    """
    if response.successful is not None:
        return bool(response.successful)
    if response.event_type == "submission" and winning_submission_key:
        if response.node_key == winning_submission_key:
            return True
    return None


def extract_patterns(
    events: list[PerspectiveEvent],
    *,
    match_id: str = "",
    match_slug: str = "",
    athlete_id: str = "",
    boundaries: set[int] | None = None,
    reaction_catalog: list[Any] | None = None,
    winning_submission_key: str | None = None,
) -> list[DecisionPattern]:
    """Window every perspective sequence into action→condition→response patterns.

    Semantics (deterministic):
    * stable-state events (guard/control, either actor) update the shared
      current position;
    * boundary indexes / neutral events close the window without emission;
    * the next OWN action-like event is the response of the pending window and
      the action of the next one (chaining);
    * an own stable event after a pending window closes it with the position as
      response ("return to position");
    * an own event with ``successful: False`` is a terminal failure — the
      window closes with ``response_key=None`` + ``failure_count``, and no new
      window starts from it;
    * opponent events between action and response collapse into one condition
      bundle (≤ ``MAX_BUNDLE_EVENTS`` distinct, deduped, in order).
    """
    patterns: list[DecisionPattern] = []
    position: str | None = None
    window: _Window | None = None
    bounds = boundaries or set()

    def close(response: PerspectiveEvent | None) -> None:
        nonlocal window
        if window is None:
            return
        w = window
        window = None
        cond = classify_opponent_condition(w.conditions, reaction_catalog)
        if response is None:
            patterns.append(DecisionPattern(
                source_position_key=w.source_position,
                action_key=w.action.node_key,
                condition_key=cond.key if cond else None,
                response_key=None,
                resulting_position_key=w.position_after_conditions,
                failure_count=1,
                outcome_type="failure",
                evidence=[_evidence(w, cond, None)],
            ))
            return
        outcome = _is_successful(response, winning_submission_key)
        result_pos = w.position_after_conditions
        if response.event_type in STABLE_STATE_TYPES:
            result_pos = response.node_key
        pattern = DecisionPattern(
            source_position_key=w.source_position,
            action_key=w.action.node_key,
            condition_key=cond.key if cond else None,
            response_key=response.node_key,
            resulting_position_key=result_pos,
            outcome_type=response.event_type,
        )
        if outcome is True:
            pattern.success_count = 1
        elif outcome is False:
            pattern.failure_count = 1
        else:
            pattern.unknown_result_count = 1
        pattern.evidence = [_evidence(w, cond, response)]
        patterns.append(pattern)

    def _evidence(w: _Window, cond: OpponentCondition | None,
                  response: PerspectiveEvent | None) -> PatternEvidence:
        return PatternEvidence(
            match_id=match_id,
            match_slug=match_slug,
            athlete_id=athlete_id,
            action_index=w.action.index,
            condition_indexes=tuple(e.index for e in w.conditions),
            response_index=response.index if response is not None else None,
            timestamp_seconds=w.action.timestamp_seconds,
        )

    for e in events:
        if e.index in bounds or e.actor == "neutral":
            window = None
            continue
        if e.actor == "opponent":
            if e.event_type in STABLE_STATE_TYPES:
                # opponent re-guards / re-controls: shared state changes, not a condition
                position = e.node_key
                if window is not None:
                    window.position_after_conditions = e.node_key
            elif window is not None:
                window.conditions.append(e)
            continue
        # athlete's own event
        if e.event_type in STABLE_STATE_TYPES:
            if e.successful is False:
                window = None  # failed position attempt — not a state change
                continue
            close(e)  # "return to position" closes any pending window
            position = e.node_key
            continue
        if e.successful is False:
            if window is None:
                # standalone failed attempt: the failed event is its own action
                window = _Window(
                    action=e,
                    source_position=position,
                    conditions=[],
                    position_after_conditions=position,
                )
            close(None)  # terminal failure — response never arrives
            continue
        # own action-like event: response of the pending window, action of the next
        close(e)
        window = _Window(
            action=e,
            source_position=position,
            conditions=[],
            position_after_conditions=position,
        )
    # end of sequence: a dangling window is discarded (no response seen)
    return patterns


def aggregate_patterns(patterns: list[DecisionPattern]) -> list[DecisionPattern]:
    """Fold repeated exchanges into one pattern per key, deterministically.

    Key = (source_position, action, condition, response). Counts sum, match
    count = distinct matches, evidence is capped at ``MAX_EVIDENCE`` in first
    seen order, confidence = Beta posterior mean of success over known
    outcomes (uniform prior — small samples pull toward 0.5).
    """
    buckets: dict[tuple[Any, ...], DecisionPattern] = {}
    match_sets: dict[tuple[Any, ...], set[str]] = {}
    for p in patterns:
        key = (p.source_position_key, p.action_key, p.condition_key, p.response_key)
        agg = buckets.get(key)
        if agg is None:
            agg = DecisionPattern(
                source_position_key=p.source_position_key,
                action_key=p.action_key,
                condition_key=p.condition_key,
                response_key=p.response_key,
                resulting_position_key=p.resulting_position_key,
                outcome_type=p.outcome_type,
                count=0,
                match_count=0,
            )
            buckets[key] = agg
            match_sets[key] = set()
        agg.count += p.count
        agg.success_count += p.success_count
        agg.failure_count += p.failure_count
        agg.unknown_result_count += p.unknown_result_count
        seen = match_sets[key]
        for ev in p.evidence:
            if ev.match_id not in seen:
                seen.add(ev.match_id)
            if len(agg.evidence) < MAX_EVIDENCE:
                agg.evidence.append(ev)
    for key, agg in buckets.items():
        agg.match_count = len(match_sets[key])
        known = agg.success_count + agg.failure_count
        agg.confidence = round(_beta_ci(agg.success_count, known)[0], 4) if known else 0.0
        agg.evidence = agg.evidence[:MAX_EVIDENCE]
    return sorted(buckets.values(), key=_pattern_sort_key)


def _pattern_sort_key(p: DecisionPattern) -> tuple[Any, ...]:
    return (-p.count, -p.confidence, p.action_key or "", p.condition_key or "",
            p.response_key or "")


def build_athlete_decision_patterns(
    athlete_id: str,
    matches: list[Any],
    *,
    match_slug_of: Any = None,
    reaction_catalog: list[Any] | None = None,
) -> list[DecisionPattern]:
    """DB-shaped convenience: one athlete, their final matches → aggregated patterns."""
    from analysis.perspective_sequence import perspective_events

    raw: list[DecisionPattern] = []
    for m in matches:
        seq = getattr(m, "sequence", None) or []
        if not seq:
            continue
        slug = match_slug_of(m) if match_slug_of else str(getattr(m, "id", ""))
        winning = None
        submission = getattr(m, "submission", None)
        if submission:
            from analysis.names import _normalize_name
            winning = _normalize_name(str(submission))
        raw.extend(extract_patterns(
            perspective_events(m, athlete_id),
            match_id=str(getattr(m, "id", "")),
            match_slug=slug,
            athlete_id=athlete_id,
            boundaries=sequence_boundaries(m, athlete_id),
            reaction_catalog=reaction_catalog,
            winning_submission_key=winning,
        ))
    return aggregate_patterns(raw)


def _to_json(p: DecisionPattern) -> dict[str, Any]:
    return {
        "sourcePositionKey": p.source_position_key,
        "actionKey": p.action_key,
        "conditionKey": p.condition_key,
        "responseKey": p.response_key,
        "resultingPositionKey": p.resulting_position_key,
        "count": p.count,
        "successCount": p.success_count,
        "failureCount": p.failure_count,
        "unknownResultCount": p.unknown_result_count,
        "matchCount": p.match_count,
        "confidence": p.confidence,
        "source": p.source,
        "evidence": [
            {"matchId": e.match_id, "matchSlug": e.match_slug, "athleteId": e.athlete_id,
             "actionIndex": e.action_index, "conditionIndexes": list(e.condition_indexes),
             "responseIndex": e.response_index, "timestampSeconds": e.timestamp_seconds}
            for e in p.evidence
        ],
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(
        description="Extract one athlete's decision patterns from match sequences (JSON out)")
    ap.add_argument("--athlete", required=True, help="athlete name or key (e.g. 'Gordon Ryan')")
    ap.add_argument("--out", type=Path, required=True, help="output JSON path")
    args = ap.parse_args()

    from db.base import db_session
    from db.models import Athlete, Reaction
    from db.repository import get_matches_for_athlete
    from export.match_breakdown import match_slug

    with db_session() as session:
        athletes = session.query(Athlete).all()
        from analysis.names import athlete_key
        target = next(
            (a for a in athletes if athlete_key(a.name) == athlete_key(args.athlete)), None)
        if target is None:
            logger.error("athlete not found: %s", args.athlete)
            return 1
        matches = [m for m in get_matches_for_athlete(target.id, session) if m.status == "final"]
        reactions = list(session.query(Reaction).all())
        patterns = build_athlete_decision_patterns(
            target.id, matches, match_slug_of=match_slug, reaction_catalog=reactions)
        payload = {
            "athlete": target.name,
            "athleteId": str(target.id),
            "matches": len(matches),
            "patterns": [_to_json(p) for p in patterns],
        }
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("wrote %d patterns for %s → %s", len(patterns), target.name, args.out)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
