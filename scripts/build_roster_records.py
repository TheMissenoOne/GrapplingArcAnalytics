#!/usr/bin/env python
"""Build a competition record for every roster athlete, including the ones BJJ Heroes skips.

Eight of the sixteen ADCC 2026 women have no record of their own. Seven are absent from BJJ
Heroes' A-Z list entirely (1435 fighters, none of them); the eighth, Ana Carolina Vieira, has a
profile page that carries no match table at all. Their bouts are not missing from the world --
they are recorded on the pages of the athletes they FOUGHT, and in our own corpus.

Three sources, in descending authority, each row stamped with which one it came from:

  own_record       her own BJJ Heroes match table
  opponent_record  read off an opponent's table and turned around
  corpus           a bout already ingested into `matches`

Turning an opponent's row around is safe, and that was verified rather than assumed: across
2768 bouts that appear on BOTH athletes' pages, `wl` inverts correctly 99.24% of the time and
the METHOD STRING IS IDENTICAL from both sides 97.94% of the time. BJJ Heroes writes the score
as winner-by-loser regardless of whose page it is, so "Pts: 8x2" needs no mirroring -- only the
W/L does. (The residual disagreements are `L` on both pages: two athletes who met twice at one
event, which the matching key cannot separate.)

A reconstructed record is BIASED and the bias has a direction: it can only contain bouts whose
opponent was notable enough to have a BJJ Heroes page. It over-represents strong opposition and
silently omits everything else. `source` is on every row so nothing downstream has to guess.

    uv run python -m scripts.build_roster_records --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from sqlalchemy import text  # noqa: E402

from analysis.date_reconcile import parse_match_history  # noqa: E402
from analysis.names import athlete_key  # noqa: E402
from db.base import get_engine  # noqa: E402
from scripts.frame_pdf import _same_person  # noqa: E402

logger = logging.getLogger(__name__)

SCOUTING = REPO / "data" / "scouting"
MANIFEST = SCOUTING / "adcc_2026_women.json"
RECORDS = SCOUTING / "adcc_2026_women_records.json"
CACHE = REPO / "data" / "raw" / "bjjheroes"

FLIP = {"W": "L", "L": "W", "D": "D"}


def _roster() -> tuple[dict[str, str], dict[str, list[str]]]:
    """``{canonical_key: display_name}`` plus every spelling to match an opponent cell against.

    The manifest carries an `aliases` list per athlete and ignoring it costs real bouts: Ana
    Carolina Vieira appears as "Ana Vieira", Paige Ivette as "Paige Ivette Climber", Anabel
    Lopez as "Anabel Lopez Beard". A first pass that keyed only on the canonical name silently
    dropped thirteen bouts, including the only one Livia Barasine has.
    """
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    roster: dict[str, str] = {}
    spellings: dict[str, list[str]] = {}
    for d in man["divisions"]:
        for x in d["athletes"]:
            name = x if isinstance(x, str) else x["name"]
            key = athlete_key(name)
            roster[key] = name
            spellings[key] = [name, *((x.get("aliases") or []) if isinstance(x, dict) else [])]
    return roster, spellings


def _match(opponent: str, spellings: dict[str, list[str]]) -> str | None:
    """Which roster athlete is this opponent cell, if any.

    Exact key first, then `frame_pdf._same_person`, which already encodes "an initial agrees
    with the name it abbreviates" and refuses genuinely open pairs. BJJ Heroes abbreviates
    freely -- "L. Barasine" is how Livia's only recorded bout is filed."""
    key = athlete_key(opponent)
    if key in spellings:
        return key
    for k, names in spellings.items():
        if any(_same_person(opponent, n) for n in names):
            return k
    return None


