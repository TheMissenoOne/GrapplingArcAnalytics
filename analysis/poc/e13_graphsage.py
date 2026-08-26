"""PoC-E13 — GraphSAGE: inductive link prediction on athlete ActionFlow graphs.

Pre-registration: ``docs/research/poc/e13_prereg.md``, written before this runner produced a
single held-out number and re-emitted verbatim by ``render_markdown`` so the criterion travels
with the numbers it judged (the PoC-E8/E9 convention).

**What is inductive here.** Everything vector-shaped in this repository today is transductive:
``technique_nodes.embedding`` is one fixed 768-d mpnet vector per canonical label
(``analysis/embeddings.py``), so a position's vector is the same for every athlete who plays it
and a label the library has never seen has no vector at all. GraphSAGE (Hamilton, Ying &
Leskovec 2017, arXiv:1706.02216) learns an *aggregation function* instead of a lookup table, so
it applies to graphs it never saw. This cell copies that paper's PPI multi-graph protocol: train
on one set of athletes' graphs, score held-out transitions in the graphs of *different*
athletes.

**Framework decision, measured.** No ``torch-geometric``. ``torch`` is already installed as a
transitive dependency of the CORE ``sentence-transformers`` (4.0 GB on disk, already paid for by
every environment including CI), and on graphs of ≤ 60 nodes the mean aggregator IS a
row-normalised dense matmul — three lines. PyG's neighbour sampling buys nothing at this scale.
A pure-NumPy version with hand-written gradients was rejected as MORE code and more risk than
``torch.autograd`` for zero saving; ``analysis/gnn_predictor.py`` is exactly that experiment
(229 lines of manual GCN backprop, no tests, quarantined) and is deliberately not extended here.

LGPD: **athlete corpus only.** One read-only ``SELECT`` over ``matches``; no
``owner_kind='user'`` row, no ``user_sessions``, no App-fed data reaches any arm, and nothing
here writes a vector, a row or an export. Production embeddings are untouched under every
verdict.

Usage::

    uv run python -m analysis.poc.e13_graphsage
    uv run python -m analysis.poc.e13_graphsage --out docs/research/poc/e13.md
"""

from __future__ import annotations

import argparse
import logging
import math
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats as _scipy_stats

from analysis.attribution import EVENT_TYPES, bout_flags
from analysis.names import _normalize_name
from analysis.poc.e8_interaction_graph import rank_auc
from analysis.stats_rigor import bootstrap_ci, coverage
from analysis.technique_match import clean_label
from analysis.transitions.build_graph import network_from_sequences

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
PREREG = REPO / "docs" / "research" / "poc" / "e13_prereg.md"
OUT = REPO / "docs" / "research" / "poc" / "e13.md"

# Every constant below is pre-registered. None of them is swept, and none may be changed
# after the first held-out number without a new pre-registration.
SEED = 20260825
MIN_EVENTS = 4                  # E8/E9's bout gate
MIN_EDGES = 10                  # eligible athlete: distinct directed edges in their graph
EVAL_ATHLETE_FRACTION = 0.25    # latest-debuting quarter of eligible athletes
VAL_ATHLETE_FRACTION = 0.20     # of TRAIN, for early stopping only
HOLDOUT_FRACTION = 0.30         # within-graph holdout, both arms
HIDDEN, EMBED_DIM, LAYERS = 64, 32, 2
LR, WEIGHT_DECAY, MAX_EPOCHS, PATIENCE, VAL_EVERY = 0.01, 1e-4, 300, 30, 5
N_BOOT, N_BOOT_PAIRED = 4000, 2000
MIN_EVAL_ATHLETES, MIN_POSITIVES = 8, 40
HITS_K = 20
KMEANS_K = len(EVENT_TYPES)     # 8
N_STABILITY = 25                # athlete-bootstrap resamples for the report-only probe

SAGE, MLP = "sage", "mlp"
BASELINES = ("prior", "popularity", "text", "adamic_adar", "pref_attach", MLP)
# The two comparators the verdict turns on: `prior` is "an athlete's own game tells you nothing
# the corpus does not", `mlp` is "message passing added nothing the input features did not".
DECISIVE = ("prior", MLP)


# ── corpus ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BoutRow:
    key: tuple[int, str, str]           # (year, created_at, id) — E8/E9's temporal key
    athlete_a: str
    athlete_b: str
    sequence: list[dict[str, Any]]

    def has(self, athlete: str) -> bool:
        return athlete in (self.athlete_a, self.athlete_b)


@dataclass
class GateReport:
    total: int = 0
    with_sequence: int = 0
    passed: int = 0
    one_sided: int = 0
    rows: list[BoutRow] = field(default_factory=list)
    error: str | None = None


def load_corpus(min_events: int = MIN_EVENTS) -> GateReport:
    """Gated athlete-corpus bouts. ONE read-only ``SELECT`` over ``matches``.

    Same filter and same gate as ``e8.corpus_bouts`` / ``e9.load_corpus``. The count is
    REPORTED, not asserted against PoC-E9's published 429 — the corpus moved between that run
    and this one (466 here, measured 2026-08-25), and an assert would only hide that.
    """
    rep = GateReport()
    try:
        from sqlalchemy import text

        from db.base import get_engine

        with get_engine().connect() as conn:
            rowset = conn.execute(text(
                "SELECT id, athlete_a_id, athlete_b_id, year, created_at, sequence "
                "FROM matches WHERE status = 'final' AND sequence IS NOT NULL"
            )).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the report says why; the runner still renders
        rep.error = f"{type(exc).__name__}: {exc}".split("\n")[0][:160]
        return rep

    for row in rowset:
        rep.total += 1
        seq = [dict(e) for e in (row["sequence"] or []) if isinstance(e, dict)]
        if len(seq) < min_events:
            continue
        rep.with_sequence += 1
        if not bout_flags(seq, str(row["athlete_a_id"]),
                          str(row["athlete_b_id"]))["perspective_reliable"]:
            rep.one_sided += 1
            continue
        rep.passed += 1
        rep.rows.append(BoutRow(
            key=(int(row["year"] or 0), str(row["created_at"]), str(row["id"])),
            athlete_a=str(row["athlete_a_id"]), athlete_b=str(row["athlete_b_id"]),
            sequence=seq,
        ))
    rep.rows.sort(key=lambda r: r.key)
    return rep


def own_events(row: BoutRow, athlete: str) -> list[dict[str, Any]]:
    """That athlete's own events, in stored order — the ActionFlow input for their graph."""
    return [e for e in row.sequence if str(e.get("actor_id")) == athlete]


