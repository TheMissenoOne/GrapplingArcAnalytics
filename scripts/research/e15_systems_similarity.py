"""PoC-E15 — "grapples most like" at the SYSTEM level.

    uv run python -m scripts.research.e15_systems_similarity                # full run → doc
    uv run python -m scripts.research.e15_systems_similarity --dry-run      # marginals only
    uv run python -m scripts.research.e15_systems_similarity --self-check   # no DB, asserts
    uv run python -m scripts.research.e15_systems_similarity --digest       # determinism probe

**Read-only.** Every read is ``matches`` + ``technique_nodes``, both PUBLIC. This cell never
queries the ``graphs`` table, so ``owner_kind`` cannot leak by construction. No write, no
replay, no export.

Pre-registration (written before any arm ran, and the only place the criterion lives):
``docs/research/e15_systems_similarity_prereg.md``. Results are appended to that same file
between ``<!-- E15:RESULTS -->`` markers, so a re-run REPLACES the block rather than
accumulating — a ``git diff`` on a re-run shows real change, not churn.

What this cell tests, and why it is not "systems vs whole graph": production ALREADY compares
systems (``site_data`` → ``compare_profiles`` → ``match_systems``). What it does not do is
look at WHICH techniques are in a system — ``system_similarity`` is 0.50·type-share cosine +
0.20·hub-TYPE + 0.15·size + 0.15·ELO, all of it coarse-type or size. The variable under test
is the per-pair SCORE; the detector, the graphs, the cohort and the split are held fixed.

Determinism: every ranking, grouping and greedy selection carries a total order, and no
dict/set/frozenset iteration decides a tie — failure-archaeology #10, the 2026-07-07
``PYTHONHASHSEED`` reshuffle of these exact analogue lists. ``--digest`` prints a hash of
every arm's distance matrix; two runs under different ``PYTHONHASHSEED`` must agree.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from analysis.athlete_systems import AthleteSystem, build_system_profile
from analysis.constellations.compare import jaccard
from analysis.names import _normalize_name
from analysis.poc.e5_grapple_like import (
    CAREER_NODE_LIMIT,
    MIN_BOUTS_PRIMARY,
    MIN_BOUTS_SENSITIVITY,
    MIN_HALF_EDGES,
    MIN_HALF_NODES,
    N_BOOT,
    PRODUCTION_ARM,
    Cohort,
    EmbeddingSupply,
    Half,
    Method,
    _centroid,
    _paired_ci,
    _system_distance,
    _to_athlete_graph,
    athlete_bouts,
    build_cohort,
    chance_mrr,
    distances,
    load_embeddings,
    wins,
)
from analysis.poc.e9_markov import BoutRow, GateReport, load_corpus
from analysis.poc.signatures import (
    cosine_distance,
    degree_signature,
    retrieval,
    size_signature,
)
from analysis.stats_rigor import bootstrap_ci

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "research" / "e15_systems_similarity_prereg.md"
MARK_OPEN = "<!-- E15:RESULTS -->"
MARK_CLOSE = "<!-- /E15:RESULTS -->"

SEED = 20260820          # PoC-E5/E8/E9's seed — every interval in the repo agrees
E5_GATED_BOUTS = 466     # PoC-E5's published gate, for the drift check

SIZE_NULL = "n1 null: size only (H0)"
DEGREE_NULL = "n2 null: degree histogram"
NODE_JACCARD = "c1 node Jaccard, whole graph (no systems)"
SHARED_JACCARD = "p1 node Jaccard, SHARED vocabulary only (confound probe)"
#: A label used by fewer than this many cohort athletes is dropped by the ``p1`` probe.
#: Fixed from the read-only marginal before the probe ran: 82 of 170 cohort labels (48%)
#: are used by exactly ONE athlete, and such a label can only ever land in the intersection
#: of an athlete's own two halves — it is a self-recognition cheat by construction.
SHARED_VOCAB_MIN_ATHLETES = 3
SYSTEM_JACCARD = "c2 system overlap (member Jaccard, size-weighted)"
FLAT_CENTROID = "r1 mpnet centroid, occurrence-weighted (flat)"
SYSTEM_CENTROID = "c3 system mpnet centroid (size-weighted)"
PROD = f"a0 {PRODUCTION_ARM}"


# ── the system view an arm is scored on ─────────────────────────────────────────
@dataclass(frozen=True)
class SystemsView:
    """One half's systems, plus everything the per-pair scorers need.

    Built once per half per arm. ``systems`` arrives from ``detect_athlete_systems``
    already sorted ``(-size, hub)``; that order is the greedy matcher's tiebreak and must
    not be re-sorted anywhere downstream.
    """

    systems: tuple[AthleteSystem, ...]
    members: tuple[frozenset[str], ...]
    centroids: tuple[np.ndarray, ...]
    total_size: float

    @property
    def empty(self) -> bool:
        return not self.systems


def _system_centroid(sys_: AthleteSystem, occ: dict[str, float],
                     emb: dict[str, np.ndarray]) -> np.ndarray:
    """Occurrence-weighted mpnet centroid over a system's members.

    Members with no embedding are DROPPED, never imputed; a system whose members are all
    unembedded returns an empty vector and scores 0 against everything (``cosine_distance``
    returns NaN on a zero-norm vector, which ``_pair_centroid`` maps to 0.0).
    """
    vecs: list[np.ndarray] = []
    ws: list[float] = []
    for m in sorted(sys_.members):          # total order: the sum is float-stable per member set
        v = emb.get(_normalize_name(str(m)))
        if v is None:
            continue
        vecs.append(v)
        ws.append(max(float(occ.get(m, 1.0)), 1.0))
    if not vecs:
        return np.zeros(0, dtype=np.float64)
    arr = np.asarray(vecs, dtype=np.float64)
    w = np.asarray(ws, dtype=np.float64)
    return np.asarray((arr * w[:, None]).sum(axis=0) / max(float(w.sum()), 1e-12))


def systems_view(h: Half, emb: dict[str, np.ndarray] | None,
                 limit: int | None = None) -> SystemsView:
    ag = _to_athlete_graph(h, limit)
    occ = {k: float(n.count) for k, n in ag.nodes.items()}
    systems = tuple(build_system_profile(h.athlete, ag).systems)
    cents = tuple(_system_centroid(s, occ, emb) for s in systems) if emb is not None \
        else tuple(np.zeros(0) for _ in systems)
    return SystemsView(
        systems=systems,
        members=tuple(frozenset(s.members) for s in systems),
        centroids=cents,
        total_size=float(sum(s.size for s in systems)),
    )


# ── the greedy matcher, with the per-pair score injected ────────────────────────
PairScore = Callable[[SystemsView, int, SystemsView, int], float]


def greedy_similarity(a: SystemsView, b: SystemsView, score: PairScore,
                      ) -> tuple[float, list[tuple[int, int, float]]]:
    """Production's pairing loop (``athlete_systems.match_systems``), scorer injected.

    A's systems are consumed in their own fixed ``(-size, hub)`` order; each takes the
    best-scoring UNMATCHED system in B; strict ``>`` so a tie falls to B's first candidate
    in B's own fixed order. ``used`` is membership-tested only and never iterated, so no
    set order reaches the output (failure-archaeology #10).

    Aggregate = ``Σ_matched size(a_sys)·score / Σ_all size(a_sys)``. An A-system with no
    partner counts as 0. The denominator is CONSTANT within a query row, so it cannot
    change the retrieval ranking — it is there so a number a dossier might print is honest.
    """
    used: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    total = 0.0
    for i, sa in enumerate(a.systems):
        best, bj = -1.0, -1
        for j in range(len(b.systems)):
            if j in used:
                continue
            s = score(a, i, b, j)
            if s > best:
                best, bj = s, j
        if bj >= 0:
            used.add(bj)
            pairs.append((i, bj, best))
            total += float(sa.size) * best
    return (total / a.total_size if a.total_size > 0 else 0.0), pairs


def _pair_jaccard(a: SystemsView, i: int, b: SystemsView, j: int) -> float:
    return jaccard(set(a.members[i]), set(b.members[j]))


def _pair_centroid(a: SystemsView, i: int, b: SystemsView, j: int) -> float:
    d = cosine_distance(a.centroids[i], b.centroids[j])
    return 0.0 if not np.isfinite(d) else max(0.0, 1.0 - d)


def _dist(score: PairScore) -> Callable[[SystemsView, SystemsView], float]:
    def fn(a: SystemsView, b: SystemsView) -> float:
        if a.empty or b.empty:
            return 1.0
        return 1.0 - greedy_similarity(a, b, score)[0]
    return fn


def _node_jaccard_distance(a: frozenset[str], b: frozenset[str]) -> float:
    """The ablation: the degenerate one-system case of ``c2``."""
    return 1.0 - jaccard(set(a), set(b))


def _l2(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def shared_vocabulary(cohort: Cohort, min_athletes: int = SHARED_VOCAB_MIN_ATHLETES
                      ) -> frozenset[str]:
    """Labels used by at least ``min_athletes`` DISTINCT cohort athletes.

    Membership-tested only, never iterated into an output, so no set order escapes.
    Unsupervised: it reads only which labels exist, never which half belongs to whom.
    """
    per: dict[str, set[str]] = {}
    for h in [*cohort.a, *cohort.b]:
        per.setdefault(h.athlete, set()).update(str(n) for n in h.graph.nodes())
    counts: dict[str, int] = {}
    for labels in per.values():
        for lb in labels:
            counts[lb] = counts.get(lb, 0) + 1
    return frozenset(lb for lb, n in counts.items() if n >= min_athletes)


def methods(sup: EmbeddingSupply, vocab: frozenset[str] | None = None) -> list[Method]:
    """The arms, in the pre-registration's order. Only c2 and c3 carry a hypothesis."""
    emb = sup.vectors if sup.usable else None
    out = [
        Method(PROD,
               lambda h: build_system_profile(h.athlete, _to_athlete_graph(h, CAREER_NODE_LIMIT)),
               _system_distance,
               "reference — the shipped percentage, reproduced on halves"),
        Method(SIZE_NULL, lambda h: size_signature(h.graph), _l2,
               "H0 — (log n, log m, mean degree), nothing but how much tape there is"),
        Method(DEGREE_NULL, lambda h: degree_signature(h.graph), _l2,
               "null — normalised degree sequence, no spectrum, no paths"),
        Method(NODE_JACCARD, lambda h: frozenset(h.graph.nodes()), _node_jaccard_distance,
               "ablation — shared techniques with NO system decomposition; the control "
               "that says whether the decomposition earns its keep (H1b)"),
        Method(SYSTEM_JACCARD, lambda h: systems_view(h, None), _dist(_pair_jaccard),
               "H1 — greedy system match scored by member-set Jaccard, size-weighted"),
    ]
    if vocab is not None:
        out.append(Method(
            SHARED_JACCARD,
            lambda h: frozenset(str(n) for n in h.graph.nodes() if str(n) in vocab),
            _node_jaccard_distance,
            "POST-HOC confound probe, no verdict — c1 restricted to labels at least "
            f"{SHARED_VOCAB_MIN_ATHLETES} cohort athletes use; strips the idiosyncratic "
            "vocabulary that can only ever land in an athlete's OWN intersection"))
    if emb is not None:
        out += [
            Method(FLAT_CENTROID, lambda h: _centroid(h, emb, True), cosine_distance,
                   "reference — PoC-E5's strongest arm, carried over as H2's control; "
                   "the SAME measurement as E5's, not an independent confirmation"),
            Method(SYSTEM_CENTROID, lambda h: systems_view(h, emb), _dist(_pair_centroid),
                   "H2 — greedy system match scored by per-system occurrence-weighted "
                   "mpnet centroid cosine, size-weighted"),
        ]
    return out


# ── scoring (E5's machinery, unmodified) ────────────────────────────────────────
@dataclass
class ArmResult:
    name: str
    note: str
    mrr: float
    lo: float
    hi: float
    top1: float
    top3: float
    top5: float
    reciprocal: list[float] = field(default_factory=list)


@dataclass
class Pass:
    floor: int
    scheme: str
    n: int
    eligible: int
    dropped: int
    chance: float
    median_nodes: int = 0
    median_edges: int = 0
    vocab_kept: int = 0
    vocab_total: int = 0
    arms: list[ArmResult] = field(default_factory=list)
    deltas: dict[tuple[str, str], tuple[float, float, float]] = field(default_factory=dict)

    def arm(self, name: str) -> ArmResult | None:
        return next((a for a in self.arms if a.name == name), None)

    def delta(self, a: str, b: str) -> tuple[float, float, float]:
        return self.deltas.get((a, b), (float("nan"),) * 3)


#: every paired comparison this cell reports — (arm, reference). Fixed by the criterion,
#: not derived from the results.
COMPARISONS: tuple[tuple[str, str], ...] = (
    (SYSTEM_JACCARD, SIZE_NULL), (SYSTEM_JACCARD, DEGREE_NULL),
    (SYSTEM_JACCARD, PROD), (SYSTEM_JACCARD, NODE_JACCARD),
    (SYSTEM_CENTROID, FLAT_CENTROID), (SYSTEM_CENTROID, SIZE_NULL),
    (SYSTEM_CENTROID, DEGREE_NULL), (SYSTEM_CENTROID, PROD),
    (NODE_JACCARD, SIZE_NULL), (NODE_JACCARD, DEGREE_NULL), (NODE_JACCARD, PROD),
    (FLAT_CENTROID, SIZE_NULL), (FLAT_CENTROID, DEGREE_NULL), (FLAT_CENTROID, PROD),
    (PROD, SIZE_NULL),
    # post-hoc, disclosed in the amendment: fills cells, decides nothing
    (SHARED_JACCARD, SIZE_NULL), (SHARED_JACCARD, DEGREE_NULL), (SHARED_JACCARD, PROD),
    (SHARED_JACCARD, NODE_JACCARD),
)


def run_pass(cohort: Cohort, sup: EmbeddingSupply, n_boot: int = N_BOOT) -> Pass:
    mn, me = cohort.median_shape
    p = Pass(cohort.floor, cohort.scheme, cohort.n, cohort.eligible, cohort.dropped,
             chance_mrr(cohort.n), mn, me)
    if cohort.n < 2:
        return p
    truth = list(range(cohort.n))
    vocab = shared_vocabulary(cohort)
    p.vocab_kept, p.vocab_total = len(vocab), len({
        str(n) for h in [*cohort.a, *cohort.b] for n in h.graph.nodes()})
    rr: dict[str, list[float]] = {}
    for m in methods(sup, vocab):
        r = retrieval(m.name, distances(m, cohort), truth)
        rr[m.name] = list(r.reciprocal)
        obs, lo, hi = bootstrap_ci(r.reciprocal, lambda v: float(np.mean(v)),
                                   n_boot=n_boot, seed=SEED)
        p.arms.append(ArmResult(m.name, m.note, obs, lo, hi,
                                r.top_k(1), r.top_k(3), r.top_k(5), list(r.reciprocal)))
    for a, b in COMPARISONS:
        if a in rr and b in rr:
            p.deltas[(a, b)] = _paired_ci(rr[a], rr[b], n_boot)
    return p


# ── verdicts, applied to the PRIMARY pass only ──────────────────────────────────
def verdicts(p: Pass) -> dict[str, str]:
    out: dict[str, str] = {}
    if p.n < 2:
        return {"cell": "NOT RUN — no cohort"}

    def clears(arm: str, *refs: str) -> bool:
        return all(wins(p.delta(arm, r)) for r in refs)

    h1 = p.arm(SYSTEM_JACCARD)
    if h1 is None:
        out["H1"] = "NOT RUN"
    else:
        ok = clears(SYSTEM_JACCARD, SIZE_NULL, DEGREE_NULL, PROD)
        out["H1 — system-level overlap beats the size descriptor"] = (
            f"{'ACCEPT' if ok else 'REJECT'} — MRR {h1.mrr:.3f} [{h1.lo:.3f}, {h1.hi:.3f}]; "
            f"Δ vs H0/size {_ci(p.delta(SYSTEM_JACCARD, SIZE_NULL))}, "
            f"Δ vs degree {_ci(p.delta(SYSTEM_JACCARD, DEGREE_NULL))}, "
            f"Δ vs production {_ci(p.delta(SYSTEM_JACCARD, PROD))}"
        )
        d = p.delta(SYSTEM_JACCARD, NODE_JACCARD)
        out["H1b — does the DECOMPOSITION earn its keep? (sub-question, no accept/reject)"] = (
            ("YES — the system decomposition adds signal over plain shared-technique "
             "overlap: " if wins(d) else
             "NO — shared-technique overlap covers the effect; on this corpus 'systems' is a "
             "PRESENTATION decision, not a metric one: ")
            + f"Δ(c2 − c1) {_ci(d)}"
        )

    h2 = p.arm(SYSTEM_CENTROID)
    if h2 is None:
        out["H2"] = "NOT RUN — embedding coverage below the 50% gate"
    else:
        ok = clears(SYSTEM_CENTROID, FLAT_CENTROID, SIZE_NULL, DEGREE_NULL)
        out["H2 — per-system mpnet centroid beats per-graph centroid"] = (
            f"{'ACCEPT' if ok else 'REJECT'} — MRR {h2.mrr:.3f} [{h2.lo:.3f}, {h2.hi:.3f}]; "
            f"Δ vs flat centroid {_ci(p.delta(SYSTEM_CENTROID, FLAT_CENTROID))}, "
            f"Δ vs H0/size {_ci(p.delta(SYSTEM_CENTROID, SIZE_NULL))}, "
            f"Δ vs degree {_ci(p.delta(SYSTEM_CENTROID, DEGREE_NULL))}"
        )

    accepted = [n for n in (SYSTEM_JACCARD, SYSTEM_CENTROID) if p.arm(n) is not None and (
        (n == SYSTEM_JACCARD and clears(n, SIZE_NULL, DEGREE_NULL, PROD))
        or (n == SYSTEM_CENTROID and clears(n, FLAT_CENTROID, SIZE_NULL, DEGREE_NULL)))]
    out["ship"] = (
        f"WIRE — {', '.join(accepted)} cleared its pre-registered criterion"
        if accepted else
        "DO NOT WIRE — no system-level arm cleared its criterion; the shipped ranking stands "
        "and the change that IS supported is presentational (§7), which needs no new metric"
    )
    return out


# ── report ──────────────────────────────────────────────────────────────────────
def _ci(d: tuple[float, float, float]) -> str:
    o, lo, hi = d
    return "—" if not np.isfinite(o) else f"{o:+.3f} [{lo:+.3f}, {hi:+.3f}]"


def _arm_table(p: Pass) -> list[str]:
    refs = (SIZE_NULL, DEGREE_NULL, PROD)
    rows = ["| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) "
            "| Δ vs degree | Δ vs production |",
            "|---|---|---|---|---|---|---|---|---|"]
    for a in p.arms:
        role = ("**H1**" if a.name == SYSTEM_JACCARD else "**H2**" if a.name == SYSTEM_CENTROID
                else "null" if a.name in (SIZE_NULL, DEGREE_NULL)
                else "ablation" if a.name == NODE_JACCARD
                else "probe (post-hoc)" if a.name == SHARED_JACCARD else "reference")
        cells = " | ".join(_ci(p.delta(a.name, r)) if a.name != r else "—" for r in refs)
        rows.append(f"| {a.name} | {role} | {a.mrr:.3f} [{a.lo:.3f}, {a.hi:.3f}] | "
                    f"{a.top1:.0%} | {a.top3:.0%} | {a.top5:.0%} | {cells} |")
    rows.append(f"| _chance (random ranking)_ | — | {p.chance:.3f} | {1 / p.n:.0%} | "
                f"{min(3, p.n) / p.n:.0%} | {min(5, p.n) / p.n:.0%} | — | — | — |")
    return rows


def _pass_section(p: Pass, title: str) -> list[str]:
    out = [f"### {title}", "",
           f"Cohort: **{p.n} athletes** (of {p.eligible} at the ≥{p.floor}-bout floor; "
           f"{p.dropped} lost a half to the ≥{MIN_HALF_NODES}-node / ≥{MIN_HALF_EDGES}-edge "
           f"gate). Split: `{p.scheme}`. Median half-graph: {p.median_nodes} nodes / "
           f"{p.median_edges} edges. Chance MRR: {p.chance:.3f}. Shared vocabulary "
           f"(≥{SHARED_VOCAB_MIN_ATHLETES} athletes): {p.vocab_kept}/{p.vocab_total} labels.",
           ""]
    if p.n < 2:
        return [*out, "Too few athletes to rank anything. No arms run.", ""]
    return [*out, *_arm_table(p), ""]


@dataclass
class Run:
    gate_note: str
    embedding_note: str
    primary: Pass
    others: list[tuple[Pass, str]] = field(default_factory=list)


def _reading(run: Run) -> list[str]:
    p = run.primary
    out = ["## Reading", ""]
    prod, size, c1, c2 = (p.arm(PROD), p.arm(SIZE_NULL), p.arm(NODE_JACCARD),
                          p.arm(SYSTEM_JACCARD))
    r1, c3 = p.arm(FLAT_CENTROID), p.arm(SYSTEM_CENTROID)
    if prod is None or size is None:
        return [*out, "Not run.", ""]

    out.append(
        f"On the split this cell pre-registered as primary — chronological halves that share "
        f"no event, no opponent set and no annotation batch — the shipped method scores MRR "
        f"{prod.mrr:.3f} [{prod.lo:.3f}, {prod.hi:.3f}] against a chance floor of "
        f"{p.chance:.3f} over {p.n} candidates, and against the three-number size descriptor's "
        f"{size.mrr:.3f} (paired Δ {_ci(p.delta(PROD, SIZE_NULL))}).")

    if c2 is not None and c1 is not None:
        out.append(
            f"**H1, the owner's decision as a metric.** Scoring matched systems by WHICH "
            f"techniques they contain (member Jaccard, size-weighted) reaches MRR "
            f"{c2.mrr:.3f} [{c2.lo:.3f}, {c2.hi:.3f}]: Δ vs H0/size "
            f"{_ci(p.delta(SYSTEM_JACCARD, SIZE_NULL))}, Δ vs production "
            f"{_ci(p.delta(SYSTEM_JACCARD, PROD))}. That is the pre-registered criterion, and "
            f"it is the only thing that decides H1.")
        d = p.delta(SYSTEM_JACCARD, NODE_JACCARD)
        out.append(
            f"**H1b, the question the ablation exists for.** Plain shared-technique overlap "
            f"with NO systems at all (whole-graph node Jaccard) scores {c1.mrr:.3f}; the "
            f"system decomposition moves it by {_ci(d)}. "
            + ("The decomposition earns its keep as a metric."
               if wins(d) else
               "The decomposition does not measurably change the ranking. The honest reading "
               "is that shared techniques carry the signal and the system grouping is worth "
               "having for what it lets a dossier SAY, not for where it ranks an athlete — "
               "which is exactly the split between §7 and the metric."))
    if c3 is not None and r1 is not None:
        out.append(
            f"**H2.** Decomposing the mpnet centroid per system reaches {c3.mrr:.3f} against "
            f"the flat occurrence-weighted centroid's {r1.mrr:.3f}, paired Δ "
            f"{_ci(p.delta(SYSTEM_CENTROID, FLAT_CENTROID))}. Note what the reference is: "
            f"r1 is PoC-E5's strongest arm re-run on nearly the same data, so its own number "
            f"here is that same measurement ± corpus drift and carries no verdict — it exists "
            f"so H2 has something to be tested against.")
        out.append(
            f"Against the null that has beaten everything so far, r1 scores Δ vs H0/size "
            f"{_ci(p.delta(FLAT_CENTROID, SIZE_NULL))} and c3 scores "
            f"{_ci(p.delta(SYSTEM_CENTROID, SIZE_NULL))}. Clearing the size null on "
            f"{p.n} queries is the bar PoC-E5 set and it has not moved.")

    probe = p.arm(SHARED_JACCARD)
    if c1 is not None:
        out.append(
            f"**The finding this cell did not go looking for.** `c1`, registered as an "
            f"ABLATION to control H1 and carrying no verdict, is the strongest arm in every "
            f"pass — MRR {c1.mrr:.3f} here — and it is the first arm in this series to clear "
            f"the size null: Δ vs H0/size {_ci(p.delta(NODE_JACCARD, SIZE_NULL))}, Δ vs "
            f"degree {_ci(p.delta(NODE_JACCARD, DEGREE_NULL))}, Δ vs production "
            f"{_ci(p.delta(NODE_JACCARD, PROD))}. It is also the simplest thing in the table: "
            f"`|A∩B| / |A∪B|` over technique labels, no communities, no embeddings, no "
            f"weighting. **This cell cannot accept it** — it was pre-registered as a control, "
            f"and promoting a control to a winner after seeing the table is the exact error "
            f"pre-registration exists to prevent. It is the pre-registered hypothesis for the "
            f"NEXT cell, and §Amendment says what that cell has to rule out first.")
    if probe is not None and c1 is not None:
        d_probe = p.delta(SHARED_JACCARD, SIZE_NULL)
        out.append(
            f"**And the confound that has to be ruled out before anyone ships `c1`.** "
            f"{p.vocab_total - p.vocab_kept} of {p.vocab_total} cohort labels are used by "
            f"fewer than {SHARED_VOCAB_MIN_ATHLETES} athletes; a label only one athlete uses "
            f"can land in the intersection of that athlete's OWN two halves and nowhere else, "
            f"which is a self-recognition cheat by construction, not style. Restricting `c1` "
            f"to the shared vocabulary drops it to {probe.mrr:.3f} "
            f"[{probe.lo:.3f}, {probe.hi:.3f}] (Δ vs c1 "
            f"{_ci(p.delta(SHARED_JACCARD, NODE_JACCARD))}), and against the size null it "
            f"scores {_ci(d_probe)}. "
            + ("The advantage SURVIVES the restriction, so the signal is shared-vocabulary "
               "style rather than annotation provenance — the strongest single result in "
               "this cell."
               if wins(d_probe) else
               "The advantage does NOT survive the restriction: on shared vocabulary alone "
               "the arm no longer clears the size null, so what `c1` recognises cannot be "
               "separated from an athlete's idiosyncratic label vocabulary — i.e. from who "
               "annotated their tape. Do not ship it on this evidence.")
            + " This probe is POST-HOC (§Amendment) and carries no verdict either way.")

    v = verdicts(p)
    out += ["", "### Verdicts", ""]
    out += [f"{i}. **{k}** — {val}" for i, (k, val) in enumerate(v.items(), start=1)]
    out += ["", "Nothing in this cell touches production: no DB write, no replay, no site "
                "export, no edit to `export/site_data.py` or `analysis/athlete_systems.py`. "
                "The presentation proposal in §7 above stands on its own and does not depend "
                "on any arm winning.", ""]
    return out


def render(run: Run) -> str:
    lines = [MARK_OPEN, "",
             "## Results", "",
             "Generated by `uv run python -m scripts.research.e15_systems_similarity` — "
             "**do not hand-edit**; a re-run replaces this block. Script: "
             "`scripts/research/e15_systems_similarity.py`.", "",
             f"**Corpus gate:** {run.gate_note}", "",
             f"**Embeddings:** {run.embedding_note}", ""]
    lines += _pass_section(run.primary, "PRIMARY — chronological split, ≥4-bout floor")
    for p, title in run.others:
        lines += _pass_section(p, title)
    lines += _reading(run)
    lines += [MARK_CLOSE, ""]
    return "\n".join(lines)


def write_results(body: str) -> None:
    doc = DOC.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(MARK_OPEN) + r".*?" + re.escape(MARK_CLOSE) + r"\n?",
                         re.DOTALL)
    doc = pattern.sub("", doc).rstrip() + "\n\n" + body
    DOC.write_text(doc, encoding="utf-8")


