"""Pure, DB-free half of the style profile: constants + event-stream reduction.

Split out of ``analysis/style_profile.py`` so importers that only need the pure
math (``analysis.pro_analytics``, ``analysis.scouting_report``) don't drag in
sqlalchemy / db.models / db.repository. ``style_profile.py`` re-exports every
name here for the DB-aware callers that already import from it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from analysis.deviance import TYPES as _TYPES
from analysis.names import _normalize_name

# Bump whenever the DERIVATION changes (this module or anything it calls: decision_flow,
# deviance, perspective_sequence...). The export caches profiles by their DB inputs, so
# without this a code fix silently keeps serving payloads computed by the old code.
PROFILE_VERSION = 3  # 3: decision_flow switched to chain conditioning (opponent's move = condition)

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


def reduce_style_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure event-stream reduction shared with the App's `buildStyleProfile` port
    (``src/services/styleProfile.ts``) — the ONLY piece under cross-module parity
    test (``tests/test_style_parity.py`` / App's ``styleProfileParity.test.ts``).

    Walks a flat sequence of ``{label, type, actor, successful}`` events, one
    athlete's perspective, ``actor`` already normalized to ``"you"`` / anything
    else = opponent. No DB/bout awareness here — a caller that needs per-bout
    scoping (pending-situation reset across bouts, cross-bout transitions) does
    that itself around this call (see ``build_style_profile``).
    """
    type_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    resp_moves: dict[str, Counter[str]] = {}
    finish_labels: Counter[str] = Counter()
    sub_attempt_labels: Counter[str] = Counter()
    pending: str | None = None

    for e in events:
        label = str(e.get("label", ""))
        typ = str(e.get("type", ""))
        if e.get("actor") == "you":
            if not label:
                continue
            type_counts[typ] += 1
            label_counts[label] += 1
            if typ == "submission":
                sub_attempt_labels[label] += 1
                if e.get("successful") is True:
                    finish_labels[label] += 1
            if pending:
                resp_moves.setdefault(pending, Counter())[label] += 1
                pending = None
        else:
            sit = _situation(typ, label, e.get("successful"))
            if sit:
                pending = sit

    total_typed = sum(type_counts.values())
    style_mix = {
        t: round(type_counts.get(t, 0) / total_typed, 3) if total_typed else 0.0
        for t in _TYPES
    }

    responses: dict[str, Any] = {}
    for sit, moves in resp_moves.items():
        tot = sum(moves.values())
        responses[sit] = {
            "total": tot,
            "moves": [
                {"move": mv, "count": c, "pct": round(c / tot, 3) if tot else 0.0}
                for mv, c in moves.most_common(4)
            ],
        }

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
        "counts": {_FAMILY_LABELS[f]: fam_counts[f] for f in fam_counts},
    }

    return {
        "type_counts": dict(type_counts),
        "label_counts": dict(label_counts),
        "sub_attempt_labels": dict(sub_attempt_labels),
        "finish_labels": dict(finish_labels),
        "signature_techniques": [
            {"label": lb, "count": c,
             "pct": round(c / total_typed, 3) if total_typed else 0.0}
            for lb, c in label_counts.most_common(8)
        ],
        "style_mix": style_mix,
        "responses": responses,
        "submissions_attempted": sum(sub_attempt_labels.values()),
        "submissions_landed": sum(finish_labels.values()),
        "submission_family": submission_family,
        "favorite_finishes": [
            {"label": lb, "count": c} for lb, c in finish_labels.most_common(4)
        ],
    }
