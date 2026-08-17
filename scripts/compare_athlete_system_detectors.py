"""Wave 6 (ADR-08, ``docs/rating_v2/01_DECISOES.md``): measure the old per-athlete detector
(``analysis.athlete_systems``) against the shared one (``analysis.constellations``).

**Read-only.** Only ``SELECT``s against ``matches``/``athletes``; never writes.

Methodology (see ``docs/rating_v2/05_COMPARACAO_DETECTORES.md`` for the write-up):
Both detectors run on the SAME per-bout, own-events-only sequence graph for each athlete
(``db.models.Match.sequence`` -> ``scripts.scouting_division_report._match_to_bout`` ->
``analysis.category_constellations.sequences_by_athlete`` -> ``analysis.transitions
.network_from_sequences``). That is deliberately NOT what ``athlete_systems`` consumes in
prod today (``export/ontology.py`` feeds it a pre-aggregated ``graphs``/``graph_edges`` row,
uniform edge weight=1, no per-bout provenance) — using the persisted graph would confound
"different detector" with "different input", which is exactly what ADR-08's wave asks not to
do. Feeding both detectors the identical bout-derived graph isolates the one variable this
wave is measuring: the algorithm.

Reuses rather than reimplements: ``compare_partitions``/``jaccard``
(``analysis/constellations/compare.py``, built in wave 4/5 and unused until now — this is
its first caller), ``detect`` (``analysis/constellations/detect.py``), ``network_from_sequences``
(``analysis/transitions``), ``sequences_by_athlete``/``_match_to_bout`` (existing DB-to-sequence
glue). New code here is: the DB read, the old-detector adapter (``nx.DiGraph`` ->
``AthleteGraph``), and the shared-resample bootstrap loop (stability.py's own
``bootstrap_jaccard`` is hardcoded to the new detector's ``detect()``, so it can't compare two
detectors within one resample — this file's ``_bootstrap_stability`` runs both on each identical
resample instead of reimplementing the resampling itself).

Usage: uv run python -m scripts.compare_athlete_system_detectors
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

import networkx as nx
from sqlalchemy import func, or_, select

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.athlete_systems import detect_athlete_systems
from analysis.category_constellations import sequences_by_athlete
from analysis.constellations import detect
from analysis.constellations.compare import compare_partitions
from analysis.transitions import network_from_sequences
from db.base import get_session_factory
from db.models import Athlete, Match
from scripts.scouting_division_report import _match_to_bout

NAMED_ATHLETES = ["Gordon Ryan", "Craig Jones", "Leandro Lo", "Kade Ruotolo", "Nick Rodriguez"]
#: extend past the named 5 with whoever else in the DB has enough bouts to bootstrap
EXTRA_ATHLETES = 10
MIN_BOUTS_FOR_STABILITY = 5
N_RESAMPLES = 100
SEED = 42
OLD_PROD_MIN_SYSTEM_SIZE = 2  # analysis.athlete_systems.detect_athlete_systems's own default


# ── DB read (read-only) ──────────────────────────────────────────────────

def _athlete_by_name(session: Any, name: str) -> Athlete | None:
    return session.execute(
        select(Athlete).where(func.lower(Athlete.name) == name.lower())
    ).scalars().first()


def _bouts_for_athlete(session: Any, athlete: Athlete) -> list[dict[str, Any]]:
    matches = session.execute(
        select(Match).where(or_(Match.athlete_a_id == athlete.id, Match.athlete_b_id == athlete.id))
    ).scalars().all()
    ids = {m.athlete_a_id for m in matches} | {m.athlete_b_id for m in matches}
    names = dict(session.execute(select(Athlete.id, Athlete.name).where(Athlete.id.in_(ids))).all())
    bouts = []
    for m in matches:
        name_a, name_b = names.get(m.athlete_a_id), names.get(m.athlete_b_id)
        if not name_a or not name_b:
            continue
        bouts.append(_match_to_bout(m, name_a, name_b, names))
    return bouts


def _extra_athletes(session: Any, exclude_ids: set[str], limit: int, min_bouts: int) -> list[Athlete]:
    """Athletes with the most matches, most-first, excluding ``exclude_ids``."""
    counts: Counter[str] = Counter()
    for (aid,) in session.execute(select(Match.athlete_a_id)):
        counts[aid] += 1
    for (aid,) in session.execute(select(Match.athlete_b_id)):
        counts[aid] += 1
    ranked_ids = [
        aid for aid, c in counts.most_common() if aid not in exclude_ids and c >= min_bouts
    ][:limit]
    rows = {a.id: a for a in session.execute(
        select(Athlete).where(Athlete.id.in_(ranked_ids))
    ).scalars().all()}
    return [rows[i] for i in ranked_ids if i in rows]


# ── old-detector adapter: identical graph in, AthleteGraph shape out ────────

def _digraph_to_athlete_graph(g: nx.DiGraph, athlete: str) -> AthleteGraph:
    ag = AthleteGraph(athlete=athlete)
    for n, d in g.nodes(data=True):
        ag.nodes[n] = AthleteNode(label=n, type=str(d.get("type", "")), count=max(int(d.get("occ", 1)), 1))
    for u, v, d in g.edges(data=True):
        ag.edges[(u, v)] = AthleteEdge(source=u, target=v, count=max(int(round(d.get("weight", 1.0))), 1))
    return ag


def old_partition(g: nx.DiGraph, athlete: str, min_system_size: int) -> list[list[str]]:
    """Old detector's partition as member lists.

    ``detect_athlete_systems`` silently drops any community smaller than
    ``min_system_size`` (nodes vanish, not "become their own community"). At
    ``min_system_size=1`` this adapter tops the dropped nodes back up as singleton
    communities, so the partition covers every graph node exactly once — required to
    Jaccard-compare against the new detector, which never drops a node. At
    ``min_system_size=2`` (prod's own default) nothing is topped up: that IS the
    real coverage gap this wave measures.
    """
    ag = _digraph_to_athlete_graph(g, athlete)
    systems = detect_athlete_systems(ag, min_system_size=min_system_size)
    out = [list(s.members) for s in systems]
    if min_system_size <= 1:
        grouped = {n for members in out for n in members}
        out += [[n] for n in sorted(set(g.nodes) - grouped)]
    return out


def new_partition(g: nx.DiGraph) -> tuple[list[list[str]], Any]:
    result = detect(g)
    return [c.members for c in result.constellations], result


# ── shared-resample bootstrap: both detectors on the identical resample ─────

def bootstrap_stability(
    own_sequences: list[list[dict[str, Any]]], athlete: str,
    n_resamples: int = N_RESAMPLES, seed: int = SEED,
) -> tuple[float, float]:
    """Mean best-match Jaccard of each bootstrap resample's partition against the baseline
    partition, for both detectors — same resampled bout list feeds both per iteration, so the
    only source of any gap between the two numbers is the algorithm, not sampling noise.

    Both detectors run at ``min_system_size=1`` here (singletons included) — the ADR-03-style
    "stability" question is about partition structure, not about the prod coverage cutoff.
    Returns ``(new_mean_jaccard, old_mean_jaccard)``, 0.0/0.0 if there are no bouts.
    """
    n = len(own_sequences)
    if n == 0:
        return 0.0, 0.0
    rng = random.Random(seed)
    base_g = network_from_sequences(own_sequences)
    base_new, _ = new_partition(base_g)
    base_old = old_partition(base_g, athlete, min_system_size=1)
    new_total = old_total = 0.0
    for _ in range(n_resamples):
        sample = [rng.choice(own_sequences) for _ in range(n)]
        g = network_from_sequences(sample)
        new_p, _ = new_partition(g)
        old_p = old_partition(g, athlete, min_system_size=1)
        new_total += compare_partitions(base_new, new_p).mean_jaccard
        old_total += compare_partitions(base_old, old_p).mean_jaccard
    return round(new_total / n_resamples, 5), round(old_total / n_resamples, 5)


# ── per-athlete row ───────────────────────────────────────────────────────

@dataclass
class AthleteRow:
    athlete: str
    n_bouts: int
    n_nodes: int
    n_edges: int
    new_n_constellations: int = 0
    new_largest: int = 0
    new_singleton_share: float = 0.0
    new_modularity: float = 0.0
    new_rejected_rate: float = 0.0
    old_n_systems: int = 0
    old_largest: int = 0
    old_coverage: float = 0.0        # fraction of nodes inside SOME system (prod min_size=2)
    jaccard_new_vs_old: float = 0.0  # symmetric average, min_system_size=1 both sides
    new_stability: float | None = None
    old_stability: float | None = None
    no_structure_new: bool = False
    no_structure_old: bool = False
    note: str = ""


def measure_athlete(athlete: str, bouts: list[dict[str, Any]]) -> AthleteRow:
    own = sequences_by_athlete({athlete: bouts})[athlete]
    g = network_from_sequences(own)
    row = AthleteRow(athlete=athlete, n_bouts=len(own), n_nodes=g.number_of_nodes(), n_edges=g.number_of_edges())
    if g.number_of_nodes() == 0:
        row.note = "sem eventos próprios no corpus"
        return row

    new_p, new_res = new_partition(g)
    new_sizes = sorted((len(c) for c in new_p), reverse=True)
    row.new_n_constellations = len(new_p)
    row.new_largest = new_sizes[0] if new_sizes else 0
    row.new_singleton_share = round(sum(1 for s in new_sizes if s == 1) / len(new_sizes), 4) if new_sizes else 0.0
    row.new_modularity = new_res.modularity
    row.new_rejected_rate = new_res.rejected_rate
    row.no_structure_new = row.new_largest <= 1

    old_p = old_partition(g, athlete, min_system_size=OLD_PROD_MIN_SYSTEM_SIZE)
    old_sizes = sorted((len(c) for c in old_p), reverse=True)
    row.old_n_systems = len(old_p)
    row.old_largest = old_sizes[0] if old_sizes else 0
    covered = sum(len(c) for c in old_p)
    row.old_coverage = round(covered / g.number_of_nodes(), 4)
    row.no_structure_old = row.old_n_systems == 0

    old_p1 = old_partition(g, athlete, min_system_size=1)
    a_vs_b = compare_partitions(new_p, old_p1).mean_jaccard
    b_vs_a = compare_partitions(old_p1, new_p).mean_jaccard
    row.jaccard_new_vs_old = round((a_vs_b + b_vs_a) / 2, 5)

    if row.n_bouts >= MIN_BOUTS_FOR_STABILITY:
        row.new_stability, row.old_stability = bootstrap_stability(own, athlete)
    else:
        row.note = f"< {MIN_BOUTS_FOR_STABILITY} lutas próprias — bootstrap não roda"
    return row


# ── orchestration ────────────────────────────────────────────────────────

def run() -> list[AthleteRow]:
    with get_session_factory()() as session:
        named: list[Athlete] = []
        seen_ids: set[str] = set()
        for name in NAMED_ATHLETES:
            a = _athlete_by_name(session, name)
            if a is not None:
                named.append(a)
                seen_ids.add(a.id)
        extra = _extra_athletes(session, seen_ids, EXTRA_ATHLETES, MIN_BOUTS_FOR_STABILITY)

        rows: list[AthleteRow] = []
        for athlete in named + extra:
            bouts = _bouts_for_athlete(session, athlete)
            rows.append(measure_athlete(athlete.name, bouts))
    return rows


if __name__ == "__main__":
    for r in run():
        print(r)
