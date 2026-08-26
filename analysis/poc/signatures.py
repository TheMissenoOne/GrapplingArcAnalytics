"""Graph signatures, sequential-pattern mining and δ-temporal motifs — the shared library
behind PoC-E5 (grapple-like v2), PoC-X3 (sequence mining) and PoC-E14 (temporal motifs).

Nothing here knows about grappling. It takes graphs, item sequences and timestamped edges.
The three runners next to this file supply the corpus, the split and the criterion.

**Why implemented rather than installed.** Each of the four methods is a short, exactly
specified computation whose reference implementation is a single file:

* `netlsd` on PyPI is unmaintained since 2018 and pins its own numpy; the heat trace is
  ``Σ_j exp(−λ_j t)`` over the normalised Laplacian spectrum — one ``eigvalsh`` call on
  graphs of ≤ 60 nodes.
* Portrait divergence has no maintained package of its own (`netrd` carries it, along with
  ~30 other distances and a POT/optimal-transport dependency tree we would use nothing
  else from). Bagrow's own reference is ~100 lines, MIT.
* `prefixspan` on PyPI depends on `extratools`, a grab-bag utility package, to mine what is
  25 lines of prefix projection for the single-item-per-position case we actually have.
* Paranjape's temporal-motif counter is a C++ codebase (SNAP) built for streams of millions
  of edges; a bout yields ~18.

Every one of them is pinned here by a hand-computable test in
``tests/test_poc_signatures.py`` — the check that matters is not "the library is popular"
but "the number is the one the paper defines".

References
----------
* Tsitsulin, Mottin, Karras, Bronstein & Müller (2018). *NetLSD: Hearing the Shape of a
  Graph.* KDD '18, 2347–2356. doi:10.1145/3219819.3219991, arXiv:1805.10712.
* Bagrow & Bollt (2019). *An information-theoretic, all-scales approach to comparing
  networks.* Applied Network Science 4:45. doi:10.1007/s41109-019-0156-x, arXiv:1804.03665.
* Pei, Han, Mortazavi-Asl, Pinto, Chen, Dayal & Hsu (2001). *PrefixSpan: Mining Sequential
  Patterns Efficiently by Prefix-Projected Pattern Growth.* ICDE '01, 215–224.
* Paranjape, Benson & Leskovec (2017). *Motifs in Temporal Networks.* WSDM '17, 601–610.
  doi:10.1145/3018661.3018731, arXiv:1612.09259.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import networkx as nx
import numpy as np

# ── NetLSD (Tsitsulin 2018) ─────────────────────────────────────────────────────

#: The paper's default grid: 250 log-spaced scales from 1e-2 to 1e2 (§5.1). Small t reads
#: local structure, large t reads global connectivity — "all scales at once" is the whole
#: point of the descriptor, so the grid is not tuned per corpus.
TIMESCALES: np.ndarray = np.logspace(-2.0, 2.0, 250)


def to_undirected(g: nx.DiGraph | nx.Graph) -> nx.Graph:
    """Directed → undirected, reciprocal weights summed.

    Both NetLSD and the network portrait are defined on undirected graphs (a symmetric
    Laplacian; an undirected shortest-path distribution). The ActionFlow graphs this is
    applied to are directed, so the projection is a real loss of information — the same
    loss gap #10 flags for the shipped Louvain path. It is declared here rather than
    hidden: an arm that wins on the undirected projection wins on less than the graph
    carries.
    """
    if not g.is_directed():
        return nx.Graph(g)
    out = nx.Graph()
    out.add_nodes_from(g.nodes())
    for u, v, d in g.edges(data=True):
        w = float(d.get("weight", 1.0))
        if out.has_edge(u, v):
            out[u][v]["weight"] += w
        else:
            out.add_edge(u, v, weight=w)
    return out


def heat_trace(g: nx.Graph, timescales: np.ndarray = TIMESCALES,
               weight: str | None = None) -> np.ndarray:
    """``h(t) = Σ_j exp(−λ_j t)`` over the normalised Laplacian spectrum, per timescale.

    ``weight=None`` is the paper's definition (unweighted adjacency). Dense ``eigvalsh``:
    exact, and on a ≤ 60-node graph faster than any approximation.
    """
    n = g.number_of_nodes()
    if n == 0:
        return np.zeros(len(timescales), dtype=np.float64)
    lam = np.linalg.eigvalsh(
        nx.normalized_laplacian_matrix(g, weight=weight).toarray().astype(np.float64)
    )
    trace = np.exp(-np.outer(np.asarray(timescales, dtype=np.float64), lam)).sum(axis=1)
    return np.asarray(trace, dtype=np.float64)


def netlsd(g: nx.DiGraph | nx.Graph, timescales: np.ndarray = TIMESCALES,
           weight: str | None = None) -> np.ndarray:
    """Size-invariant NetLSD signature: the heat trace divided by ``n`` (§4.3, "empty-graph
    normalisation").

    Chosen over the complete-graph normalisation because it has a fixed reading — the
    signature of ANY edgeless graph is the all-ones vector, so "1.0 everywhere" means
    "no structure" regardless of size, and every other signature is read against that.
    """
    ug = to_undirected(g)
    n = ug.number_of_nodes()
    if n == 0:
        return np.zeros(len(timescales), dtype=np.float64)
    return heat_trace(ug, timescales, weight) / float(n)


def netlsd_distance(a: np.ndarray, b: np.ndarray) -> float:
    """L2 between two normalised heat traces — the paper's ``ℓ2`` comparison (§4.4)."""
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


# ── Network portrait divergence (Bagrow & Bollt 2019) ───────────────────────────

def portrait(g: nx.DiGraph | nx.Graph) -> np.ndarray:
    """``B[ℓ, k]`` = number of nodes having exactly ``k`` nodes in their ℓ-th shell.

    Row 0 is therefore always ``B[0, 1] = n`` (every node is alone in its own shell 0).
    A node whose eccentricity is below the graph's diameter contributes ``k = 0`` to every
    shell past it — Bagrow's convention, and the reason the portrait of a disconnected
    graph is still well-formed: unreachable nodes are simply never counted.
    """
    ug = to_undirected(g)
    n = ug.number_of_nodes()
    if n == 0:
        return np.zeros((1, 1), dtype=np.float64)

    shells: list[Counter[int]] = []       # per node: shell index → how many nodes in it
    max_ecc = 0
    for _src, dists in nx.all_pairs_shortest_path_length(ug):
        c: Counter[int] = Counter(dists.values())
        shells.append(c)
        max_ecc = max(max_ecc, max(c) if c else 0)

    b = np.zeros((max_ecc + 1, n + 1), dtype=np.float64)
    for c in shells:
        for shell in range(max_ecc + 1):
            b[shell, c.get(shell, 0)] += 1.0   # absent shell → k=0, Bagrow's convention
    return b


def _pad_to_same(b1: np.ndarray, b2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bagrow's ``pad_portraits_to_same_size``: trim trailing all-zero columns, then pad
    both to a common shape so the two distributions live on one support."""
    def _last_col(b: np.ndarray) -> int:
        nz = np.nonzero(b)[1]
        return int(nz.max()) if nz.size else 0

    last = max(_last_col(b1), _last_col(b2))
    rows = max(b1.shape[0], b2.shape[0])
    out1 = np.zeros((rows, last + 1), dtype=np.float64)
    out2 = np.zeros((rows, last + 1), dtype=np.float64)
    out1[:b1.shape[0], :min(b1.shape[1], last + 1)] = b1[:, :last + 1]
    out2[:b2.shape[0], :min(b2.shape[1], last + 1)] = b2[:, :last + 1]
    return out1, out2


def _jsd(p: np.ndarray, q: np.ndarray) -> float:
    """Jensen–Shannon divergence in bits. 0 iff the distributions are identical, 1 at most."""
    m = 0.5 * (p + q)

    def _kl(x: np.ndarray, y: np.ndarray) -> float:
        mask = x > 0
        return float(np.sum(x[mask] * np.log2(x[mask] / y[mask])))

    return 0.5 * (_kl(p, m) + _kl(q, m))


def portrait_divergence(a: nx.DiGraph | nx.Graph | np.ndarray,
                        b: nx.DiGraph | nx.Graph | np.ndarray) -> float:
    """Jensen–Shannon divergence between two graphs' portraits, in bits.

    The random variable is a node PAIR: ``P(k, ℓ) ∝ k · B[ℓ, k]``, i.e. draw a node
    uniformly and then one of the ``k`` nodes in its ℓ-th shell. That weighting is
    Bagrow's, and it is what makes the measure a comparison of shortest-path structure
    rather than of the shell-count histogram.

    Accepts graphs or pre-computed portraits, so a runner can compute each portrait once
    and still call this pairwise.
    """
    ba = a if isinstance(a, np.ndarray) else portrait(a)
    bb = b if isinstance(b, np.ndarray) else portrait(b)
    ba, bb = _pad_to_same(ba, bb)
    v = np.tile(np.arange(ba.shape[1], dtype=np.float64), (ba.shape[0], 1))
    xa, xb = ba * v, bb * v
    sa, sb = xa.sum(), xb.sum()
    if sa <= 0 or sb <= 0:
        # A graph with no NODES has no distribution at all; returning 0 ("identical")
        # would be a lie. NaN propagates into the runner's report as a missing cell.
        # An EDGELESS graph is different and not guarded: it has a well-formed portrait
        # (all mass in the ℓ=0 row), which happens to be identical for every size — a
        # degeneracy the callers keep out by gating half-graphs at ≥ 2 edges.
        return float("nan")
    return _jsd((xa / sa).ravel(), (xb / sb).ravel())


# ── PrefixSpan (Pei 2001), single-item-per-position sequences ───────────────────

def prefixspan(seqs: Sequence[Sequence[str]], min_support: int,
               max_len: int = 4) -> list[tuple[tuple[str, ...], int]]:
    """Frequent GAPPED subsequences by prefix projection, with their sequence support.

    Support = the number of input sequences containing the pattern as a subsequence, NOT
    the number of occurrences: a pattern repeated eight times inside one bout must not
    out-rank one seen once in eight bouts, which is the same clustering discipline
    ``stats_rigor.coverage`` enforces elsewhere.

    Gapped, not contiguous. A grappler's own actions are interleaved with the opponent's
    and with unrecorded moments, so ``guard → sweep → back take`` must still match when
    something happened in between. This is the definition PrefixSpan mines; a
    contiguous-only variant would be a different (and, here, much sparser) question.

    ``max_len`` caps the pattern length; the number of patterns grows fast and nothing in
    these cells reads a pattern longer than four events.
    """
    if min_support < 1:
        raise ValueError("min_support must be >= 1")
    out: list[tuple[tuple[str, ...], int]] = []

    def grow(prefix: tuple[str, ...], proj: list[tuple[int, int]]) -> None:
        # First occurrence of each item at or after each projected position — counting a
        # sequence once however often the item repeats in it.
        support: dict[str, set[int]] = {}
        for si, pos in proj:
            for item in dict.fromkeys(seqs[si][pos:]):
                support.setdefault(item, set()).add(si)
        for item in sorted(support):
            sup = len(support[item])
            if sup < min_support:
                continue
            pattern = (*prefix, item)
            out.append((pattern, sup))
            if len(pattern) >= max_len:
                continue
            nxt: list[tuple[int, int]] = []
            for si, pos in proj:
                s = seqs[si]
                for j in range(pos, len(s)):
                    if s[j] == item:
                        nxt.append((si, j + 1))
                        break
            grow(pattern, nxt)

    grow((), [(i, 0) for i in range(len(seqs))])
    return out


def contains(seq: Sequence[str], pattern: Sequence[str]) -> bool:
    """Is ``pattern`` a (gapped) subsequence of ``seq``? Greedy — correct for subsequence
    matching because the earliest match never forecloses a later one."""
    it = iter(seq)
    return all(any(x == p for x in it) for p in pattern)


# ── δ-temporal motifs (Paranjape 2017) ──────────────────────────────────────────

@dataclass(frozen=True)
class TEdge:
    """One timestamped directed edge of a temporal network."""

    u: str
    v: str
    t: float


def motif_id(edges: Sequence[TEdge]) -> str:
    """Canonical identity of an ordered edge tuple: nodes renamed by first appearance.

    ``a→b, b→c, c→a`` and ``x→y, y→z, z→x`` are the same motif and get the same id
    (``0>1|1>2|2>0``). This produces exactly Paranjape's equivalence classes — their 6×6
    grid of 3-node/3-edge motifs is this same first-appearance canonicalisation, drawn —
    under our own numbering rather than theirs, because nothing here cross-references
    their figure.
    """
    order: dict[str, int] = {}
    parts: list[str] = []
    for e in edges:
        for node in (e.u, e.v):
            if node not in order:
                order[node] = len(order)
        parts.append(f"{order[e.u]}>{order[e.v]}")
    return "|".join(parts)


def motif_counts(edges: Sequence[TEdge], delta: float, k: int = 3,
                 max_nodes: int = 3) -> Counter[str]:
    """Count ordered ``k``-edge patterns spanning ≤ ``max_nodes`` nodes within a ``δ`` window.

    ponytail: brute force over ordered k-tuples, O(m^k). A bout yields ~18 temporal edges,
    so k=3 is ~5 800 tuples — the counting cost is invisible next to a single bootstrap
    draw. Upgrade path if this is ever pointed at a stream: Paranjape §4's linear-time
    counter.

    **Deliberate deviation from the paper, stated so it cannot be mistaken for their
    number:** these are NOT *induced* temporal motifs. Paranjape requires the k edges to be
    consecutive among all edges touching the motif's nodes; this counts every ordered
    k-tuple inside the δ window, which is a superset. The deviation is acceptable here
    because the counts are used as PREDICTIVE FEATURES and always compared against
    themselves under a different window (δ vs index) — the same superset on both sides of a
    paired difference cancels. It would not be acceptable in a published motif census.

    ``delta`` is compared against ``t_k − t_1`` (the paper's window). ``edges`` need not be
    sorted; it is sorted here.
    """
    if k < 1:
        raise ValueError("k must be >= 1")
    ordered = sorted(edges, key=lambda e: (e.t, e.u, e.v))
    m = len(ordered)
    counts: Counter[str] = Counter()

    def walk(chosen: list[int]) -> None:
        if len(chosen) == k:
            counts[motif_id([ordered[i] for i in chosen])] += 1
            return
        start = chosen[-1] + 1 if chosen else 0
        for j in range(start, m):
            if chosen and ordered[j].t - ordered[chosen[0]].t > delta:
                break
            nodes = {n for i in (*chosen, j) for n in (ordered[i].u, ordered[i].v)}
            if len(nodes) > max_nodes:
                continue
            walk([*chosen, j])

    walk([])
    return counts


def index_motif_counts(edges: Sequence[TEdge], span: int, k: int = 3,
                       max_nodes: int = 3) -> Counter[str]:
    """``motif_counts`` with the δ window replaced by an INDEX window: the k edges must lie
    within ``span`` positions of each other in the temporal ordering.

    This is the order-only control PoC-E14 pairs against the δ version. Same edges, same
    motif alphabet, same counter — the ONLY difference is whether the window is measured
    in seconds or in events, which is precisely the hypothesis "timing carries information
    beyond order".
    """
    ordered = sorted(edges, key=lambda e: (e.t, e.u, e.v))
    stamped = [TEdge(e.u, e.v, float(i)) for i, e in enumerate(ordered)]
    return motif_counts(stamped, float(span), k, max_nodes)


# ── retrieval scoring (the E5 / E14 self-recognition criterion) ─────────────────

@dataclass(frozen=True)
class Retrieval:
    """Per-query retrieval outcome for one method.

    ``reciprocal`` is kept per query (never only its mean) because every comparison between
    two methods is a PAIRED bootstrap over queries, and a mean cannot be re-resampled.
    """

    name: str
    ranks: list[int]
    reciprocal: list[float]
    n_candidates: int

    @property
    def mrr(self) -> float:
        return float(np.mean(self.reciprocal)) if self.reciprocal else float("nan")

    def top_k(self, k: int) -> float:
        if not self.ranks:
            return float("nan")
        return sum(1 for r in self.ranks if 1 <= r <= k) / len(self.ranks)


def rank_targets(dist: np.ndarray, truth: Sequence[int]) -> list[int]:
    """1-based rank of each query's true target under ``dist`` (rows = queries).

    Ties are resolved PESSIMISTICALLY — the target is placed after every candidate it ties
    with. A method that returns the same distance for everything (a degenerate signature)
    would otherwise score as if it had ranked correctly by luck, which is the failure mode
    a null arm exists to catch. NaN distances rank last.
    """
    ranks: list[int] = []
    for i, target in enumerate(truth):
        row = np.asarray(dist[i], dtype=np.float64)
        d = row[target]
        if not np.isfinite(d):
            ranks.append(len(row))
            continue
        better = int(np.sum(np.where(np.isfinite(row), row, np.inf) <= d)) - 1
        ranks.append(better + 1)
    return ranks


def retrieval(name: str, dist: np.ndarray, truth: Sequence[int]) -> Retrieval:
    ranks = rank_targets(dist, truth)
    return Retrieval(name, ranks, [1.0 / r for r in ranks], int(dist.shape[1]))


def vector_distances(vecs: Sequence[np.ndarray],
                     metric: Callable[[np.ndarray, np.ndarray], float]) -> np.ndarray:
    """Full (asymmetric-safe) distance matrix from a list of signature vectors."""
    n = len(vecs)
    out = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            out[i, j] = metric(vecs[i], vecs[j])
    return out


def pairwise_distances(items: Sequence[Any],
                       metric: Callable[[Any, Any], float]) -> np.ndarray:
    """Full distance matrix for a metric that takes the objects themselves."""
    n = len(items)
    out = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i, n):
            d = metric(items[i], items[j])
            out[i, j] = out[j, i] = d
    return out


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na <= 0 or nb <= 0:
        return float("nan")
    return 1.0 - float(np.dot(a, b) / (na * nb))


def counter_vector(counts: Mapping[str, float], keys: Sequence[str]) -> np.ndarray:
    """Dense vector over a fixed key order — the feature encoder both pattern cells share."""
    return np.asarray([float(counts.get(k, 0.0)) for k in keys], dtype=np.float64)


def size_signature(g: nx.DiGraph | nx.Graph) -> np.ndarray:
    """The null arm: (log n, log m, mean degree) — nothing but scale.

    Any signature that fails to beat this is measuring how much tape an athlete has, not
    how they grapple. It is a required arm, not a nicety: NetLSD advertises size-invariance
    and portrait divergence is known to react to size, so the two need the same yardstick.
    """
    ug = to_undirected(g)
    n, m = ug.number_of_nodes(), ug.number_of_edges()
    return np.asarray([np.log1p(n), np.log1p(m), (2.0 * m / n) if n else 0.0],
                      dtype=np.float64)


def degree_signature(g: nx.DiGraph | nx.Graph, bins: int = 8) -> np.ndarray:
    """Second null arm: the normalised degree-sequence histogram (log-spaced bins).

    Stronger than ``size_signature`` — it carries degree HETEROGENEITY, which is most of
    what cheap structural descriptors pick up. A spectral or path-based signature earns its
    complexity only by beating this.
    """
    ug = to_undirected(g)
    degs = np.asarray([d for _, d in ug.degree()], dtype=np.float64)
    if degs.size == 0:
        return np.zeros(bins, dtype=np.float64)
    edges = np.logspace(0.0, np.log10(max(degs.max(), 1.0) + 1.0), bins + 1)
    hist, _ = np.histogram(degs, bins=edges)
    total = hist.sum()
    return hist.astype(np.float64) / total if total else hist.astype(np.float64)


def as_sequences(chains: Iterable[Sequence[str]]) -> list[list[str]]:
    """Materialise chains as lists — PrefixSpan indexes them repeatedly."""
    return [list(c) for c in chains]
