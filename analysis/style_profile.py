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
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.archetype import _TYPES
from analysis.names import _normalize_name
from db.models import Archetype, Athlete
from db.repository import _perspective_view, get_matches_for_athlete

# A fighter needs at least this many sequence-bearing bouts to be worth profiling.
MIN_SEQUENCE_BOUTS = 3

# …and at least this many of their OWN grappling events across those bouts, or the
# dossier is noise (a striker with a couple of scrambles isn't a grappling profile).
MIN_DOSSIER_EVENTS = 15

# The offensive buckets (share of these = ``offense_ratio``, mirrors analysis.archetype).
_OFFENSE = ("submission", "takedown", "sweep")

# Submission taxonomy — keyword → family, checked in order (strangle before joint so
# "arm triangle" reads as a strangle, leg before joint so "heel hook" reads as a leglock).
_SUB_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("strangle", ("choke", "rnc", "rear naked", "guillotine", "triangle", "darce",
                  "anaconda", "ezekiel", "north south", "bow and arrow", "strangle")),
    ("leglock", ("heel", "kneebar", "knee bar", "ankle", "toe hold", "footlock",
                 "foot lock", "calf", "estima", "leg lock", "leglock")),
    ("armlock", ("armbar", "kimura", "americana", "omoplata", "wrist", "bicep")),
)
_FAMILY_LABELS = {"strangle": "Strangles", "leglock": "Leg locks", "armlock": "Arm locks"}

# Style-fingerprint tuning: bouts-worth of events that reads as a "high pace", and how
# many top-ELO athletes count as "elite" opposition for the record-vs-elite split.
_PACE_NORM = 26.0
_ELITE_TOP_N = 10


def _sub_family(label: str) -> str | None:
    """Classify a submission label into strangle / leglock / armlock (None if unknown)."""
    norm = _normalize_name(label)
    for family, keywords in _SUB_FAMILIES:
        if any(k in norm for k in keywords):
            return family
    return None


def _slug(name: str) -> str:
    return _normalize_name(name).replace(" ", "-")


def _bout_slug(a_name: str, b_name: str, year: int | None) -> str:
    """Match the exporter's slug (STORED a/b order) so links resolve to the match JSON."""
    return f"{_slug(a_name)}-vs-{_slug(b_name)}-{year if year is not None else 'tbd'}"


def _situation(typ: str, label: str, successful: Any) -> str | None:
    """The recurring problem an opponent action poses (None = not a tracked situation)."""
    if typ == "takedown" and successful is not False:
        return "taken down"
    if typ == "pass":
        return "guard passed"
    if typ == "control" and "back" in _normalize_name(label):
        return "back taken"
    if typ == "sweep" and successful is not False:
        return "swept"
    return None


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

    # ADCC-leaderboard standings → this fighter's rank + the elite-opposition set.
    # rank_elo is the public leaderboard target (vs the grown graph ``elo``); rank only
    # within athletes that carry one, so the number means a real leaderboard position.
    ranked = list(session.execute(
        select(Athlete.id, Athlete.rank_elo, Athlete.weight_class)
        .where(Athlete.rank_elo.isnot(None))
        .order_by(Athlete.rank_elo.desc())
    ))
    elite_ids = {row[0] for row in ranked[:_ELITE_TOP_N]}
    same_class = [row for row in ranked if row[2] == athlete.weight_class]
    elo_rank = next(
        (i + 1 for i, row in enumerate(same_class) if row[0] == athlete.id), None
    )

    type_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    transitions: Counter[tuple[str, str]] = Counter()
    resp_moves: dict[str, Counter[str]] = {}
    resp_bouts: dict[str, set[str]] = {}
    finish_labels: Counter[str] = Counter()
    sub_attempt_labels: Counter[str] = Counter()
    own_events = back_events = 0
    elite_wins = elite_losses = 0
    wins = losses = draws = by_sub = by_dec = 0
    bouts: list[dict[str, Any]] = []
    notable: list[dict[str, Any]] = []

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
            if e.get("actor") == "you":
                if label:
                    own_events += 1
                    type_counts[typ] += 1
                    label_counts[label] += 1
                    if "back" in _normalize_name(label):
                        back_events += 1
                    if prev_own and _normalize_name(prev_own) != _normalize_name(label):
                        transitions[(prev_own, label)] += 1
                    prev_own = label
                    if typ == "submission":
                        sub_attempt_labels[label] += 1
                        if e.get("successful") is True:
                            finish_labels[label] += 1
                    if pending:
                        resp_moves.setdefault(pending, Counter())[label] += 1
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
        bouts.append({"slug": slug, "opponent": opp_name, "year": m.year,
                      "result": result, "win_type": m.win_type})

    total_typed = sum(type_counts.values())
    style_mix: dict[str, float] = {
        t: round(type_counts.get(t, 0) / total_typed, 3) if total_typed else 0.0
        for t in _TYPES
    }
    offense = sum(type_counts.get(t, 0) for t in _OFFENSE)
    style_mix["offense_ratio"] = round(offense / total_typed, 3) if total_typed else 0.0

    responses: dict[str, Any] = {}
    for sit, moves in resp_moves.items():
        tot = sum(moves.values())
        responses[sit] = {
            "total": tot,
            "moves": [
                {"move": mv, "count": c, "pct": round(c / tot, 3) if tot else 0.0}
                for mv, c in moves.most_common(4)
            ],
            "bouts": sorted(resp_bouts.get(sit, set())),
        }

    archetype = None
    if athlete.archetype_id is not None:
        arch = session.get(Archetype, athlete.archetype_id)
        archetype = arch.name if arch else None

    # ── derived dossier analytics ───────────────────────────────────────────
    # Submission-family split — from finishes, falling back to attempts if unfinished.
    fam_source = finish_labels if finish_labels else sub_attempt_labels
    fam_counts: Counter[str] = Counter()
    for lb, c in fam_source.items():
        fam = _sub_family(lb)
        if fam:
            fam_counts[fam] += c
    fam_total = sum(fam_counts.values())
    submission_family = {
        "dominant": _FAMILY_LABELS[fam_counts.most_common(1)[0][0]] if fam_counts else None,
        "shares": {
            _FAMILY_LABELS[f]: round(fam_counts.get(f, 0) / fam_total, 3)
            for f in ("strangle", "leglock", "armlock") if fam_counts.get(f)
        },
    }

    decided = wins + losses
    finish_rate = round(by_sub / wins, 3) if wins else 0.0
    decision_rate = round(by_dec / decided, 3) if decided else 0.0

    # Style fingerprint (radar) — six 0..1 axes derived from the move mix + labels.
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
            "finish_rate": finish_rate,
            "record": {"wins": wins, "losses": losses, "draws": draws},
        },
        "archetype": archetype,
        "style_mix": style_mix,
        "fingerprint": fingerprint,
        "signature_techniques": [
            {"label": lb, "count": c,
             "pct": round(c / total_typed, 3) if total_typed else 0.0}
            for lb, c in label_counts.most_common(8)
        ],
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
            "favorite_finishes": [
                {"label": lb, "count": c} for lb, c in finish_labels.most_common(4)
            ],
            "notable_wins": [
                {k: v for k, v in n.items() if k != "rank_elo"} for n in notable[:3]
            ],
        },
        "bouts": bouts,
        "grappling_events": own_events,  # relevance signal — see MIN_DOSSIER_EVENTS
        "career_graph_ref": f"fighters/{_slug(athlete.name)}.json",
    }
