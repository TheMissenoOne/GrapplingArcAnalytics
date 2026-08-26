"""Hand-computable pins for the four methods in ``analysis/poc/signatures.py``.

Every assertion here is a number derivable with a pencil from the source paper, not a
snapshot of what the code happened to print. That is the whole point: these methods are
implemented rather than installed, so the test IS the correctness argument.
"""

from __future__ import annotations

import math

import networkx as nx
import numpy as np
import pytest

from analysis.poc.signatures import (
    TEdge,
    contains,
    counter_vector,
    degree_signature,
    heat_trace,
    index_motif_counts,
    motif_counts,
    motif_id,
    netlsd,
    netlsd_distance,
    pairwise_distances,
    portrait,
    portrait_divergence,
    prefixspan,
    rank_targets,
    retrieval,
    size_signature,
    to_undirected,
)

# ── NetLSD ──────────────────────────────────────────────────────────────────────


def test_heat_trace_of_an_edgeless_graph_is_n_at_every_scale() -> None:
    """No edges → the normalised Laplacian is all-zero → every eigenvalue is 0 → h(t) = n."""
    g = nx.Graph()
    g.add_nodes_from(range(5))
    ts = np.array([0.01, 1.0, 100.0])
    assert np.allclose(heat_trace(g, ts), [5.0, 5.0, 5.0])


def test_netlsd_of_an_edgeless_graph_is_all_ones_whatever_the_size() -> None:
    """The reading empty-graph normalisation buys: 1.0 everywhere means 'no structure'."""
    ts = np.array([0.01, 1.0, 100.0])
    for n in (3, 7, 40):
        g = nx.Graph()
        g.add_nodes_from(range(n))
        assert np.allclose(netlsd(g, ts), np.ones(3))


def test_heat_trace_of_k2_matches_its_closed_form() -> None:
    """K2's normalised Laplacian has spectrum {0, 2} → h(t) = 1 + exp(−2t)."""
    g = nx.Graph([(0, 1)])
    ts = np.array([0.1, 0.5, 3.0])
    assert np.allclose(heat_trace(g, ts), 1.0 + np.exp(-2.0 * ts))


def test_heat_trace_of_p3_matches_its_closed_form() -> None:
    """The path on 3 nodes has normalised-Laplacian spectrum {0, 1, 2}."""
    g = nx.Graph([(0, 1), (1, 2)])
    ts = np.array([0.2, 1.0, 5.0])
    assert np.allclose(heat_trace(g, ts), 1.0 + np.exp(-ts) + np.exp(-2.0 * ts))


def test_netlsd_is_permutation_invariant() -> None:
    g = nx.gnm_random_graph(12, 22, seed=7)
    h = nx.relabel_nodes(g, {i: f"n{(i * 5) % 12}" for i in g.nodes()})
    assert netlsd_distance(netlsd(g), netlsd(h)) < 1e-9


def test_netlsd_separates_a_star_from_a_path_a_size_null_cannot_tell_apart() -> None:
    """Same n, same m, different shape — exactly the case the size null is blind to."""
    star = nx.star_graph(9)                       # 10 nodes, 9 edges
    path = nx.path_graph(10)                      # 10 nodes, 9 edges
    assert np.allclose(size_signature(star), size_signature(path))

    shape_gap = netlsd_distance(netlsd(star), netlsd(path))
    scale_gap = netlsd_distance(netlsd(nx.path_graph(10)), netlsd(nx.path_graph(11)))
    assert shape_gap > 5 * scale_gap             # shape dominates scale, which is the claim


def test_to_undirected_sums_reciprocal_weights() -> None:
    g = nx.DiGraph()
    g.add_edge("a", "b", weight=3)
    g.add_edge("b", "a", weight=4)
    u = to_undirected(g)
    assert u.number_of_edges() == 1
    assert u["a"]["b"]["weight"] == 7


# ── Portrait divergence ─────────────────────────────────────────────────────────


def test_portrait_of_p3_is_the_hand_computed_b_matrix() -> None:
    """Path 0-1-2. Shell 0: every node alone (k=1, three of them). Shell 1: the endpoints
    see 1 neighbour, the centre sees 2. Shell 2: the endpoints see each other (k=1 twice),
    the centre has nobody left (k=0)."""
    b = portrait(nx.path_graph(3))
    assert b.shape[0] == 3
    assert b[0, 1] == 3 and b[0].sum() == 3
    assert b[1, 1] == 2 and b[1, 2] == 1
    assert b[2, 0] == 1 and b[2, 1] == 2


def test_portrait_divergence_of_a_graph_with_itself_is_zero() -> None:
    g = nx.gnm_random_graph(15, 30, seed=3)
    assert portrait_divergence(g, g) == pytest.approx(0.0, abs=1e-12)


