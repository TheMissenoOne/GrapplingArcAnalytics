"""Discipline classification (mma / grappling / wrestling) from ``Match.event``.

Export-time heuristic on importer-controlled event tags — every MMA bout is tagged
``UFC*`` or ``None`` (career dumps), every wrestling bout ``NCAA*``, everything else is
grappling (gi + no-gi share one board by product decision).

ponytail: string-prefix heuristic; upgrade path = a ``Match.discipline`` column if event
tags ever stop being importer-controlled, and ``elo_rankings/*.csv`` as an MMA rank seed.
"""

from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.names import athlete_key
from analysis.rating_v2.config import SITE_RATING_RUN_ID
from db.models import Athlete, Match

logger = logging.getLogger(__name__)

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


def athlete_disciplines(
    session: Session, run_id: str | None = SITE_RATING_RUN_ID
) -> dict[str, str]:
    """athlete_id → majority discipline over their FINAL matches (tie → grappling), with
    a rating-run override for the one case the event-tag heuristic genuinely cannot call.

    ``match_discipline`` maps an untagged event (``None``) to mma, because career dumps
    used to be MMA career dumps. They no longer only are: of 124 untagged final bouts,
    some are Georges St-Pierre's and Khabib's MMA careers and others are Craig Jones's
    (21) and Leandro Lo's (22) GRAPPLING careers. The tag carries no discipline
    information either way, and flipping the default is worse — it would move 76 pure MMA
    fighters onto the grappling board, which is the exact failure ADR-05 exists to prevent.

    So the tie is broken with information that already exists: **an athlete carrying a
    Rating V2 state for ``run_id`` is a grappler.** The V2 corpus is grappling-only by
    construction (its own event map, ADR-10), so presence in a run IS grappling evidence,
    and absence protects the board — St-Pierre, Khabib, Oliveira and Usman have no row in
    the run at all. Measured on the pinned run: 635 athletes were already grappling, 11
    were mislabelled mma (Kade Ruotolo, Craig Jones, Nicholas Meregali, Leandro Lo, Tye
    Ruotolo among them) and zero wrestlers are touched.

    The misclassification was not only a leaderboard problem: ``analysis/style_profile.py``
    picks an athlete's percentile POOL from this map, so a mislabelled grappler landed in
    the mma pool, where the rating source is the UFC Elo CSV they don't appear in — and
    their dossier lost its rank and percentile entirely.

    The override applies ONLY where the guess was actually a guess. An explicit ``UFC*``
    or ``NCAA*`` tag is positive evidence and outranks it: an athlete with a real UFC bout
    stays mma even if a V2 row exists for them, because a definite tag beats an inference
    drawn from absence. In practice no UFC-tagged athlete has a V2 row, but the rule
    should say what it means rather than rely on that staying true.

    ``run_id=None`` disables the override and restores the pure event-tag heuristic.
    """
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    tagged: set[str] = set()  # athletes with at least one UFC*/NCAA* bout
    rows = session.execute(
        select(Match.athlete_a_id, Match.athlete_b_id, Match.event)
        .where(Match.status == "final")
    )
    for a_id, b_id, event in rows:
        d = match_discipline(event)
        definite = event is not None and d != "grappling"
        for aid in (a_id, b_id):
            counts[aid][d] += 1
            if definite:
                tagged.add(aid)
    out = {
        aid: max(DISCIPLINES, key=lambda d: (c[d], _TIE_ORDER.index(d)))
        for aid, c in counts.items()
    }
    if run_id:
        for aid in _v2_grappling_ratings(session, run_id):
            if aid in out and aid not in tagged:
                out[aid] = "grappling"
    return out


@lru_cache(maxsize=1)
def ufc_elo_by_key() -> dict[str, float]:
    """athlete_key → current UFC Elo (hyphens folded: CSV has "Benoit Saint Denis")."""
    with open(_UFC_ELO_CSV, newline="", encoding="utf-8") as f:
        return {
            athlete_key(row["Fighter"].replace("-", " ")): float(row["Elo Rating"])
            for row in csv.DictReader(f)
        }


def _v2_grappling_ratings(session: Session, run_id: str) -> dict[str, float]:
    """athlete_id -> rating_v2 ``rating`` for one pinned run. ADR-02: explicit run_id
    only, never a "latest run" query."""
    from db.models import AthleteRatingStateV2

    rows = session.execute(
        select(AthleteRatingStateV2.athlete_id, AthleteRatingStateV2.rating)
        .where(AthleteRatingStateV2.run_id == run_id)
    ).all()
    return {athlete_id: rating for athlete_id, rating in rows}


def ranked_pools(
    session: Session, run_id: str | None = SITE_RATING_RUN_ID
) -> dict[str, list[tuple[str, str, float]]]:
    """discipline → [(athlete_id, name, rating)] sorted desc — the percentile pools.

    Rating source per pool: grappling = rating_v2 ``AthleteRatingStateV2.rating`` for
    the pinned ``run_id`` (ADR-02 — V2 only rated the grappling corpus; keyed by an
    explicit run_id, never a "latest run" query). No run pinned (``run_id=None``) falls
    back to V1 ``rank_elo`` and logs a warning — this function must keep working with
    nothing pinned. mma = UFC Elo from the NBAtrev CSV (no CSV match → unranked);
    wrestling = grown graph ``elo`` with never-replayed defaults excluded. An athlete
    absent from the pinned run has no V2 rating and simply doesn't appear in the pool,
    exactly as an athlete with no ``rank_elo`` doesn't today.

    Confidence (RD) is deliberately NOT filtered here — this pool is the percentile
    denominator (``_elo_standings`` / dossier ``elo_percentile``), and dropping
    uncertain athletes from a denominator inflates everyone else's percentile. The
    confidence gate belongs at publication (``export/site_data.py:build_elo``)."""
    # Same run drives the discipline override, so `run_id=None` restores the pure V1 path
    # on BOTH legs instead of half-migrating.
    disc = athlete_disciplines(session, run_id)
    ufc = ufc_elo_by_key()
    v2: dict[str, float] | None = None
    if run_id:
        v2 = _v2_grappling_ratings(session, run_id)
    else:
        logger.warning(
            "ranked_pools: no rating_v2 run pinned -- grappling pool falls back to V1 rank_elo")
    pools: dict[str, list[tuple[str, str, float]]] = {d: [] for d in DISCIPLINES}
    rows = session.execute(select(Athlete.id, Athlete.name, Athlete.elo, Athlete.rank_elo))
    for aid, name, elo, rank_elo in rows:
        d = disc.get(aid, "grappling")
        if d == "grappling":
            rating = v2.get(aid) if v2 is not None else rank_elo
        elif d == "mma":
            rating = ufc.get(athlete_key(name.replace("-", " ")))
        else:
            rating = None if elo == _GRAPH_ELO_DEFAULT else elo
        if rating is not None:
            pools[d].append((aid, name, float(rating)))
    for pool in pools.values():
        pool.sort(key=lambda r: r[2], reverse=True)
    return pools
