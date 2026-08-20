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
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from sqlalchemy import text  # noqa: E402

from analysis.date_reconcile import parse_match_history  # noqa: E402
from analysis.names import (  # noqa: E402
    _deaccent,
    _resolve_aliases,
    athlete_key,
)
from db.base import get_engine  # noqa: E402
from scripts.frame_pdf import _same_person  # noqa: E402

logger = logging.getLogger(__name__)

SCOUTING = REPO / "data" / "scouting"
MANIFEST = SCOUTING / "adcc_2026_women.json"
RECORDS = SCOUTING / "adcc_2026_women_records.json"
MANUAL = SCOUTING / "adcc_2026_women_manual.json"
CACHE = REPO / "data" / "raw" / "bjjheroes"

FLIP = {"W": "L", "L": "W", "D": "D"}
# Filled by `_roster`. The manifest is the authority on which bracket an athlete is in;
# reading it off the previous records file lets a stale division outlive a re-seed.
DIVISION: dict[str, str] = {}


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
            DIVISION[key] = d["name"]
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


def from_manual(roster: dict[str, str],
                spellings: dict[str, list[str]]) -> dict[str, list[dict[str, Any]]]:
    """Bouts confirmed by hand that no source carries, written from both corners.

    These exist because a record can be missing a whole CLASS of results, not just a row.
    Helena Crevar's BJJ Heroes page holds 48 bouts of which 45 are no-gi and none are IBJJF gi,
    so her March 2025 Pan quarter-final against Sarah Galvao is absent from every automated
    path -- and so is the rest of her gi career. Folded in on every run so a rebuild cannot
    wipe them, and stamped `manual` so nothing mistakes them for a scrape."""
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in roster}
    if not MANUAL.exists():
        return out
    for bout in json.loads(MANUAL.read_text(encoding="utf-8")).get("bouts", []):
        for side, other in ((bout["a"], bout["b"]), (bout["b"], bout["a"])):
            key = _match(side, spellings)
            if key is None:
                logger.warning("manual bout names %r, who is not on the roster", side)
                continue
            out[key].append({
                "opp": other,
                "wl": "W" if bout.get("winner") == side else "L",
                "method": bout.get("method"), "comp": bout.get("comp"),
                "weight": bout.get("weight", ""), "stage": bout.get("stage", ""),
                "year": bout.get("year"), "source": "manual", "note": bout.get("note"),
            })
    return out


# Method strings that name the same ending in two vocabularies. The corpus writes what the
# transcript said and BJJ Heroes writes its own shorthand, so the SAME bout arrives as "RNC"
# from one source and "Rear Naked Choke" from the other -- and the dedup key, which uses the
# method to tell a rematch from a duplicate, then keeps both. `NAME_ALIASES` already collapses
# the submission half of this vocabulary for the ADCC pipeline; only the outcome words below
# are new here.
_OUTCOME_ALIASES = {
    "referee decision": "decision", "judges decision": "decision", "judge decision": "decision",
    "ref decision": "decision", "points": "pts", "submission": "sub", "sub": "sub",
}


