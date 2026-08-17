"""Global shadow replay — reads the DB, writes only a JSON artefact. Never writes to the DB.

Eligibility (ADR-06, ADR-05, ADR-04):
- ``matches.status == 'final'``;
- discipline (from ``data/rating_v2/disciplines.json``) is in ``config.disciplines``;
- winner known (``winner_id`` set) -> 1.0/0.0; ``win_type == 'DRAW'`` with no winner -> 0.5;
  ``winner_id`` NULL otherwise (typically ``DECISION``) -> excluded, counted as lost coverage;
- ``athlete_a_id != athlete_b_id`` (self-match guard, currently 0 rows);
- period = ``matches.year``; every bout in a period reads pre-period state (``periods.py``).

    uv run python -m analysis.rating_v2.replay [--seed-from-rank-elo] [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.models import RatingState
from analysis.rating_v2.periods import Bout, run_periods

DISCIPLINES_MAP_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "rating_v2" / "disciplines.json"
)
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent.parent / "reports" / "rating_v2"


def load_discipline_map() -> tuple[dict[str, str], str]:
    """(event -> discipline, discipline for NULL event) from the versioned map."""
    doc = json.loads(DISCIPLINES_MAP_PATH.read_text())
    return doc["events"], doc["null_event"]


def _discipline_of(event: str | None, events_map: dict[str, str], null_discipline: str) -> str:
    if event is None:
        return null_discipline
    return events_map.get(event, "unknown")


def _score_a(winner_id: str | None, athlete_a_id: str, win_type: str | None) -> float | None:
    """ADR-06: None means "exclude, coverage loss" — never guess a draw."""
    if winner_id is not None:
        return 1.0 if winner_id == athlete_a_id else 0.0
    if win_type == "DRAW":
        return 0.5
    return None


class MatchRow:
    """Row shape ``build_bouts`` needs — matches ``db.models.Match`` columns used here."""

    __slots__ = (
        "athlete_a_id", "athlete_b_id", "winner_id", "event", "year", "win_type", "status",
        "sequence",
    )

    def __init__(
        self,
        athlete_a_id: str,
        athlete_b_id: str,
        winner_id: str | None,
        event: str | None,
        year: int | None,
        win_type: str | None,
        status: str = "final",
        sequence: list[dict[str, Any]] | None = None,
    ) -> None:
        self.athlete_a_id = athlete_a_id
        self.athlete_b_id = athlete_b_id
        self.winner_id = winner_id
        self.event = event
        self.year = year
        self.win_type = win_type
        self.status = status
        # ``sequence`` unused by build_bouts (global replay never reads it) — carried through
        # only so node_replay.py can build node observations off the same MatchRow, without a
        # second DB read (wave 5, ADR-03).
        self.sequence = sequence


def build_bouts(
    matches: list[MatchRow],
    config: EngineConfig,
    events_map: dict[str, str],
    null_discipline: str,
) -> tuple[list[Bout], dict[str, int]]:
    """Filter + map matches -> Bouts. Returns (bouts, coverage counters)."""
    counters = {
        "total": len(matches),
        "not_final": 0,
        "self_match": 0,
        "out_of_discipline": 0,
        "no_year": 0,
        "unknown_winner": 0,
        "eligible": 0,
    }
    bouts: list[Bout] = []
    for m in matches:
        if m.status != "final":
            counters["not_final"] += 1
            continue
        if m.athlete_a_id == m.athlete_b_id:
            counters["self_match"] += 1
            continue
        discipline = _discipline_of(m.event, events_map, null_discipline)
        if discipline not in config.disciplines:
            counters["out_of_discipline"] += 1
            continue
        if m.year is None:
            counters["no_year"] += 1
            continue
        score_a = _score_a(m.winner_id, m.athlete_a_id, m.win_type)
        if score_a is None:
            counters["unknown_winner"] += 1
            continue
        counters["eligible"] += 1
        bouts.append(
            Bout(
                period=m.year,
                athlete_a=m.athlete_a_id,
                athlete_b=m.athlete_b_id,
                score_a=score_a,
            )
        )
    return bouts, counters


def build_seeds(
    bouts: list[Bout],
    config: EngineConfig,
    rank_elo_by_athlete: dict[str, float],
) -> dict[str, RatingState]:
    """Every athlete in the eligible bout set gets a seed state (ADR-01)."""
    athlete_ids: set[str] = set()
    for b in bouts:
        athlete_ids.add(b.athlete_a)
        athlete_ids.add(b.athlete_b)
    seeds: dict[str, RatingState] = {}
    for aid in athlete_ids:
        rating = config.athlete_seed
        if config.seed_from_rank_elo and aid in rank_elo_by_athlete:
            rating = rank_elo_by_athlete[aid]
        seeds[aid] = RatingState(rating, config.initial_rd, config.initial_volatility)
    return seeds


def bouts_hash(bouts: list[Bout]) -> str:
    """Deterministic hash of the replay input — independent of DB row order."""
    canonical = sorted(
        [(b.period, b.athlete_a, b.athlete_b, b.score_a) for b in bouts]
    )
    payload = json.dumps(canonical, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def summarize(
    states: dict[str, RatingState],
    bouts: list[Bout],
    names_by_id: dict[str, str],
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for b in bouts:
        counts[b.athlete_a] = counts.get(b.athlete_a, 0) + 1
        counts[b.athlete_b] = counts.get(b.athlete_b, 0) + 1

    n_bouts = [counts.get(aid, 0) for aid in states]
    ratings = [s.rating for s in states.values()]
    rds = [s.deviation for s in states.values()]

    conservative = sorted(
        ((aid, s.rating - s.deviation, s.rating, s.deviation, counts.get(aid, 0))
         for aid, s in states.items()),
        key=lambda row: row[1],
        reverse=True,
    )[:12]

    return {
        "athletes_with_state": len(states),
        "bouts_per_athlete": {
            "median": statistics.median(n_bouts) if n_bouts else 0,
            "mean": statistics.fmean(n_bouts) if n_bouts else 0.0,
            "max": max(n_bouts) if n_bouts else 0,
        },
        "rating_distribution": {
            "min": min(ratings) if ratings else 0.0,
            "p25": _percentile(ratings, 0.25),
            "median": statistics.median(ratings) if ratings else 0.0,
            "p75": _percentile(ratings, 0.75),
            "max": max(ratings) if ratings else 0.0,
        },
        "rd_distribution": {
            "min": min(rds) if rds else 0.0,
            "p25": _percentile(rds, 0.25),
            "median": statistics.median(rds) if rds else 0.0,
            "p75": _percentile(rds, 0.75),
            "max": max(rds) if rds else 0.0,
        },
        "pct_rd_ge_200": (sum(1 for r in rds if r >= 200) / len(rds) * 100) if rds else 0.0,
        "top_12_conservative": [
            {
                "athlete_id": aid,
                "name": names_by_id.get(aid, aid),
                "rating": rating,
                "rd": rd,
                "conservative": cons,
                "bouts": n,
            }
            for aid, cons, rating, rd, n in conservative
        ],
    }


def run_replay(config: EngineConfig) -> dict[str, Any]:
    """DB-reading orchestration. The only impure function in this package."""
    from sqlalchemy import select

    from db.base import db_session
    from db.models import Athlete, Match

    events_map, null_discipline = load_discipline_map()

    with db_session() as session:
        rows = session.execute(
            select(
                Match.athlete_a_id, Match.athlete_b_id, Match.winner_id,
                Match.event, Match.year, Match.win_type, Match.status, Match.sequence,
            )
        ).all()
        matches = [MatchRow(*r) for r in rows]

        rank_elo_rows = session.execute(
            select(Athlete.id, Athlete.rank_elo).where(Athlete.rank_elo.is_not(None))
        ).all()
        rank_elo_by_athlete = {aid: float(elo) for aid, elo in rank_elo_rows}

        names_rows = session.execute(select(Athlete.id, Athlete.name)).all()
        names_by_id = dict(names_rows)

    bouts, coverage = build_bouts(matches, config, events_map, null_discipline)
    seeds = build_seeds(bouts, config, rank_elo_by_athlete)
    final_states = run_periods(seeds, bouts, config)
    summary = summarize(final_states, bouts, names_by_id)

    return {
        "config": config.to_dict(),
        "input_hash": bouts_hash(bouts),
        "coverage": coverage,
        "summary": summary,
        "states": {
            aid: {"rating": s.rating, "deviation": s.deviation, "volatility": s.volatility}
            for aid, s in final_states.items()
        },
    }


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Rating V2 global shadow replay (read-only).")
    parser.add_argument("--seed-from-rank-elo", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    config = EngineConfig(seed_from_rank_elo=args.seed_from_rank_elo)
    result = run_replay(config)

    seed_mode = "rankelo" if args.seed_from_rank_elo else "flat"
    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"{config.engine_version}-{seed_mode}.json"
    out_path.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n")

    print(f"wrote {out_path}")
    print(json.dumps(result["coverage"], indent=2))
    print(json.dumps(result["summary"], indent=2, default=str))


if __name__ == "__main__":
    main()
