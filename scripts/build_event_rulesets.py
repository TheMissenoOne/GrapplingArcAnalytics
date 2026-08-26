"""One-off, read-only generator for ``data/scouting/event_rulesets.json``.

The join that did not exist. ``data/scouting/rulesets.json`` has carried ruleset FAMILIES and
versioned ADCC scoring windows since 2026-08-13, and nothing ever connected them to a
``matches`` row -- ``analysis/scouting_rulesets.py`` only ever resolved a preset the CALLER
named. This maps every distinct ``matches.event`` string to a ruleset family and, where one
applies, to a preset id in that file.

Same shape and the same discipline as ``scripts/build_rating_v2_disciplines.py`` (ADR-05):
explicit substring rules, a small set of named ``MANUAL_OVERRIDES`` for events no keyword
reaches, and both kept visible in the output so a reader can tell which entry came from which.

    uv run python -m scripts.build_event_rulesets            # --check by default
    uv run python -m scripts.build_event_rulesets --write
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "scouting" / "event_rulesets.json"

RULES_APPLIED = (
    "Precedence, first match wins. (1) non_grappling: name starts with 'UFC' or 'NCAA' "
    "-- these are MMA/wrestling cards and carry no grappling ruleset (analysis/discipline.py "
    "ADR-05); the one exception is the named override 'UFC BJJ 4', a UFC-hosted pure-grappling "
    "card. (2) adcc: contains 'adcc'. (3) cji: contains 'cji' or 'combat jiu-jitsu'. "
    "(4) ibjjf: contains 'ibjjf', or is one of the IBJJF-run championship names listed in "
    "MANUAL_OVERRIDES -- the promotion's own championships ('World No-Gi 2024', 'Pan No-Gi "
    "2025', 'European No-Gi 2024') do not carry the letters IBJJF and a keyword cannot reach "
    "them. (5) other: every remaining named event -- a residual, NOT a ruleset. "
    "(6) unknown: NULL/empty event (career dumps carry no event name, so no ruleset)."
)

_NON_GRAPPLING_PREFIXES = ("UFC", "NCAA")
_ADCC = ("adcc",)
_CJI = ("cji", "combat jiu-jitsu")
_IBJJF = ("ibjjf",)

# Named events no keyword reaches, classified by hand. IBJJF runs the Worlds / Pan / European
# / No-Gi championships under its own rule book but does not put its initials in the event
# name, so each is a per-event decision rather than a keyword that would also swallow an
# unrelated promotion's "Worlds". 'UFC BJJ 4' is the mirror case: a UFC-branded name on a
# pure-grappling card (same override that data/rating_v2/disciplines.json already carries).
MANUAL_OVERRIDES: dict[str, str] = {
    "World No-Gi 2024": "ibjjf",
    "World No-Gi 2025": "ibjjf",
    "NoGi Worlds": "ibjjf",
    "European No-Gi 2024": "ibjjf",
    "European No-Gi 2025": "ibjjf",
    "Pan No-Gi 2024": "ibjjf",
    "Pan No-Gi 2025": "ibjjf",
    "No Gi Pan Am.": "ibjjf",
    "UFC BJJ 4": "other",
}

FAMILIES = ("adcc", "ibjjf", "cji", "other", "non_grappling", "unknown")

# Which preset in data/scouting/rulesets.json a family's bouts sit under. ADCC splits by
# whether the event name says "trials"; see SNAPSHOT_NOTE for why a 2026 snapshot is applied
# to bouts from 2017-2024 at all.
_ADCC_WORLDS = "adcc-worlds-current-2026-08-13"
_ADCC_TRIALS = "adcc-trials-current-2026-08-13"

SNAPSHOT_NOTE = (
    "The only VERIFIED ADCC presets in data/scouting/rulesets.json are a 2026-08-13 snapshot, "
    "and every ADCC bout in the corpus is from 2017-2024 (measured: 2017 n=8, 2019 n=8, "
    "2022 n=63, 2023 n=26, 2024 n=76). The snapshot is applied on the assumption that ADCC's "
    "match durations and half-time points structure have been stable across that span; the "
    "assumption is NOT verified against period rule books and is the reason every ADCC row "
    "here carries ruleset_confidence='snapshot_assumed_stable' rather than 'verified'."
)


def classify(event: str | None) -> str:
    if not event:
        return "unknown"
    if event in MANUAL_OVERRIDES:
        return MANUAL_OVERRIDES[event]
    if event.startswith(_NON_GRAPPLING_PREFIXES):
        return "non_grappling"
    e = event.lower()
    if any(k in e for k in _ADCC):
        return "adcc"
    if any(k in e for k in _CJI):
        return "cji"
    if any(k in e for k in _IBJJF):
        return "ibjjf"
    return "other"


def ruleset_for(event: str, family: str) -> tuple[str | None, str]:
    """(preset id in rulesets.json, confidence). ``None`` where no preset applies."""
    if family == "adcc":
        trials = "trials" in event.lower()
        return (_ADCC_TRIALS if trials else _ADCC_WORLDS), "snapshot_assumed_stable"
    if family == "ibjjf":
        # rulesets.json carries a gi and a no-gi IBJJF v6 preset; every IBJJF-family event in
        # this corpus is a no-gi championship (measured: 44/44), so the no-gi book applies.
        return "ibjjf-v6-no-gi", "verified"
    if family == "cji":
        # CJI 2 (2025) only. CJI 1 (2024) ran submission-only with no points and no cards at
        # all, which is a DIFFERENT ruleset -- see the doc; that is why cji is excluded from
        # the points contrast rather than made a third arm.
        return ("cji-2025-women" if event.startswith("CJI 2") else None), (
            "verified" if event.startswith("CJI 2") else "no_preset_submission_only")
    return None, "no_preset"


# ── the deterministic action → points tables ────────────────────────────────────
# Keyed on the SEVEN-SYMBOL collapse of the Lamas action space (analysis/lamas_chain.STATES
# with each attempt/success pair folded), because that is the finest resolution the corpus's
# event stream actually supports. Values are the points the ruleset awards for ACHIEVING the
# position, i.e. for the SUCCESS half of the pair; an attempt scores nothing under either book.
#
# The two tables differ on exactly ONE symbol (BTK 4 vs 3) plus ADCC's guard-pull penalty.
# Everything the two rule books actually disagree about at match-deciding resolution --
# advantages, knee-on-belly, mount, the 4-point ADCC takedown-to-pass, ADCC's no-points first
# half -- is INVISIBLE in this space. That is a property of the action vocabulary, not of the
# tables, and it is the central finding of docs/research/ruleset_scoring.md.
POINTS: dict[str, dict[str, int] | None] = {
    "ibjjf": {"CDP": 0, "PGD": 0, "SWP": 2, "TKD": 2, "GPS": 3, "BTK": 4, "SUB": 0},
    "adcc": {"CDP": 0, "PGD": 0, "SWP": 2, "TKD": 2, "GPS": 3, "BTK": 3, "SUB": 0},
    # No points exist to award. CJI 1 (2024) was submission-only; CJI 2 (2025) scores whole
    # ROUNDS on judges' cards, never actions. A per-action point value would be an invention.
    "cji": None,
    "other": None,
    "non_grappling": None,
    "unknown": None,
}

# Applied to PGD, and ONLY inside ADCC's negative-points window. Held separately from POINTS
# because it is the one value in this file whose applicability depends on the match clock, and
# the corpus cannot establish that clock (see the doc's census). Default OFF everywhere.
ADCC_GUARD_PULL_PENALTY = -1

POINTS_SOURCES = {
    "ibjjf": {"source_url": "https://ibjjf.com/books-videos", "preset": "ibjjf-v6-no-gi",
              "captured_on": "2026-08-25",
              "note": "Rule book v6 positional points: takedown 2, sweep 2, knee-on-belly 2, "
                      "guard pass 3, mount 4, back control with hooks 4. Knee-on-belly and "
                      "mount have no Lamas symbol and are therefore unrepresentable here."},
    "adcc": {"source_url": "https://adcombat.com/adcc-rules-regulations/",
             "preset": "adcc-worlds-current-2026-08-13", "captured_on": "2026-08-25",
             "note": "Takedown ending guard/half-guard 2, sweep 2, passing the guard 3, "
                     "back mount with hooks 3, mount 2, knee-on-belly 2; negative -1 for "
                     "pulling guard / fleeing during the negative phase. The 4-point "
                     "takedown-that-passes and every negative are unrepresentable here: the "
                     "first needs a landing position the event carries no field for, the "
                     "second needs the match clock."},
}


def build(events: list[str | None]) -> dict[str, object]:
    named = sorted({e for e in events if e})
    mapping: dict[str, dict[str, object]] = {}
    for e in named:
        fam = classify(e)
        preset, confidence = ruleset_for(e, fam)
        mapping[e] = {"family": fam, "ruleset_id": preset, "ruleset_confidence": confidence}
    return {
        "version": 1,
        "rules_applied": RULES_APPLIED,
        "snapshot_note": SNAPSHOT_NOTE,
        "families": list(FAMILIES),
        "manual_overrides": {e: f for e, f in MANUAL_OVERRIDES.items() if e in mapping},
        "events": mapping,
        "null_event": {"family": "unknown", "ruleset_id": None,
                       "ruleset_confidence": "no_event_name"},
        "points": POINTS,
        "points_sources": POINTS_SOURCES,
        "adcc_guard_pull_penalty": ADCC_GUARD_PULL_PENALTY,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="write the file (default: --check)")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    from sqlalchemy import select

    from db.base import db_session
    from db.models import Match

    with db_session() as session:
        events = list(session.scalars(select(Match.event).distinct()))
    doc = build(events)
    text = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if args.write:
        OUT_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {OUT_PATH} ({len(doc['events'])} events)")  # type: ignore[arg-type]
        return
    current = OUT_PATH.read_text(encoding="utf-8") if OUT_PATH.exists() else ""
    print("up to date" if current == text else "DIFFERS -- rerun with --write")
    raise SystemExit(0 if current == text else 1)


if __name__ == "__main__":
    main()
