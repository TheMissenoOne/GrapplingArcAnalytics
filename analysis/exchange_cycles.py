"""Exchange cycles — who holds initiative, and who is stuck in a loop.

Two measures over one pass, both absent until now: nothing in ``analysis/`` counted
consecutive same-actor events, and ``network_metrics`` imports networkx without ever calling
a cycle function.

**Run length** — a run is a maximal stretch of consecutive events by one side. A long opponent
run means the athlete absorbed several actions without answering: the defensive burden. A long
own run is the mirror image, uninterrupted work.

    A_guard  B_pass  B_kneeslice  B_pass  A_sweep
             \\____ opponent run = 3 ____/

**Position revisits** — returning to a ``node_key`` already seen in the same exchange. A
defender who keeps arriving back where they started is cycling, not progressing:

    A_guard -> B_pass -> A_recover_guard -> B_pass
                                            ^^^^^^ `pass` seen twice: one revisit

The two are deliberately separate. A player can hold a long run while going nowhere (cycling),
or trade single moves while advancing steadily; neither number implies the other.

Both are bounded by the same things that bound a decision window — ``sequence_boundaries``
(round break, referee stoppage, >120 s gap) and neutral events — so a run never spans a
restart, which would otherwise manufacture enormous fake runs across rounds.

    from analysis.exchange_cycles import athlete_cycle_profile
    prof = athlete_cycle_profile(athlete_id, matches)
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from analysis.perspective_sequence import (
    PerspectiveEvent,
    perspective_events,
    sequence_boundaries,
)

# A run must reach this length to mean "held the exchange" rather than "took a turn".
# 1 is the alternating baseline, so the interesting threshold is 2+.
SUSTAINED_RUN = 2


@dataclass(frozen=True)
class Run:
    actor: str  # "you" | "opponent"
    start_index: int
    node_keys: tuple[str, ...]

    @property
    def length(self) -> int:
        return len(self.node_keys)


@dataclass
class CycleMetrics:
    own_runs: list[int] = field(default_factory=list)
    opp_runs: list[int] = field(default_factory=list)
    own_events: int = 0
    opp_events: int = 0
    revisits: int = 0
    exchanges: int = 0
    loops: Counter[str] = field(default_factory=Counter)

    def merge(self, other: CycleMetrics) -> None:
        self.own_runs += other.own_runs
        self.opp_runs += other.opp_runs
        self.own_events += other.own_events
        self.opp_events += other.opp_events
        self.revisits += other.revisits
        self.exchanges += other.exchanges
        self.loops.update(other.loops)

    def to_json(self) -> dict[str, Any]:
        total = self.own_events + self.opp_events
        return {
            "ownRunMean": round(mean(self.own_runs), 2) if self.own_runs else 0.0,
            "ownRunMax": max(self.own_runs, default=0),
            "oppRunMean": round(mean(self.opp_runs), 2) if self.opp_runs else 0.0,
            "oppRunMax": max(self.opp_runs, default=0),
            # share of events the athlete authored — >0.5 means they drove the exchanges
            "initiativeShare": round(self.own_events / total, 3) if total else 0.0,
            "ownSustainedRuns": sum(1 for r in self.own_runs if r >= SUSTAINED_RUN),
            "oppSustainedRuns": sum(1 for r in self.opp_runs if r >= SUSTAINED_RUN),
            "runs": len(self.own_runs) + len(self.opp_runs),
            "exchanges": self.exchanges,
            "revisits": self.revisits,
            "revisitsPerExchange": (
                round(self.revisits / self.exchanges, 2) if self.exchanges else 0.0
            ),
            "mostRepeated": [
                {"key": k, "revisits": c} for k, c in self.loops.most_common(5)
            ],
        }


def split_exchanges(
    events: list[PerspectiveEvent], boundaries: set[int] | None = None
) -> list[list[PerspectiveEvent]]:
    """Cut at boundaries and neutral events; each piece is one continuous exchange."""
    bounds = boundaries or set()
    out: list[list[PerspectiveEvent]] = []
    current: list[PerspectiveEvent] = []
    for e in events:
        if e.index in bounds or e.actor == "neutral":
            if current:
                out.append(current)
            current = []
            continue
        current.append(e)
    if current:
        out.append(current)
    return out


def runs_of(events: list[PerspectiveEvent]) -> list[Run]:
    """Maximal consecutive same-actor stretches within ONE exchange."""
    if not events:
        return []
    out: list[Run] = []
    actor = events[0].actor
    start = events[0].index
    keys: list[str] = []
    for e in events:
        if e.actor != actor:
            out.append(Run(actor=actor, start_index=start, node_keys=tuple(keys)))
            actor, start, keys = e.actor, e.index, []
        keys.append(e.node_key)
    out.append(Run(actor=actor, start_index=start, node_keys=tuple(keys)))
    return out


def count_revisits(events: list[PerspectiveEvent]) -> tuple[int, Counter[str]]:
    """Returns to a node_key already seen in this exchange, and which keys they were.

    Counts *returns*, not occurrences: a key seen three times is two revisits. An immediate
    repeat (same key twice in a row) is not a revisit — that is one action logged twice or
    held, not a loop back to it.
    """
    seen: set[str] = set()
    loops: Counter[str] = Counter()
    revisits = 0
    previous: str | None = None
    for e in events:
        key = e.node_key
        if key in seen and key != previous:
            revisits += 1
            loops[key] += 1
        seen.add(key)
        previous = key
    return revisits, loops


def cycle_metrics(
    events: list[PerspectiveEvent], boundaries: set[int] | None = None
) -> CycleMetrics:
    """Run lengths + revisits for one athlete's view of one match."""
    m = CycleMetrics()
    for exchange in split_exchanges(events, boundaries):
        m.exchanges += 1
        for run in runs_of(exchange):
            if run.actor == "you":
                m.own_runs.append(run.length)
                m.own_events += run.length
            elif run.actor == "opponent":
                m.opp_runs.append(run.length)
                m.opp_events += run.length
        revisits, loops = count_revisits(exchange)
        m.revisits += revisits
        m.loops.update(loops)
    return m


def athlete_cycle_profile(athlete_id: str, matches: list[Any]) -> dict[str, Any]:
    """Aggregate cycle metrics across an athlete's matches.

    Shaped like ``analysis.defense_rate.defense_profile`` so it drops into the profile
    assembly the dossier already uses.
    """
    total = CycleMetrics()
    counted = 0
    for match in matches:
        if not (getattr(match, "sequence", None) or []):
            continue
        total.merge(
            cycle_metrics(
                perspective_events(match, athlete_id),
                sequence_boundaries(match, athlete_id),
            )
        )
        counted += 1
    payload = total.to_json()
    payload["matches"] = counted
    return payload
