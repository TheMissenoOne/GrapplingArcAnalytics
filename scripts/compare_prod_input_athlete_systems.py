"""Wave-6 follow-up (ADR-08, ``docs/rating_v2/01_DECISOES.md``): measure the old
per-athlete detector (``analysis.athlete_systems``) on its REAL production input, and
compare against the wave-6 numbers (same detector, sequence-derived graph).

**Read-only.** Only ``SELECT``s against ``graphs``/``graph_edges``/``technique_nodes``/
``athletes``/``matches``; never writes. Does not touch ``analysis/athlete_systems.py``.

Wave 6 (``docs/rating_v2/05_COMPARACAO_DETECTORES.md``) deliberately fed both detectors
the SAME sequence-derived graph to isolate the algorithm as the only variable, and
flagged as unmeasured that in production ``athlete_systems`` never sees that graph — it
consumes the persisted ``graphs``/``graph_edges`` row via ``export/ontology.py``: edge
weight forced to 1 (``graph_edges`` has no transition-occurrence column), no per-bout
provenance, grappling-only nodes (``export.ontology.eligible_grappling_graphs`` filter).
This script measures the old detector on THAT input, for the SAME 15 athletes wave 6
already measured — it reuses ``scripts.compare_athlete_system_detectors``'s athlete
selection + ``measure_athlete`` to get the wave-6 numbers fresh from the same DB
snapshot, rather than retyping the published table.

**Bootstrap — two units, and why both are reported.** Wave 6's bootstrap resamples bouts
(the real observation unit) with replacement.

*Edge unit (wave 6b, kept for comparability).* When 6b ran, the persisted graph had no
bout-level unit to resample, so the closest available unit was the edge itself
(``graph_edges`` is deduplicated to one row per ``(source, target)``): each resample
draws ``len(edges)`` edges with replacement — drawing the same edge twice makes it
heavier in that resample. 6b said plainly that deciding on that number would repeat the
error it had just exposed, and ADR-08 was reopened on exactly this point.

*Bout unit (wave 6c, this pass — the ADR-08 (a) condition).* ``graph_edge_bouts``
(alembic 0046) is that missing unit: one row per ``(graph edge, match)``. A resample now
draws ``len(bouts)`` BOUTS with replacement and rebuilds the edge set from what it drew,
the same shape as wave 6 — an unsampled bout takes its edges with it, which is real
structural variation instead of a reweighting artefact. Provenance records presence, not
per-bout transition count, so the default resample keeps production's uniform weight 1
(``weighted=False``: the full-bout draw reproduces the production graph exactly, so the
baseline IS the input under test). ``weighted=True`` is reported alongside as a
sensitivity check — weight = number of sampled bouts containing the edge — because the
whole reason this ADR reopened was a number that turned out to depend on the input's
shape.

Both bootstraps run against the SAME DB snapshot in one process, because the live prod DB
drifts between runs (see the report's "drift do banco vivo").

**Direct cross-input comparison.** For each athlete, also runs the old detector on
BOTH graphs (sequence-derived, persisted) and reports the Jaccard between the two
resulting partitions directly — this is what answers "does the entry change the
answer for the same athlete", independent of whichever detector is used.

Usage: uv run python -m scripts.compare_prod_input_athlete_systems
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.athlete_systems import athlete_graph_to_nx
from analysis.category_constellations import sequences_by_athlete
from analysis.constellations.compare import compare_partitions
from analysis.names import _normalize_name, canonicalize
from analysis.transitions import network_from_sequences
from db.base import get_session_factory
from db.models import Athlete, Graph, GraphEdge, GraphEdgeBout, TechniqueNode
from db.repository import graphs_for_clustering
from export.ontology import eligible_grappling_graphs
from scripts.compare_athlete_system_detectors import (
    MIN_BOUTS_FOR_STABILITY,
    OLD_PROD_MIN_SYSTEM_SIZE,
    AthleteRow,
    _athlete_by_name,
    _bouts_for_athlete,
    measure_athlete,
    new_partition,
    old_partition,
)

MIN_EDGES_FOR_STABILITY = 5  # analogous floor to wave 6's MIN_BOUTS_FOR_STABILITY
N_RESAMPLES = 100
SEED = 42

#: The 15 athletes waves 6 and 6b published, pinned BY NAME.
#:
#: Wave 6 selected 5 named + "the 10 with the most ``matches`` rows"
#: (``compare_athlete_system_detectors._extra_athletes``). That tail is not reproducible:
#: the corpus keeps growing, and re-running the same code in 2026-08-19 returned Cam Hurd
#: and Lucas Barbosa in place of Roberto Jimenez and Victor Hugo. Comparing a new bootstrap
#: unit against the published table requires the cohort to be the constant it was assumed
#: to be — otherwise a cohort swap is charged to the instrument. Extending the corpus is
#: what ``run(extend=True)`` is for, deliberately and separately.
WAVE6_COHORT = [
    "Gordon Ryan", "Craig Jones", "Leandro Lo", "Kade Ruotolo", "Nick Rodriguez",
    "Tye Ruotolo", "Felipe Pena", "Giancarlo Bodoni", "Helena Crevar", "Mica Galvão",
    "Vagner Rocha", "Roberto Jimenez", "Jake Strauss", "Shawn Melanson", "Victor Hugo",
]


# ── production-input graph builder ───────────────────────────────────────
# Mirrors export.ontology._athlete_systems_by_graph's construction exactly (grappling-
# filtered nodes, uniform edge weight 1, self-loops + out-of-set edges dropped) —
# replicated here (not imported) because that function returns finished system JSON,
# not the intermediate AthleteGraph this script needs to run both detectors + bootstrap.

def prod_athlete_graph(session: Any, graph_id: str, allowed_node_keys: set[str]) -> AthleteGraph:
    """The AthleteGraph ``athlete_systems`` actually receives in production for one
    athlete graph: ``allowed_node_keys`` (grappling-filtered, from
    ``export.ontology.eligible_grappling_graphs``) as nodes, this graph's edges among
    them at weight 1."""
    node_types = dict(session.execute(
        select(TechniqueNode.node_key, TechniqueNode.node_type)
        .where(TechniqueNode.node_key.in_(allowed_node_keys))
    ).all())
    labels = dict(session.execute(
        select(TechniqueNode.node_key, TechniqueNode.label)
        .where(TechniqueNode.node_key.in_(allowed_node_keys))
    ).all())
    ag = AthleteGraph(athlete=str(graph_id))
    for k in allowed_node_keys:
        ag.nodes[k] = AthleteNode(label=labels.get(k, k), type=node_types.get(k, ""), count=1)
    edges = session.execute(select(GraphEdge).where(GraphEdge.graph_id == graph_id)).scalars()
    for e in edges:
        if e.source_key == e.target_key or e.source_key not in ag.nodes or e.target_key not in ag.nodes:
            continue
        ag.edges[(e.source_key, e.target_key)] = AthleteEdge(source=e.source_key, target=e.target_key, count=1)
    return ag


# ── bootstrap: resample the edge list (the only observation-like unit this input has) ──

def _rebuild(ag: AthleteGraph, edge_keys: list[tuple[str, str]]) -> AthleteGraph:
    """Rebuild an AthleteGraph from a (possibly-repeated) sample of ``ag``'s edge keys —
    repeat count becomes the resampled edge's weight."""
    out = AthleteGraph(athlete=ag.athlete)
    counts: dict[tuple[str, str], int] = {}
    for key in edge_keys:
        counts[key] = counts.get(key, 0) + 1
    touched = {n for pair in counts for n in pair}
    for n in touched:
        src = ag.nodes[n]
        out.nodes[n] = AthleteNode(label=src.label, type=src.type, count=src.count)
    for (s, t), c in counts.items():
        out.edges[(s, t)] = AthleteEdge(source=s, target=t, count=c)
    return out


