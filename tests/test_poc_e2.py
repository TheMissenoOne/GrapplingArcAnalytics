"""PoC-E2 — Leiden vs shipped Louvain. No DB: pure graph/algorithm unit checks only
(the DB-backed sources are exercised by hand via `uv run python -m analysis.poc.e2_leiden`,
same convention as PoC-E8's corpus pass)."""

from __future__ import annotations

import networkx as nx

from analysis.poc.e2_leiden import (
    GAMMAS,
    Cell,
    Report,
    aggregate_cells,
    bootstrap_stability,
    leiden_result,
    louvain_result,
    measure_graph,
    render_markdown,
    resolution_bound,
    run_algorithm,
    seed_ari,
)
from analysis.taxonomy import load_taxonomy

# The exact toy graph found by brute-force search (analysis.constellations.detect at
# resolution=1.0, seed=1 rejects one raw community here — see tests below).
_TOY_EDGES = [
    ("0", "1", 0.945), ("0", "4", 2.4404), ("3", "4", 1.4067), ("3", "6", 0.8252),
    ("4", "5", 1.9829), ("4", "9", 2.4926), ("5", "9", 2.3607), ("7", "8", 2.1863),
]


def _toy_digraph() -> nx.DiGraph:
    g = nx.DiGraph()
    for u, v, w in _TOY_EDGES:
        g.add_edge(u, v, weight=w)
        g.add_edge(v, u, weight=w)
    return g


def _event(label: str, actor: str) -> dict[str, str]:
    return {"label": label, "type": "x", "actor_id": actor}


def _two_triangles() -> nx.DiGraph:
    """Two fully disconnected triangles — an unambiguous 2-community graph, any
    reasonable seed should land on the same partition."""
    g = nx.DiGraph()
    for a, b in [("a", "b"), ("b", "c"), ("c", "a")]:
        g.add_edge(a, b, weight=1.0)
        g.add_edge(b, a, weight=1.0)
    for a, b in [("x", "y"), ("y", "z"), ("z", "x")]:
        g.add_edge(a, b, weight=1.0)
        g.add_edge(b, a, weight=1.0)
    return g


# ── 1. Leiden partition validity on a toy graph ──────────────────────────────────────

def test_leiden_partition_covers_every_node_exactly_once() -> None:
    g = _toy_digraph()
    res = leiden_result(g, resolution=1.0, seed=1)
    covered = [n for comm in res.members for n in comm]
    assert sorted(covered) == sorted(g.nodes())
    assert len(covered) == len(set(covered))  # no node in two communities


def test_leiden_every_community_is_internally_connected() -> None:
    """Validity, verified rather than assumed: every returned community induces a
    connected subgraph of the undirected projection — same check the shipped Louvain
    detector applies to itself (ADR-07)."""
    g = _toy_digraph()
    from analysis.constellations.detect import _undirected_weighted

    und = _undirected_weighted(g)
    res = leiden_result(g, resolution=1.0, seed=1)
    for comm in res.members:
        if len(comm) > 1:
            assert nx.is_connected(und.subgraph(comm)), comm
    assert res.rejected_count == 0


# ── 2. rejected_rate = 0 for Leiden where Louvain rejects ───────────────────────────

def test_louvain_rejects_on_toy_graph_leiden_does_not() -> None:
    """Fixture where the shipped detector's own connectivity-rejection machinery fires
    (found by search, pinned here): Louvain's raw community {'4','5','9'} at
    resolution=1.0, seed=1 induces a disconnected subgraph and gets split + counted."""
    g = _toy_digraph()
    lo = louvain_result(g, resolution=1.0, seed=1)
    le = leiden_result(g, resolution=1.0, seed=1)
    assert lo.rejected_count > 0
    assert lo.rejected_rate > 0.0
    assert le.rejected_count == 0
    assert le.rejected_rate == 0.0


def test_run_algorithm_dispatches_both() -> None:
    g = _toy_digraph()
    assert run_algorithm("louvain", g, 1.0, 1).algorithm == "louvain"
    assert run_algorithm("leiden", g, 1.0, 1).algorithm == "leiden"


# ── 3. seed-ARI computation correctness ──────────────────────────────────────────────

def test_seed_ari_is_one_when_partition_never_changes() -> None:
    g = _two_triangles()
    mean, lo = seed_ari(g, lambda s: run_algorithm("leiden", g, 1.0, s).members, seeds=range(5))
    assert mean == 1.0
    assert lo == 1.0


