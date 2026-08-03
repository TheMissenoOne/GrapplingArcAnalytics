#!/usr/bin/env python
"""Normalise the hand-authored Bruno Rocha dump into the repo's match event model.

    uv run python scripts/normalize_bruno_dump.py \
        transcripts/source/BrunoRocha/grafos_lutas.py transcripts/BrunoRocha.py
    uv run python scripts/convert_dump.py transcripts/BrunoRocha.py bruno_rocha

The source is a first-person narration of the athlete's own bouts, converted by hand,
so it carries three systematic deviations from `docs/match_event_model.md`:

1. Attempts are encoded as a distinct label ("Guillotine Attempt") typed `transition`.
   The model says an attempt is the technique itself with ``successful: False`` — the
   label is the move, the flag is the outcome. Typing it `transition` also loses the
   category (a failed guillotine is still a submission) and breaks library matching.
2. Timestamps are `"M:SS"` strings under a `timestamp` key. Every other dump in
   `scripts/dumps/` carries integer seconds under `ts`.
3. Names are short forms ("Xian", "Wellison Jeff") that would each create their own
   athlete row rather than resolving to the competitor's registered name.

Deliberately NOT done here: inventing missing years or results. Those are reported and
left for a human — a wrong year silently breaks bout de-duplication.
"""

from __future__ import annotations

import ast
import pprint
import re
import sys
from pathlib import Path
from typing import Any

# Registered names, from the athlete's official competition record. Short forms in the
# narration would otherwise each create a separate athlete row.
OPPONENT_NAMES = {
    "Xian": "Xian Maximo Machain",
    "Wellison Jeff": "Wellison Jefferson da Silva",
    "Lucas Ferreira": "Lucas Ferreira da Silva",
}
ATHLETE = "Bruno Fernandes Rocha"
ATHLETE_ALIASES = {"Bruno Rocha", "Bruno Fernandes Rocha"}

# Bouts confirmed against the official record: (event, year, weight_class, stage).
CONFIRMED = {
    "Xian Maximo Machain": ("Sao Paulo No-Gi 2026", 2026, "Feather", "Purple / Adult / Male"),
    "Wellison Jefferson da Silva": ("Sao Paulo No-Gi 2026", 2026, "Feather",
                                    "Purple / Adult / Male"),
    "Lucas Ferreira da Silva": ("Sao Paulo No-Gi 2026", 2026, "Feather",
                                "Purple / Adult / Male"),
}

# Bouts the source left undated are all 2026 (confirmed by the athlete). An explicit year
# in the source is never overwritten — only missing ones are filled.
DEFAULT_YEAR = 2026

_ATTEMPT = re.compile(r"\s*\b(attempt|attempted|attempting|feint)\b\s*", re.I)

# What the base move IS, when the dump typed the attempt as a generic transition.
# Keyed on a substring of the base label, first match wins, longest first.
_TYPE_BY_MOVE: tuple[tuple[str, str], ...] = (
    ("heel hook", "submission"), ("ankle lock", "submission"), ("toe hold", "submission"),
    ("kneebar", "submission"), ("knee bar", "submission"),
    ("guillotine", "submission"), ("triangle", "submission"), ("armbar", "submission"),
    ("kimura", "submission"), ("omoplata", "submission"), ("necktie", "submission"),
    ("choke", "submission"), ("ezekiel", "submission"),
    ("double leg", "takedown"), ("single leg", "takedown"), ("body lock takedown", "takedown"),
    ("arm drag takedown", "takedown"), ("counter takedown", "takedown"),
    ("takedown", "takedown"),
    ("pass", "pass"),
    ("sweep", "sweep"), ("reversal", "sweep"),
    ("back take", "transition"), ("arm drag", "transition"),
)