def bootstrap_prod_stability(
    ag: AthleteGraph, n_resamples: int = N_RESAMPLES, seed: int = SEED,
) -> tuple[float, float]:
    """Mirrors ``compare_athlete_system_detectors.bootstrap_stability`` (same resample
    feeds both detectors per iteration, same instrument — ``compare_partitions``) but
    the resampled unit is edges, not bouts (see module docstring). Returns
    ``(new_mean_jaccard, old_mean_jaccard)``, 0.0/0.0 if there are no edges."""
    edge_keys = list(ag.edges.keys())
    n = len(edge_keys)
    if n == 0:
        return 0.0, 0.0
    rng = random.Random(seed)
    base_g = athlete_graph_to_nx(ag)
    base_new_p, _ = new_partition(base_g)
    base_old_p = old_partition(base_g, ag.athlete, min_system_size=1)
    new_total = old_total = 0.0
    for _ in range(n_resamples):
        sample = [rng.choice(edge_keys) for _ in range(n)]
        g = athlete_graph_to_nx(_rebuild(ag, sample))
        new_p, _ = new_partition(g)
        old_p = old_partition(g, ag.athlete, min_system_size=1)
        new_total += compare_partitions(base_new_p, new_p).mean_jaccard
        old_total += compare_partitions(base_old_p, old_p).mean_jaccard
    return round(new_total / n_resamples, 5), round(old_total / n_resamples, 5)


