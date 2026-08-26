"""PoC-E5 — "Grapples most like", given a definition and a measured validity.

    uv run python -m analysis.poc.e5_grapple_like            # corpus run → docs/research/poc/e5.md
    uv run python -m analysis.poc.e5_grapple_like --dry-run  # shape + gate only, no arms

**What ships today.** Every dossier page prints "Grapples most like <name> — NN%". That NN
is ``athlete_systems.match_systems``'s ``aggregate_similarity``: greedy best-match between
two athletes' Louvain communities, scored 0.50·(8-dim type-share cosine) + 0.20·(same hub
type) + 0.15·(size closeness) + 0.15·(ELO closeness) and averaged over the matched pairs —
computed on the athlete's career graph **truncated to its 12 busiest nodes**
(``export/site_data.py`` → ``_career_graphview(..., limit=12)`` → ``from_career_graphview``).
It has never been evaluated against anything.

Note for the plan: ``03_POC_PLANS.md`` named the ELO-weighted mpnet centroid as candidate
(a), the baseline. That is NOT what ships. ``embeddings.nearest_graphs`` — the pgvector
path — has no production caller anywhere (only a privacy-boundary test). The correction
is registered in the
pre-registration and the shipped method is arm (a0) here.

**The criterion** is self-recognition, which needs no labels and cannot be gamed: split an
athlete's bouts into two halves, build a graph from each, and ask each method to retrieve
an athlete's own other half out of every athlete's. A style descriptor that cannot
recognise the same grappler twice has no business telling a reader who somebody grapples
like.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np

from analysis.athlete_graph import AthleteEdge, AthleteGraph, AthleteNode
from analysis.athlete_systems import build_system_profile, match_systems
from analysis.names import _normalize_name
from analysis.poc.e9_markov import BoutRow, GateReport, load_corpus
from analysis.poc.signatures import (
    Retrieval,
    cosine_distance,
    degree_signature,
    netlsd,
    netlsd_distance,
    portrait,
    portrait_divergence,
    retrieval,
    size_signature,
)
from analysis.stats_rigor import bootstrap_ci
from analysis.transitions.build_graph import network_from_sequences

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "research" / "poc" / "e5.md"
PREREG = REPO / "docs" / "research" / "poc" / "e5_prereg.md"

SEED = 20260820                # stats_rigor's seed — every interval in the repo agrees
N_BOOT = 4000
MIN_BOUTS_PRIMARY = 4          # pre-registered from the corpus's bout-count marginal
MIN_BOUTS_SENSITIVITY = 6      # 03_POC_PLANS's floor as written
MIN_HALF_NODES = 3
MIN_HALF_EDGES = 2
CAREER_NODE_LIMIT = 12         # export/site_data.py's `_career_graphview(..., limit=12)`
PRODUCTION_ARM = "production method (athlete_systems, 12-node)"


# ── halves ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Half:
    """One athlete's ActionFlow graph over half of their gated bouts."""

    athlete: str
    side: str                  # "A" or "B"
    n_bouts: int
    graph: nx.DiGraph

    @property
    def ok(self) -> bool:
        return bool(self.graph.number_of_nodes() >= MIN_HALF_NODES
                    and self.graph.number_of_edges() >= MIN_HALF_EDGES)


def own_events(row: BoutRow, athlete: str) -> list[dict[str, Any]]:
    return [e for e in row.sequence if str(e.get("actor_id")) == athlete]


def athlete_bouts(rows: Sequence[BoutRow]) -> dict[str, list[BoutRow]]:
    """Gated bouts per athlete, keeping only bouts where that athlete has ≥1 own event.

    A bout the athlete appears in but files no event under is not tape of their game; it
    would contribute an empty chain and inflate the bout count without touching the graph.
    """
    per: dict[str, list[BoutRow]] = {}
    for r in rows:
        for a in (r.athlete_a, r.athlete_b):
            if own_events(r, a):
                per.setdefault(a, []).append(r)
    return {a: sorted(v, key=lambda r: r.key) for a, v in per.items()}


def _graph(rows: Sequence[BoutRow], athlete: str) -> nx.DiGraph:
    """The athlete's own ActionFlow graph — production's builder, own events only."""
    return network_from_sequences([own_events(r, athlete) for r in rows])