# ── run ─────────────────────────────────────────────────────────────────────────
def gate_note(gate: GateReport) -> str:
    base = (f"{gate.passed} gated bouts of {gate.total} final+sequence "
            f"({gate.total - gate.with_sequence} under 4 events, {gate.one_sided} dropped as "
            f"one-sided)")
    if gate.passed == E5_GATED_BOUTS:
        return f"{base} — matching PoC-E5's published {E5_GATED_BOUTS}"
    return (f"{base} — **DRIFT** against PoC-E5's published {E5_GATED_BOUTS}; the corpus moved "
            f"since that cell ran, so its numbers are not byte-comparable to these.")


def _supply(gate: GateReport) -> tuple[dict[str, list[BoutRow]], EmbeddingSupply, str]:
    from analysis.poc.e5_grapple_like import _graph
    per = athlete_bouts(gate.rows)
    labels = sorted({str(lb) for a, rows in per.items() for lb in _graph(rows, a).nodes()})
    sup = load_embeddings(labels)
    note = (f"NOT AVAILABLE — {sup.error}" if sup.error else
            f"{sup.covered} of {sup.total} cohort labels carry a `technique_nodes.embedding` "
            f"({sup.covered / max(sup.total, 1):.0%}); the mpnet arms "
            f"{'run' if sup.usable else 'are SKIPPED (coverage below the 50% gate)'}")
    return per, sup, note