# ── bootstrap: resample BOUTS, the real observation unit (alembic 0046) ────

def edges_by_bout(
    session: Any, graph_id: str, edges: set[tuple[str, str]],
) -> dict[str, list[tuple[str, str]]]:
    """``match_id -> the graph's edges observed in that bout`` (``graph_edge_bouts``),
    restricted to ``edges`` — the grappling-filtered set the detectors actually receive,
    so provenance for an edge the production input drops cannot re-enter through the
    bootstrap."""
    rows = session.execute(
        select(GraphEdgeBout.match_id, GraphEdgeBout.source_key, GraphEdgeBout.target_key)
        .where(GraphEdgeBout.graph_id == graph_id)
    ).all()
    out: dict[str, list[tuple[str, str]]] = {}
    for match_id, source_key, target_key in rows:
        if (source_key, target_key) in edges:
            out.setdefault(match_id, []).append((source_key, target_key))
    return out


def _graph_from_bouts(
    ag: AthleteGraph, by_bout: dict[str, list[tuple[str, str]]], bouts: list[str], weighted: bool,
) -> AthleteGraph:
    """Rebuild the athlete graph from a (possibly-repeated) draw of bout ids.

    ``weighted=False`` keeps production's uniform edge weight — an edge is present iff
    some drawn bout contains it — so drawing every bout exactly once reproduces the
    production graph itself. ``weighted=True`` lets a repeated bout make its edges
    heavier, the closest thing to wave 6's transition counts that presence-only
    provenance can express.
    """
    keys = [e for b in bouts for e in by_bout[b]]
    return _rebuild(ag, sorted(set(keys)) if not weighted else keys)


def bootstrap_bout_stability(
    ag: AthleteGraph,
    by_bout: dict[str, list[tuple[str, str]]],
    weighted: bool = False,
    n_resamples: int = N_RESAMPLES,
    seed: int = SEED,
) -> tuple[float, float] | None:
    """Wave 6's instrument on the production input: ``len(bouts)`` bouts drawn with
    replacement, the edge set rebuilt from the draw, the SAME resample feeding both
    detectors each iteration. Returns ``(new_mean_jaccard, old_mean_jaccard)``, or
    ``None`` when there are no bouts with provenance.

    The baseline is the full-bout draw under the identical construction, not ``ag`` — at
    ``weighted=False`` those are the same graph, and at ``weighted=True`` using ``ag``
    would compare a uniform-weight baseline against weighted resamples and charge the
    weighting convention as instability.

    Not reusing ``measure_community_stability_under_weight.resample_partitions``,
    despite it being generic in its units: it hardcodes the NEW detector's ``detect()``,
    so it cannot run both detectors on one identical resample — the invariant wave 6
    established so the gap between the two numbers is the algorithm and not sampling
    noise.
    """
    bouts = sorted(by_bout)
    n = len(bouts)
    if n == 0:
        return None
    rng = random.Random(seed)
    base_g = athlete_graph_to_nx(_graph_from_bouts(ag, by_bout, bouts, weighted))
    base_new_p, _ = new_partition(base_g)
    base_old_p = old_partition(base_g, ag.athlete, min_system_size=1)
    new_total = old_total = 0.0
    for _ in range(n_resamples):
        sample = [rng.choice(bouts) for _ in range(n)]
        g = athlete_graph_to_nx(_graph_from_bouts(ag, by_bout, sample, weighted))
        new_p, _ = new_partition(g)
        old_p = old_partition(g, ag.athlete, min_system_size=1)
        new_total += compare_partitions(base_new_p, new_p).mean_jaccard
        old_total += compare_partitions(base_old_p, old_p).mean_jaccard
    return round(new_total / n_resamples, 5), round(old_total / n_resamples, 5)


# ── per-athlete row (production input) ───────────────────────────────────

@dataclass
class ProdRow:
    athlete: str
    graph_id: str | None
    n_nodes: int = 0
    n_edges: int = 0
    old_n_systems: int = 0
    old_largest: int = 0
    old_coverage: float = 0.0          # fraction of nodes inside SOME system (prod min_size=2)
    new_n_constellations: int = 0
    new_largest: int = 0
    new_singleton_share: float = 0.0
    n_bouts: int = 0                   # distinct bouts with provenance for this graph
    jaccard_new_vs_old: float = 0.0    # both detectors, same prod graph, min_system_size=1
    new_stability: float | None = None       # edge unit (wave 6b)
    old_stability: float | None = None
    new_stability_bout: float | None = None  # bout unit, uniform weight (wave 6c, primary)
    old_stability_bout: float | None = None
    new_stability_bout_w: float | None = None  # bout unit, bout-count weight (sensitivity)
    old_stability_bout_w: float | None = None
    note: str = ""


