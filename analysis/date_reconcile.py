"""Cross-reference DB match years against BJJ Heroes (bjjheroes.com) — find matches whose
``year`` is wrong and propose the corrected year. REVIEW ARTIFACT by default; ``--apply`` fixes.

BJJ Heroes fighter pages carry a full match table (Opponent, W/L, Method, Competition, Stage,
Year). The ``Year`` column is authoritative for the athletes it covers (established ADCC/IBJJF
names) — far more reliable than a transcript-derived year, which often inherits the *video upload*
year (why old footage lands in 2025/2026). We scrape only the overlap between our athletes and
BJJ Heroes' A-Z list (polite: one list fetch + the intersection, cached HTML), pair each row by
normalized athlete key, and flag every DB match whose year disagrees.

    uv run python -m analysis.date_reconcile            # scrape (cached) + write report
    uv run python -m analysis.date_reconcile --apply    # also update Match.year + re-replay
    uv run python -m analysis.date_reconcile --check     # in-memory parse/reconcile self-check
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from analysis.names import athlete_key

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"(19|20)\d{2}")
_CACHE = Path(__file__).resolve().parents[1] / "data" / "raw" / "bjjheroes"
_REPORT = Path(__file__).resolve().parents[1] / "docs" / "date_reconcile_report"


@dataclass
class MatchRow:
    opponent: str
    wl: str
    method: str
    competition: str
    stage: str
    year: int | None


def parse_match_history(html: str) -> list[MatchRow]:
    """Parse a BJJ Heroes fighter page's single match table into rows. Opponent = the cell's
    anchor text (the visible label doubles it with a span, so ``get_text`` is unreliable)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return []
    rows: list[MatchRow] = []
    for tr in table.find_all("tr")[1:]:  # skip header
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        opp_a = tds[1].find("a")
        opponent = opp_a.get_text(strip=True) if opp_a else _dedup(tds[1].get_text(strip=True))
        ym = _YEAR_RE.search(tds[7].get_text(strip=True))
        rows.append(MatchRow(
            opponent=opponent,
            wl=tds[2].get_text(strip=True),
            method=tds[3].get_text(strip=True),
            competition=tds[4].get_text(strip=True),
            stage=tds[6].get_text(strip=True),
            year=int(ym.group()) if ym else None,
        ))
    return rows


