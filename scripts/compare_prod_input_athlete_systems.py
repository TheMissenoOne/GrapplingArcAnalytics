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

**Bootstrap, and the gap this leaves honest.** Wave 6's bootstrap resamples bouts (the
real observation unit) with replacement. The persisted graph has no bout-level unit to
resample — that absence is exactly what wave 6 flagged as unmeasured. The closest
available unit is the edge itself (``graph_edges`` is already deduplicated to one row
per ``(source, target)``, so there is no per-edge multiplicity to resample from
either): each resample draws ``len(edges)`` edges with replacement — drawing the same
edge twice makes it heavier in that resample, the only lever this input has to vary
structure between resamples. Documented here and in the report section this script
feeds, not swept under the rug.

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
from db.models import Athlete, Graph, GraphEdge, TechniqueNode
from db.repository import graphs_for_clustering
from export.ontology import eligible_grappling_graphs
from scripts.compare_athlete_system_detectors import (
    EXTRA_ATHLETES,
    MIN_BOUTS_FOR_STABILITY,
    NAMED_ATHLETES,
    OLD_PROD_MIN_SYSTEM_SIZE,
    AthleteRow,
    _athlete_by_name,
    _bouts_for_athlete,
    _extra_athletes,
    measure_athlete,
    new_partition,
    old_partition,
)

MIN_EDGES_FOR_STABILITY = 5  # analogous floor to wave 6's MIN_BOUTS_FOR_STABILITY
N_RESAMPLES = 100
SEED = 42


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
    jaccard_new_vs_old: float = 0.0    # both detectors, same prod graph, min_system_size=1
    new_stability: float | None = None
    old_stability: float | None = None
    note: str = ""


def measure_prod_athlete(ag: AthleteGraph, athlete_name: str) -> ProdRow:
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
        row.note = f"< {MIN_EDGES_FOR_STABILITY} arestas — bootstrap não roda"
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
    seq: AthleteRow   # wave 6 — same detector, sequence-derived graph
    prod: ProdRow      # this wave — same detector, persisted production graph
    cross_jaccard: float | None  # old detector's partition, sequence graph vs prod graph


def run() -> list[ComparisonRow]:
    with get_session_factory()() as session:
        named: list[Athlete] = []
        seen_ids: set[str] = set()
        for name in NAMED_ATHLETES:
            a = _athlete_by_name(session, name)
            if a is not None:
                named.append(a)
                seen_ids.add(a.id)
        extra = _extra_athletes(session, seen_ids, EXTRA_ATHLETES, MIN_BOUTS_FOR_STABILITY)
        athletes = named + extra

        eligible = eligible_grappling_graphs(graphs_for_clustering(session, owner_kind="athlete"))
        allowed_by_graph = {gid: {n.node_key for n in nodes} for gid, nodes in eligible}
        owner_rows = session.execute(
            select(Graph.id, Graph.owner_id).where(Graph.owner_kind == "athlete")
        ).all()
        graph_by_athlete_id = {owner_id: gid for gid, owner_id in owner_rows if gid in allowed_by_graph}

        results: list[ComparisonRow] = []
        for athlete in athletes:
            bouts = _bouts_for_athlete(session, athlete)
            seq_row = measure_athlete(athlete.name, bouts)

            graph_id = graph_by_athlete_id.get(athlete.id)
            if graph_id is None:
                results.append(ComparisonRow(
                    seq=seq_row,
                    prod=ProdRow(
                        athlete=athlete.name, graph_id=None,
                        note="sem grafo athlete elegível (owner_kind='athlete' + filtro grappling)",
                    ),
                    cross_jaccard=None,
                ))
                continue

            ag = prod_athlete_graph(session, graph_id, allowed_by_graph[graph_id])
            prod_row = measure_prod_athlete(ag, athlete.name)

            own = sequences_by_athlete({athlete.name: bouts})[athlete.name]
            seq_g = network_from_sequences(own)
            cj = cross_input_jaccard(seq_g, ag, athlete.name)

            results.append(ComparisonRow(seq=seq_row, prod=prod_row, cross_jaccard=cj))
    return results


if __name__ == "__main__":
    for r in run():
        print(r.seq.athlete)
        print("  wave6 (sequência) :", r.seq)
        print("  produção          :", r.prod)
        print("  cross-input jaccard (antigo, min_size=1):", r.cross_jaccard)
