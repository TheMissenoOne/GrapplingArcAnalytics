"""Discipline classification (mma / grappling / wrestling) from ``Match.event``.

Export-time heuristic on importer-controlled event tags — every MMA bout is tagged
``UFC*`` or ``None`` (career dumps), every wrestling bout ``NCAA*``, everything else is
grappling (gi + no-gi share one board by product decision).

ponytail: string-prefix heuristic; upgrade path = a ``Match.discipline`` column if event
tags ever stop being importer-controlled, and ``elo_rankings/*.csv`` as an MMA rank seed.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.names import athlete_key
from db.models import Athlete, Match

DISCIPLINES = ("grappling", "mma", "wrestling")

# Tie-break preference: definite tags beat the event=None default (which maps to mma) —
# so 1 untagged bout + 1 NCAA bout resolves to wrestling, and any grappling tie wins.
_TIE_ORDER = ("mma", "wrestling", "grappling")

# UFC Elo (NBAtrev/UFC-Elo-Engine, finish-weighted current ratings) — the MMA pool's
# rating source; graph elo is useless there (never-replayed defaults top the board).
_UFC_ELO_CSV = (Path(__file__).resolve().parent.parent
                / "elo_rankings" / "k_factor_adjust_current.csv")

# Athlete.elo column default — an athlete still exactly here was never grown by a replay.
_GRAPH_ELO_DEFAULT = 1000.0


def match_discipline(event: str | None) -> str:
    """Discipline of one bout from its event tag."""
    if event is None or event.startswith("UFC"):
        return "mma"
    if event.startswith("NCAA"):
        return "wrestling"
    return "grappling"


def athlete_disciplines(session: Session) -> dict[str, str]:
    """athlete_id → majority discipline over their FINAL matches (tie → grappling)."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    rows = session.execute(
        select(Match.athlete_a_id, Match.athlete_b_id, Match.event)
        .where(Match.status == "final")
    )
    for a_id, b_id, event in rows:
        d = match_discipline(event)
        counts[a_id][d] += 1
        counts[b_id][d] += 1
    return {
        aid: max(DISCIPLINES, key=lambda d: (c[d], _TIE_ORDER.index(d)))
        for aid, c in counts.items()
    }


@lru_cache(maxsize=1)
def ufc_elo_by_key() -> dict[str, float]:
    """athlete_key → current UFC Elo (hyphens folded: CSV has "Benoit Saint Denis")."""
    with open(_UFC_ELO_CSV, newline="", encoding="utf-8") as f:
        return {
            athlete_key(row["Fighter"].replace("-", " ")): float(row["Elo Rating"])
            for row in csv.DictReader(f)
        }


def ranked_pools(session: Session) -> dict[str, list[tuple[str, str, float]]]:
    """discipline → [(athlete_id, name, rating)] sorted desc — the percentile pools.

    Rating source per pool: grappling = ``rank_elo`` (ADCC leaderboard target, as
    always); mma = UFC Elo from the NBAtrev CSV (no CSV match → unranked); wrestling =
    grown graph ``elo`` with never-replayed defaults excluded. Athletes without a
    rating simply don't appear (renderers show "Unranked")."""
    disc = athlete_disciplines(session)
    ufc = ufc_elo_by_key()
    pools: dict[str, list[tuple[str, str, float]]] = {d: [] for d in DISCIPLINES}
    rows = session.execute(select(Athlete.id, Athlete.name, Athlete.elo, Athlete.rank_elo))
    for aid, name, elo, rank_elo in rows:
        d = disc.get(aid, "grappling")
        if d == "grappling":
            rating = rank_elo
        elif d == "mma":
            rating = ufc.get(athlete_key(name.replace("-", " ")))
        else:
            rating = None if elo == _GRAPH_ELO_DEFAULT else elo
        if rating is not None:
            pools[d].append((aid, name, float(rating)))
    for pool in pools.values():
        pool.sort(key=lambda r: r[2], reverse=True)
    return pools
