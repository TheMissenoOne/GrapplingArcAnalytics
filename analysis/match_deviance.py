"""Match deviance — how far a bout departs from each athlete's *usual* game (a QA/recheck signal).

For every (match, athlete) we compare the athlete's per-type event mix **in that bout** to their
own career mix over their **other** bouts (leave-one-out), via Jensen–Shannon divergence
(Lin 1991 — a bounded [0,1], symmetric distributional distance; metric under sqrt, Endres &
Schindelin 2003). A high score means "this athlete didn't fight the way they usually do here",
which for our purpose flags a bout whose events may be mis-refined (wrong actor ownership, wrong
athlete, noisy labels) and worth a recheck. Leave-one-out matters: without it a fighter's few
bouts define their own norm and never look deviant.

Type buckets reuse ``analysis.deviance.TYPES`` (the same 8 the archetype pipeline uses). Works off
the raw ``Match.sequence`` (events keyed by ``actor_id``), so the athlete's side is taken straight
from event ownership — the same actor convention documented in ``docs/match_event_model.md``.

    uv run python -m analysis.match_deviance            # print the recheck list (reliable first)
    uv run python -m analysis.match_deviance --all      # include low-event / thin-norm rows too
"""
from __future__ import annotations

from collections import Counter
from math import log2
from typing import Any, Protocol

from analysis.deviance import TYPES

MIN_MATCH_EVENTS = 3    # below this, the bout's own mix is too sparse to trust
MIN_CAREER_EVENTS = 12  # below this, the leave-one-out norm is too thin to judge against


class _MatchLike(Protocol):
    id: Any
    athlete_a_id: str
    athlete_b_id: str
    sequence: list[dict[str, Any]] | None


def _dist(counts: Counter[str]) -> list[float]:
    tot = sum(counts.get(t, 0) for t in TYPES)
    return [counts.get(t, 0) / tot for t in TYPES] if tot else [0.0] * len(TYPES)


def _kl(p: list[float], q: list[float]) -> float:
    return sum(pi * log2(pi / qi) for pi, qi in zip(p, q) if pi > 0 and qi > 0)


def jensen_shannon(p: list[float], q: list[float]) -> float:
    """JS divergence, base 2, in [0, 1]. 0 = identical mix, 1 = disjoint support."""
    m = [(pi + qi) / 2 for pi, qi in zip(p, q)]
    return max(0.0, min(1.0, 0.5 * _kl(p, m) + 0.5 * _kl(q, m)))


def _type_counts(sequence: list[dict[str, Any]] | None, actor_id: str) -> Counter[str]:
    """Per-type counts of the events this athlete *performed* (owns) in one bout."""
    c: Counter[str] = Counter()
    for e in sequence or []:
        if isinstance(e, dict) and e.get("actor_id") == actor_id and e.get("label"):
            t = str(e.get("type", "")).lower().strip()
            if t in TYPES:
                c[t] += 1
    return c


def _top_shift(match: Counter[str], career: Counter[str]) -> str:
    """The single type whose share moved most from career→bout, for an actionable 'why'."""
    md, cd = _dist(match), _dist(career)
    deltas = sorted(zip(TYPES, md, cd), key=lambda x: abs(x[1] - x[2]), reverse=True)
    t, m, c = deltas[0]
    return f"{t} {round(c * 100)}%→{round(m * 100)}%"


def deviance_rows(matches: list[_MatchLike]) -> list[dict[str, Any]]:
    """Per (athlete, match) deviance over a set of bouts. One pass: tally each bout's per-type
    counts per side, pool per athlete, then leave-one-out JS vs the rest of the athlete's career.
    Rows: {match_id, athlete_id, n_events, career_events, deviance, reliable, shift}, sorted so
    the reliable, most-deviant bouts come first (the recheck queue)."""
    per: dict[str, list[tuple[_MatchLike, Counter[str]]]] = {}
    for m in matches:
        for aid in (m.athlete_a_id, m.athlete_b_id):
            per.setdefault(aid, []).append((m, _type_counts(m.sequence, aid)))

    rows: list[dict[str, Any]] = []
    for aid, mcs in per.items():
        career: Counter[str] = sum((c for _, c in mcs), Counter())
        for m, c in mcs:
            n = sum(c.values())
            loo = career - c  # leave THIS bout out of the norm
            cn = sum(loo.values())
            dev = jensen_shannon(_dist(c), _dist(loo)) if (n and cn) else 0.0
            rows.append({
                "match_id": m.id, "athlete_id": aid,
                "n_events": n, "career_events": cn,
                "deviance": round(dev, 3),
                "reliable": n >= MIN_MATCH_EVENTS and cn >= MIN_CAREER_EVENTS,
                "shift": _top_shift(c, loo) if (n and cn) else "",
            })
    rows.sort(key=lambda r: (r["reliable"], r["deviance"]), reverse=True)
    return rows