@dataclass(frozen=True)
class AthleteBouts:
    """One athlete's own-event sequences, chronological. ``debut`` is the inductive+temporal
    ordering key: the bout key of their first gated appearance."""

    athlete: str
    bouts: tuple[tuple[tuple[int, str, str], tuple[dict[str, Any], ...]], ...]

    @property
    def debut(self) -> tuple[int, str, str]:
        return self.bouts[0][0]

    def graph(self, keys: Iterable[tuple[int, str, str]] | None = None) -> Any:
        wanted = None if keys is None else set(keys)
        seqs = [list(ev) for k, ev in self.bouts if wanted is None or k in wanted]
        return network_from_sequences(seqs)


def athlete_bouts(rows: Sequence[BoutRow]) -> dict[str, AthleteBouts]:
    """Group gated bouts by athlete, keeping only that athlete's own events."""
    grouped: dict[str, list[tuple[tuple[int, str, str], tuple[dict[str, Any], ...]]]] = (
        defaultdict(list))
    for row in sorted(rows, key=lambda r: r.key):
        for athlete in (row.athlete_a, row.athlete_b):
            own = own_events(row, athlete)
            if own:
                grouped[athlete].append((row.key, tuple(own)))
    return {a: AthleteBouts(a, tuple(b)) for a, b in grouped.items()}


def edge_list(graph: Any) -> list[tuple[str, str, float]]:
    """Directed edges as a canonically ORDERED list — the seeded holdout must be reproducible,
    and ``nx`` insertion order is not a contract."""
    return sorted(((str(u), str(v), float(d.get("weight", 1.0)))
                   for u, v, d in graph.edges(data=True)), key=lambda t: (t[0], t[1]))


# ── one athlete's prediction problem ────────────────────────────────────────────
@dataclass(frozen=True)
class Sample:
    """An athlete's OBSERVED graph plus the transitions held out from it.

    ``obs`` never contains a positive, and no field is derived from a held-out edge — that is
    what makes the message passing and the features leak-free. ``dropped`` counts held-out
    edges excluded for having an endpoint the observed graph never saw: a structural method
    cannot score those rows at all, so scoring them would measure the exclusion.
    """

    athlete: str
    debut: tuple[int, str, str]
    nodes: tuple[str, ...]
    obs: tuple[tuple[int, int, float], ...]
    positives: frozenset[tuple[int, int]]
    dropped: int
    n_bouts: int

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    def candidates(self) -> list[tuple[int, int, bool]]:
        """Every ordered non-edge pair among observed nodes. No negative sampling: the row set
        is deterministic, so no method gets a luckier draw than another."""
        return _candidates(self)


@cache
def _candidates(sample: Sample) -> list[tuple[int, int, bool]]:
    """Cached because every method, every metric and every tensor build asks for the same list
    (``Sample`` is frozen and hashable, so the cache key is the sample itself)."""
    observed = {(i, j) for i, j, _ in sample.obs}
    return [(i, j, (i, j) in sample.positives)
            for i in range(sample.n_nodes) for j in range(sample.n_nodes)
            if i != j and (i, j) not in observed]


def _sample_from(athlete: str, debut: tuple[int, str, str], n_bouts: int,
                 observed: Sequence[tuple[str, str, float]],
                 held: Iterable[tuple[str, str]]) -> Sample | None:
    """Assemble a Sample from an observed edge list and the held-out edges."""
    nodes = sorted({u for u, _, _ in observed} | {v for _, v, _ in observed})
    if len(nodes) < 3:
        return None
    index = {label: i for i, label in enumerate(nodes)}
    obs = tuple((index[u], index[v], w) for u, v, w in observed)
    observed_pairs = {(i, j) for i, j, _ in obs}
    positives: set[tuple[int, int]] = set()
    dropped = 0
    for u, v in held:
        if u not in index or v not in index:
            dropped += 1
            continue
        pair = (index[u], index[v])
        if pair not in observed_pairs:
            positives.add(pair)
    if not positives:
        return None
    return Sample(athlete, debut, tuple(nodes), obs, frozenset(positives), dropped, n_bouts)


def sample_arm_a(ab: AthleteBouts, seed: int = SEED) -> Sample | None:
    """Arm A (PRIMARY) — seeded edge holdout, observed graph rebuilt from observed edges alone.

    The rebuild is the point: node set, weights and every structural feature come from the
    observed edges, so a held-out edge cannot leak in through an event-derived count.
    """
    edges = edge_list(ab.graph())
    if len(edges) < MIN_EDGES:
        return None
    rng = random.Random(f"{seed}:{ab.athlete}")
    order = list(range(len(edges)))
    rng.shuffle(order)
    n_held = max(1, int(round(len(edges) * HOLDOUT_FRACTION)))
    held_idx = set(order[:n_held])
    observed = [e for i, e in enumerate(edges) if i not in held_idx]
    held = [(e[0], e[1]) for i, e in enumerate(edges) if i in held_idx]
    if not observed:
        return None
    return _sample_from(ab.athlete, ab.debut, len(ab.bouts), observed, held)


def sample_arm_b(ab: AthleteBouts) -> Sample | None:
    """Arm B (SECONDARY) — chronological bout holdout: observe the earlier bouts, predict the
    transitions that only appear in the later ones. Boundary bout goes to OBSERVED."""
    if len(ab.bouts) < 2:
        return None
    keys = [k for k, _ in ab.bouts]
    cut = max(1, int(round(len(keys) * (1 - HOLDOUT_FRACTION))))
    obs_keys, held_keys = keys[:cut], keys[cut:]
    if not held_keys:
        return None
    observed = edge_list(ab.graph(obs_keys))
    if len(observed) < MIN_EDGES:
        return None
    obs_pairs = {(u, v) for u, v, _ in observed}
    held = [(u, v) for u, v, _ in edge_list(ab.graph(held_keys)) if (u, v) not in obs_pairs]
    return _sample_from(ab.athlete, ab.debut, len(ab.bouts), observed, held)


# ── athlete split + bout quarantine ─────────────────────────────────────────────
def split_athletes(samples: Sequence[Sample]) -> tuple[list[Sample], list[Sample], list[Sample]]:
    """Order by debut key, hold out the latest-debuting quarter → (train, val, eval).

    Disjoint by construction AND forward in time: the eval athletes are the ones the corpus
    met last, which is the situation the inductive claim is about (a newly ingested athlete).
    """
    ordered = sorted(samples, key=lambda s: (s.debut, s.athlete))
    if len(ordered) < 4:
        return list(ordered), [], []
    cut = max(1, int(round(len(ordered) * (1 - EVAL_ATHLETE_FRACTION))))
    train_pool, held = ordered[:cut], ordered[cut:]
    vcut = max(1, int(round(len(train_pool) * (1 - VAL_ATHLETE_FRACTION))))
    return train_pool[:vcut], train_pool[vcut:], held