PASSES: tuple[tuple[int, str, str], ...] = (
    (MIN_BOUTS_PRIMARY, "chronological", "PRIMARY — chronological split, ≥4-bout floor"),
    (MIN_BOUTS_PRIMARY, "odd_even",
     "Sensitivity — odd/even split, ≥4-bout floor (PoC-E5's primary split)"),
    (MIN_BOUTS_SENSITIVITY, "chronological", "Sensitivity — chronological split, ≥6-bout floor"),
    (MIN_BOUTS_SENSITIVITY, "odd_even", "Sensitivity — odd/even split, ≥6-bout floor"),
)


def digest(per: dict[str, list[BoutRow]], sup: EmbeddingSupply) -> str:
    """sha256 over every arm's distance matrix on the primary cohort — the determinism probe.

    Run twice under different ``PYTHONHASHSEED``; the two digests must be identical. Skips
    the bootstrap entirely, so it is cheap enough to run as a gate.
    """
    cohort = build_cohort(per, MIN_BOUTS_PRIMARY, "chronological")
    vocab = shared_vocabulary(cohort)
    h = hashlib.sha256()
    h.update(f"{cohort.n}|{'|'.join(x.athlete for x in cohort.a)}".encode())
    h.update("|".join(sorted(vocab)).encode())   # the probe's filter is part of the check
    for m in methods(sup, vocab):
        d = np.round(distances(m, cohort), 9)
        h.update(m.name.encode())
        h.update(np.ascontiguousarray(d).tobytes())
    return h.hexdigest()