def test_seed_ari_matches_sklearn_on_a_fixed_labeling_sequence() -> None:
    """Correctness against the reference implementation directly: feed `seed_ari` two
    hand-picked partitions (not run through any detector) and check its mean/min against
    `sklearn.metrics.adjusted_rand_score` computed by hand on the same node order."""
    from sklearn.metrics import adjusted_rand_score

    g = nx.DiGraph()
    g.add_nodes_from(["p", "q", "r", "s"])
    partitions = {
        0: [["p", "q"], ["r", "s"]],
        1: [["p", "q"], ["r", "s"]],          # identical -> ARI 1.0 vs seed 0
        2: [["p", "r"], ["q", "s"]],          # totally different grouping
    }
    mean, lo = seed_ari(g, lambda s: partitions[s], seeds=[0, 1, 2])

    def labels(members: list[list[str]]) -> list[int]:
        nodes = sorted(g.nodes())
        by_node = {n: i for i, c in enumerate(members) for n in c}
        return [by_node[n] for n in nodes]

    expected = [
        adjusted_rand_score(labels(partitions[0]), labels(partitions[1])),
        adjusted_rand_score(labels(partitions[0]), labels(partitions[2])),
        adjusted_rand_score(labels(partitions[1]), labels(partitions[2])),
    ]
    assert mean == round(sum(expected) / len(expected), 4)
    assert lo == round(min(expected), 4)


def test_seed_ari_empty_graph_is_perfect_by_convention() -> None:
    g = nx.DiGraph()
    assert seed_ari(g, lambda s: [], seeds=[0, 1]) == (1.0, 1.0)


# ── 4. gamma sweep table shape ───────────────────────────────────────────────────────

def test_bootstrap_stability_matches_baseline_on_a_stable_toy_graph() -> None:
    """Two disconnected triangles resampled at the bout level (one bout per triangle
    edge here) should reproduce the same 2-community partition almost every draw —
    stability close to 1.0, not some arbitrary number."""
    g = _two_triangles()
    baseline = leiden_result(g, resolution=1.0, seed=1).members
    units = [
        [_event("a", "1"), _event("b", "1")],
        [_event("b", "1"), _event("c", "1")],
        [_event("c", "1"), _event("a", "1")],
        [_event("x", "2"), _event("y", "2")],
        [_event("y", "2"), _event("z", "2")],
        [_event("z", "2"), _event("x", "2")],
    ]
    mean, p10 = bootstrap_stability(
        units, lambda gg: leiden_result(gg, 1.0, 1).members, baseline, n_resamples=20, rng_seed=1,
    )
    assert 0.0 <= p10 <= mean <= 1.0
    assert mean > 0.7  # a stable structure should mostly reproduce itself


def test_resolution_bound_is_sqrt_2l() -> None:
    import math

    g = _two_triangles()
    # 6 undirected edges, each accumulating BOTH directions' weight (`_undirected_weighted`
    # sums forward + reverse) -> 1.0 + 1.0 = 2.0 per edge, 6 edges -> L = 12.0.
    total_weight = 12.0
    assert resolution_bound(g) == round(math.sqrt(2 * total_weight), 2)


def test_gamma_sweep_produces_one_row_per_algorithm_per_gamma() -> None:
    """`measure_graph` over the full GAMMAS sweep on a small synthetic graph, both
    algorithms — the shape `render_markdown` renders one table row per, with no DB
    involved (mirrors what `run()` does per-source, without the DB reads)."""
    tax = load_taxonomy()
    g = _toy_digraph()
    units = [[_event(a, "1"), _event(b, "1")] for a, b, _w in _TOY_EDGES]
    cells: list[Cell] = []
    for algorithm in ("louvain", "leiden"):
        for gamma in GAMMAS:
            cells.append(measure_graph("toy", g, units, algorithm, gamma, tax, n_resamples=3))

    assert len(cells) == 2 * len(GAMMAS)
    assert {c.algorithm for c in cells} == {"louvain", "leiden"}
    assert {c.gamma for c in cells} == set(GAMMAS)

    report = Report(cells=cells, athletes_measured=0)
    md = render_markdown(report, n_resamples=3)
    header_line = next(line for line in md.splitlines() if line.startswith("| source |"))
    body_rows = [
        line for line in md.splitlines()
        if line.startswith("| toy |")
    ]
    assert header_line
    assert len(body_rows) == 2 * len(GAMMAS)


def test_aggregate_cells_keeps_worst_case_not_mean_for_rejected_and_ari_min() -> None:
    a = Cell(source="a", algorithm="leiden", gamma=1.0, n_graphs=1, n_nodes=10, n_communities=2,
             largest_community=6, rejected_rate=0.0, rejected_rate_max=0.0, modularity=0.4,
             stability_mean=0.9, stability_p10=0.8, resolution_bound=5.0, over_bound_communities=0,
             seed_ari_mean=0.9, seed_ari_min=0.9, family_merges=0)
    b = Cell(source="b", algorithm="leiden", gamma=1.0, n_graphs=1, n_nodes=10, n_communities=2,
             largest_community=6, rejected_rate=0.1, rejected_rate_max=0.2, modularity=0.4,
             stability_mean=0.5, stability_p10=0.4, resolution_bound=5.0, over_bound_communities=0,
             seed_ari_mean=0.9, seed_ari_min=0.1, family_merges=0)
    agg = aggregate_cells("ab", [a, b])
    assert agg.rejected_rate_max == 0.2  # worst single graph, not averaged away
    assert agg.seed_ari_min == 0.1
    assert agg.stability_mean == round((0.9 + 0.5) / 2, 5)