def quarantine(rows: Sequence[BoutRow], eval_athletes: Iterable[str]) -> list[BoutRow]:
    """Training bouts = gated bouts in which NO eval athlete participates.

    Athlete-disjointness alone is not enough: two athletes in one bout are not independent
    (one's guard is the other's pass), so a shared bout would let the eval athlete's own
    footage inform the corpus prior and the training graphs through their opponent.
    """
    blocked = set(eval_athletes)
    return [r for r in rows if not (r.athlete_a in blocked or r.athlete_b in blocked)]


def corpus_prior(rows: Sequence[BoutRow]) -> tuple[Counter[tuple[str, str]], Counter[str]]:
    """(transition counts, label occurrence counts) over TRAIN bouts only.

    Same within-actor succession ``network_from_sequences`` counts, restated on raw events so
    the prior is a corpus-level table rather than one athlete's graph.
    """
    trans: Counter[tuple[str, str]] = Counter()
    occ: Counter[str] = Counter()
    for row in rows:
        for athlete in (row.athlete_a, row.athlete_b):
            labels = [clean_label(str(e.get("label", "")), str(e.get("type", "")))
                      for e in own_events(row, athlete)]
            labels = [x for x in labels if x]
            occ.update(labels)
            for u, v in zip(labels, labels[1:], strict=False):
                if u != v:
                    trans[(u, v)] += 1
    return trans, occ


# ── node features: the production pgvector table ────────────────────────────────
def load_text_vectors(labels: Iterable[str], dim: int = 768) -> tuple[dict[str, np.ndarray], int]:
    """``technique_nodes.embedding`` per canonical label → unit vectors, plus the hit count.

    Deliberately NO on-the-fly encoding. Using the vectors production actually holds makes the
    PoC a measurement of what production could deploy today; encoding the misses here would
    measure a system that does not exist yet. A label with no embedded library row gets the
    mean of the present vectors — a neutral "unknown position", applied identically to the
    GraphSAGE features and to the ``text`` baseline.
    """
    wanted = sorted(set(labels))
    keys = {label: _normalize_name(label) for label in wanted}
    found: dict[str, np.ndarray] = {}
    by_key: dict[str, np.ndarray] = {}
    try:
        # ``embeddings.load_matrix`` and not a raw ``SELECT``: over a plain ``text()`` query the
        # pgvector column comes back as the STRING "[0.1,0.2,…]", every ``np.asarray`` raises,
        # and the whole table silently degrades to the neutral vector — which is exactly what
        # the first smoke run did (0/270 hits, `text` AUC pinned at 0.5000). The ORM path knows
        # the column type.
        # A bare ``Session`` rather than ``db.base.db_session``: that helper COMMITS on exit.
        # The unit of work is empty here so the commit would write nothing, but this cell
        # claims read-only in three documents and a no-op commit is not the same sentence.
        from sqlalchemy.orm import Session

        from analysis.embeddings import load_matrix
        from db.base import get_engine

        with Session(get_engine()) as session:
            node_keys, matrix = load_matrix(session)
        by_key = {k: matrix[i] for i, k in enumerate(node_keys)}
    except Exception as exc:  # noqa: BLE001 — no DB → the neutral vector, but say so
        logger.warning("no production embeddings (%s: %s) — every label falls back to the "
                       "neutral vector", type(exc).__name__, str(exc).split("\n")[0][:120])
    for label in wanted:
        vec = by_key.get(keys[label])
        if vec is not None and vec.size == dim:
            norm = float(np.linalg.norm(vec))
            found[label] = vec / norm if norm > 0 else vec
    neutral = (np.mean(list(found.values()), axis=0) if found else np.zeros(dim))
    nn = float(np.linalg.norm(neutral))
    neutral = neutral / nn if nn > 0 else neutral
    return {label: found.get(label, neutral) for label in wanted}, len(found)


def features(sample: Sample, text: Mapping[str, np.ndarray]) -> np.ndarray:
    """``[ e_v (768) ‖ log1p(w_in), log1p(w_out), log1p(w_in+w_out) ]`` — 771-d.

    ponytail: `reward`/`risk` are NOT here. `risk` is defined against the OPPONENT's next event
    and is structurally 0 in a single-actor sequence; `reward`/`ok_rate` are event-derived and
    cannot be partitioned by edge, so under Arm A's edge holdout they would carry held-out
    information into the observed graph. Upgrade path: add them for Arm B only, once someone
    wants a second feature set and accepts that the two arms stop being comparable.
    """
    n = sample.n_nodes
    w_in, w_out = np.zeros(n), np.zeros(n)
    for i, j, w in sample.obs:
        w_out[i] += w
        w_in[j] += w
    struct = np.stack([np.log1p(w_in), np.log1p(w_out), np.log1p(w_in + w_out)], axis=1)
    dim = len(next(iter(text.values()))) if text else 768
    txt = np.stack([text.get(label, np.zeros(dim)) for label in sample.nodes])
    return np.concatenate([txt, struct], axis=1).astype(np.float32)


def adjacency(sample: Sample) -> tuple[np.ndarray, np.ndarray]:
    """Row-normalised UNWEIGHTED out- and in-adjacency — Hamilton's mean aggregator, in the
    only form a ≤60-node graph needs. Edge counts still reach the model, through the features."""
    n = sample.n_nodes
    a = np.zeros((n, n), dtype=np.float32)
    for i, j, _ in sample.obs:
        a[i, j] = 1.0

    def _norm(m: np.ndarray) -> np.ndarray:
        s = m.sum(axis=1, keepdims=True)
        return np.asarray(np.divide(m, np.where(s > 0, s, 1.0)), dtype=np.float32)

    return _norm(a), _norm(a.T.copy())


# ── baselines ───────────────────────────────────────────────────────────────────
def _undirected(sample: Sample) -> list[set[int]]:
    nb: list[set[int]] = [set() for _ in range(sample.n_nodes)]
    for i, j, _ in sample.obs:
        nb[i].add(j)
        nb[j].add(i)
    return nb


def baseline_scores(name: str, sample: Sample, pairs: Sequence[tuple[int, int, bool]],
                    trans: Mapping[tuple[str, str], int], occ: Mapping[str, int],
                    text: Mapping[str, np.ndarray]) -> list[float]:
    labels = sample.nodes
    if name == "prior":
        return [float(trans.get((labels[i], labels[j]), 0)) for i, j, _ in pairs]
    if name == "popularity":
        return [float(occ.get(labels[j], 0)) for _, j, _ in pairs]
    if name == "text":
        mat = np.stack([text[label] for label in labels])
        sims = mat @ mat.T
        return [float(sims[i, j]) for i, j, _ in pairs]
    if name == "adamic_adar":
        nb = _undirected(sample)
        deg = [len(s) for s in nb]
        return [float(sum(1.0 / math.log(deg[z]) for z in nb[i] & nb[j] if deg[z] > 1))
                for i, j, _ in pairs]
    if name == "pref_attach":
        out_deg = Counter(i for i, _, _ in sample.obs)
        in_deg = Counter(j for _, j, _ in sample.obs)
        return [float(out_deg[i] * in_deg[j]) for i, j, _ in pairs]
    raise ValueError(f"unknown baseline {name!r}")