def _method_key(method: str | None) -> str:
    """One spelling per ending, so a rematch stays two rows and a duplicate becomes one."""
    m = _deaccent(method or "").casefold().strip().rstrip(".")
    m = re.sub(r"[^a-z0-9x ]", " ", m)
    m = re.sub(r"\s+", " ", m).strip()
    return _OUTCOME_ALIASES.get(m) or _resolve_aliases(m) or m


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Bout identity across sources: opponent, year, and how it ended.

    The competition string is deliberately absent -- BJJ Heroes writes "Euro NoGi" where the
    corpus writes "IBJJF European No-Gi 2024", so keying on it would duplicate every bout that
    both sources hold. The METHOD carries the separation instead, and it can: it is identical
    from both sides of a bout (97.9% over 2768 cross-referenced bouts) and differs between two
    different bouts. Opponent+year alone is not enough -- Sarah Galvao met Yara Soares twice in
    2021, and a first version of this key merged those two bouts into one.

    What it does need is ONE spelling per ending. Compared raw, "RNC" and "Rear Naked Choke"
    are two bouts, and Ane Svendsen's record carried the same 2025 loss to Helena Crevar twice
    for exactly that reason. `_method_key` folds the vocabularies together without touching the
    scores, which are what separate a genuine rematch ("Pts: 5x0" against "Pts: 3x0")."""
    return (athlete_key(row.get("opp") or ""), row.get("year"), _method_key(row.get("method")))


def _load(path: Path, roster: dict[str, str]) -> dict[str, dict[str, Any]]:
    """The records file, re-keyed onto ONE identity per athlete.

    The file is written under display names because a human reads it, and a display name is
    not an identity: "Sarah Galvão" and "Sarah Galvao" are the same person and two dict keys.
    Everything here is keyed by `athlete_key` instead -- the same normalisation the rest of the
    codebase uses -- so a re-spelling in the manifest merges into the athlete who is already
    there rather than opening a second record beside her.

    Reads the current shape and the flat legacy one (``{display: entry}``) with no ceremony,
    because the legacy file is what is on disk until this script runs once.
    """
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    flat = raw.get("athletes") if isinstance(raw.get("athletes"), dict) else raw
    out: dict[str, dict[str, Any]] = {}
    for name, entry in flat.items():
        if not isinstance(entry, dict):
            continue
        key = entry.get("key") or athlete_key(name)
        prev = out.get(key)
        if prev is None:
            out[key] = {**entry, "key": key, "display": roster.get(key, name)}
            continue
        # Two spellings of one athlete already in the file. Merge rather than let the later one
        # win: the earlier may hold her own table and the later only a reconstruction.
        seen = {_key(r) for r in (prev.get("rows") or [])}
        prev["rows"] = (prev.get("rows") or []) + [
            r for r in (entry.get("rows") or []) if _key(r) not in seen]
        prev["division"] = prev.get("division") or entry.get("division")
        logger.warning("merged duplicate record spelling %r into %r", name, prev["display"])
    return out


def _provenance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What this record is made of. One identity, the mix visible on it.

    There is no "own record" entity and no "reconstructed record" entity -- there is one
    athlete, and some of her bouts came from her own table while others were read off the
    pages of the athletes she fought. A later-arriving own table adds rows to the same record;
    it does not replace it with a different kind of thing.
    """
    by = Counter(r.get("source", "own_record") for r in rows)
    return {"total": len(rows), "by_source": dict(by),
            "has_own_table": bool(by.get("own_record")),
            # Kept because it is the honest headline: a record built only from opponents' pages
            # can only contain opponents notable enough to have one, so it over-represents
            # strong opposition and omits the rest in silence.
            "reconstructed_share": round(1 - by.get("own_record", 0) / len(rows), 3)
            if rows else None}


def _opponent_index(records: dict[str, dict[str, Any]],
                    roster: dict[str, str]) -> dict[str, dict[str, Any]]:
    """One canonical identity for every athlete who appears on the OTHER side of a roster bout.

    Scope, decided and stated: this is the roster plus its opponents, **not** the whole corpus.
    The corpus holds ~1315 athletes; reconstructing a career record for each would ship about a
    megabyte of derived personal history for hundreds of people who are not in this bracket and
    whom no reader of this report can click on. The boundary is the report's own subject.

    Within that boundary the identity rules are the same as the roster's: `athlete_key`
    collapses accents, case and the alias table, so "Helena Cravar" cannot open a row beside
    "Helena Crevar". What is NOT built here is a career record -- only what this report already
    knows about her: how many times she met the roster, which spellings she was recorded under,
    and whether a source for more exists.
    """
    out: dict[str, dict[str, Any]] = {}
    for entry in records.values():
        name = entry.get("display") or entry.get("key") or "?"
        for r in entry.get("rows") or []:
            raw = (r.get("opp") or "").strip()
            if not raw:
                continue
            key = athlete_key(raw)
            if key in roster:
                continue                                  # she is on the roster; not an opponent
            o = out.setdefault(key, {"key": key, "display": raw, "spellings": [],
                                     "bouts_vs_roster": 0, "vs": [], "sources": []})
            if raw not in o["spellings"]:
                o["spellings"].append(raw)
            o["bouts_vs_roster"] += 1
            if name not in o["vs"]:
                o["vs"].append(name)
            src = r.get("source", "own_record")
            if src not in o["sources"]:
                o["sources"].append(src)
    # BJJ Heroes abbreviates the opponent cell -- "L. Bernales" for Leilani Bernales -- and
    # `athlete_key` cannot see through that, so the same person opens two identities. The same
    # rule the roster matcher already trusts closes them: last token equal, first token equal or
    # an initial of it. It refuses "Jon Hansen" against "John Hansen", which is a real open
    # question in this corpus and must not be settled by a string.
    #
    # ponytail: O(n^2) over ~314 opponents, which is 49k comparisons and runs in milliseconds.
    # If the scope ever widens to the whole corpus, bucket by last token first.
    merged: dict[str, dict[str, Any]] = {}
    for key in sorted(out, key=lambda k: -out[k]["bouts_vs_roster"]):
        o = out[key]
        into = next((m for m in merged.values()
                     if any(_same_person(s1, s2) for s1 in o["spellings"]
                            for s2 in m["spellings"])), None)
        if into is None:
            merged[key] = o
            continue
        into["spellings"] += [s for s in o["spellings"] if s not in into["spellings"]]
        into["bouts_vs_roster"] += o["bouts_vs_roster"]
        into["vs"] += [v for v in o["vs"] if v not in into["vs"]]
        into["sources"] += [s for s in o["sources"] if s not in into["sources"]]

    for o in merged.values():
        # Longest spelling as the display form: an initial is the abbreviation, never the name.
        o["display"] = max(o["spellings"], key=len)
        o["has_own_page"] = (CACHE / f"match__{o['key'].replace(' ', '_')}.html").exists()
    return merged