def _dedup(text: str) -> str:
    """A cell whose text is a name repeated twice ("Tex JohnsonTex Johnson") → single copy."""
    n = len(text)
    if n % 2 == 0 and text[: n // 2] == text[n // 2:]:
        return text[: n // 2]
    return text


def list_fighters(html: str) -> dict[str, str]:
    """A-Z list page → ``{athlete_key: fighter_url}``. Name = First + Last columns."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    out: dict[str, str] = {}
    if table is None:
        return out
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        first, last = tds[0].get_text(strip=True), tds[1].get_text(strip=True)
        a = tr.find("a")
        href = str(a.get("href", "")) if a else ""
        name = f"{first} {last}".strip()
        if name and href:
            out.setdefault(athlete_key(name), href)
    return out


def reconcile(
    db_matches: Sequence[tuple[str, str, str, int | None, str | None]],
    bjjh_years: dict[frozenset[str], set[int]],
) -> list[dict[str, Any]]:
    """Pure comparison. ``db_matches`` = (id, key_a, key_b, year, event); ``bjjh_years`` maps a
    participant-pair to the year(s) BJJ Heroes lists for it. A mismatch = the pair is on BJJ
    Heroes but our year isn't among its years.

    A year is only *auto-suggested* (safe to `--apply`) when the pair is unambiguous on BOTH sides:
      * BJJ Heroes lists exactly one year for the pair, AND
      * we hold exactly one bout for that pair, AND
      * our event name doesn't itself carry our year (corroboration).
    Matching is pair-level, not bout-level, so a rematched pair (multiple meetings, but BJJ Heroes
    surfaces one year) could otherwise collapse a correct year onto the wrong one — those are
    reported with ``suggested_year=None`` for manual review instead."""
    from collections import Counter
    pair_count = Counter(frozenset((a, b)) for _, a, b, _, _ in db_matches)
    out: list[dict[str, Any]] = []
    for mid, a, b, year, event in db_matches:
        pair = frozenset((a, b))
        years = bjjh_years.get(pair)
        if not years or year in years:
            continue
        # If our event name itself carries our year (e.g. "ADCC 2024" @ year 2024), the DB year
        # is corroborated and BJJ Heroes is almost certainly matching a *different* meeting.
        em = _YEAR_RE.search(event or "")
        corroborated = em is not None and int(em.group()) == year
        unambiguous = len(years) == 1 and pair_count[pair] == 1 and not corroborated
        out.append({
            "match_id": mid, "pair": sorted((a, b)), "db_year": year, "event": event,
            "bjjh_years": sorted(years), "event_corroborated": corroborated,
            "db_match_count": pair_count[pair],
            "suggested_year": next(iter(years)) if unambiguous else None,
        })
    return out


# ── network / DB glue (thin; core above is pure) ──────────────────────────────────────────

def _bjjh_years_from_pages(
    pages: dict[str, str], our_keys: set[str]
) -> dict[frozenset[str], set[int]]:
    """From each scraped fighter page (keyed by that fighter's athlete_key), pair every table
    row's opponent against the fighter, keeping only pairs where BOTH sides are athletes we have."""
    years: dict[frozenset[str], set[int]] = {}
    for fighter_key, html in pages.items():
        for row in parse_match_history(html):
            opp_key = athlete_key(row.opponent)
            if row.year is None or opp_key not in our_keys or opp_key == fighter_key:
                continue
            years.setdefault(frozenset((fighter_key, opp_key)), set()).add(row.year)
    return years


def _scrape(our_keys: set[str], *, force: bool = False) -> dict[str, str]:
    """Fetch the A-Z list, intersect with our athletes, scrape+cache only the overlap.
    Returns ``{athlete_key: html}`` for the fighters we could fetch."""
    import urllib.request

    from pipelines.bjjheroes import BASE_URL, USER_AGENT, BJJHeroesPipeline

    _CACHE.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        "https://www.bjjheroes.com/a-z-bjj-fighters-list", headers={"User-Agent": USER_AGENT}
    )
    fighters = list_fighters(urllib.request.urlopen(req, timeout=30).read().decode())
    overlap = {k: fighters[k] for k in our_keys & set(fighters)}
    logger.info("A-Z list %d fighters; %d overlap our %d athletes",
                len(fighters), len(overlap), len(our_keys))

    pages: dict[str, str] = {}
    to_fetch: dict[str, str] = {}
    for key, href in overlap.items():
        cache = _CACHE / f"match__{key.replace(' ', '_')}.html"
        if cache.exists() and not force:
            pages[key] = cache.read_text(encoding="utf-8")
        else:
            to_fetch[key] = href if href.startswith("http") else BASE_URL + href

    if to_fetch:
        import asyncio
        # no nest_asyncio here: a plain CLI has no running loop, and patching it breaks
        # aiohttp's asyncio.wait_for timeout ("context manager used outside a task").
        fetched = asyncio.run(BJJHeroesPipeline._fetch_pages(list(to_fetch.values())))
        url_to_key = {u: k for k, u in to_fetch.items()}
        for url, html in fetched:
            fkey = url_to_key.get(url)
            if fkey:
                (_CACHE / f"match__{fkey.replace(' ', '_')}.html").write_text(
                    html, encoding="utf-8")
                pages[fkey] = html
    logger.info("fighter pages available: %d (%d newly fetched)", len(pages), len(to_fetch))
    return pages


def _load_db_matches(
    session: Any,
) -> tuple[list[tuple[str, str, str, int | None, str | None]], set[str]]:
    from sqlalchemy import select

    from db.models import Athlete, Match
    names = {a.id: athlete_key(a.name) for a in session.execute(select(Athlete)).scalars()}
    rows = session.execute(
        select(Match.id, Match.athlete_a_id, Match.athlete_b_id, Match.year, Match.event)
        .where(Match.status == "final")
    )
    return [(mid, names[a], names[b], y, ev) for mid, a, b, y, ev in rows
            if a in names and b in names], set(names.values())


def run(*, apply: bool = False, force: bool = False) -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    from db.base import db_session

    with db_session() as session:
        db_matches, our_keys = _load_db_matches(session)
        pages = _scrape(our_keys, force=force)
        bjjh = _bjjh_years_from_pages(pages, our_keys)
        mismatches = reconcile(db_matches, bjjh)
        fixable = [m for m in mismatches if m["suggested_year"] is not None]
        logger.info("%d year mismatches (%d unambiguously fixable) across %d compared pairs",
                    len(mismatches), len(fixable), len(bjjh))

        _REPORT.parent.mkdir(parents=True, exist_ok=True)
        _REPORT.with_suffix(".json").write_text(
            json.dumps(mismatches, indent=2, ensure_ascii=False)
        )
        logger.info("Report → %s.json", _REPORT)

        if not apply:
            return 0

        from db.models import Athlete, Match
        from db.repository import replay_and_persist_athlete
        touched: set[str] = set()
        for m in fixable:
            match = session.get(Match, m["match_id"])
            if match is None:
                continue
            logger.info("  fix %s: %s → %s", m["pair"], m["db_year"], m["suggested_year"])
            match.year = m["suggested_year"]
            touched.update((match.athlete_a_id, match.athlete_b_id))
        session.flush()
        for aid in touched:
            ath = session.get(Athlete, aid)
            if ath is not None:
                replay_and_persist_athlete(ath, session)
        logger.info("Applied %d year fixes; re-replayed %d athletes", len(fixable), len(touched))
    return 0


def _check() -> None:
    html = """<table><tr><th>ID</th><th>Opponent</th><th>W/L</th><th>Method</th>
      <th>Competition</th><th>Weight</th><th>Stage</th><th>Year</th></tr>
      <tr><td>1</td><td><a href="/?p=9">Tex Johnson</a><span>Tex Johnson</span></td>
      <td>L</td><td>Points</td><td>ADCC</td><td>ABS</td><td>F</td><td>2016</td></tr></table>"""
    rows = parse_match_history(html)
    assert len(rows) == 1 and rows[0].opponent == "Tex Johnson" and rows[0].year == 2016, rows
    assert _dedup("Tex JohnsonTex Johnson") == "Tex Johnson"

    lst = list_fighters(
        '<table><tr><th>F</th><th>L</th><th>N</th><th>T</th></tr>'
        '<tr><td>Gordon</td><td>Ryan</td><td></td><td>NG</td>'
        '<td><a href="/?p=1">x</a></td></tr></table>'
    )
    assert athlete_key("Gordon Ryan") in lst, lst

    ga, tex = athlete_key("Gordon Ryan"), athlete_key("Tex Johnson")
    ga, lo = athlete_key("Gordon Ryan"), athlete_key("Leandro Lo")
    kade, mica = athlete_key("Kade Ruotolo"), athlete_key("Mica Galvao")
    # single bout, wrong year, event has no year → the one auto-fixable case.
    single = [("m1", ga, lo, 2019, "WNO")]
    fix = [m for m in reconcile(single, {frozenset((ga, lo)): {2017}})
           if m["suggested_year"] is not None]
    assert len(fix) == 1 and fix[0]["suggested_year"] == 2017, fix
    # event corroborates our year → never auto-fix.
    corr = reconcile([("m2", kade, mica, 2024, "ADCC 2024")], {frozenset((kade, mica)): {2016}})
    assert corr[0]["suggested_year"] is None and corr[0]["event_corroborated"], corr
    # rematched pair (2 DB bouts) but BJJ Heroes surfaces one year → ambiguous, NEVER auto-fix
    # (must not collapse a correct year). Reported, but suggested_year stays None.
    rems = reconcile([("m3", ga, tex, 2019, "WNO"), ("m4", ga, tex, 2016, "ADCC")],
                     {frozenset((ga, tex)): {2016}})
    assert all(m["suggested_year"] is None for m in rems), rems
    assert rems[0]["db_match_count"] == 2, rems
    print("date_reconcile self-check OK")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Reconcile DB match years vs BJJ Heroes")
    ap.add_argument("--apply", action="store_true", help="update Match.year + re-replay")
    ap.add_argument("--force", action="store_true", help="re-fetch pages, ignore cache")
    ap.add_argument("--check", action="store_true", help="in-memory self-check, no network/DB")
    args = ap.parse_args()
    if args.check:
        _check()
        return 0
    return run(apply=args.apply, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