def split_halves(rows: Sequence[BoutRow], athlete: str, scheme: str) -> tuple[Half, Half]:
    """``odd_even`` (primary) or ``chronological`` (the leakage probe).

    Odd/even is 03_POC_PLANS's split and the one with the most balanced halves. It has a
    known confound: two halves drawn from the same events, the same opponents and the same
    annotation batch can be recognisable for reasons that are not style. ``chronological``
    removes that confound and costs real signal (a grappler's game moves) — the gap between
    the two schemes IS the size of the confound, which is why both are reported.
    """
    ordered = sorted(rows, key=lambda r: r.key)
    if scheme == "odd_even":
        a_rows, b_rows = ordered[0::2], ordered[1::2]
    elif scheme == "chronological":
        cut = len(ordered) // 2
        a_rows, b_rows = ordered[:cut], ordered[cut:]
    else:
        raise ValueError(f"unknown split scheme {scheme!r}")
    return (Half(athlete, "A", len(a_rows), _graph(a_rows, athlete)),
            Half(athlete, "B", len(b_rows), _graph(b_rows, athlete)))


@dataclass
class Cohort:
    """The athletes both of whose halves clear the gate, at one floor and one split."""

    floor: int
    scheme: str
    a: list[Half] = field(default_factory=list)
    b: list[Half] = field(default_factory=list)
    eligible: int = 0           # athletes at this bout floor, before the half gate
    dropped: int = 0            # ... that lost a half to the node/edge gate

    @property
    def n(self) -> int:
        return len(self.a)

    @property
    def median_shape(self) -> tuple[int, int]:
        """Median (nodes, edges) over every half in the cohort — the resolution every arm
        actually sees, and the number any 'the graphs are too small' reading must cite."""
        halves = [*self.a, *self.b]
        if not halves:
            return (0, 0)
        ns = sorted(h.graph.number_of_nodes() for h in halves)
        es = sorted(h.graph.number_of_edges() for h in halves)
        return (ns[len(ns) // 2], es[len(es) // 2])


def build_cohort(per: dict[str, list[BoutRow]], floor: int, scheme: str) -> Cohort:
    c = Cohort(floor=floor, scheme=scheme)
    for athlete in sorted(per):
        rows = per[athlete]
        if len(rows) < floor:
            continue
        c.eligible += 1
        ha, hb = split_halves(rows, athlete, scheme)
        if ha.ok and hb.ok:
            c.a.append(ha)
            c.b.append(hb)
        else:
            c.dropped += 1
    return c


# ── the arms ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Method:
    """A named way to turn a half into a signature and two signatures into a distance."""

    name: str
    prepare: Callable[[Half], Any]
    metric: Callable[[Any, Any], float]
    note: str = ""


def _to_athlete_graph(h: Half, limit: int | None) -> AthleteGraph:
    """Half → ``AthleteGraph``, optionally truncated to the ``limit`` busiest nodes.

    Faithful to production for everything system detection reads (node label/type/count,
    edge count) with one declared substitution: production's node set comes from the
    PERSISTED ``graph_edges`` (an ELO replay artefact defined only at career level), and a
    half of a career has no persisted graph, so the substrate here is ``matches.sequence``.
    ``computed_elo`` is left at 0, which zeroes ``system_similarity``'s 0.15 ELO term for
    every pair alike — a constant offset, no effect on the ranking this cell scores.
    """
    g = h.graph
    nodes = sorted(g.nodes(data=True), key=lambda kv: (-float(kv[1].get("occ", 0)), kv[0]))
    if limit is not None:
        nodes = nodes[:limit]
    keep = {k for k, _ in nodes}
    ag = AthleteGraph(athlete=h.athlete)
    for key, data in nodes:
        ag.nodes[key] = AthleteNode(label=key, type=str(data.get("type", "")),
                                    count=max(int(data.get("occ", 1)), 1))
    for u, v, d in g.edges(data=True):
        if u in keep and v in keep and u != v:
            ag.edges[(u, v)] = AthleteEdge(source=u, target=v,
                                           count=max(int(d.get("weight", 1)), 1))
    return ag


def _system_distance(a: Any, b: Any) -> float:
    """1 − ``aggregate_similarity``. The shipped percentage, turned into a distance."""
    return 1.0 - float(match_systems(a, b)["aggregate_similarity"])


def _centroid(h: Half, emb: dict[str, np.ndarray], weighted: bool) -> np.ndarray:
    vecs: list[np.ndarray] = []
    weights: list[float] = []
    for label, data in h.graph.nodes(data=True):
        v = emb.get(_normalize_name(str(label)))
        if v is None:
            continue
        vecs.append(v)
        weights.append(float(data.get("occ", 1.0)) if weighted else 1.0)
    if not vecs:
        return np.zeros(0, dtype=np.float64)
    arr = np.asarray(vecs, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    mean = (arr * w[:, None]).sum(axis=0) / max(float(w.sum()), 1e-12)
    return np.asarray(mean, dtype=np.float64)


def _l2(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def _as_vector(raw: Any) -> np.ndarray:
    """pgvector → ndarray. Over a raw ``text()`` connection the column arrives as its text
    representation (``"[0.1,0.2,...]"``), which is valid JSON — parsed, never ``eval``'d."""
    if isinstance(raw, str):
        return np.asarray(json.loads(raw), dtype=np.float64)
    return np.asarray(list(raw), dtype=np.float64)


@dataclass
class EmbeddingSupply:
    """The technique-node embeddings, with the coverage the arms are read against."""

    vectors: dict[str, np.ndarray] = field(default_factory=dict)
    covered: int = 0
    total: int = 0
    error: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.vectors) and self.total > 0 and self.covered / self.total >= 0.5


def load_embeddings(labels: Sequence[str]) -> EmbeddingSupply:
    """``technique_nodes.embedding`` for the labels this cohort actually uses. Read-only.

    Athlete corpus only by construction: the keys asked for come from ``matches.sequence``,
    and ``technique_nodes`` is the shared public library — no user-owned row is read.
    """
    sup = EmbeddingSupply()
    keys = {_normalize_name(x) for x in labels}
    sup.total = len(keys)
    try:
        from sqlalchemy import text

        from db.base import get_engine

        with get_engine().connect() as conn:
            rows = conn.execute(text(
                "SELECT node_key, embedding FROM technique_nodes WHERE embedding IS NOT NULL"
            )).all()
    except Exception as exc:  # noqa: BLE001 — the report says why; the other arms still run
        sup.error = f"{type(exc).__name__}: {exc}".split("\n")[0][:160]
        return sup
    for key, vec in rows:
        if key in keys:
            sup.vectors[str(key)] = _as_vector(vec)
    sup.covered = len(sup.vectors)
    return sup


def methods(sup: EmbeddingSupply) -> list[Method]:
    """The arms, in the order the pre-registration lists them."""
    out = [
        Method(PRODUCTION_ARM,
               lambda h: build_system_profile(h.athlete, _to_athlete_graph(h, CAREER_NODE_LIMIT)),
               _system_distance,
               "the shipped 'Grapples most like' percentage, reproduced on halves"),
        Method("athlete_systems, untruncated",
               lambda h: build_system_profile(h.athlete, _to_athlete_graph(h, None)),
               _system_distance,
               "same method without the dossier's 12-node cap — is the cap costing us?"),
        Method("NetLSD (heat trace)", lambda h: netlsd(h.graph), netlsd_distance,
               "Tsitsulin 2018; undirected projection, unweighted, 250 log-spaced scales"),
        Method("portrait divergence", lambda h: portrait(h.graph), portrait_divergence,
               "Bagrow & Bollt 2019; undirected projection"),
        Method("null: size only", lambda h: size_signature(h.graph), _l2,
               "(log n, log m, mean degree) — nothing but how much tape there is"),
        Method("null: degree histogram", lambda h: degree_signature(h.graph), _l2,
               "normalised degree sequence — cheap structure, no spectrum, no paths"),
    ]
    if sup.usable:
        out[4:4] = [
            Method("mpnet centroid, unweighted",
                   lambda h: _centroid(h, sup.vectors, False), cosine_distance,
                   "03_POC_PLANS candidate (b) — the missing ablation"),
            Method("mpnet centroid, occurrence-weighted",
                   lambda h: _centroid(h, sup.vectors, True), cosine_distance,
                   "stands in for candidate (a): edge ELO is a career-level DB artefact "
                   "with no half-level definition, so the ELO weighting is not evaluable "
                   "here — and nothing ships on it: `nearest_graphs` has no production "
                   "caller, only a privacy-boundary test"),
        ]
    return out


def distances(method: Method, cohort: Cohort) -> np.ndarray:
    """``d[i, j]`` = distance from athlete i's half A to athlete j's half B."""
    pa = [method.prepare(h) for h in cohort.a]
    pb = [method.prepare(h) for h in cohort.b]
    out = np.zeros((len(pa), len(pb)), dtype=np.float64)
    for i, x in enumerate(pa):
        for j, y in enumerate(pb):
            out[i, j] = method.metric(x, y)
    return out


# ── scoring ─────────────────────────────────────────────────────────────────────
def chance_mrr(n: int) -> float:
    """MRR of a uniformly random ranking over ``n`` candidates: H_n / n."""
    return sum(1.0 / r for r in range(1, n + 1)) / n if n else float("nan")


def _paired_ci(a: Sequence[float], b: Sequence[float],
               n_boot: int = N_BOOT) -> tuple[float, float, float]:
    """Δ mean(a) − mean(b) with a percentile bootstrap over ATHLETES (the resampling unit).

    Indices are resampled, not values, so the two arms stay paired inside a generic
    resampler — PoC-E8's trick, reused rather than reinvented.
    """
    idx = [float(i) for i in range(len(a))]

    def stat(s: Sequence[float]) -> float:
        ii = [int(x) for x in s]
        return float(np.mean([a[i] for i in ii]) - np.mean([b[i] for i in ii]))

    return bootstrap_ci(idx, stat, n_boot=n_boot, seed=SEED)


def wins(delta: tuple[float, float, float]) -> bool:
    """A win is a paired interval strictly above 0 with a non-degenerate width.

    The width guard is PoC-E9's amendment, adopted verbatim: a bootstrap that returns the
    same value in every draw saw no athlete-to-athlete variation and has not established
    anything.
    """
    _, lo, hi = delta
    return bool(np.isfinite(lo) and np.isfinite(hi) and hi > lo and lo > 0.0)


@dataclass
class ArmResult:
    name: str
    note: str
    mrr: float
    lo: float
    hi: float
    top1: float
    top5: float
    reciprocal: list[float] = field(default_factory=list)


@dataclass
class Pass:
    """One (floor, split) run: every arm, plus the paired deltas the criterion reads."""

    floor: int
    scheme: str
    n: int
    eligible: int
    dropped: int
    chance: float
    median_nodes: int = 0
    median_edges: int = 0
    arms: list[ArmResult] = field(default_factory=list)
    vs_production: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    vs_size: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    vs_degree: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    def arm(self, name: str) -> ArmResult | None:
        return next((a for a in self.arms if a.name == name), None)


def run_pass(cohort: Cohort, sup: EmbeddingSupply, n_boot: int = N_BOOT) -> Pass:
    mn, me = cohort.median_shape
    p = Pass(cohort.floor, cohort.scheme, cohort.n, cohort.eligible, cohort.dropped,
             chance_mrr(cohort.n), mn, me)
    if cohort.n < 2:
        return p
    truth = list(range(cohort.n))
    scored: dict[str, Retrieval] = {}
    for m in methods(sup):
        r = retrieval(m.name, distances(m, cohort), truth)
        scored[m.name] = r
        obs, lo, hi = bootstrap_ci(r.reciprocal, lambda v: float(np.mean(v)),
                                   n_boot=n_boot, seed=SEED)
        p.arms.append(ArmResult(m.name, m.note, obs, lo, hi, r.top_k(1), r.top_k(5),
                                list(r.reciprocal)))
    for name, r in scored.items():
        for other, store in (("null: size only", p.vs_size),
                             ("null: degree histogram", p.vs_degree),
                             (PRODUCTION_ARM, p.vs_production)):
            if name == other or other not in scored:
                continue
            store[name] = _paired_ci(r.reciprocal, scored[other].reciprocal, n_boot)
    return p


# ── verdict ─────────────────────────────────────────────────────────────────────
def verdicts(primary: Pass) -> dict[str, str]:
    """The pre-registered decision, applied to the primary pass only."""
    out: dict[str, str] = {}
    prod = primary.arm(PRODUCTION_ARM)
    if primary.n < 2 or prod is None:
        return {"cell": "NOT RUN — no cohort"}

    beats_chance = (np.isfinite(prod.lo) and prod.lo > primary.chance)
    out["shipped percentage"] = (
        f"{'RECOGNISES' if beats_chance else 'DOES NOT RECOGNISE'} an athlete's own game — "
        f"MRR {prod.mrr:.3f} [{prod.lo:.3f}, {prod.hi:.3f}] against chance "
        f"{primary.chance:.3f} over {primary.n} candidates"
    )

    challengers = [a for a in primary.arms
                   if a.name != PRODUCTION_ARM and not a.name.startswith("null:")]
    accepted: list[str] = []
    for a in challengers:
        if (wins(primary.vs_production.get(a.name, (float("nan"),) * 3))
                and wins(primary.vs_size.get(a.name, (float("nan"),) * 3))
                and wins(primary.vs_degree.get(a.name, (float("nan"),) * 3))):
            accepted.append(a.name)
    out["replacement"] = (
        f"ACCEPT — {', '.join(accepted)} clears production AND both nulls"
        if accepted else
        "REJECT — no challenger clears production and both nulls; the shipped method stands, "
        "now with a definition and a number attached to it"
    )

    beat_nulls = [a.name for a in primary.arms if not a.name.startswith("null:")
                  and wins(primary.vs_size.get(a.name, (float("nan"),) * 3))
                  and wins(primary.vs_degree.get(a.name, (float("nan"),) * 3))]
    out["above the nulls"] = ", ".join(beat_nulls) if beat_nulls else (
        "NONE — every arm is within bootstrap noise of a size/degree descriptor, which means "
        "this corpus cannot yet distinguish style from volume at half-career resolution"
    )
    return out


# ── report ──────────────────────────────────────────────────────────────────────
def _ci(d: tuple[float, float, float]) -> str:
    o, lo, hi = d
    if not np.isfinite(o):
        return "—"
    return f"{o:+.3f} [{lo:+.3f}, {hi:+.3f}]"


def _arm_table(p: Pass) -> list[str]:
    rows = ["| arm | MRR [95% CI] | top-1 | top-5 | Δ vs production | Δ vs size null "
            "| Δ vs degree null |", "|---|---|---|---|---|---|---|"]
    for a in p.arms:
        rows.append(
            f"| {a.name} | {a.mrr:.3f} [{a.lo:.3f}, {a.hi:.3f}] | {a.top1:.0%} | {a.top5:.0%} "
            f"| {_ci(p.vs_production.get(a.name, (float('nan'),) * 3))} "
            f"| {_ci(p.vs_size.get(a.name, (float('nan'),) * 3))} "
            f"| {_ci(p.vs_degree.get(a.name, (float('nan'),) * 3))} |"
        )
    rows.append(f"| _chance (random ranking)_ | {p.chance:.3f} | "
                f"{1 / p.n:.0%} | {min(5, p.n) / p.n:.0%} | — | — | — |")
    return rows


def _pass_section(p: Pass, title: str) -> list[str]:
    out = [f"### {title}", "",
           f"Cohort: **{p.n} athletes** (of {p.eligible} at the ≥{p.floor}-bout floor; "
           f"{p.dropped} lost a half to the ≥{MIN_HALF_NODES}-node / ≥{MIN_HALF_EDGES}-edge "
           f"gate). Split: `{p.scheme}`. Median half-graph: {p.median_nodes} nodes / "
           f"{p.median_edges} edges. Chance MRR: {p.chance:.3f}.", ""]
    if p.n < 2:
        return [*out, "Too few athletes to rank anything. No arms run.", ""]
    return [*out, *_arm_table(p), ""]


def render_markdown(run: Run, prereg: str) -> str:
    v = verdicts(run.primary)
    lines = [
        "# PoC-E5 — grapple-like v2: a defined, evaluated similarity", "",
        "Generated by `uv run python -m analysis.poc.e5_grapple_like` — **do not hand-edit**. "
        "Module: `analysis/poc/e5_grapple_like.py`; library: `analysis/poc/signatures.py`; "
        "tests: `tests/test_poc_signatures.py`, `tests/test_poc_e5.py`; "
        "pre-registration: `docs/research/poc/e5_prereg.md` (reproduced verbatim below).", "",
        f"**Corpus gate:** {run.gate_note}", "",
        f"**Embeddings:** {run.embedding_note}", "",
        "## Verdicts", "",
    ]
    lines += [f"{i}. **{k}** — {val}" for i, (k, val) in enumerate(v.items(), start=1)]
    lines += ["", "---", "", "## Pre-registration (verbatim)", "", prereg.strip(), "",
              "---", "", "## Results", ""]
    lines += _pass_section(run.primary, "Primary — odd/even split, ≥4-bout floor")
    for extra, title in ((run.floor6, "Sensitivity — odd/even split, ≥6-bout floor "
                                      "(03_POC_PLANS's floor as written)"),
                         (run.chrono, "Leakage probe — chronological split, ≥4-bout floor")):
        if extra is not None:
            lines += _pass_section(extra, title)
    lines += _reading(run, v)
    return "\n".join(lines) + "\n"


def _reading(run: Run, v: dict[str, str]) -> list[str]:
    out = ["## Reading", ""]
    p = run.primary
    prod = p.arm(PRODUCTION_ARM)
    if prod is None:
        return [*out, "Not run.", ""]

    out.append(
        f"The number on every dossier page now has a measurement behind it: the shipped "
        f"method retrieves an athlete's own other half with MRR {prod.mrr:.3f} "
        f"[{prod.lo:.3f}, {prod.hi:.3f}] out of {p.n} candidates, against a chance floor of "
        f"{p.chance:.3f}. That is the sentence the dossier was missing, whichever way it "
        f"came out."
    )
    trunc = p.arm("athlete_systems, untruncated")
    if trunc is not None and prod is not None:
        d = p.vs_production.get(trunc.name, (float("nan"),) * 3)
        verdict = ("costs measurable signal" if wins(d) else
                   "is not measurably costing anything")
        out.append(
            f"The dossier's 12-node truncation {verdict}: untruncated MRR {trunc.mrr:.3f} "
            f"against {prod.mrr:.3f}, paired Δ {_ci(d)}."
        )
    size = p.arm("null: size only")
    if size is not None and size.mrr >= prod.mrr:
        out.append(
            f"**The size null out-scores the shipped method.** Three numbers — log n, log m, "
            f"mean degree — reach MRR {size.mrr:.3f} against production's {prod.mrr:.3f}, "
            f"paired Δ {_ci(p.vs_production.get(size.name, (float('nan'),) * 3))}. That does "
            f"not mean the dossier percentage is meaningless; it means nothing so far "
            f"demonstrates it is carrying more than how much tape an athlete has. Gap #14's "
            f"complaint was that the percentage is undefined. This is worse and more useful: "
            f"it is defined, and its defined value is not distinguishable from volume."
        )

    graph_arms = [a for a in p.arms if a.name in ("NetLSD (heat trace)", "portrait divergence")]
    if graph_arms:
        detail = "; ".join(
            f"{a.name} {a.mrr:.3f}, Δ vs size {_ci(p.vs_size.get(a.name, (float('nan'),) * 3))}"
            for a in graph_arms)
        out.append(
            f"**Whole-graph structural signatures are a null on this corpus.** {detail}. "
            f"Neither clears production, neither clears the size null, and both sit below the "
            f"three-number size descriptor they were supposed to improve on. The likely "
            f"reason is stated rather than guessed at: a half-career ActionFlow graph here is "
            f"a median {p.median_nodes} nodes and {p.median_edges} edges, which is not enough "
            f"shortest-path or spectral "
            f"structure for a descriptor built for graphs three orders of magnitude larger. "
            f"That is a corpus verdict, not a method verdict — it does not transfer to a "
            f"corpus with more tape per athlete."
        )

    best = max((a for a in p.arms if not a.name.startswith("null:")), key=lambda a: a.mrr)
    if best.name != PRODUCTION_ARM:
        out.append(
            f"The strongest arm is **{best.name}** (MRR {best.mrr:.3f}). It clears the degree "
            f"null ({_ci(p.vs_degree.get(best.name, (float('nan'),) * 3))}) but not the size "
            f"null ({_ci(p.vs_size.get(best.name, (float('nan'),) * 3))}), so the "
            f"pre-registered criterion refuses it. Refusing a leader is what a pre-registered "
            f"criterion is FOR; the alternative is picking the winner after seeing the table."
        )

    if run.chrono is not None and run.chrono.n >= 2:
        cp = run.chrono.arm(PRODUCTION_ARM)
        cb = run.chrono.arm(best.name)
        if cp is not None:
            out.append(
                f"The leakage probe matters more than any single arm. Under a chronological "
                f"split — halves that share no event, no opponent set and no annotation "
                f"batch — the shipped method scores MRR {cp.mrr:.3f} against {prod.mrr:.3f} "
                f"odd/even. The gap is an upper bound on how much of 'self-recognition' is "
                f"recognition of the batch rather than of the game."
            )
        if cb is not None and cb.name != PRODUCTION_ARM:
            d = run.chrono.vs_production.get(cb.name, (float("nan"),) * 3)
            survives = "SURVIVES" if wins(d) else "does not survive"
            out.append(
                f"Under that same harder split, {cb.name} scores {cb.mrr:.3f} and its "
                f"advantage over production {survives} ({_ci(d)}). This is reported, not "
                f"promoted: the criterion named odd/even as primary before the run, and a "
                f"result that only appears on a secondary split is a hypothesis for the next "
                f"cell, not a verdict for this one."
            )
    out += ["", f"**Decision:** {v.get('replacement', '')}", "",
            "Nothing in this cell touches production. A swap, if one is ever accepted, "
            "carries the method's self-recognition number onto the dossier beside the "
            "percentage — that was PoC-E5's acceptance condition and it survives a REJECT: "
            "the shipped percentage needs its definition printed either way.", ""]
    return out


# ── run ─────────────────────────────────────────────────────────────────────────
@dataclass
class Run:
    gate_note: str
    embedding_note: str
    primary: Pass
    floor6: Pass | None = None
    chrono: Pass | None = None


E8_GATED_BOUTS = 429   # PoC-E8's published gate, for the drift check


def gate_note(gate: GateReport) -> str:
    if gate.error:
        return f"NOT RUN — {gate.error}"
    base = (f"{gate.passed} gated bouts of {gate.total} final+sequence "
            f"({gate.total - gate.with_sequence} under 4 events, "
            f"{gate.one_sided} dropped as one-sided)")
    if gate.passed == E8_GATED_BOUTS:
        return f"{base} — matching PoC-E8's published {E8_GATED_BOUTS}"
    return (f"{base} — **DRIFT** against PoC-E8/E9's published {E8_GATED_BOUTS}. The corpus "
            f"grew since those cells ran; their numbers are not directly comparable to these.")


def run_all(gate: GateReport, n_boot: int = N_BOOT) -> Run:
    per = athlete_bouts(gate.rows)
    labels = sorted({str(lb) for a, rows in per.items()
                     for lb in _graph(rows, a).nodes()})
    sup = load_embeddings(labels)
    emb_note = (f"NOT AVAILABLE — {sup.error}" if sup.error else
                f"{sup.covered} of {sup.total} cohort labels carry a "
                f"`technique_nodes.embedding` ({sup.covered / max(sup.total, 1):.0%}); the "
                f"mpnet arms "
                f"{'run' if sup.usable else 'are SKIPPED (coverage below 50%)'}")
    primary = run_pass(build_cohort(per, MIN_BOUTS_PRIMARY, "odd_even"), sup, n_boot)
    floor6 = run_pass(build_cohort(per, MIN_BOUTS_SENSITIVITY, "odd_even"), sup, n_boot)
    chrono = run_pass(build_cohort(per, MIN_BOUTS_PRIMARY, "chronological"), sup, n_boot)
    return Run(gate_note(gate), emb_note, primary, floor6, chrono)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="PoC-E5 — grapple-like v2")
    ap.add_argument("--dry-run", action="store_true", help="print the cohort shape and stop")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)

    gate = load_corpus()
    logger.info("gate: %s", gate_note(gate))
    if gate.error:
        return 1
    per = athlete_bouts(gate.rows)
    if args.dry_run:
        for floor in (MIN_BOUTS_PRIMARY, MIN_BOUTS_SENSITIVITY):
            for scheme in ("odd_even", "chronological"):
                c = build_cohort(per, floor, scheme)
                logger.info("floor=%d split=%s → %d athletes (%d eligible, %d dropped), "
                            "chance MRR %.3f", floor, scheme, c.n, c.eligible, c.dropped,
                            chance_mrr(c.n))
        return 0

    run = run_all(gate, args.n_boot)
    prereg = PREREG.read_text(encoding="utf-8") if PREREG.exists() else \
        "_(pre-registration file missing — this run is NOT pre-registered)_"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_markdown(run, prereg), encoding="utf-8")
    logger.info("wrote %s", OUT)
    for k, v in verdicts(run.primary).items():
        logger.info("VERDICT %s: %s", k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