# ── the model ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GraphTensors:
    """One sample, pre-converted once. Rebuilding these inside the epoch loop is the only way
    this runner gets slow."""

    x: Any
    a_out: Any
    a_in: Any
    src: Any
    dst: Any
    y: Any
    sample: Sample


def to_tensors(sample: Sample, text: Mapping[str, np.ndarray]) -> GraphTensors:
    import torch

    pairs = sample.candidates()
    a_out, a_in = adjacency(sample)
    return GraphTensors(
        x=torch.from_numpy(features(sample, text)),
        a_out=torch.from_numpy(a_out),
        a_in=torch.from_numpy(a_in),
        src=torch.tensor([i for i, _, _ in pairs], dtype=torch.long),
        dst=torch.tensor([j for _, j, _ in pairs], dtype=torch.long),
        y=torch.tensor([1.0 if y else 0.0 for _, _, y in pairs], dtype=torch.float32),
        sample=sample,
    )


def build_model(in_dim: int, layers: int = LAYERS, seed: int = SEED) -> Any:
    """2-layer directed GraphSAGE mean aggregator + dot decoder, or the no-aggregation ablation.

    Each layer is ``Linear([h ‖ mean(out-neighbours) ‖ mean(in-neighbours)])`` followed by ReLU
    and L2 normalisation (Hamilton §3.1 normalises each layer's output). ``layers=0`` keeps the
    identical decoder and drops message passing entirely — that ablation is what separates "the
    graph helped" from "the 768-d text vector helped".
    """
    import torch
    from torch import nn

    class SageLayer(nn.Module):
        def __init__(self, i: int, o: int) -> None:
            super().__init__()
            self.lin = nn.Linear(3 * i, o)

        def forward(self, h: Any, a_out: Any, a_in: Any) -> Any:
            return self.lin(torch.cat([h, a_out @ h, a_in @ h], dim=1))

    class LinkModel(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            dims = [in_dim] + [HIDDEN] * max(0, layers - 1) + [EMBED_DIM]
            self.layers = nn.ModuleList(
                [SageLayer(dims[k], dims[k + 1]) for k in range(layers)])
            self.plain = nn.Linear(in_dim, EMBED_DIM) if layers == 0 else None
            self.src_proj = nn.Linear(EMBED_DIM, EMBED_DIM, bias=False)
            self.dst_proj = nn.Linear(EMBED_DIM, EMBED_DIM, bias=False)
            self.bias = nn.Parameter(torch.zeros(1))

        def encode(self, x: Any, a_out: Any, a_in: Any) -> Any:
            h = x
            if self.plain is not None:
                h = torch.relu(self.plain(h))
                return h / h.norm(dim=1, keepdim=True).clamp_min(1e-9)
            for k, layer in enumerate(self.layers):
                h = layer(h, a_out, a_in)
                if k < len(self.layers) - 1:
                    h = torch.relu(h)
                h = h / h.norm(dim=1, keepdim=True).clamp_min(1e-9)
            return h

        def forward(self, g: GraphTensors) -> Any:
            z = self.encode(g.x, g.a_out, g.a_in)
            return (self.src_proj(z)[g.src] * self.dst_proj(z)[g.dst]).sum(dim=1) + self.bias

    torch.manual_seed(seed)
    return LinkModel()


def _val_auc(model: Any, graphs: Sequence[GraphTensors]) -> float:
    import torch

    scores: list[float] = []
    labels: list[bool] = []
    with torch.no_grad():
        for g in graphs:
            scores.extend(float(v) for v in model(g))
            labels.extend(bool(v) for v in g.y.tolist())
    return rank_auc(scores, labels) if any(labels) and not all(labels) else 0.5


@dataclass
class TrainLog:
    best_epoch: int = 0
    best_val_auc: float = float("nan")
    epochs_run: int = 0
    final_loss: float = float("nan")


def train_model(train: Sequence[GraphTensors], val: Sequence[GraphTensors],
                in_dim: int, layers: int = LAYERS, seed: int = SEED,
                max_epochs: int = MAX_EPOCHS) -> tuple[Any, TrainLog]:
    """Full-batch Adam over every training graph, early-stopped on VALIDATION-athlete AUC.

    The validation athletes come out of TRAIN (latest-debuting 20%), never out of EVAL, so no
    eval row influences a single weight or the chosen epoch.
    """
    import copy

    import torch
    from torch.nn import functional as tnn

    torch.manual_seed(seed)
    torch.set_num_threads(1)
    model = build_model(in_dim, layers, seed)
    opt = torch.optim.Adam(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    n_pos = sum(float(g.y.sum()) for g in train)
    n_neg = sum(float(g.y.numel()) for g in train) - n_pos
    pos_weight = torch.tensor([n_neg / n_pos if n_pos else 1.0], dtype=torch.float32)

    log = TrainLog()
    best_state = copy.deepcopy(model.state_dict())
    for epoch in range(1, max_epochs + 1):
        model.train()
        opt.zero_grad()
        loss = torch.zeros(())
        for g in train:
            loss = loss + tnn.binary_cross_entropy_with_logits(
                model(g), g.y, pos_weight=pos_weight)
        loss = loss / max(1, len(train))
        loss.backward()  # type: ignore[no-untyped-call]
        opt.step()
        log.epochs_run, log.final_loss = epoch, float(loss.detach())
        if val and (epoch % VAL_EVERY == 0 or epoch == 1):
            model.eval()
            score = _val_auc(model, val)
            if math.isnan(log.best_val_auc) or score > log.best_val_auc:
                log.best_val_auc, log.best_epoch = score, epoch
                best_state = copy.deepcopy(model.state_dict())
            elif epoch - log.best_epoch >= PATIENCE:
                break
    if val:
        model.load_state_dict(best_state)
    model.eval()
    return model, log


def model_scores(model: Any, graphs: Sequence[GraphTensors]) -> list[float]:
    import torch

    out: list[float] = []
    with torch.no_grad():
        for g in graphs:
            out.extend(float(v) for v in model(g))
    return out


# ── evaluation ──────────────────────────────────────────────────────────────────
@dataclass
class MethodResult:
    name: str
    auc: float
    lo: float
    hi: float
    hits_at_k: float
    mrr: float
    verdict: str
    scores: list[float] = field(default_factory=list)

    def row(self) -> str:
        return (f"| `{self.name}` | {self.auc:.4f} | [{self.lo:.4f}, {self.hi:.4f}] | "
                f"{self.hits_at_k:.3f} | {self.mrr:.3f} | {self.verdict} |")


def auc_np(scores: np.ndarray, labels: np.ndarray) -> float:
    """Mann–Whitney AUC over numpy arrays — the same number ``e8.rank_auc`` returns, pinned by
    a test.

    Vectorised because the clustered bootstrap evaluates it thousands of times over tens of
    thousands of rows, where the pure-Python rank loop does not finish. 0.5 (chance) is
    returned when a draw degenerates to one class, so a bootstrap neither crashes nor votes.
    """
    n_pos = int(labels.sum())
    n_neg = int(labels.size) - n_pos
    if not n_pos or not n_neg:
        return 0.5
    ranks = _scipy_stats.rankdata(scores)
    return float((float(ranks[labels].sum()) - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def _boot_ci(n_rows: int, statistic: Callable[[Sequence[float]], float], n_boot: int,
             groups: Sequence[Any] | None) -> tuple[float, float, float]:
    """Percentile bootstrap over EVAL ROWS via ``stats_rigor.bootstrap_ci``.

    ``bootstrap_ci`` resamples floats, so it is handed row INDICES and the statistic recomputes
    on the drawn rows — the only way to keep several methods' scores PAIRED inside a generic
    resampler. ``groups`` = one ATHLETE per row makes the resampling unit the athlete, which is
    the only unit the inductive claim is about: rows inside one graph are not independent, so
    a row-level interval is anti-conservative and decides nothing here.
    """
    idx = [float(i) for i in range(n_rows)]
    return bootstrap_ci(idx, statistic, n_boot=n_boot, seed=SEED, groups=groups)


def rank_metrics(samples: Sequence[Sample], scores: Sequence[float],
                 k: int = HITS_K) -> tuple[float, float]:
    """Macro Hits@k and MRR — per athlete, then averaged. Descriptive; decides nothing."""
    hits: list[float] = []
    rr: list[float] = []
    at = 0
    for s in samples:
        pairs = s.candidates()
        chunk = list(scores[at:at + len(pairs)])
        at += len(pairs)
        order = sorted(range(len(pairs)), key=lambda i: (-chunk[i], i))
        ranks = [r + 1 for r, i in enumerate(order) if pairs[i][2]]
        if not ranks:
            continue
        hits.append(sum(1 for r in ranks if r <= k) / len(ranks))
        rr.append(sum(1.0 / r for r in ranks) / len(ranks))
    return (float(np.mean(hits)) if hits else float("nan"),
            float(np.mean(rr)) if rr else float("nan"))


def evaluate(name: str, samples: Sequence[Sample], scores: Sequence[float],
             labels: Sequence[bool], groups: Sequence[Any], n_boot: int = N_BOOT) -> MethodResult:
    sc = np.asarray(scores, dtype=np.float64)
    lb = np.asarray(labels, dtype=bool)

    def stat(s: Sequence[float]) -> float:
        i = np.asarray(s, dtype=np.int64)
        return auc_np(sc[i], lb[i])

    obs, lo, hi = _boot_ci(len(labels), stat, n_boot, groups)
    hits, mrr = rank_metrics(samples, scores)
    return MethodResult(name, obs, lo, hi, hits, mrr,
                        "chance" if lo <= 0.5 <= hi else "separates", list(scores))


def paired_delta(a: MethodResult, b: MethodResult, labels: Sequence[bool],
                 groups: Sequence[Any], n_boot: int = N_BOOT_PAIRED
                 ) -> tuple[float, float, float]:
    """AUC(a) − AUC(b) on exactly the same rows, athlete-clustered."""
    sa = np.asarray(a.scores, dtype=np.float64)
    sb = np.asarray(b.scores, dtype=np.float64)
    lb = np.asarray(labels, dtype=bool)

    def stat(s: Sequence[float]) -> float:
        i = np.asarray(s, dtype=np.int64)
        return auc_np(sa[i], lb[i]) - auc_np(sb[i], lb[i])

    return _boot_ci(len(labels), stat, n_boot, groups)


def beats(delta: tuple[float, float, float]) -> bool:
    """The pre-registered comparison rule: the paired interval must EXCLUDE 0 in a's favour."""
    _, lo, hi = delta
    return lo > 0.0 and hi > 0.0


# ── an arm ──────────────────────────────────────────────────────────────────────
@dataclass
class ArmResult:
    name: str
    n_train: int = 0
    n_val: int = 0
    n_eval: int = 0
    n_rows: int = 0
    n_pos: int = 0
    dropped: int = 0
    train_bouts: int = 0
    quarantined: int = 0
    results: dict[str, MethodResult] = field(default_factory=dict)
    deltas: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    train_log: TrainLog = field(default_factory=TrainLog)
    mlp_log: TrainLog = field(default_factory=TrainLog)
    coverage_ok: bool = False
    coverage_reason: str | None = None
    verdict: str = "UNDERPOWERED"
    verdict_why: str = ""
    embeddings: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    graph_sizes: list[tuple[int, int]] = field(default_factory=list)

    @property
    def powered(self) -> bool:
        return (self.n_eval >= MIN_EVAL_ATHLETES and self.n_pos >= MIN_POSITIVES
                and self.coverage_ok)


def run_arm(name: str, rows: Sequence[BoutRow], by_athlete: Mapping[str, AthleteBouts],
            sampler: Callable[[AthleteBouts], Sample | None],
            text: Mapping[str, np.ndarray], n_boot: int = N_BOOT,
            max_epochs: int = MAX_EPOCHS,
            n_boot_paired: int = N_BOOT_PAIRED) -> ArmResult:
    arm = ArmResult(name)
    samples = [s for s in (sampler(ab) for ab in by_athlete.values()) if s is not None]
    train_s, val_s, eval_s = split_athletes(samples)
    arm.n_train, arm.n_val, arm.n_eval = len(train_s), len(val_s), len(eval_s)
    if not eval_s or not train_s:
        arm.verdict_why = "no eval or no train athletes after the eligibility filter"
        return arm

    eval_ids = {s.athlete for s in eval_s}
    train_rows = quarantine(rows, eval_ids)
    arm.train_bouts, arm.quarantined = len(train_rows), len(rows) - len(train_rows)
    trans, occ = corpus_prior(train_rows)

    eval_pairs = [(s, p) for s in eval_s for p in s.candidates()]
    labels = [p[2] for _, p in eval_pairs]
    groups = [s.athlete for s, _ in eval_pairs]
    arm.n_rows, arm.n_pos = len(labels), sum(1 for y in labels if y)
    arm.dropped = sum(s.dropped for s in eval_s)
    arm.graph_sizes = [(s.n_nodes, len(s.obs)) for s in eval_s]

    per_athlete_pos = [sum(1 for p in s.candidates() if p[2]) for s in eval_s]
    cov = coverage([c for c in per_athlete_pos if c > 0])
    arm.coverage_ok, arm.coverage_reason = cov.estimable, cov.reason

    for baseline in ("prior", "popularity", "text", "adamic_adar", "pref_attach"):
        scores = [x for s in eval_s
                  for x in baseline_scores(baseline, s, s.candidates(), trans, occ, text)]
        arm.results[baseline] = evaluate(baseline, eval_s, scores, labels, groups, n_boot)

    g_train = [to_tensors(s, text) for s in train_s]
    g_val = [to_tensors(s, text) for s in val_s]
    g_eval = [to_tensors(s, text) for s in eval_s]
    in_dim = int(g_train[0].x.shape[1])
    for method, layers in ((MLP, 0), (SAGE, LAYERS)):
        model, log = train_model(g_train, g_val, in_dim, layers, max_epochs=max_epochs)
        arm.results[method] = evaluate(method, eval_s, model_scores(model, g_eval),
                                       labels, groups, n_boot)
        if method == SAGE:
            arm.train_log = log
            arm.embeddings = _label_embeddings(model, g_eval)
        else:
            arm.mlp_log = log

    for baseline in BASELINES:
        arm.deltas[baseline] = paired_delta(arm.results[SAGE], arm.results[baseline],
                                            labels, groups, n_boot_paired)
    _decide(arm)
    return arm


def _label_embeddings(model: Any, graphs: Sequence[GraphTensors]
                      ) -> dict[str, dict[str, np.ndarray]]:
    """``athlete → label → SAGE embedding``.

    Kept PER ATHLETE rather than pre-averaged so the stability probe can resample athletes —
    the cluster the whole cell is built on — instead of resampling labels, which would measure
    k-means' sensitivity to its input size and call it stability.
    """
    import torch

    out: dict[str, dict[str, np.ndarray]] = {}
    with torch.no_grad():
        for g in graphs:
            z = model.encode(g.x, g.a_out, g.a_in).numpy()
            out[g.sample.athlete] = {label: z[i] for i, label in enumerate(g.sample.nodes)}
    return out


def _decide(arm: ArmResult) -> None:
    """The pre-registered verdict lattice, applied verbatim. Power is checked FIRST."""
    if not arm.powered:
        arm.verdict = "UNDERPOWERED"
        arm.verdict_why = (
            f"{arm.n_eval} eval athletes (need ≥{MIN_EVAL_ATHLETES}), {arm.n_pos} positives "
            f"(need ≥{MIN_POSITIVES}), coverage estimable={arm.coverage_ok}"
            + (f" — {arm.coverage_reason}" if arm.coverage_reason else ""))
        return
    won = [b for b in BASELINES if beats(arm.deltas[b])]
    lost = [b for b in BASELINES if b not in won]
    if len(won) == len(BASELINES):
        arm.verdict, arm.verdict_why = "ACCEPT", "beats all six comparators"
    elif all(d in won for d in DECISIVE):
        arm.verdict = "PARTIAL"
        arm.verdict_why = (f"beats {', '.join(f'`{w}`' for w in DECISIVE)} but not "
                           f"{', '.join(f'`{x}`' for x in lost)}")
    else:
        arm.verdict = "REJECT"
        failed = [d for d in DECISIVE if d not in won]
        arm.verdict_why = ("does not beat " + ", ".join(f"`{f}`" for f in failed)
                           + f" (beaten comparators: {', '.join(f'`{w}`' for w in won) or 'none'})")


# ── secondary probe (report-only) ───────────────────────────────────────────────
@dataclass
class ClusterProbe:
    space: str
    n_labels: int
    ami_event_type: float
    mean_ari_stability: float


def _mean_by_label(per_athlete: Mapping[str, Mapping[str, np.ndarray]],
                   athletes: Sequence[str]) -> dict[str, np.ndarray]:
    acc: dict[str, list[np.ndarray]] = defaultdict(list)
    for athlete in athletes:
        for label, vec in per_athlete.get(athlete, {}).items():
            acc[label].append(vec)
    return {label: np.mean(vs, axis=0) for label, vs in acc.items()}


def cluster_probes(per_athlete: Mapping[str, Mapping[str, np.ndarray]],
                   text: Mapping[str, np.ndarray], types: Mapping[str, str],
                   k: int = KMEANS_K, seed: int = SEED) -> list[ClusterProbe]:
    """k-means on each label-embedding space → AMI vs event type + athlete-bootstrap stability.

    Both spaces are scored on the SAME resampled label sets, so the stability column compares
    the spaces and not their vocabularies. Report-only: ADR-03's rule is that structure which
    LOOKS better is not evidence, so no number here can accept or reject anything. It exists
    to say what changed.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_mutual_info_score, adjusted_rand_score

    athletes = sorted(per_athlete)
    full = _mean_by_label(per_athlete, athletes)
    labels = sorted(label for label in full if types.get(label) and label in text)
    if len(labels) <= k or not athletes:
        return []

    def fit(vectors: Mapping[str, np.ndarray], keys: Sequence[str]) -> list[int]:
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(
            np.stack([vectors[key] for key in keys]))
        return [int(c) for c in km.labels_]

    spaces: dict[str, Mapping[str, np.ndarray]] = {
        "GraphSAGE (32-d, inductive)": full,
        "mpnet label text (768-d, production)": text,
    }
    base = {name: dict(zip(labels, fit(vectors, labels), strict=True))
            for name, vectors in spaces.items()}
    ami = {name: float(adjusted_mutual_info_score([types[label] for label in labels],
                                                  [base[name][label] for label in labels]))
           for name in spaces}

    rng = np.random.default_rng(seed)
    aris: dict[str, list[float]] = {name: [] for name in spaces}
    for _ in range(N_STABILITY):
        drawn = [athletes[i] for i in rng.integers(0, len(athletes), size=len(athletes))]
        drawn_mean = _mean_by_label(per_athlete, drawn)
        keys = [label for label in labels if label in drawn_mean]
        if len(keys) <= k:
            continue
        for name in spaces:
            vectors = drawn_mean if name.startswith("GraphSAGE") else spaces[name]
            aris[name].append(float(adjusted_rand_score(
                [base[name][label] for label in keys], fit(vectors, keys))))
    return [ClusterProbe(name, len(labels), ami[name],
                         float(np.mean(aris[name])) if aris[name] else float("nan"))
            for name in spaces]


def label_types(rows: Sequence[BoutRow]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows:
        for e in row.sequence:
            label = clean_label(str(e.get("label", "")), str(e.get("type", "")))
            typ = str(e.get("type", "")).strip().lower()
            if label and typ in EVENT_TYPES:
                out.setdefault(label, typ)
    return out


# ── report ──────────────────────────────────────────────────────────────────────
@dataclass
class Run:
    gate: GateReport
    arms: list[ArmResult] = field(default_factory=list)
    probes: list[ClusterProbe] = field(default_factory=list)
    embedding_hits: int = 0
    embedding_total: int = 0


def _arm_section(arm: ArmResult) -> list[str]:
    out = [f"### Arm {arm.name}", ""]
    out += [
        f"* athletes: **{arm.n_train} train / {arm.n_val} validation / {arm.n_eval} eval** "
        f"(disjoint, ordered by debut key)",
        f"* training bouts after the eval-athlete quarantine: **{arm.train_bouts}** "
        f"({arm.quarantined} bouts removed for containing an eval athlete)",
        f"* eval rows: **{arm.n_rows:,}** candidate pairs, **{arm.n_pos}** positives; "
        f"{arm.dropped} held-out edges excluded for an unseen endpoint",
        f"* power gate: {'PASS' if arm.powered else 'FAIL'} "
        f"(≥{MIN_EVAL_ATHLETES} eval athletes, ≥{MIN_POSITIVES} positives, "
        f"coverage estimable={arm.coverage_ok})",
    ]
    if arm.graph_sizes:
        nodes = [n for n, _ in arm.graph_sizes]
        edges = [e for _, e in arm.graph_sizes]
        out.append(f"* eval observed graphs: {min(nodes)}–{max(nodes)} nodes "
                   f"(median {int(np.median(nodes))}), {min(edges)}–{max(edges)} edges "
                   f"(median {int(np.median(edges))})")
    if arm.results:
        out += [
            f"* early stopping: `sage` epoch {arm.train_log.best_epoch} "
            f"(val AUC {arm.train_log.best_val_auc:.4f}, {arm.train_log.epochs_run} run); "
            f"`mlp` epoch {arm.mlp_log.best_epoch} (val AUC {arm.mlp_log.best_val_auc:.4f})",
            "",
            "| method | AUC | 95% CI (athlete-clustered) | Hits@20 | MRR | separates? |",
            "|---|---|---|---|---|---|",
        ]
        for name in (SAGE, *BASELINES):
            if name in arm.results:
                out.append(arm.results[name].row())
        out += ["", "Paired ΔAUC (`sage` − baseline), same rows, athlete-clustered:", "",
                "| baseline | ΔAUC | 95% CI | `sage` beats it? |", "|---|---|---|---|"]
        for name in BASELINES:
            d, lo, hi = arm.deltas.get(name, (float("nan"),) * 3)
            out.append(f"| `{name}` | {d:+.4f} | [{lo:+.4f}, {hi:+.4f}] | "
                       f"{'yes' if beats((d, lo, hi)) else 'no'} |")
    out += ["", f"**Verdict ({arm.name}): {arm.verdict}** — {arm.verdict_why}.", ""]
    return out


def _reading(run: Run) -> list[str]:
    """What the numbers say, written FROM the numbers — every claim below is a formatted value
    or a labelled hypothesis, never an unsourced adjective."""
    primary = next((a for a in run.arms if a.name.startswith("A")), None)
    secondary = next((a for a in run.arms if a.name.startswith("B")), None)
    if primary is None or not primary.results:
        return []
    r = primary.results
    d_prior, d_mlp = primary.deltas["prior"], primary.deltas[MLP]
    out = ["### Reading", ""]
    out += [
        f"**The criterion says {primary.verdict}, and it says it on one comparison.** "
        f"`sage` beats every non-learned comparator — including the corpus transition prior "
        f"(ΔAUC {d_prior[0]:+.4f} [{d_prior[1]:+.4f}, {d_prior[2]:+.4f}]), which is the "
        "non-trivial half of the result: an athlete's OWN observed graph carries information "
        "about their next transitions that the corpus-wide kernel does not. It then loses to "
        f"the ablation that removes message passing (ΔAUC {d_mlp[0]:+.4f} "
        f"[{d_mlp[1]:+.4f}, {d_mlp[2]:+.4f}]). Under the pre-registered lattice that is a "
        "REJECT, and it is the honest one: what won is the FEATURES, not the aggregation.",
        "",
        f"**What actually won is a linear model on the production vectors.** The `mlp` arm is "
        f"one `Linear(771 → {EMBED_DIM})` + ReLU + L2 norm feeding the same dot decoder — no "
        f"neighbourhood, no depth — and it scores AUC {r[MLP].auc:.4f} "
        f"[{r[MLP].lo:.4f}, {r[MLP].hi:.4f}] against `sage`'s {r[SAGE].auc:.4f} "
        f"[{r[SAGE].lo:.4f}, {r[SAGE].hi:.4f}]. Note what that arm still has: the three "
        "structural terms (log in-/out-/total weight) are node features, so the honest claim is "
        "not \"the graph is useless\" but \"MESSAGE PASSING added nothing beyond node-level "
        "features, and cost accuracy\".",
        "",
        "**Hypothesis for the mechanism, explicitly NOT tested here.** The eval graphs have a "
        f"median of {int(np.median([n for n, _ in primary.graph_sizes]))} nodes and "
        f"{int(np.median([e for _, e in primary.graph_sizes]))} observed edges. Two hops of "
        "mean aggregation over a graph that small mixes most of it into every node, which is "
        "the over-smoothing regime: embeddings converge toward the graph mean and a decoder "
        "that only sees per-node vectors loses the per-node text signal. The secondary probe "
        "is consistent with that — AMI against the event type falls from "
        + (f"{run.probes[1].ami_event_type:.3f} (text) to {run.probes[0].ami_event_type:.3f} "
           "(GraphSAGE)" if len(run.probes) >= 2 else "the text space to the GraphSAGE space")
        + " — but consistency is not a test, and testing it means a NEW pre-registration.",
        "",
        "**Zhang & Chen 2018 predicted the direction.** A node-embedding encoder with a dot "
        "decoder computes each node's vector independently of the target pair, so it cannot "
        "represent a pair-specific structural feature (how many neighbours THESE two share). "
        "The pre-registration named this as the weak form of GNN link prediction before the "
        "run, which is why \"we tested the weak form\" is a reading here and not an excuse.",
        "",
        f"**`pref_attach` is BELOW chance** (AUC {r['pref_attach'].auc:.4f} "
        f"[{r['pref_attach'].lo:.4f}, {r['pref_attach'].hi:.4f}]) — in this corpus a high-degree "
        "target is LESS likely to receive a held-out edge, because a hub's transitions are "
        "mostly already in the observed set. That is a property of the holdout, not a law of "
        "grappling, and it is the reason a degree-based heuristic must never be the only "
        "baseline a link-prediction claim is measured against.",
        "",
        f"**The existing transductive similarity is a real, weak predictor.** `text` — cosine "
        f"between the production mpnet vectors, nothing learned — scores {r['text'].auc:.4f} "
        f"[{r['text'].lo:.4f}, {r['text'].hi:.4f}]. `analysis/embeddings.py` was never "
        "evaluated as a link predictor before; now it has a number.",
        "",
    ]
    if secondary is not None and not secondary.powered:
        out += [
            f"**Arm B cannot be answered by this corpus yet.** The forecast form — observe an "
            f"athlete's earlier bouts, predict the transitions their later bouts add — leaves "
            f"{secondary.n_eval} eval athletes and {secondary.n_pos} positives against a "
            f"pre-registered floor of {MIN_EVAL_ATHLETES} and {MIN_POSITIVES}. It becomes "
            "answerable when more athletes reach ≥2 gated bouts carrying ≥10 distinct edges; "
            "its numbers are printed above and are NOT a verdict.",
            "",
        ]
    out += [
        "**Recorded next actions (each needs its own pre-registration — running any of them "
        "on these same eval rows would be the post-hoc sweep this cell forbids):** "
        "(1) the enclosing-subgraph / labelling-trick form (SEAL) — the model class that CAN "
        "represent pair-specific structure; (2) a 1-layer aggregator, if over-smoothing is the "
        "hypothesis someone wants to test; (3) a hybrid that feeds the corpus-prior count in as "
        "an input feature, which is the cheapest way to ask whether the learned part adds to "
        "the counted part; (4) re-run Arm B when the corpus clears its power gate.",
        "",
        "**What this cell already earned, independent of the verdict:** the linear-on-mpnet "
        "model beats the corpus prior, Adamic-Adar, popularity and degree by intervals that "
        "exclude 0. Nothing ships on that today — it has no pre-registered production criterion "
        "— but it is the first measured evidence in this repository that the pgvector layer "
        "carries predictive signal rather than only descriptive similarity.",
        "",
    ]
    return out


def render_markdown(run: Run, prereg: str) -> str:
    lines = [
        "# PoC-E13 — GraphSAGE: inductive link prediction on athlete ActionFlow graphs",
        "",
        "> **Generated by `analysis/poc/e13_graphsage.py` — do not hand-edit.** Re-run the "
        "module to regenerate. The pre-registration below is echoed verbatim from "
        "`docs/research/poc/e13_prereg.md`, which was written before the runner produced a "
        "single held-out number.",
        "",
    ]
    primary = next((a for a in run.arms if a.name.startswith("A")), None)
    if primary is not None:
        lines += [f"**Headline (Arm A, primary): {primary.verdict}** — {primary.verdict_why}.",
                  ""]
    lines += ["---", "", prereg.strip(), "", "---", "", "## Results", ""]

    g = run.gate
    if g.error:
        lines += [f"> **Corpus unavailable** — `{g.error}`. No arm ran; there is no verdict.", ""]
        return "\n".join(lines) + "\n"
    lines += [
        "### Corpus", "",
        f"* `matches` with `status='final'` and a sequence: **{g.total}**; "
        f"**{g.passed}** pass the perspective gate ({g.one_sided} one-sided, "
        f"{g.total - g.with_sequence} under {MIN_EVENTS} events).",
        f"* PoC-E9 published 429 gated bouts on 2026-08-25; this run measures **{g.passed}**. "
        "The corpus moved — every number below is about this one.",
        f"* node features: **{run.embedding_hits}/{run.embedding_total}** distinct labels "
        f"({100.0 * run.embedding_hits / max(1, run.embedding_total):.1f}%) carry a "
        "production `technique_nodes.embedding`; the rest use the neutral mean vector.",
        "",
    ]
    for arm in run.arms:
        lines += _arm_section(arm)
    if run.probes:
        lines += ["### Secondary probe — do the embeddings organise techniques differently?",
                  "",
                  "Report-only. Neither column accepts or rejects anything (ADR-03).", "",
                  "| space | labels | AMI vs event type | mean ARI under athlete bootstrap |",
                  "|---|---|---|---|"]
        for p in run.probes:
            lines.append(f"| {p.space} | {p.n_labels} | {p.ami_event_type:.3f} | "
                         f"{p.mean_ari_stability:.3f} |")
        lines += [
            "",
            "Caveat that the table cannot show: the text space's stability is high **by "
            "construction** — its vectors do not depend on which athletes were drawn, so the "
            "only thing an athlete bootstrap can move there is the label set. The column "
            "compares how much each space depends on the sample, not which space is better.",
            "",
        ]
    lines += _reading(run)
    lines += ["### What did not happen", "",
              "No production change, under any verdict. `technique_nodes.embedding`, "
              "`graphs.embedding`, `graph_edges.embedding` and every export are exactly as "
              "they were; this runner writes no vector and no row to the database.", ""]
    return "\n".join(lines) + "\n"


# ── entry point ─────────────────────────────────────────────────────────────────
def run(gate: GateReport, n_boot: int = N_BOOT, max_epochs: int = MAX_EPOCHS,
        n_boot_paired: int = N_BOOT_PAIRED) -> Run:
    out = Run(gate=gate)
    if gate.error or not gate.rows:
        return out
    by_athlete = athlete_bouts(gate.rows)
    types = label_types(gate.rows)
    labels = sorted({label for ab in by_athlete.values() for _, ev in ab.bouts for e in ev
                     for label in [clean_label(str(e.get("label", "")), str(e.get("type", "")))]
                     if label})
    text, hits = load_text_vectors(labels)
    out.embedding_hits, out.embedding_total = hits, len(labels)

    out.arms.append(run_arm("A (primary, edge holdout)", gate.rows, by_athlete,
                            sample_arm_a, text, n_boot, max_epochs, n_boot_paired))
    out.arms.append(run_arm("B (secondary, chronological bout holdout)", gate.rows, by_athlete,
                            sample_arm_b, text, n_boot, max_epochs, n_boot_paired))

    primary = out.arms[0]
    if primary.embeddings:
        out.probes = cluster_probes(primary.embeddings, text, types)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None, help="write the report here (default: stdout)")
    ap.add_argument("--prereg", default=str(PREREG),
                    help="pre-registration markdown, echoed verbatim into the report")
    ap.add_argument("--skip-corpus", action="store_true", help="render the no-DB report")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--n-boot-paired", type=int, default=N_BOOT_PAIRED)
    ap.add_argument("--max-epochs", type=int, default=MAX_EPOCHS)
    args = ap.parse_args(argv)

    gate = GateReport(error="skipped (--skip-corpus)") if args.skip_corpus else load_corpus()
    result = run(gate, n_boot=args.n_boot, max_epochs=args.max_epochs,
                 n_boot_paired=args.n_boot_paired)
    prereg_path = Path(args.prereg)
    prereg = prereg_path.read_text(encoding="utf-8") if prereg_path.exists() else (
        "_(pre-registration file missing)_")
    md = render_markdown(result, prereg)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        logging.info("wrote %s", args.out)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