def _seconds(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    parts = [int(p) for p in value.strip().split(":") if p.isdigit()]
    if not parts:
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def _base_type(base_label: str, declared: str) -> str:
    """The move's real category. A failed guillotine is still a submission."""
    low = base_label.lower()
    for needle, kind in _TYPE_BY_MOVE:
        if needle in low:
            return kind
    return declared


def normalise_event(ev: dict[str, Any], name_map: dict[str, str]) -> dict[str, Any]:
    label = str(ev.get("label", "")).strip()
    declared = str(ev.get("type", "")).strip()
    out: dict[str, Any] = {}

    if _ATTEMPT.search(label):
        base = _ATTEMPT.sub(" ", label)
        base = re.sub(r"\s{2,}", " ", base).strip(" /-")
        if base:
            out["label"] = base
            out["type"] = _base_type(base, declared)
            # An attempt the narration did not mark as landing is an unresolved attempt,
            # not a failure — only an explicit False stays False.
            out["successful"] = bool(ev["successful"]) if "successful" in ev else False
        else:
            out["label"], out["type"] = label, declared
    else:
        out["label"], out["type"] = label, declared
        if "successful" in ev:
            out["successful"] = bool(ev["successful"])

    actor = str(ev.get("actor", "")).strip()
    out["actor"] = name_map.get(actor, ATHLETE if actor in ATHLETE_ALIASES else actor)
    ts = _seconds(ev.get("ts", ev.get("timestamp")))
    if ts is not None:
        out["ts"] = ts
    return out


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    raw = ast.literal_eval(src.read_text())

    out: list[dict[tuple[str, int | None], dict[str, Any]]] = []
    report: list[str] = []

    for (key, year), bout in raw.items():
        opponent = OPPONENT_NAMES.get(bout.get("opponent", ""), bout.get("opponent", ""))
        name_map = dict(OPPONENT_NAMES)
        b = dict(bout)
        b["opponent"] = opponent
        winner = str(b.get("winner") or "").strip()
        b["winner"] = (ATHLETE if winner in ATHLETE_ALIASES
                       else name_map.get(winner, winner)) or None

        if opponent in CONFIRMED:
            event, yr, weight, stage = CONFIRMED[opponent]
            b["event"], b["weight_class"] = event, weight
            b["stage"] = b.get("stage") or stage
            year = yr
        elif year is None:
            year = DEFAULT_YEAR

        if not b.get("winner"):
            report.append(f"RESULT UNKNOWN {opponent or key}  (imports as a draw)")
        if not b.get("event"):
            report.append(f"EVENT UNKNOWN  {opponent or key}  (no card page grouping)")

        b["events"] = [normalise_event(e, name_map) for e in b.get("events", [])]

        # Key on the full matchup. dump_import.build_matches splits a key containing
        # " vs " and takes BOTH participants from it; anything else falls through to
        # opponent-derivation, which on a first-person dump picks the narrator himself
        # as his own opponent and then discards every event as an unknown actor.
        # The source's "[vs X — division]" bracket form is not that shape.
        new_key = (f"{ATHLETE} vs {opponent}", year)
        if any(new_key in blk for blk in out):
            report.append(
                f"DUPLICATE PAIR {opponent} ({year}) — same pair, same year: dump_import "
                f"de-dupes on (participants, year), so these collapse to ONE bout. "
                f"Give them their real years to keep both.")
        # One dict literal per bout, like the other career compilations: convert_dump
        # turns each into its own RAW block, so a repeated key cannot drop a match here.
        out.append({new_key: b})

    header = (
        '"""Bruno Fernandes Rocha — own-bout dump, normalised by '
        'scripts/normalize_bruno_dump.py.\n\n'
        "Attempts carry the move's own label + type with successful=False (never a\n"
        '"... Attempt" label typed transition); timestamps are integer seconds under\n'
        '"ts"; names are the competitors\' registered full names."""\n'
        "# ruff: noqa: E501\n\n"
    )
    body = "\n\n".join(pprint.pformat(blk, width=100, sort_dicts=False) for blk in out)
    dst.write_text(header + body + "\n")

    events = sum(len(b["events"]) for blk in out for b in blk.values())
    print(f"{dst}: {len(out)} bouts, {events} events")
    if report:
        print("\nNeeds a human decision before import:")
        for line in dict.fromkeys(report):
            print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