def measure_prod_athlete(
    ag: AthleteGraph,
    athlete_name: str,
    by_bout: dict[str, list[tuple[str, str]]] | None = None,
) -> ProdRow:
    row = ProdRow(athlete=athlete_name, graph_id=ag.athlete, n_nodes=len(ag.nodes), n_edges=len(ag.edges))
    if not ag.edges:
        row.note = "grafo de produção sem arestas grappling"
        return row

    g = athlete_graph_to_nx(ag)

    new_p, _new_res = new_partition(g)
    new_sizes = sorted((len(c) for c in new_p), reverse=True)
    row.new_n_constellations = len(new_p)
    row.new_largest = new_sizes[0] if new_sizes else 0
    row.new_singleton_share = round(sum(1 for s in new_sizes if s == 1) / len(new_sizes), 4) if new_sizes else 0.0

    old_p = old_partition(g, athlete_name, min_system_size=OLD_PROD_MIN_SYSTEM_SIZE)
    old_sizes = sorted((len(c) for c in old_p), reverse=True)
    row.old_n_systems = len(old_p)
    row.old_largest = old_sizes[0] if old_sizes else 0
    row.old_coverage = round(sum(len(c) for c in old_p) / g.number_of_nodes(), 4)

    old_p1 = old_partition(g, athlete_name, min_system_size=1)
    a_vs_b = compare_partitions(new_p, old_p1).mean_jaccard
    b_vs_a = compare_partitions(old_p1, new_p).mean_jaccard
    row.jaccard_new_vs_old = round((a_vs_b + b_vs_a) / 2, 5)

    if row.n_edges >= MIN_EDGES_FOR_STABILITY:
        row.new_stability, row.old_stability = bootstrap_prod_stability(ag)
    else:
        row.note = f"< {MIN_EDGES_FOR_STABILITY} arestas — bootstrap por aresta não roda"

    by_bout = by_bout or {}
    row.n_bouts = len(by_bout)
    if row.n_bouts >= MIN_BOUTS_FOR_STABILITY:
        plain = bootstrap_bout_stability(ag, by_bout, weighted=False)
        weighted = bootstrap_bout_stability(ag, by_bout, weighted=True)
        if plain and weighted:
            row.new_stability_bout, row.old_stability_bout = plain
            row.new_stability_bout_w, row.old_stability_bout_w = weighted
    else:
        row.note = (
            f"{row.note}; " if row.note else ""
        ) + f"{row.n_bouts} lutas com proveniência (< {MIN_BOUTS_FOR_STABILITY}) — bootstrap por luta não roda"
    return row


def _to_node_key_space(partition: list[list[str]]) -> list[list[str]]:
    """``network_from_sequences`` nodes are ``analysis.technique_match.clean_label``'s
    canonical DISPLAY label (e.g. ``"Closed Guard"``); production ``node_key``s are
    ``canonicalize(_normalize_name(label))`` (e.g. ``"closed guard"``, synonym-collapsed
    — see ``analysis.athlete_elo.replay_matches``, the persisted graph's own builder).
    Comparing the two partitions directly would Jaccard on disjoint string spaces (every
    node "different" by casing alone, not by structure) — this maps a sequence-graph
    partition into the SAME key space production uses, so the comparison measures actual
    structural agreement."""
    return [[canonicalize(_normalize_name(n)) for n in members] for members in partition]


def cross_input_jaccard(seq_g: Any, prod_ag: AthleteGraph, athlete_name: str) -> float | None:
    """Old detector, ``min_system_size=1``, run on the SAME athlete's two different
    graphs (sequence-derived vs persisted) — the direct answer to "does the entry
    change the partition for this athlete", independent of which detector is used.
    ``None`` if either graph has no edges (nothing to compare)."""
    if seq_g.number_of_edges() == 0 or not prod_ag.edges:
        return None
    seq_p = _to_node_key_space(old_partition(seq_g, athlete_name, min_system_size=1))
    prod_p = old_partition(athlete_graph_to_nx(prod_ag), athlete_name, min_system_size=1)
    a_vs_b = compare_partitions(seq_p, prod_p).mean_jaccard
    b_vs_a = compare_partitions(prod_p, seq_p).mean_jaccard
    return round((a_vs_b + b_vs_a) / 2, 5)


# ── orchestration ────────────────────────────────────────────────────────

