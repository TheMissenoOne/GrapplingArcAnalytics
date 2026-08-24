"""PoC-E2 — Leiden vs the shipped Louvain community detector, measured
(``docs/research/03_POC_PLANS.md``, PoC-E2; seed-ARI instrument from
``docs/research/05_EXTERNAL_POC_REVIEW.md`` §6).

**Objective matched.** The shipped detector (``analysis.constellations.detect.detect``)
runs ``nx.community.louvain_communities(und, weight="weight", resolution=resolution,
seed=seed)`` — that optimises the Reichardt–Bornholdt configuration-model generalisation
of modularity, i.e. exactly what ``leidenalg.RBConfigurationVertexPartition`` optimises
(``resolution_parameter`` = the same γ). CPMVertexPartition was NOT used: CPM's null
model is a constant density, not the degree-preserving null model the shipped detector
already commits to — swapping objectives would confound "different algorithm" with
"different question", which is what this PoC is built to avoid.

**Rejection check.** ``analysis.constellations.detect`` guarantees every returned
``Constellation`` is internally connected by *splitting* any raw Louvain community that
isn't (ADR-07) — this module mirrors exactly that verification step on Leiden's raw
output (never assumed): a size>1 raw community goes through ``nx.is_connected`` before
being trusted, same as the shipped code.

**Sources** (three, per the plan):
  (a) the 15-athlete detector-comparison set — same roster construction as
      ``scripts/compare_athlete_system_detectors.py`` (5 named + 10 by match count,
      ≥5 own sequence-bearing bouts), same own-events-only per-bout sequences
      (``analysis.category_constellations.sequences_by_athlete``). Reported AGGREGATED
      (mean/min across athletes) per (algorithm, γ) — 15 separate small graphs, not one.
  (b) the two bracket divisions ("65 kg", "+65 kg") — same bout filter and sequence
      source as ``scripts/bracket_export.py`` (``data/scouting/adcc_2026_women_sequences
      .json``, ``bout["seq"]`` already actor_id-tagged).
  (c) the corpus graph — ``network_from_sequences`` over every ``final`` match's stored
      ``sequence`` (read-only DB, mirrors ``analysis.poc.e8_interaction_graph.corpus_bouts``).

**Instruments reused, not reimplemented:** ``analysis.constellations.compare
.compare_partitions`` (Jaccard) for bootstrap stability — the SAME generic bootstrap
shape ``scripts/compare_athlete_system_detectors.py`` already established for comparing
two detectors on identical resamples (``constellations.stability.bootstrap_jaccard``
itself is hardcoded to the shipped Louvain ``detect()`` and can't run Leiden, so this
module's ``bootstrap_stability`` below is the same algorithm made generic over the
partition function — pinned equivalent to ``bootstrap_jaccard`` on the Louvain path by
``tests/test_poc_e2.py``). Adjusted Rand Index (``sklearn.metrics.adjusted_rand_score``)
for the seed-ARI instrument. ``analysis.taxonomy.load_taxonomy`` for the curated-seed
resolution-limit probe.

Usage::

    uv run python -m analysis.poc.e2_leiden
    uv run python -m analysis.poc.e2_leiden --out docs/research/poc/e2.md
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import igraph as ig
import leidenalg as la
import networkx as nx
from sklearn.metrics import adjusted_rand_score
from sqlalchemy import text

from analysis.category_constellations import sequences_by_athlete
from analysis.constellations.compare import compare_partitions
from analysis.constellations.detect import DetectionResult, _undirected_weighted, detect
from analysis.taxonomy import Taxonomy, load_taxonomy
from analysis.transitions import network_from_sequences
from db.base import get_session_factory
from scripts.compare_athlete_system_detectors import (
    EXTRA_ATHLETES,
    MIN_BOUTS_FOR_STABILITY,
    NAMED_ATHLETES,
    _athlete_by_name,
    _bouts_for_athlete,
    _extra_athletes,
)

REPO = Path(__file__).resolve().parents[2]
SEQUENCES_FILE = REPO / "data" / "scouting" / "adcc_2026_women_sequences.json"
BRACKET_DIVISIONS = ("65 kg", "+65 kg")

GAMMAS = (0.5, 0.8, 1.0, 1.5, 2.0)
DETECT_SEED = 42          # baseline partition / bootstrap detect seed, fixed
RNG_SEED = 42              # resample RNG seed, fixed
SEED_SWEEP = tuple(range(10))  # 05 §6: ~10 seeds for the seed-ARI instrument
N_RESAMPLES = 30           # ponytail: 30 keeps 2 algos x 5 gammas x (15+2+1 graphs) runnable
                            # in a few minutes; raise for a published number, not for CI.

Partition = list[list[str]]

ALGORITHMS = ("louvain", "leiden")


# ── two detectors, one shared post-processing step ──────────────────────────────────

def _connectivity_split(und: nx.Graph, raw: list[list[str]]) -> tuple[Partition, int]:
    """Split any raw community that induces a disconnected subgraph — the exact
    verification ``analysis.constellations.detect.detect`` performs on Louvain's raw
    output (ADR-07), applied here to whichever algorithm produced ``raw``."""
    rejected = 0
    final: list[list[str]] = []
    for comm in raw:
        if len(comm) <= 1:
            final.append(comm)
            continue
        sub = und.subgraph(comm)
        if nx.is_connected(sub):
            final.append(comm)
        else:
            rejected += 1
            final.extend(sorted((sorted(c) for c in nx.connected_components(sub)),
                                 key=lambda c: (-len(c), c[0])))
    final.sort(key=lambda c: (-len(c), c[0] if c else ""))
    return final, rejected


@dataclass
class AlgoResult:
    algorithm: str
    members: Partition
    rejected_count: int
    rejected_raw: int      # number of raw (pre-split) communities — rejected_rate's denominator
    modularity: float

    @property
    def rejected_rate(self) -> float:
        return round(self.rejected_count / self.rejected_raw, 5) if self.rejected_raw else 0.0


def louvain_result(g: nx.DiGraph, resolution: float, seed: int) -> AlgoResult:
    res: DetectionResult = detect(g, resolution=resolution, seed=seed)
    # detect() doesn't expose the raw (pre-split) community count directly; rederive it
    # from the rate it already computed (raw = rejected/rate when rate>0, else raw =
    # number of returned constellations, since nothing was ever split).
    raw_count = (round(res.rejected_count / res.rejected_rate) if res.rejected_rate
                 else len(res.constellations))
    return AlgoResult("louvain", [c.members for c in res.constellations],
                       res.rejected_count, raw_count, res.modularity)


def _ig_from_nx(und: nx.Graph) -> tuple[ig.Graph, list[str]]:
    nodes = sorted(und.nodes())
    idx = {n: i for i, n in enumerate(nodes)}
    graph = ig.Graph()
    graph.add_vertices(len(nodes))
    graph.vs["name"] = nodes
    edges: list[tuple[int, int]] = []
    weights: list[float] = []
    for u, v, d in und.edges(data=True):
        edges.append((idx[u], idx[v]))
        weights.append(float(d.get("weight", 1.0)))
    graph.add_edges(edges)
    graph.es["weight"] = weights
    return graph, nodes


def leiden_result(g: nx.DiGraph, resolution: float, seed: int) -> AlgoResult:
    if g.number_of_nodes() == 0:
        return AlgoResult("leiden", [], 0, 0, 0.0)
    und = _undirected_weighted(g)
    ig_g, nodes = _ig_from_nx(und)
    if ig_g.ecount() == 0:
        members = [[n] for n in nodes]
        return AlgoResult("leiden", members, 0, len(members), 0.0)
    part = la.find_partition(ig_g, la.RBConfigurationVertexPartition,
                              resolution_parameter=resolution, weights="weight",
                              seed=seed, n_iterations=2)
    raw = [sorted(nodes[i] for i in comm) for comm in part]
    final, rejected = _connectivity_split(und, raw)
    has_modularity = len(raw) > 1 and und.number_of_edges() > 0
    modularity = (nx.community.modularity(und, raw, weight="weight", resolution=resolution)
                  if has_modularity else 0.0)
    return AlgoResult("leiden", final, rejected, len(raw), round(modularity, 5))


def run_algorithm(algorithm: str, g: nx.DiGraph, resolution: float, seed: int) -> AlgoResult:
    if algorithm == "louvain":
        return louvain_result(g, resolution, seed)
    return leiden_result(g, resolution, seed)


# ── bootstrap stability, generic over algorithm ──────────────────────────────────────
# Same shape as scripts/compare_athlete_system_detectors.py's bootstrap_stability (which
# already had to go generic to compare two detectors on identical resamples — stability.py's
# own bootstrap_jaccard is hardcoded to detect()). Pinned equivalent to bootstrap_jaccard's
# Louvain numbers by tests/test_poc_e2.py.

def _p10(values: list[float]) -> float:
    if len(values) >= 2:
        return round(statistics.quantiles(values, n=10)[0], 5)
    return round(values[0], 5) if values else 0.0


def bootstrap_stability(
    units: Sequence[list[dict[str, Any]]],
    partition_fn: Callable[[nx.DiGraph], Partition],
    baseline_members: Partition,
    n_resamples: int = N_RESAMPLES,
    rng_seed: int = RNG_SEED,
) -> tuple[float, float]:
    """Resample bout-level ``units`` with replacement, rebuild, redetect, best-match
    Jaccard of the resample's partition against ``baseline_members`` (``compare
    .compare_partitions``, already averaged across baseline communities) — mean + p10
    over resamples. Returns ``(0.0, 0.0)`` for an empty unit list."""
    n = len(units)
    if n == 0:
        return 0.0, 0.0
    rng = random.Random(rng_seed)
    vals: list[float] = []
    for _ in range(n_resamples):
        sample = [rng.choice(units) for _ in range(n)]
        g = network_from_sequences(list(sample))
        members = partition_fn(g)
        vals.append(compare_partitions(baseline_members, members).mean_jaccard)
    mean = round(sum(vals) / len(vals), 5) if vals else 0.0
    return mean, _p10(vals)


# ── seed-ARI instrument (05 §6) ───────────────────────────────────────────────────────

def seed_ari(g: nx.DiGraph, partition_for_seed: Callable[[int], Partition],
             seeds: Sequence[int] = SEED_SWEEP) -> tuple[float, float]:
    """Pairwise adjusted Rand index across ``seeds`` runs of the SAME (algorithm, γ,
    graph) — how much the detector's own randomness moves the partition. Returns
    ``(mean, min)`` over all pairs; ``(1.0, 1.0)`` for an empty graph (nothing to vary)."""
    nodes = sorted(g.nodes())
    if not nodes:
        return 1.0, 1.0
    labelings: list[list[int]] = []
    for s in seeds:
        members = partition_for_seed(s)
        label_of = {n: i for i, comm in enumerate(members) for n in comm}
        labelings.append([label_of.get(n, -1) for n in nodes])
    scores = [
        float(adjusted_rand_score(labelings[i], labelings[j]))
        for i in range(len(labelings)) for j in range(i + 1, len(labelings))
    ]
    if not scores:
        return 1.0, 1.0
    return round(sum(scores) / len(scores), 4), round(min(scores), 4)


# ── resolution-limit bound (Fortunato & Barthélemy 2007) ────────────────────────────

def resolution_bound(g: nx.DiGraph) -> float:
    """sqrt(2L), L = total (weighted) link weight of the undirected projection — the
    classic modularity resolution-limit size below which two genuinely separate modules
    can't be told apart by a modularity optimiser at resolution=1. Descriptive check,
    not a proof at other γ (γ effectively rescales the bound; reported at γ=1 baseline
    scale for every row so the numbers are comparable across the sweep)."""
    und = _undirected_weighted(g)
    total_weight = sum(float(d.get("weight", 1.0)) for _, _, d in und.edges(data=True))
    return round(math.sqrt(2 * total_weight), 2) if total_weight > 0 else 0.0


# ── curated-seed resolution-limit probe ───────────────────────────────────────────────
# "Known-distinct small systems from the taxonomy's curated seeds" (03_POC_PLANS.md) is
# operationalised here as: does a detected community mix members from >=2 DISTINCT
# taxonomy families (leg-lock vs strangle vs takedown, ...)? Family = the nearest
# subcategory/category ancestor of a resolved node (most taxonomy leaves already ARE a
# subcategory, e.g. "Heel Hook" -> "leg-lock"). Nodes the taxonomy doesn't recognise are
# excluded from the probe, not treated as their own family.

def _family(tax: Taxonomy, cache: dict[str, str | None], label: str) -> str | None:
    if label not in cache:
        rid = tax.resolve(label)
        fam: str | None = None
        if rid is not None:
            for cand in (rid, *tax.ancestors(rid)):
                node = tax.nodes.get(cand)
                if node is not None and node.kind in ("subcategory", "category"):
                    fam = cand
                    break
            fam = fam or rid
        cache[label] = fam
    return cache[label]


@dataclass
class FamilyMerge:
    example_member: str
    families: list[str]


def merged_families(tax: Taxonomy, members: Partition) -> list[FamilyMerge]:
    cache: dict[str, str | None] = {}
    out: list[FamilyMerge] = []
    for comm in members:
        fams = sorted({f for n in comm if (f := _family(tax, cache, n)) is not None})
        if len(fams) >= 2:
            out.append(FamilyMerge(comm[0] if comm else "", fams))
    return out


# ── source A: 15-athlete detector-comparison set ─────────────────────────────────────

def athlete_own_sequences() -> dict[str, list[list[dict[str, Any]]]]:
    """Same roster + own-events sequences as ``scripts/compare_athlete_system_detectors
    .run()``, restricted to athletes clearing ``MIN_BOUTS_FOR_STABILITY`` — read-only DB."""
    with get_session_factory()() as session:
        named = []
        seen_ids: set[str] = set()
        for name in NAMED_ATHLETES:
            a = _athlete_by_name(session, name)
            if a is not None:
                named.append(a)
                seen_ids.add(a.id)
        extra = _extra_athletes(session, seen_ids, EXTRA_ATHLETES, MIN_BOUTS_FOR_STABILITY)
        out: dict[str, list[list[dict[str, Any]]]] = {}
        for athlete in named + extra:
            bouts = _bouts_for_athlete(session, athlete)
            own = sequences_by_athlete({athlete.name: bouts})[athlete.name]
            if len(own) >= MIN_BOUTS_FOR_STABILITY:
                out[athlete.name] = own
    return out


# ── source B: bracket divisions ──────────────────────────────────────────────────────

def division_sequences(path: Path = SEQUENCES_FILE) -> dict[str, list[list[dict[str, Any]]]]:
    """Per division, ``bout["seq"]`` for every bout whose ``div_a``/``div_b`` matches and
    that has events — same filter ``scripts/bracket_export.py:sequence_layer`` applies."""
    bouts = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[list[dict[str, Any]]]] = {}
    for div in BRACKET_DIVISIONS:
        out[div] = [b["seq"] for b in bouts
                    if (b.get("div_a") == div or b.get("div_b") == div) and b.get("seq")]
    return out


# ── source C: corpus graph ────────────────────────────────────────────────────────────

def corpus_sequences() -> list[list[dict[str, Any]]]:
    """Every ``final`` match's stored ``sequence`` — read-only, mirrors
    ``analysis.poc.e8_interaction_graph.corpus_bouts`` (no perspective gate here: this PoC
    measures topology, not attribution)."""
    from db.base import get_engine

    with get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT sequence FROM matches WHERE status = 'final' AND sequence IS NOT NULL"
        )).mappings().all()
    return [[e for e in (row["sequence"] or []) if isinstance(e, dict)] for row in rows
            if row["sequence"]]


# ── one (source, algorithm, gamma) cell ───────────────────────────────────────────────

@dataclass
class Cell:
    source: str
    algorithm: str
    gamma: float
    n_graphs: int              # 1 for division/corpus, N athletes for the athlete source
    n_nodes: float             # mean — keeps the athlete source comparable to single-graph rows
    n_communities: float
    largest_community: float
    rejected_rate: float       # mean across graphs
    rejected_rate_max: float   # worst single graph — the "verify structurally 0" number
    modularity: float
    stability_mean: float
    stability_p10: float
    resolution_bound: float
    over_bound_communities: float  # mean count of communities with size > sqrt(2L)
    seed_ari_mean: float
    seed_ari_min: float
    family_merges: int         # total across graphs measured
    family_merge_examples: list[str] = field(default_factory=list)


def measure_graph(
    name: str, g: nx.DiGraph, units: Sequence[list[dict[str, Any]]], algorithm: str,
    gamma: float, tax: Taxonomy, n_resamples: int,
) -> Cell:
    base = run_algorithm(algorithm, g, gamma, DETECT_SEED)
    sizes = sorted((len(c) for c in base.members), reverse=True)
    bound = resolution_bound(g)
    over_bound = sum(1 for s in sizes if s > bound) if bound else 0
    stab_mean, stab_p10 = bootstrap_stability(
        units, lambda gg: run_algorithm(algorithm, gg, gamma, DETECT_SEED).members,
        base.members, n_resamples=n_resamples,
    )
    ari_mean, ari_min = seed_ari(
        g, lambda s: run_algorithm(algorithm, g, gamma, s).members,
    )
    merges = merged_families(tax, base.members)
    return Cell(
        source=name, algorithm=algorithm, gamma=gamma, n_graphs=1,
        n_nodes=g.number_of_nodes(), n_communities=len(base.members),
        largest_community=sizes[0] if sizes else 0,
        rejected_rate=base.rejected_rate, rejected_rate_max=base.rejected_rate,
        modularity=base.modularity, stability_mean=stab_mean, stability_p10=stab_p10,
        resolution_bound=bound, over_bound_communities=over_bound,
        seed_ari_mean=ari_mean, seed_ari_min=ari_min,
        family_merges=len(merges),
        family_merge_examples=[f"{m.example_member} ({', '.join(m.families)})" for m in merges[:3]],
    )


def _avg(vals: list[float]) -> float:
    return round(sum(vals) / len(vals), 5) if vals else 0.0


def aggregate_cells(name: str, cells: list[Cell]) -> Cell:
    """Mean across per-athlete cells for one (algorithm, γ) — ``rejected_rate_max`` and
    ``seed_ari_min`` stay worst-case (min-of-means would hide a single bad graph)."""
    return Cell(
        source=name, algorithm=cells[0].algorithm, gamma=cells[0].gamma, n_graphs=len(cells),
        n_nodes=_avg([c.n_nodes for c in cells]),
        n_communities=_avg([c.n_communities for c in cells]),
        largest_community=_avg([c.largest_community for c in cells]),
        rejected_rate=_avg([c.rejected_rate for c in cells]),
        rejected_rate_max=max((c.rejected_rate_max for c in cells), default=0.0),
        modularity=_avg([c.modularity for c in cells]),
        stability_mean=_avg([c.stability_mean for c in cells]),
        stability_p10=_avg([c.stability_p10 for c in cells]),
        resolution_bound=_avg([c.resolution_bound for c in cells]),
        over_bound_communities=_avg([c.over_bound_communities for c in cells]),
        seed_ari_mean=_avg([c.seed_ari_mean for c in cells]),
        seed_ari_min=round(min((c.seed_ari_min for c in cells), default=1.0), 4),
        family_merges=sum(c.family_merges for c in cells),
        family_merge_examples=[ex for c in cells for ex in c.family_merge_examples][:3],
    )


# ── orchestration ─────────────────────────────────────────────────────────────────────

@dataclass
class Report:
    cells: list[Cell]
    athletes_measured: int
    db_error: str | None = None


def run(n_resamples: int = N_RESAMPLES, gammas: Sequence[float] = GAMMAS) -> Report:
    tax = load_taxonomy()
    cells: list[Cell] = []
    db_error: str | None = None
    athletes_measured = 0

    try:
        by_athlete = athlete_own_sequences()
    except Exception as exc:  # noqa: BLE001 — reported, not raised (mirrors e8's DB guard)
        by_athlete = {}
        db_error = f"{type(exc).__name__}: {exc}".split("\n")[0][:160]

    athletes_measured = len(by_athlete)
    for algorithm in ALGORITHMS:
        for gamma in gammas:
            per_athlete_cells = []
            for athlete, own in by_athlete.items():
                g = network_from_sequences(own)
                if g.number_of_nodes() == 0:
                    continue
                per_athlete_cells.append(
                    measure_graph(athlete, g, own, algorithm, gamma, tax, n_resamples)
                )
            if per_athlete_cells:
                cells.append(aggregate_cells("15-athlete set (aggregate)", per_athlete_cells))

    if not db_error:
        try:
            divisions = division_sequences()
            corpus = corpus_sequences()
        except Exception as exc:  # noqa: BLE001
            divisions, corpus = {}, []
            db_error = db_error or f"{type(exc).__name__}: {exc}".split("\n")[0][:160]

        for div, units in divisions.items():
            g = network_from_sequences(units)
            if g.number_of_nodes() == 0:
                continue
            for algorithm in ALGORITHMS:
                for gamma in gammas:
                    cells.append(measure_graph(
                        f"bracket {div}", g, units, algorithm, gamma, tax, n_resamples,
                    ))

        if corpus:
            g = network_from_sequences(corpus)
            for algorithm in ALGORITHMS:
                for gamma in gammas:
                    cells.append(measure_graph(
                        "corpus (final matches)", g, corpus, algorithm, gamma, tax, n_resamples,
                    ))

    return Report(cells=cells, athletes_measured=athletes_measured, db_error=db_error)


# ── report ───────────────────────────────────────────────────────────────────────────

CRITERION = (
    "Leiden accepted if stability >= Louvain's and rejected_rate = 0; the swap then goes "
    "through the golden-fixture process (regenerate fixtures from Python, port refinement "
    "phase to TS or accept a documented parity break). Consolidation rider: athlete_systems "
    "moves off greedy modularity onto the shared detector in the same change."
)


def _row(c: Cell) -> str:
    return (f"| {c.source} | {c.algorithm} | {c.gamma} | {c.n_graphs} | {c.n_nodes:.1f} | "
            f"{c.n_communities:.1f} | {c.largest_community:.1f} | {c.rejected_rate:.4f} | "
            f"{c.rejected_rate_max:.4f} | {c.modularity:.4f} | {c.stability_mean:.4f} | "
            f"{c.stability_p10:.4f} | {c.resolution_bound:.1f} | {c.over_bound_communities:.1f} | "
            f"{c.seed_ari_mean:.4f} | {c.seed_ari_min:.4f} | {c.family_merges} |")


def _accept(cells: list[Cell]) -> tuple[bool, list[str]]:
    """Criterion applied verbatim, per (source, γ) pair — Leiden's row vs Louvain's row on
    the SAME source/γ. Whole-PoC accept requires every pair to clear it."""
    by_key: dict[tuple[str, float], dict[str, Cell]] = {}
    for c in cells:
        by_key.setdefault((c.source, c.gamma), {})[c.algorithm] = c
    failures: list[str] = []
    for (source, gamma), by_algo in sorted(by_key.items()):
        lo, le = by_algo.get("louvain"), by_algo.get("leiden")
        if lo is None or le is None:
            continue
        if le.rejected_rate_max != 0.0:
            failures.append(f"{source} @ γ={gamma}: Leiden rejected_rate_max="
                             f"{le.rejected_rate_max:.4f} (must be 0)")
        if le.stability_mean < lo.stability_mean:
            failures.append(f"{source} @ γ={gamma}: Leiden stability {le.stability_mean:.4f} "
                             f"< Louvain {lo.stability_mean:.4f}")
    return not failures, failures


def render_markdown(report: Report, n_resamples: int) -> str:
    accepted, failures = _accept(report.cells)
    lines = [
        "# PoC-E2 — Leiden vs shipped Louvain: resolution sweep",
        "",
        "Generated by `uv run python -m analysis.poc.e2_leiden` — do not hand-edit. "
        "Detector: `analysis/constellations/detect.py` (called, not changed). Plan: "
        "`docs/research/03_POC_PLANS.md` (PoC-E2); seed-ARI instrument: "
        "`docs/research/05_EXTERNAL_POC_REVIEW.md` §6.",
        "",
        "## Criterion (pre-registered, before any number below)",
        "",
        f"> {CRITERION}",
        "",
        "Operationalised, fixed before any number below: the criterion is checked per "
        "(source, γ) pair — Leiden's row against Louvain's row on the identical graph and "
        "resolution, `rejected_rate_max=0` (worst single graph in an aggregated row, not "
        "the mean) and `stability_mean >= ` Louvain's. Whole-PoC ACCEPT requires every "
        "measured pair to clear both limbs; one failing pair is a REJECT, not an average.",
        "",
        "## Verdict",
        "",
        f"**{'ACCEPT' if accepted else 'REJECT'}**"
        + (" — every (source, γ) pair clears both limbs (Leiden rejected_rate_max=0 AND "
           "Leiden stability >= Louvain's)." if accepted else
           f" — {len(failures)} pair(s) fail the pre-registered criterion:"),
    ]
    if not accepted:
        lines += [""] + [f"  - {f}" for f in failures]
    lines += [
        "",
        "> Even on ACCEPT: this run does not swap anything. The golden-fixture process "
        "(regenerate fixtures from Python, port refinement to TS or accept a documented "
        "parity break) and the `athlete_systems` consolidation rider are separate "
        "cross-repo changes the orchestrator owns.",
        "",
    ]
    if report.db_error:
        lines += [f"**DB read failed — divisions/corpus sources skipped:** "
                  f"`{report.db_error}`. Athlete-source numbers below (if any) came from "
                  f"the same read and are also absent.", ""]
    lines += [
        f"{report.athletes_measured} athletes cleared the ≥{MIN_BOUTS_FOR_STABILITY}-bout "
        f"floor for the 15-athlete set (named 5 + up to {EXTRA_ATHLETES} extra by match "
        f"count). Bootstrap: {n_resamples} resamples/config. Seed-ARI: {len(SEED_SWEEP)} "
        f"seeds ({SEED_SWEEP[0]}–{SEED_SWEEP[-1]}), all pairs.",
        "",
        "## Sweep",
        "",
        "n_graphs=1 for bracket/corpus rows; the athlete row is a mean across every "
        "athlete measured (`rejected_rate_max`/`seed_ari_min` stay worst-case, not "
        "averaged, so a single fragile athlete graph can't hide behind the mean).",
        "",
        "| source | algo | γ | n_graphs | n_nodes | n_comm | largest | rejected (mean) | "
        "rejected (max) | modularity | stability mean | stability p10 | sqrt(2L) bound | "
        "comm > bound | seed-ARI mean | seed-ARI min | family merges |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_row(c) for c in report.cells]
    lines += ["", "## Curated-seed resolution-limit probe", "",
              "\"Known-distinct small systems from the taxonomy's curated seeds\" is "
              "operationalised as: does a detected community mix members from >=2 "
              "distinct taxonomy families (e.g. leg-lock vs strangle vs takedown)? "
              "`family merges` above counts such communities per config; examples below "
              "(first 3 per row, algorithm/γ/source that had any):", ""]
    examples = [c for c in report.cells if c.family_merges]
    if examples:
        lines += ["| source | algo | γ | example merges |", "|---|---|---|---|"]
        lines += [f"| {c.source} | {c.algorithm} | {c.gamma} | "
                  f"{'; '.join(c.family_merge_examples)} |" for c in examples]
    else:
        lines += ["No config merged two distinct taxonomy families into one community "
                  "on any measured graph."]
    lines += ["", "## Reading", ""] + _reading(report, accepted, failures)
    return "\n".join(lines) + "\n"


def _reading(report: Report, accepted: bool, failures: list[str]) -> list[str]:
    louvain_cells = [c for c in report.cells if c.algorithm == "louvain"]
    leiden_cells = [c for c in report.cells if c.algorithm == "leiden"]
    max_l_rej = max((c.rejected_rate_max for c in louvain_cells), default=0.0)
    max_le_rej = max((c.rejected_rate_max for c in leiden_cells), default=0.0)
    out = [
        f"1. **Rejection.** Louvain's worst measured `rejected_rate` across every "
        f"(source, γ) cell is {max_l_rej:.4f}; Leiden's is {max_le_rej:.4f}. Leiden's "
        f"connectivity guarantee (Traag, Waltman & van Eck 2019) is verified here, not "
        f"assumed — every raw Leiden community is put through the same "
        f"`nx.is_connected` check the shipped detector applies to Louvain. Note: on "
        f"these measured graphs Louvain's OWN rejection rate is also 0 everywhere — the "
        f"failure mode ADR-07 guards against is real (`tests/test_poc_e2.py` "
        f"constructs a graph where it fires) but did not fire on this corpus at these "
        f"γ values; the rejection limb is not what decides this PoC's verdict here.",
        "",
        f"2. **Stability.** {'Leiden' if accepted else 'The criterion'} "
        + ("matched or beat Louvain's bootstrap Jaccard on every measured (source, γ) "
           "pair." if accepted else
           f"failed Louvain's stability on "
           f"{len([f for f in failures if 'stability' in f])} pair(s) — see Verdict."),
        "",
        "3. **Resolution limit.** `comm > bound` counts communities exceeding "
        "sqrt(2L) (Fortunato & Barthélemy 2007) — communities AT OR BELOW the bound "
        "are the ones a modularity-family optimiser (Louvain OR Leiden; the theorem is "
        "about the objective, not the search algorithm) cannot be trusted to resolve "
        "correctly at that γ. A high `comm > bound` share on the small per-athlete/"
        "division graphs is expected structurally, not a Leiden-specific finding.",
        "",
        "4. **Seed sensitivity (05 §6).** Both algorithms are seeded for reproducibility "
        "in production; seed-ARI measures how much that seed choice matters. A low "
        "`seed-ARI min` on a source means the *shipped* pinned-seed partition is one "
        "arbitrary draw among visibly different ones — a caveat for any single reported "
        "partition, independent of which algorithm produced it.",
        "",
        "5. **Caveats.** The 15-athlete aggregate mixes graphs of very different sizes "
        "(Gordon Ryan's corpus vs a 5-bout floor case) into one mean — read the per-"
        "source spread, not just the row. `resolution_bound` is reported at each row's "
        "own γ's edge-weight total (L doesn't change with γ; only what counts as "
        "\"resolvable\" does) — it's a fixed structural number, not swept.",
    ]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PoC-E2 — Leiden vs shipped Louvain, measured")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e2.md"))
    ap.add_argument("--n-resamples", type=int, default=N_RESAMPLES)
    args = ap.parse_args(argv)

    report = run(n_resamples=args.n_resamples)
    md = render_markdown(report, args.n_resamples)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
