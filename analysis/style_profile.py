"""Aggregate one fighter's bouts into a "Grapple like X" style profile.

Walks every FINAL match the athlete took part in (from THEIR side, via
``db.repository._perspective_view``) and rolls the sequences up into a style picture:
the technique-type mix, signature moves + transitions, how they answered recurring
situations (taken down / guard passed / back taken / swept), and finishing tendencies.

Deterministic and DB-only — feeds ``export/narrative.py`` (prose) + the public fighter
page. Node keys use the shared ``_normalize_name`` so they line up with the match data.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from analysis.decision_flow import aggregate_patterns, extract_chain_patterns
from analysis.names import _normalize_name
from analysis.perspective_sequence import perspective_events
from analysis.style_profile_core import (
    _ELITE_TOP_N,
    _FAMILY_LABELS,
    _OFFENSE,
    _PACE_NORM,
    _SUB_FAMILIES,
    MIN_DOSSIER_EVENTS,
    MIN_SEQUENCE_BOUTS,
    PROFILE_VERSION,
    _bout_slug,
    _situation,
    _slug,
    _sub_family,
    reduce_style_events,
)
from db.models import Archetype, Athlete
from db.repository import _perspective_view, get_matches_for_athlete

__all__ = [
    "PROFILE_VERSION", "MIN_SEQUENCE_BOUTS", "MIN_DOSSIER_EVENTS",
    "reduce_style_events", "qualifies", "build_style_profile",
    "_situation", "_sub_family", "_slug", "_bout_slug",
    "_OFFENSE", "_SUB_FAMILIES", "_FAMILY_LABELS", "_PACE_NORM", "_ELITE_TOP_N",
]


def qualifies(athlete_id: str, session: Session) -> bool:
    """True if the athlete has >= MIN_SEQUENCE_BOUTS final bouts with a sequence."""
    n = 0
    for m in get_matches_for_athlete(athlete_id, session):
        if m.status == "final" and m.sequence:
            n += 1
            if n >= MIN_SEQUENCE_BOUTS:
                return True
    return False


def build_style_profile(athlete: Athlete, session: Session) -> dict[str, Any]:
    """Roll the fighter's bouts up into the style-profile bundle the site renders."""
    matches = [
        m for m in get_matches_for_athlete(athlete.id, session)
        if m.status == "final" and m.sequence
    ]

    # Standings scoped to the fighter's own DISCIPLINE pool (mma / grappling /
    # wrestling) — grappling ranks by rating_v2 (pinned run, wave 9), MMA by UFC Elo,
    # wrestling by grown graph elo. See analysis.discipline.ranked_pools for sources.
    from analysis.discipline import athlete_disciplines, ranked_pools

    disc = athlete_disciplines(session).get(athlete.id, "grappling")
    ranked = ranked_pools(session)[disc]  # [(athlete_id, name, rating)] desc
    # Wave 9: for grappling this pool now orders by rating_v2, not rank_elo -- who counts
    # as "elite" for the elite-wins/elite-losses narrative below changes with it. Intended
    # (the migration is meant to move every consumer funneling through ranked_pools), but
    # flagged here because it's easy to read this line and assume nothing moved.
    elite_ids = {row[0] for row in ranked[:_ELITE_TOP_N]}
    # Rank is the fighter's position in the WHOLE discipline pool, the same ordering the
    # published leaderboard uses, so the site cannot contradict itself.
    #
    # It used to be their rank inside their own weight class, and that was quietly broken:
    # `Athlete.weight_class` is NULL for 883 of 1327 athletes and, where set, holds opaque
    # codes ("1".."4") rather than real divisions. So the "class" was a meaningless bucket
    # — three different athletes rendered as "#1 Grappling ELO" while the site's own board
    # ranked them #1, #3 and #8, and everyone in the NULL bucket got a rank out of 883
    # (one dossier read "#532"). The bug was invisible until the wave-10 discipline fix
    # restored ranks for 11 athletes who had been silently unranked; before that only one
    # athlete displayed the number at all, and he genuinely was #1.
    #
    # `elo_percentile` below was ALREADY overall, and the two render side by side, so the
    # weight-class rank was also disagreeing with the number next to it.
    overall = next((i + 1 for i, row in enumerate(ranked) if row[0] == athlete.id), None)
    elo_rank = overall
    elo_percentile = (max(1, round(overall / len(ranked) * 100))
                      if overall and len(ranked) >= 5 else None)

    transitions: Counter[tuple[str, str]] = Counter()
    resp_bouts: dict[str, set[str]] = {}
    own_events = back_events = 0
    elite_wins = elite_losses = 0
    wins = losses = draws = by_sub = by_dec = 0
    bouts: list[dict[str, Any]] = []
    notable: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []  # flattened → reduce_style_events

    for m in matches:
        other_id = m.athlete_b_id if m.athlete_a_id == athlete.id else m.athlete_a_id
        other = session.get(Athlete, other_id)
        opp_name = other.name if other else "Unknown"
        a_name = athlete.name if m.athlete_a_id == athlete.id else opp_name
        b_name = opp_name if m.athlete_a_id == athlete.id else athlete.name
        slug = _bout_slug(a_name, b_name, m.year)

        pv = _perspective_view(m, athlete.id)
        prev_own: str | None = None
        pending: str | None = None
        for e in pv.sequence:
            label = str(e.get("label", ""))
            typ = str(e.get("type", ""))
            is_you = e.get("actor") == "you"
            all_events.append({
                "label": label, "type": typ,
                "actor": "you" if is_you else "other",
                "successful": e.get("successful"),
            })
            if is_you:
                if label:
                    own_events += 1
                    if "back" in _normalize_name(label):
                        back_events += 1
                    if prev_own and _normalize_name(prev_own) != _normalize_name(label):
                        transitions[(prev_own, label)] += 1
                    prev_own = label
                    if pending:
                        resp_bouts.setdefault(pending, set()).add(slug)
                        pending = None
            else:
                sit = _situation(typ, label, e.get("successful"))
                if sit:
                    pending = sit

        # Outcome bookkeeping (raw match fields, not the perspective view).
        is_elite = other is not None and other.id in elite_ids
        won = m.winner_id == athlete.id
        if m.winner_id is None:
            draws += 1
            result = f"drew {opp_name}"
        elif won:
            wins += 1
            if is_elite:
                elite_wins += 1
            result = f"def. {opp_name}"
            if (m.win_type or "").upper() == "SUBMISSION":
                by_sub += 1
            else:
                by_dec += 1
            if other is not None and other.rank_elo is not None:
                notable.append({"opponent": opp_name, "year": m.year, "slug": slug,
                                "rank_elo": other.rank_elo})
        else:
            losses += 1
            if is_elite:
                elite_losses += 1
            result = f"lost to {opp_name}"
        # match_id lets the exporter swap in the page slug it actually wrote — two bouts
        # between the same pair in one year share this computed slug (see build_breakdowns).
        bouts.append({"slug": slug, "match_id": str(m.id), "opponent": opp_name,
                      "year": m.year, "result": result, "win_type": m.win_type})

    # ── shared event-stream reduction (parity-tested against the App port) ──
    # ponytail: reduce_style_events walks ALL bouts concatenated in one pass, so its
    # own `pending`-situation tracking (unlike `resp_bouts` above, which stays bout-
    # scoped) can in theory carry a trailing opponent situation across a bout
    # boundary into the next bout's first "you" move. No current test exercises
    # this (single-bout fixture in test_discipline.py); if a multi-bout athlete's
    # `responses` ever look off at a bout seam, give reduce_style_events a bout-
    # boundary marker instead of one flat list.
    reduced = reduce_style_events(all_events)
    total_typed = sum(reduced["type_counts"].values())
    style_mix: dict[str, float] = dict(reduced["style_mix"])
    offense = sum(reduced["type_counts"].get(t, 0) for t in _OFFENSE)
    style_mix["offense_ratio"] = round(offense / total_typed, 3) if total_typed else 0.0

    responses: dict[str, Any] = {
        sit: {**r, "bouts": sorted(resp_bouts.get(sit, set()))}
        for sit, r in reduced["responses"].items()
    }

    archetype = None
    if athlete.archetype_id is not None:
        arch = session.get(Archetype, athlete.archetype_id)
        archetype = arch.name if arch else None

    # ── derived dossier analytics ───────────────────────────────────────────
    submission_family = {
        "dominant": reduced["submission_family"]["dominant"],
        "shares": reduced["submission_family"]["shares"],
    }

    decided = wins + losses
    finish_rate = round(by_sub / wins, 3) if wins else 0.0
    decision_rate = round(by_dec / decided, 3) if decided else 0.0

    # Style fingerprint (radar) — six 0..1 axes derived from the move mix + labels.
    sub_attempt_labels = reduced["sub_attempt_labels"]
    leg_subs = sum(c for lb, c in sub_attempt_labels.items() if _sub_family(lb) == "leglock")
    total_subs = sum(sub_attempt_labels.values())
    avg_events = own_events / len(matches) if matches else 0.0
    fingerprint = {
        "top": style_mix.get("control", 0.0),
        "back": round(back_events / own_events, 3) if own_events else 0.0,
        "legs": round(leg_subs / total_subs, 3) if total_subs else 0.0,
        "guard": style_mix.get("guard", 0.0),
        "pace": round(min(avg_events / _PACE_NORM, 1.0), 3),
        "scramble": round(style_mix.get("transition", 0.0) + style_mix.get("sweep", 0.0), 3),
    }

    # Decision Flow patterns (for Grapple Like dossier integration).
    # Stored as plain dicts, NOT dataclasses: the export ItemCache normalises payloads
    # through json.dumps(default=str), which would turn a dataclass into its repr string.
    decision_flow_patterns: list[dict[str, Any]] = []
    if len(matches) >= MIN_SEQUENCE_BOUTS:
        all_patterns = []
        for m in matches:
            other_id = m.athlete_b_id if m.athlete_a_id == athlete.id else m.athlete_a_id
            other = session.get(Athlete, other_id)
            opp_name = other.name if other else "Unknown"
            a_name = athlete.name if m.athlete_a_id == athlete.id else opp_name
            b_name = opp_name if m.athlete_a_id == athlete.id else athlete.name
            winning = None
            submission = getattr(m, "submission", None)
            if submission:
                winning = _normalize_name(str(submission))
            all_patterns.extend(extract_chain_patterns(
                perspective_events(m, athlete.id),
                match_id=str(m.id),
                match_slug=_bout_slug(a_name, b_name, m.year),
                athlete_id=athlete.id,
                opponent_id=str(other_id or ""),
                boundaries=None,
                reaction_catalog=None,
                winning_submission_key=winning,
            ))
        decision_flow_patterns = [
            asdict(p) for p in aggregate_patterns(all_patterns) if p.source == "observed"
        ]

    notable.sort(key=lambda r: r["rank_elo"], reverse=True)
    bouts.sort(key=lambda r: (r["year"] or 0), reverse=True)

    return {
        "fighter": {
            "name": athlete.name, "slug": _slug(athlete.name),
            "nickname": athlete.nickname, "team": athlete.team,
            "weight_class": athlete.weight_class,
            "graph_elo": round(athlete.elo, 1),
            "elo_series": [round(float(x), 1) for x in (athlete.elo_series or [])],
            "elo_rank": elo_rank,
            "elo_percentile": elo_percentile,
            "finish_rate": finish_rate,
            "record": {"wins": wins, "losses": losses, "draws": draws},
        },
        "archetype": archetype,
        "style_mix": style_mix,
        "fingerprint": fingerprint,
        "signature_techniques": reduced["signature_techniques"],
        "signature_transitions": [
            {"from": fr, "to": to, "count": c}
            for (fr, to), c in transitions.most_common(6)
        ],
        "responses": responses,
        "finishing": {
            "wins": wins, "losses": losses, "draws": draws,
            "by_submission": by_sub, "by_decision": by_dec,
            "finish_rate": finish_rate, "decision_rate": decision_rate,
            "submission_family": submission_family,
            "record_vs_elite": {"wins": elite_wins, "losses": elite_losses},
            "favorite_finishes": reduced["favorite_finishes"],
            "notable_wins": [
                {k: v for k, v in n.items() if k != "rank_elo"} for n in notable[:3]
            ],
        },
        "bouts": bouts,
        "grappling_events": own_events,
        "career_graph_ref": f"fighters/{_slug(athlete.name)}.json",
        "decision_flow_patterns": decision_flow_patterns,
    }