def self_check() -> None:
    """One runnable check, no DB: the greedy matcher and the aggregate, on hand-built input."""
    def sys_(hub: str, members: list[str]) -> AthleteSystem:
        return AthleteSystem(name=hub, hub=hub, hub_type="guard", members=members,
                             type_vector=[0.0] * 8, size=len(members), system_elo=None,
                             transition_count=0, internal_edges=[])

    def view(sets: list[list[str]]) -> SystemsView:
        ss = tuple(sys_(m[0], m) for m in sets)
        return SystemsView(ss, tuple(frozenset(m) for m in sets),
                           tuple(np.zeros(0) for _ in ss),
                           float(sum(len(m) for m in sets)))

    a = view([["x", "y", "z"], ["p", "q"]])
    same, _ = greedy_similarity(a, a, _pair_jaccard)
    assert abs(same - 1.0) < 1e-12, same                      # identical → 1.0

    b = view([["p", "q"], ["x", "y", "z"]])
    sim, pairs = greedy_similarity(a, b, _pair_jaccard)
    assert abs(sim - 1.0) < 1e-12, sim                        # order-independent
    assert pairs == [(0, 1, 1.0), (1, 0, 1.0)], pairs         # matched across positions

    c = view([["m", "n"]])
    sim, pairs = greedy_similarity(a, c, _pair_jaccard)
    assert len(pairs) == 1 and pairs[0][2] == 0.0             # A's extra system → unmatched
    assert sim == 0.0, sim

    d = view([["x", "y", "w"], ["p", "r"]])
    sim, _ = greedy_similarity(a, d, _pair_jaccard)
    # 3/5 · (3 sized) + 1/3 · (2 sized), over total size 5
    assert abs(sim - (3 * 0.5 + 2 * (1 / 3)) / 5) < 1e-12, sim

    # a bigger system must dominate the aggregate over a smaller perfect match
    e = view([["a1", "a2", "a3", "a4"], ["p", "q"]])
    f = view([["z9"], ["p", "q"]])
    sim, _ = greedy_similarity(e, f, _pair_jaccard)
    assert abs(sim - (4 * 0.0 + 2 * 1.0) / 6) < 1e-12, sim

    # empty side → distance 1.0, never NaN (a NaN would rank last by luck, not by measure)
    empty = SystemsView((), (), (), 0.0)
    assert _dist(_pair_jaccard)(a, empty) == 1.0
    assert _dist(_pair_jaccard)(empty, a) == 1.0

    # the tiebreak is B's own fixed order, never set iteration
    g = view([["x", "y", "z"]])
    hh = view([["k1"], ["k2"]])
    _, pairs = greedy_similarity(g, hh, _pair_jaccard)
    assert pairs == [(0, 0, 0.0)], pairs

    # ablation agrees with the degenerate one-system case
    assert _node_jaccard_distance(frozenset("abc"), frozenset("abc")) == 0.0
    assert _node_jaccard_distance(frozenset("abc"), frozenset("xyz")) == 1.0
    print("self-check OK")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="PoC-E15 — system-level grapple-like")
    ap.add_argument("--self-check", action="store_true", help="pure asserts, no DB")
    ap.add_argument("--dry-run", action="store_true", help="marginals only, no arms")
    ap.add_argument("--digest", action="store_true", help="determinism probe, no bootstrap")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)

    if args.self_check:
        self_check()
        return 0

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    gate = load_corpus()
    logger.info("gate: %s", gate_note(gate))
    if gate.error:
        return 1
    per, sup, emb_note = _supply(gate)
    logger.info("embeddings: %s", emb_note)

    if args.dry_run:
        for floor, scheme, _ in PASSES:
            c = build_cohort(per, floor, scheme)
            logger.info("floor=%d split=%-14s → %d athletes (%d eligible, %d dropped), "
                        "chance MRR %.3f", floor, scheme, c.n, c.eligible, c.dropped,
                        chance_mrr(c.n))
        return 0

    if args.digest:
        print(digest(per, sup))
        return 0

    passes = [(run_pass(build_cohort(per, f, s), sup, args.n_boot), t) for f, s, t in PASSES]
    run = Run(gate_note(gate), emb_note, passes[0][0], passes[1:])
    write_results(render(run))
    logger.info("wrote %s", DOC)
    for k, v in verdicts(run.primary).items():
        logger.info("VERDICT %s: %s", k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