def build(dry_run: bool) -> int:
    roster, spellings = _roster()
    recs = _load(RECORDS, roster)
    legacy = {roster[k]: v for k, v in recs.items() if k in roster}
    manual = from_manual(roster, spellings)
    opp = from_opponents(roster, spellings)
    from_recs = from_roster_records(legacy, roster, spellings)
    corpus = from_corpus(roster, spellings)

    report: list[tuple[str, int, int, int, int, int]] = []
    for key, name in roster.items():
        entry = recs.setdefault(key, {"key": key, "division": None, "rows": []})
        entry["display"] = name
        entry["key"] = key
        entry["division"] = DIVISION.get(key) or entry.get("division")
        entry["aliases"] = [s for s in spellings[key] if s != name]
        # Only rows that came from HER OWN table survive into the next run's base. Reading back
        # every row would make a second run treat the previous run's reconstruction as
        # authoritative and stop rebuilding -- the builder has to be re-runnable, because the
        # sources move.
        rows = entry.get("rows") or []
        for r in rows:
            r.setdefault("source", "own_record")
        own = [r for r in rows if r.get("source") == "own_record"]

        # ONE identity, whether or not she has a table of her own. This used to short-circuit:
        # an athlete with an own record kept it and nothing was grafted, which made "own" and
        # "reconstructed" two different kinds of record rather than one record with a visible
        # mix. Her own table is still authoritative -- it is merged FIRST and the dedup key
        # keeps the first of each bout -- but a bout it does not carry is still a bout.
        seen = {_key(r) for r in own}
        merged = list(own)
        added_opp = added_corpus = 0
        for row in manual[key] + from_recs[key] + opp[key] + corpus[key]:
            k = _key(row)
            if k in seen:
                continue
            seen.add(k)
            merged.append(row)
            if row["source"] == "opponent_record":
                added_opp += 1
            elif row["source"] != "manual":
                added_corpus += 1
        merged.sort(key=lambda r: (r.get("year") or 0, r.get("comp") or ""))
        entry["rows"] = merged
        entry["provenance"] = _provenance(merged)
        entry.pop("record_source", None)        # the binary this replaces
        report.append((name, len(own), added_opp, added_corpus, len(merged),
                       sum(1 for r in merged if r.get("source") == "manual")))

    opponents = _opponent_index(recs, roster)

    print(f"{'atleta':24} {'própria':>8} {'advers.':>8} {'corpus':>7} {'manual':>7} {'total':>7}")
    for name, o, a, c, tot, man in sorted(report, key=lambda r: -r[4]):
        flag = "   <-- nenhuma fonte" if tot == 0 else ""
        print(f"{name:24} {o:8d} {a:8d} {c:7d} {man:7d} {tot:7d}{flag}")
    mixed = sum(1 for _, o, a, c, t, _m in report if o and (a or c))
    print(f"\n{sum(1 for _, o, _, _, t, _m in report if not o and t)} ficha(s) sem tabela "
          f"própria; {mixed} com tabela própria E linhas recuperadas; "
          f"{sum(1 for _, o, _, _, t, _m in report if not t)} sem fonte alguma; "
          f"{sum(m for *_, m in report)} linha(s) manual(is)")
    print(f"{len(opponents)} adversárias com identidade canônica "
          f"({sum(1 for o in opponents.values() if o['has_own_page'])} com página própria)")

    if dry_run:
        print("\n--dry-run: nada escrito")
        return 0
    doc = {"generated": datetime.now(UTC).isoformat(timespec="seconds"),
           "scope": "roster + adversárias diretas; NÃO o corpus inteiro",
           "athletes": {roster[k]: v for k, v in recs.items() if k in roster},
           "opponents": opponents}
    RECORDS.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nescrito {RECORDS}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    return build(ap.parse_args().dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
