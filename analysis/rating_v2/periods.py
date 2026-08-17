"""Group bouts into rating periods and replay them — pure, zero I/O.

Period = year (ADR-04: no per-bout date exists yet). ``run_periods`` advances every
pre-seeded athlete through every period spanned by the input bouts, in period order; all
bouts inside one period use the state each athlete held at the START of that period, so the
order bouts arrive in the input list never changes the result (ADR-04's "order of import
must not change the outcome").

An athlete who is seeded but has no bout in a period still widens for inactivity (Glicko-2
RD growth), because they are present in every period's pass regardless of activity —
consistent through the whole span of periods the replay covers.

**Widening semantics (decided 2026-08-17, keep as-is):** an athlete widens all the way to
the LAST period the replay covers, not just to their own last bout. This is deliberate: the
rating answers "how much confidence do we have TODAY", not "what was their rating when they
last competed". An athlete whose only fight was in 2010 must show a huge RD now — that is
the correct answer to the question the leaderboard is asking. Stopping widening at an
athlete's own last bout would answer a different question ("rating as of their last fight")
and silently understate today's uncertainty for everyone who has gone quiet. Changing this
is an ``engine_version`` bump + full replay (ADR-02), not a config tweak.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.glicko2 import update_period
from analysis.rating_v2.models import Observation, RatingState


@dataclass(frozen=True)
class Bout:
    """One eligible result, already resolved to a period and a score for athlete_a.

    ``score_a``: 1.0 (a won) / 0.0 (b won) / 0.5 (draw). Decisions with no winner never
    become a ``Bout`` (ADR-06) — that filtering happens upstream in ``replay.py``.
    """

    period: int
    athlete_a: str
    athlete_b: str
    score_a: float


def run_periods(
    seeds: Mapping[str, RatingState],
    bouts: Sequence[Bout],
    config: EngineConfig,
) -> dict[str, RatingState]:
    """Advance every seeded athlete through every period the bouts span."""
    if not bouts:
        return dict(seeds)

    periods = sorted({b.period for b in bouts})
    states: dict[str, RatingState] = dict(seeds)

    for period in periods:
        period_bouts = [b for b in bouts if b.period == period]
        pre_period = dict(states)  # snapshot — every bout this period reads this, not `states`
        observations: dict[str, list[Observation]] = defaultdict(list)

        for b in period_bouts:
            a_state = pre_period.get(b.athlete_a)
            b_state = pre_period.get(b.athlete_b)
            if a_state is None or b_state is None:
                continue  # both participants must be seeded before period 1 (replay.py's job)
            observations[b.athlete_a].append(
                Observation(b_state.rating, b_state.deviation, b.score_a)
            )
            observations[b.athlete_b].append(
                Observation(a_state.rating, a_state.deviation, 1.0 - b.score_a)
            )

        for athlete_id, state in pre_period.items():
            states[athlete_id] = update_period(
                state, observations.get(athlete_id, []), tau=config.tau
            )

    return states
