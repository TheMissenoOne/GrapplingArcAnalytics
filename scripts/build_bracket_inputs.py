#!/usr/bin/env python
"""Rebuild the corpus-sequence input that `scripts.bracket_export` reads.

This file exists because the input did not. The sequences were produced once, by hand, into a
scratch directory, and then went stale the moment an athlete merge changed identity in the DB:
nine bouts still named athlete rows that no longer existed, and nine events still carried a
dead `actor_id`. Repointing a foreign key without repointing the denormalised copy of that same
reference is a scar this project already has -- see the 2026-06-29 dedupe, where a merged
athlete replayed to an empty graph for ten hours.

A bout is included when EITHER side is on the ADCC 2026 women's roster. `div_a`/`div_b` carry
the roster division for a rostered side and null for the opponent, which is what lets the
export tell "this athlete's own events" from "her opponent's".

    uv run python -m scripts.build_bracket_inputs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from sqlalchemy import text  # noqa: E402

from analysis.names import athlete_key  # noqa: E402
from db.base import get_engine  # noqa: E402

SCOUTING = REPO / "data" / "scouting"
MANIFEST = SCOUTING / "adcc_2026_women.json"
OUT = SCOUTING / "adcc_2026_women_sequences.json"


def divisions_from_manifest(path: Path) -> dict[str, str]:
    """`athlete_key -> division name` for any manifest shaped like `adcc_2026_women.json`
    (a list of athlete strings/dicts under `divisions[*].athletes`). Generic on purpose: the
    extended-cohort manifest (`adcc_women_65_extended.json`) adds `origin`/`original_division`
    fields this function never reads, so one loader serves both without knowing either's extra
    shape."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {athlete_key(x if isinstance(x, str) else x["name"]): d["name"]
            for d in manifest["divisions"] for x in d["athletes"]}


def roster_divisions() -> dict[str, str]:
    return divisions_from_manifest(MANIFEST)


def build(divisions: dict[str, str]) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            select m.id::text, m.event, m.year, m.win_type, m.winner_id::text,
                   m.sequence,
                   a.id::text as a_id, a.name as a_name,
                   b.id::text as b_id, b.name as b_name
              from matches m
              join athletes a on a.id = m.athlete_a_id
              join athletes b on b.id = m.athlete_b_id
             where m.status = 'final'
             order by m.year, m.id
        """)).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        ka, kb = athlete_key(r.a_name), athlete_key(r.b_name)
        da, db = divisions.get(ka), divisions.get(kb)
        if not (da or db):
            continue
        out.append({
            "id": r.id, "a": r.a_name, "b": r.b_name, "a_key": ka, "b_key": kb,
            "div_a": da, "div_b": db, "event": r.event, "year": r.year,
            "winner": r.winner_id, "a_id": r.a_id, "b_id": r.b_id,
            "win_type": r.win_type, "seq": r.sequence or [],
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=Path, default=MANIFEST,
                    help="roster/cohort manifest (division -> athletes), e.g. the extended "
                         "women ±65 kg manifest instead of the ADCC 2026 bracket roster")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()

    divisions = divisions_from_manifest(a.manifest)
    bouts = build(divisions)
    a.out.write_text(json.dumps(bouts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    events = sum(len(b["seq"]) for b in bouts)
    with_seq = sum(1 for b in bouts if b["seq"])
    print(f"wrote {a.out} — {len(bouts)} bouts ({with_seq} with events, {events} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
