#!/usr/bin/env python
"""Read-only proof for wave 7: two rating_v2 runs on the SAME input must agree.

Compares ``athlete_rating_states_v2`` between two ``run_id``s: athlete count per run,
athletes present in only one, and any per-athlete ``rating``/``rating_deviation``/
``volatility`` divergence above a tight tolerance. Never writes anything — this is meant
to become a CI/replay gate, so exit code 1 on any divergence.

    uv run python -m scripts.verify_rating_v2_determinism RUN_ID_1 RUN_ID_2 [--tol 1e-6]
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

DEFAULT_TOLERANCE = 1e-6
FIELDS = ("rating", "rating_deviation", "volatility")


def compare_states(
    states_1: dict[str, dict[str, float]],
    states_2: dict[str, dict[str, float]],
    tol: float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """Pure comparison — no DB, no I/O. Takes ``{athlete_id: {field: value}}`` per run."""
    ids_1, ids_2 = set(states_1), set(states_2)
    divergences = []
    for aid in sorted(ids_1 & ids_2):
        a, b = states_1[aid], states_2[aid]
        for field in FIELDS:
            delta = abs(a[field] - b[field])
            if delta > tol:
                divergences.append(
                    {"athlete_id": aid, "field": field, "run1": a[field], "run2": b[field],
                     "delta": delta}
                )
    return {
        "n_athletes_run1": len(ids_1),
        "n_athletes_run2": len(ids_2),
        "only_in_run1": sorted(ids_1 - ids_2),
        "only_in_run2": sorted(ids_2 - ids_1),
        "divergences": divergences,
    }


def fetch_states(session: Any, run_id: str) -> dict[str, dict[str, float]]:
    from sqlalchemy import select

    from db.models import AthleteRatingStateV2

    rows = session.execute(
        select(
            AthleteRatingStateV2.athlete_id,
            AthleteRatingStateV2.rating,
            AthleteRatingStateV2.rating_deviation,
            AthleteRatingStateV2.volatility,
        ).where(AthleteRatingStateV2.run_id == run_id)
    ).all()
    return {
        aid: {"rating": rating, "rating_deviation": rd, "volatility": vol}
        for aid, rating, rd, vol in rows
    }


def _report_ok(report: dict[str, Any]) -> bool:
    return not report["only_in_run1"] and not report["only_in_run2"] and not report["divergences"]


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id_1")
    parser.add_argument("run_id_2")
    parser.add_argument("--tol", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args()

    from db.base import db_session

    with db_session() as session:
        states_1 = fetch_states(session, args.run_id_1)
        states_2 = fetch_states(session, args.run_id_2)

    report = compare_states(states_1, states_2, args.tol)

    print(f"run1 {args.run_id_1}: {report['n_athletes_run1']} athletes")
    print(f"run2 {args.run_id_2}: {report['n_athletes_run2']} athletes")
    print(f"only in run1: {len(report['only_in_run1'])} {report['only_in_run1'][:10]}")
    print(f"only in run2: {len(report['only_in_run2'])} {report['only_in_run2'][:10]}")
    print(f"divergences (> {args.tol}): {len(report['divergences'])}")
    for d in report["divergences"][:20]:
        print(f"  {d['athlete_id']} {d['field']}: {d['run1']} vs {d['run2']} (delta={d['delta']})")

    ok = _report_ok(report)
    print("DETERMINISTIC" if ok else "DIVERGED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