def from_opponents(roster: dict[str, str],
                   spellings: dict[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    """Every cached BJJ Heroes page, read for bouts against a roster athlete."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in roster}
    for page in sorted(CACHE.glob("match__*.html")):
        owner = page.stem.removeprefix("match__").replace("_", " ")
        try:
            rows = parse_match_history(page.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:                      # a malformed cache entry is not fatal
            logger.warning("unreadable %s: %s", page.name, exc)
            continue
        for r in rows:
            key = _match(r.opponent, spellings)
            if key is None:
                continue
            out[key].append({
                "opp": owner.title(), "wl": FLIP.get(r.wl, r.wl),
                # NOT mirrored: the score is written winner-by-loser on both pages.
                "method": r.method, "comp": r.competition, "weight": r.weight,
                "stage": r.stage, "year": r.year,
                "source": "opponent_record", "via": owner.title(),
            })
    return out


def from_roster_records(recs: dict[str, Any], roster: dict[str, str],
                        spellings: dict[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    """The roster's OWN records, read for bouts against a roster athlete who has none.

    The eight athletes with their own tables have 602 rows between them, and those rows name
    each other. This source is preferred over re-parsing the HTML cache for the same athletes:
    it is the current parse, it survives a page cached under a different filename (Gabi
    Garcia's is), and it cannot go stale relative to what the report actually uses."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in roster}
    for owner, entry in recs.items():
        for r in entry.get("rows") or []:
            key = _match(r.get("opp") or "", spellings)
            if key is None or (recs.get(roster[key], {}).get("rows")):
                continue                              # she has her own table; do not graft
            out[key].append({
                "opp": owner, "wl": FLIP.get(r.get("wl"), r.get("wl")),
                "method": r.get("method"), "comp": r.get("comp"), "weight": r.get("weight"),
                "stage": r.get("stage"), "year": r.get("year"),
                "source": "opponent_record", "via": owner,
            })
    return out


def from_corpus(roster: dict[str, str],
                spellings: dict[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    """Bouts already in `matches`. Coarser method than BJJ Heroes -- a technique name when we
    have one, otherwise just how it ended."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in roster}
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            select a.name as own, o.name as opp, m.event, m.year, m.win_type, m.submission,
                   (m.winner_id = a.id) as won, m.winner_id is null as undecided
              from matches m
              join athletes a on a.id in (m.athlete_a_id, m.athlete_b_id)
              join athletes o on o.id in (m.athlete_a_id, m.athlete_b_id) and o.id <> a.id
             where m.status = 'final'
        """)).fetchall()
    for r in rows:
        key = _match(r.own, spellings)
        if key is None:
            continue
        method = r.submission or (r.win_type or "").title() or "Unknown"
        out[key].append({
            "opp": r.opp, "wl": "?" if r.undecided else ("W" if r.won else "L"),
            "method": method, "comp": r.event, "weight": "", "stage": "",
            "year": r.year, "source": "corpus",
        })
    return out


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Bout identity across sources: opponent, year, and method.

    The competition string is deliberately absent -- BJJ Heroes writes "Euro NoGi" where the
    corpus writes "IBJJF European No-Gi 2024", so keying on it would duplicate every bout that
    both sources hold. The METHOD carries the separation instead, and it can: it is identical
    from both sides of a bout (97.9% over 2768 cross-referenced bouts) and differs between two
    different bouts. Opponent+year alone is not enough -- Sarah Galvao met Yara Soares twice in
    2021, and a first version of this key merged those two bouts into one."""
    return (athlete_key(row.get("opp") or ""), row.get("year"),
            (row.get("method") or "").casefold().strip())


def build(dry_run: bool) -> int:
    roster, spellings = _roster()
    recs = json.loads(RECORDS.read_text(encoding="utf-8"))
    opp = from_opponents(roster, spellings)
    from_recs = from_roster_records(recs, roster, spellings)
    corpus = from_corpus(roster, spellings)

    report: list[tuple[str, int, int, int, int]] = []
    for key, name in roster.items():
        entry = recs.setdefault(name, {"division": None, "rows": []})
        own = entry.get("rows") or []
        for r in own:
            r.setdefault("source", "own_record")
        if own:
            report.append((name, len(own), 0, 0, len(own)))
            continue                                  # her own table wins; never mix

        seen = {_key(r) for r in own}
        merged = list(own)
        added_opp = added_corpus = 0
        # Roster records first (current parse), then the wider HTML cache, then the corpus:
        # descending method-string richness, and the dedup key keeps the first of each bout.
        for row in from_recs[key] + opp[key] + corpus[key]:
            k = _key(row)
            if k in seen:
                continue
            seen.add(k)
            merged.append(row)
            if row["source"] == "opponent_record":
                added_opp += 1
            else:
                added_corpus += 1
        merged.sort(key=lambda r: (r.get("year") or 0, r.get("comp") or ""))
        entry["rows"] = merged
        entry["record_source"] = "reconstructed" if merged else "none"
        report.append((name, 0, added_opp, added_corpus, len(merged)))

    print(f"{'atleta':24} {'própria':>8} {'advers.':>8} {'corpus':>7} {'total':>7}")
    for name, o, a, c, tot in sorted(report, key=lambda r: -r[4]):
        flag = "   <-- nenhuma fonte" if tot == 0 else ""
        print(f"{name:24} {o:8d} {a:8d} {c:7d} {tot:7d}{flag}")
    built = sum(1 for _, o, _, _, t in report if not o and t)
    print(f"\n{built} fichas reconstruídas; "
          f"{sum(1 for _, o, _, _, t in report if not o and not t)} sem fonte alguma")

    if dry_run:
        print("\n--dry-run: nada escrito")
        return 0
    RECORDS.write_text(json.dumps(recs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nescrito {RECORDS}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    return build(ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