def test_portrait_divergence_is_permutation_invariant() -> None:
    g = nx.gnm_random_graph(14, 26, seed=11)
    h = nx.relabel_nodes(g, {i: f"x{(i * 3) % 14}" for i in g.nodes()})
    assert portrait_divergence(g, h) == pytest.approx(0.0, abs=1e-12)


def test_portrait_divergence_of_k3_vs_p3_is_the_hand_computed_jsd() -> None:
    """Worked by hand, in bits.

    K3 portrait: B[0,1]=3, B[1,2]=3. Weighted by k: 3 at (0,1), 6 at (1,2); total 9.
      P = {(0,1): 1/3, (1,2): 2/3}.
    P3 portrait (pinned above): B[0,1]=3, B[1,1]=2, B[1,2]=1, B[2,0]=1, B[2,1]=2.
      Weighted by k: 3 at (0,1), 2 at (1,1), 2 at (1,2), 0 at (2,0), 2 at (2,1); total 9.
      Q = {(0,1): 1/3, (1,1): 2/9, (1,2): 2/9, (2,1): 2/9}.
    M = ½(P+Q) = {(0,1): 1/3, (1,1): 1/9, (1,2): 4/9, (2,1): 1/9}.
      KL(P‖M) = ⅔·log₂((2/3)/(4/9))         = ⅔·log₂(3/2)  = 0.3899750
      KL(Q‖M) = 2/9·(1) + 2/9·(−1) + 2/9·(1) = 2/9         = 0.2222222
      JSD     = ½(0.3899750 + 0.2222222)                   = 0.3060986
    """
    expected = 0.5 * ((2 / 3) * math.log2(1.5) + 2 / 9)
    assert expected == pytest.approx(0.3060986, abs=1e-6)
    got = portrait_divergence(nx.complete_graph(3), nx.path_graph(3))
    assert got == pytest.approx(expected, abs=1e-9)


def test_portrait_divergence_of_an_empty_graph_is_nan_not_zero() -> None:
    """No nodes → no distribution at all. Returning 0 would read as 'identical'."""
    assert math.isnan(portrait_divergence(nx.Graph(), nx.path_graph(4)))


def test_every_edgeless_graph_has_the_same_portrait_whatever_its_size() -> None:
    """A stated degeneracy, not a bug: with no edges all the mass sits in the ℓ=0 row
    (each node alone with itself), so portrait divergence cannot tell a 4-node dust cloud
    from a 40-node one. The half-graph gate (≥ 2 edges) is what keeps it out of the cells."""
    small, big = nx.Graph(), nx.Graph()
    small.add_nodes_from(range(4))
    big.add_nodes_from(range(40))
    assert portrait_divergence(small, big) == pytest.approx(0.0, abs=1e-12)


# ── PrefixSpan ──────────────────────────────────────────────────────────────────


def test_prefixspan_hand_corpus() -> None:
    """Three sequences, min_support 2. By hand:
    a:3, b:2, c:3, (a,c):3, (b,c):2 — and nothing else clears support 2.
    (a,b) is only in seq 0; (b,a) only in seq 2; (a,b,c) only in seq 0."""
    seqs = [["a", "b", "c"], ["a", "c"], ["b", "a", "c"]]
    got = dict(prefixspan(seqs, min_support=2, max_len=3))
    assert got == {
        ("a",): 3, ("b",): 2, ("c",): 3, ("a", "c"): 3, ("b", "c"): 2,
    }


def test_prefixspan_support_counts_sequences_not_occurrences() -> None:
    """One sequence with eight repeats must not out-rank a pattern seen in two sequences."""
    seqs = [["x"] * 8, ["y", "z"], ["z", "y"]]
    got = dict(prefixspan(seqs, min_support=2, max_len=2))
    assert ("x",) not in got            # support 1, however many occurrences
    assert got[("y",)] == 2 and got[("z",)] == 2


def test_prefixspan_mines_gapped_not_contiguous_subsequences() -> None:
    seqs = [["a", "q", "b"], ["a", "r", "s", "b"]]
    got = dict(prefixspan(seqs, min_support=2, max_len=2))
    assert got[("a", "b")] == 2


def test_prefixspan_respects_max_len() -> None:
    seqs = [["a", "b", "c", "d"]] * 3
    got = dict(prefixspan(seqs, min_support=2, max_len=2))
    assert all(len(p) <= 2 for p in got)


def test_prefixspan_rejects_zero_support() -> None:
    with pytest.raises(ValueError, match="min_support"):
        prefixspan([["a"]], min_support=0)


def test_contains_is_subsequence_matching() -> None:
    assert contains(["a", "q", "b", "c"], ["a", "b"])
    assert contains(["a", "b"], ["a", "b"])
    assert not contains(["b", "a"], ["a", "b"])
    assert contains(["a"], [])


# ── δ-temporal motifs ───────────────────────────────────────────────────────────


