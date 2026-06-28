"""Event-level aggregator — analyse a whole card (WNO 31, CJI, …) as one entity.

``build_event_profile(event_name, session) -> dict`` walks every ``final`` match tagged with
that ``event`` and rolls the card up: results split (finishes vs decisions), submissions used,
the headline bout (highest combined Grappling ELO), the participants/headliners, the card's
aggregate style mix + most-used techniques, and a linkable bout list. Feeds
``export.narrative.event_narrative`` (prose) and the event detail page.

A "card" qualifies for its own page at ``MIN_EVENT_BOUTS`` bouts — below that it's just a
loose match or two, not an event worth narrating.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from db.models import Athlete

MIN_EVENT_BOUTS = 3

# Technique-type buckets, in the App SpiderChart order (shared with the radar).
_CATS = ("pass", "control", "submission", "escape", "guard", "sweep", "takedown")


def event_names(session: Session) -> list[str]:
    """Distinct non-null ``event`` tags carrying at least ``MIN_EVENT_BOUTS`` final bouts."""
    from export.match_breakdown import _final_matches  # lazy: avoid analysis→export cycle

    counts: Counter[str] = Counter()
    for m in _final_matches(session):
        if m.event:
            counts[str(m.event)] += 1
    return sorted(e for e, n in counts.items() if n >= MIN_EVENT_BOUTS)


def build_event_profile(event_name: str, session: Session) -> dict[str, Any]:
    from export.match_breakdown import _final_matches, match_slug  # lazy: cycle guard

    matches = [m for m in _final_matches(session) if str(m.event or "") == event_name]

    bouts: list[dict[str, Any]] = []
    type_counts: Counter[str] = Counter()
    techniques: Counter[str] = Counter()
    submissions: Counter[str] = Counter()
    years: Counter[int] = Counter()
    finishes = decided = 0
    participants: dict[str, float] = {}  # athlete name → rank_elo (for headliners)
    headline: dict[str, Any] | None = None
    best_score = -1.0

    for m in matches:
        a = session.get(Athlete, m.athlete_a_id)
        b = session.get(Athlete, m.athlete_b_id)
        if a is None or b is None:
            continue
        if m.year:
            years[m.year] += 1
        participants.setdefault(a.name, a.rank_elo or 0.0)
        participants.setdefault(b.name, b.rank_elo or 0.0)

        wt = (m.win_type or "").upper()
        winner = a if m.winner_id == a.id else (b if m.winner_id == b.id else None)
        is_finish = bool(winner) and bool(wt) and wt != "DECISION"
        if winner is not None:
            decided += 1
            if is_finish:
                finishes += 1
            if wt == "SUBMISSION" and m.submission:
                submissions[str(m.submission)] += 1

        for e in m.sequence or []:
            t = str(e.get("type", ""))
            if t in _CATS:
                type_counts[t] += 1
            lb = str(e.get("label", "")).strip()
            if lb:
                techniques[lb] += 1

        bouts.append({
            "slug": match_slug(a, b, m.year),
            "a": a.name, "b": b.name,
            "winner": winner.name if winner else None,
            "method": _method(wt, m.submission),
            "year": m.year, "finish": is_finish,
        })
        score = (a.rank_elo or 0.0) + (b.rank_elo or 0.0)
        if winner is not None and score > best_score:
            best_score = score
            headline = {
                "slug": match_slug(a, b, m.year),
                "a": a.name, "b": b.name,
                "winner": winner.name, "method": _method(wt, m.submission),
            }

    total = sum(type_counts.values())
    style_mix = {k: round(type_counts[k] / total, 3) for k in _CATS} if total else {}
    headliners = [n for n, _ in sorted(participants.items(), key=lambda kv: kv[1], reverse=True)]

    return {
        "event": event_name,
        "year": years.most_common(1)[0][0] if years else None,
        "bout_count": len(bouts),
        "participant_count": len(participants),
        "decided": decided,
        "finishes": finishes,
        "finish_rate": round(finishes / decided, 3) if decided else 0.0,
        "submissions": submissions.most_common(5),
        "top_techniques": techniques.most_common(6),
        "style_mix": style_mix,
        "headliners": headliners[:4],
        "headline_bout": headline,
        "bouts": sorted(bouts, key=lambda x: (x["finish"], x["slug"]), reverse=True),
    }


def _method(win_type: str, submission: str | None) -> str:
    wt = (win_type or "").upper()
    if wt == "SUBMISSION" and submission:
        return f"Submission · {submission}"
    if wt == "SUBMISSION":
        return "Submission"
    if wt == "DECISION":
        return "Decision"
    return wt.title() if wt else "No decision"