@dataclass
class ComparisonRow:
    seq: AthleteRow | None  # wave 6 — same detector, sequence-derived graph (wave-6 cohort only)
    prod: ProdRow      # this wave — same detector, persisted production graph
    cross_jaccard: float | None  # old detector's partition, sequence graph vs prod graph
    cohort: str = "wave6"  # "wave6" (the 15 already published) | "extended"


def _athletes_with_bout_provenance(session: Any, min_bouts: int) -> list[Athlete]:
    """Every athlete whose persisted graph carries ``>= min_bouts`` distinct bouts in
    ``graph_edge_bouts`` — the ADR-08 (b) universe, which the bout unit itself bounds:
    below the floor a bout resample has nothing to vary."""
    counts: dict[str, set[str]] = {}
    rows = session.execute(
        select(Graph.owner_id, GraphEdgeBout.match_id)
        .join(Graph, Graph.id == GraphEdgeBout.graph_id)
        .where(Graph.owner_kind == "athlete")
    ).all()
    for owner_id, match_id in rows:
        counts.setdefault(owner_id, set()).add(match_id)
    ids = sorted(a for a, m in counts.items() if len(m) >= min_bouts)
    by_id = {
        a.id: a
        for a in session.execute(select(Athlete).where(Athlete.id.in_(ids))).scalars().all()
    }
    return [by_id[i] for i in ids if i in by_id and by_id[i].name]


def run(extend: bool = False) -> list[ComparisonRow]:
    """``extend=False`` → the 15 athletes waves 6/6b published, with their sequence-side
    numbers recomputed from the same snapshot. ``extend=True`` adds every other athlete
    that clears the bout floor (ADR-08 condition (b)); those rows carry prod-input
    numbers only — the sequence-side bootstrap is wave 6's axis, not this one's, and
    running it over the extension would cost minutes to answer a question already
    answered."""
    with get_session_factory()() as session:
        athletes: list[Athlete] = []
        for name in WAVE6_COHORT:
            a = _athlete_by_name(session, name)
            if a is not None:
                athletes.append(a)
        missing = len(WAVE6_COHORT) - len(athletes)
        if missing:
            print(f"AVISO: {missing} atleta(s) da coorte da wave 6 não estão mais no banco")
        wave6_ids = {a.id for a in athletes}
        if extend:
            athletes += [
                a for a in _athletes_with_bout_provenance(session, MIN_BOUTS_FOR_STABILITY)
                if a.id not in wave6_ids
            ]

        eligible = eligible_grappling_graphs(graphs_for_clustering(session, owner_kind="athlete"))
        allowed_by_graph = {gid: {n.node_key for n in nodes} for gid, nodes in eligible}
        owner_rows = session.execute(
            select(Graph.id, Graph.owner_id).where(Graph.owner_kind == "athlete")
        ).all()
        graph_by_athlete_id = {owner_id: gid for gid, owner_id in owner_rows if gid in allowed_by_graph}

        results: list[ComparisonRow] = []
        for athlete in athletes:
            in_wave6 = athlete.id in wave6_ids
            cohort = "wave6" if in_wave6 else "extended"
            bouts = _bouts_for_athlete(session, athlete) if in_wave6 else []
            seq_row = measure_athlete(athlete.name, bouts) if in_wave6 else None

            graph_id = graph_by_athlete_id.get(athlete.id)
            if graph_id is None:
                results.append(ComparisonRow(
                    seq=seq_row,
                    prod=ProdRow(
                        athlete=athlete.name, graph_id=None,
                        note="sem grafo athlete elegível (owner_kind='athlete' + filtro grappling)",
                    ),
                    cross_jaccard=None,
                    cohort=cohort,
                ))
                continue

            ag = prod_athlete_graph(session, graph_id, allowed_by_graph[graph_id])
            by_bout = edges_by_bout(session, graph_id, set(ag.edges))
            prod_row = measure_prod_athlete(ag, athlete.name, by_bout)

            cj = None
            if seq_row is not None:
                own = sequences_by_athlete({athlete.name: bouts})[athlete.name]
                cj = cross_input_jaccard(network_from_sequences(own), ag, athlete.name)

            results.append(
                ComparisonRow(seq=seq_row, prod=prod_row, cross_jaccard=cj, cohort=cohort)
            )
    return results


if __name__ == "__main__":
    import sys

    for r in run(extend="--extend" in sys.argv):
        print(f"{r.prod.athlete}  [{r.cohort}]")
        if r.seq is not None:
            print("  wave6 (sequência) :", r.seq)
            print("  cross-input jaccard (antigo, min_size=1):", r.cross_jaccard)
        print("  produção          :", r.prod)