def test_motif_id_is_invariant_to_node_names() -> None:
    tri1 = [TEdge("a", "b", 0), TEdge("b", "c", 1), TEdge("c", "a", 2)]
    tri2 = [TEdge("x", "y", 0), TEdge("y", "z", 1), TEdge("z", "x", 2)]
    assert motif_id(tri1) == motif_id(tri2) == "0>1|1>2|2>0"


def test_motif_id_distinguishes_a_cycle_from_an_out_star() -> None:
    cycle = [TEdge("a", "b", 0), TEdge("b", "c", 1), TEdge("c", "a", 2)]
    star = [TEdge("a", "b", 0), TEdge("a", "c", 1), TEdge("a", "b", 2)]
    assert motif_id(cycle) != motif_id(star)


def test_motif_counts_on_a_hand_triangle() -> None:
    """Exactly three edges, all inside δ, three distinct nodes → exactly one 3-tuple."""
    edges = [TEdge("a", "b", 0.0), TEdge("b", "c", 1.0), TEdge("c", "a", 2.0)]
    got = motif_counts(edges, delta=5.0, k=3)
    assert sum(got.values()) == 1
    assert got["0>1|1>2|2>0"] == 1


def test_delta_window_excludes_the_tuple_that_spans_too_long() -> None:
    edges = [TEdge("a", "b", 0.0), TEdge("b", "c", 1.0), TEdge("c", "a", 100.0)]
    assert sum(motif_counts(edges, delta=5.0, k=3).values()) == 0
    assert sum(motif_counts(edges, delta=200.0, k=3).values()) == 1


def test_motif_counts_refuses_tuples_spanning_more_than_max_nodes() -> None:
    edges = [TEdge("a", "b", 0.0), TEdge("c", "d", 1.0), TEdge("e", "f", 2.0)]
    assert sum(motif_counts(edges, delta=10.0, k=3, max_nodes=3).values()) == 0


def test_motif_counts_k2_enumerates_every_in_window_pair() -> None:
    """Four edges on two nodes inside the window → C(4,2) = 6 ordered pairs."""
    edges = [TEdge("a", "b", float(i)) for i in range(4)]
    assert sum(motif_counts(edges, delta=10.0, k=2).values()) == 6


def test_index_window_ignores_the_clock_but_keeps_the_order() -> None:
    """Same three edges, same order; one stream is compressed in time, the other stretched.
    The δ counter disagrees between them; the index counter cannot."""
    tight = [TEdge("a", "b", 0.0), TEdge("b", "c", 0.5), TEdge("c", "a", 1.0)]
    loose = [TEdge("a", "b", 0.0), TEdge("b", "c", 50.0), TEdge("c", "a", 100.0)]
    assert motif_counts(tight, delta=2.0, k=3) != motif_counts(loose, delta=2.0, k=3)
    assert index_motif_counts(tight, span=2, k=3) == index_motif_counts(loose, span=2, k=3)


# ── retrieval scoring ───────────────────────────────────────────────────────────


def test_rank_targets_is_one_based_and_pessimistic_on_ties() -> None:
    dist = np.array([[0.0, 1.0, 2.0],
                     [5.0, 5.0, 5.0]])
    # query 0's target is candidate 2 (the worst) → rank 3.
    # query 1 ties everything → the target is placed LAST, not first.
    assert rank_targets(dist, [2, 0]) == [3, 3]


def test_a_degenerate_signature_scores_at_the_floor() -> None:
    """Every distance equal — the failure a null arm exists to catch."""
    n = 6
    dist = np.zeros((n, n))
    r = retrieval("degenerate", dist, list(range(n)))
    assert r.ranks == [n] * n
    assert r.mrr == pytest.approx(1.0 / n)


def test_a_perfect_signature_scores_one() -> None:
    dist = np.ones((4, 4)) - np.eye(4)
    r = retrieval("perfect", dist, list(range(4)))
    assert r.mrr == pytest.approx(1.0)
    assert r.top_k(1) == 1.0


def test_nan_distances_rank_last() -> None:
    dist = np.array([[float("nan"), 1.0, 2.0]])
    assert rank_targets(dist, [0]) == [3]


def test_pairwise_distances_is_symmetric_with_zero_diagonal() -> None:
    gs = [nx.path_graph(4), nx.cycle_graph(5), nx.star_graph(4)]
    d = pairwise_distances(gs, portrait_divergence)
    assert np.allclose(d, d.T)
    assert np.allclose(np.diag(d), 0.0)


def test_counter_vector_and_degree_signature_shapes() -> None:
    assert list(counter_vector({"a": 2.0}, ["a", "b"])) == [2.0, 0.0]
    h = degree_signature(nx.star_graph(6), bins=8)
    assert h.shape == (8,) and h.sum() == pytest.approx(1.0)