def rank_matches(session: Any, reliable_only: bool = True) -> list[dict[str, Any]]:
    """Recheck list over all FINAL bouts, with athlete + opponent names attached."""
    from sqlalchemy import select

    from db.models import Athlete, Match

    matches = list(session.execute(
        select(Match).where(Match.status == "final", Match.sequence.isnot(None))
    ).scalars())
    list(session.execute(select(Athlete)).scalars())  # warm identity map for name lookups

    def name(aid: str) -> str:
        a = session.get(Athlete, aid)
        return a.name if a else aid
    by_match = {m.id: m for m in matches}

    rows = deviance_rows(matches)
    if reliable_only:
        rows = [r for r in rows if r["reliable"]]
    for r in rows:
        m = by_match[r["match_id"]]
        other = m.athlete_b_id if r["athlete_id"] == m.athlete_a_id else m.athlete_a_id
        r["athlete"], r["opponent"], r["year"] = name(r["athlete_id"]), name(other), m.year
    return rows


def _demo() -> None:
    """Self-check: an athlete with a steady guard game across 3 bouts + 1 takedown-heavy bout;
    the odd bout must score highest deviance and be flagged reliable."""
    class M:
        def __init__(self, mid, a, b, seq):
            self.id, self.athlete_a_id, self.athlete_b_id, self.sequence = mid, a, b, seq

    def ev(actor, typ):
        return {"actor_id": actor, "type": typ, "label": "x"}

    guard = [ev("A", "guard")] * 5 + [ev("A", "sweep")] * 2 + [ev("B", "pass")] * 4
    odd = [ev("A", "takedown")] * 6 + [ev("A", "control")] * 3 + [ev("B", "guard")] * 4
    matches = [M(1, "A", "B", guard), M(2, "A", "C", guard),
               M(3, "A", "D", guard), M(4, "A", "E", odd)]
    rows = deviance_rows(matches)
    a_rows = {r["match_id"]: r for r in rows if r["athlete_id"] == "A"}
    # The odd bout has disjoint type support vs the steady norm → JS ≈ 1.0, and it must rank
    # well clear of the steady bouts (which sit low, even though the odd bout pollutes their norm).
    assert a_rows[4]["deviance"] == max(r["deviance"] for r in a_rows.values()), a_rows
    assert a_rows[4]["deviance"] >= 0.9, a_rows[4]
    assert max(a_rows[i]["deviance"] for i in (1, 2, 3)) < 0.4 < a_rows[4]["deviance"], a_rows
    assert a_rows[4]["reliable"] and a_rows[4]["shift"], a_rows[4]  # a 'why' is reported
    # identical distributions → zero divergence
    assert jensen_shannon([0.5, 0.5], [0.5, 0.5]) == 0.0
    assert round(jensen_shannon([1.0, 0.0], [0.0, 1.0]), 3) == 1.0  # disjoint → 1
    print("match_deviance self-check OK")


def main() -> int:
    import sys

    if "--demo" in sys.argv:
        _demo()
        return 0
    from dotenv import load_dotenv
    load_dotenv(".env")
    from db.base import db_session

    with db_session() as session:
        rows = rank_matches(session, reliable_only="--all" not in sys.argv)
    print(f"{'DEVIANCE':>8}  {'ev':>3} {'norm':>4}  {'shift':22}  athlete vs opponent (year)")
    print("-" * 92)
    for r in rows[:40]:
        print(f"{r['deviance']:>8.3f}  {r['n_events']:>3} {r['career_events']:>4}  "
              f"{r['shift']:22}  {r['athlete']} vs {r['opponent']} ({r['year']})")
    print(f"\n{len(rows)} (athlete, bout) rows — highest deviance = most worth a recheck.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
