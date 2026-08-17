"""One-off, read-only generator for ``data/rating_v2/disciplines.json``.

Maps each distinct ``matches.event`` string to a discipline via explicit substring rules
(see ``docs/rating_v2/README.md`` ADR-05), plus a small set of named MANUAL_OVERRIDES for
events with no matching keyword — those are a human decision by event name, not a keyword
extension, so they don't silently catch future events the way a new keyword would. The
resolved ``events`` map in the JSON is rule-result with overrides applied on top; the JSON
also carries ``manual_overrides`` separately so it's visible which entries came from which
source. Only the NULL/empty event stays ``unknown`` — there's no name to classify or
override.

    uv run python -m scripts.build_rating_v2_disciplines
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

RULES_APPLIED = (
    "Precedence: if the event name contains a grappling-priority keyword "
    "(bjj|jiu-jitsu|jiu jitsu|grappling|no-gi|nogi|submission), it is "
    "submission_grappling regardless of ufc/mma/ncaa keywords also present "
    "(e.g. 'UFC BJJ 4' is a UFC-hosted pure-grappling card, not MMA). "
    "Else mma: contains ufc|bellator|one championship|mma. "
    "Else wrestling: contains ncaa. "
    "Else submission_grappling: contains adcc|cji|wno|polaris|ebi|ibjjf|quintet|"
    "who fight|pgf|spyder|sapateiro. "
    "Else (incl. NULL/empty event): unknown, unless overridden by name — see "
    "manual_overrides."
)

# Checked BEFORE mma/wrestling — wins the tie (ADR-05 precedence, 2026-08-17 correction).
_PRIORITY_GRAPPLING_KEYWORDS = (
    "bjj", "jiu-jitsu", "jiu jitsu", "grappling", "no-gi", "nogi", "submission",
)
_MMA_KEYWORDS = ("ufc", "bellator", "one championship", "mma")
_WRESTLING_KEYWORDS = ("ncaa",)
_GRAPPLING_KEYWORDS = (
    "adcc", "cji", "wno", "polaris", "ebi", "ibjjf",
    "quintet", "who fight", "pgf", "spyder", "sapateiro",
)

# Named events with no matching keyword, classified by hand after human review of the
# 2026-08-17 shadow replay's `unknown` list — all known submission-grappling promotions
# (Combat Jiu-Jitsu, Fight to Win, Kasai, Metamoris, Musumeci exhibitions, Submission
# Underground, etc). A per-event decision, not a new keyword, so it can't misfire on an
# unrelated future event that happens to share a substring.
MANUAL_OVERRIDES: dict[str, str] = {
    "Combat Jiu-Jitsu Worlds": "submission_grappling",
    "F2W 30": "submission_grappling",
    "F2W 34": "submission_grappling",
    "Goodfight Pro": "submission_grappling",
    "Grappling Ind.": "submission_grappling",
    "Kasai Dallas": "submission_grappling",
    "Kasai Pro": "submission_grappling",
    "Kinektic 1": "submission_grappling",
    "Metamoris": "submission_grappling",
    "Musumeci": "submission_grappling",
    "No Gi Pan Am.": "submission_grappling",
    "Onnit Inv. 2": "submission_grappling",
    "SUG 10": "submission_grappling",
    "SUG 3": "submission_grappling",
    "Studio 540 SPF": "submission_grappling",
    "Sub Stars": "submission_grappling",
    "Third Coast III": "submission_grappling",
    "World Festival": "submission_grappling",
    "PTL Sunday Open": "submission_grappling",
}

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "rating_v2" / "disciplines.json"


def classify(event: str | None) -> str:
    if not event:
        return "unknown"
    e = event.lower()
    if any(k in e for k in _PRIORITY_GRAPPLING_KEYWORDS):
        return "submission_grappling"
    if any(k in e for k in _MMA_KEYWORDS):
        return "mma"
    if any(k in e for k in _WRESTLING_KEYWORDS):
        return "wrestling"
    if any(k in e for k in _GRAPPLING_KEYWORDS):
        return "submission_grappling"
    return "unknown"


def build(events: list[str | None]) -> dict[str, object]:
    named = sorted(e for e in events if e)
    mapping = {e: MANUAL_OVERRIDES.get(e, classify(e)) for e in named}
    applied_overrides = {e: d for e, d in MANUAL_OVERRIDES.items() if e in mapping}
    return {
        "version": 2,
        "rules_applied": RULES_APPLIED,
        "manual_overrides": applied_overrides,
        "events": mapping,
        "null_event": classify(None),
    }


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    from db.base import db_session
    from db.models import Match

    with db_session() as session:
        rows = session.execute(select(Match.event).distinct()).all()
    events = [r[0] for r in rows]
    doc = build(events)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n")

    unknowns = sorted(e for e, d in doc["events"].items() if d == "unknown")
    print(f"wrote {OUT_PATH} — {len(doc['events'])} events, {len(unknowns)} unknown")
    for e in unknowns:
        print(f"  unknown: {e!r}")
    if doc["null_event"] == "unknown":
        print("  unknown: NULL event")


if __name__ == "__main__":
    main()
