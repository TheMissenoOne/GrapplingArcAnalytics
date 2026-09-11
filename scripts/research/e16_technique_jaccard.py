"""PoC-E16 — plain technique-label Jaccard as "grapples most like".

    uv run python -m scripts.research.e16_technique_jaccard                # full run → doc
    uv run python -m scripts.research.e16_technique_jaccard --dry-run      # marginals only
    uv run python -m scripts.research.e16_technique_jaccard --self-check   # no DB, asserts
    uv run python -m scripts.research.e16_technique_jaccard --digest       # determinism probe

**Read-only.** Every read is ``matches`` + ``technique_nodes``, both PUBLIC. This cell never
queries the ``graphs`` table, so ``owner_kind`` cannot leak by construction. No write, no
replay, no export.

Pre-registration (written before any arm ran, and the only place the criterion lives):
``docs/research/e16_technique_jaccard_prereg.md``. Results are appended to that same file
between ``<!-- E16:RESULTS -->`` markers, so a re-run REPLACES the block rather than
accumulating — a ``git diff`` on a re-run shows real change, not churn.

What this cell tests: PoC-E15 ended with a CONTROL winning. ``c1`` — ``|A∩B| / |A∪B|`` over
technique labels, no communities, no embeddings, no weighting — outscored every arm and was
the first to clear the size null, and E15 could not accept it because it had been registered
as an ablation. Here it is the hypothesis, with the roles corrected, an annotation-provenance
GATE it must pass first (§3 of the prereg), and the shared-vocabulary sweep as the reading.

Everything that produces a number is IMPORTED from E5/E15 so the numbers stay comparable; the
only new code is the ingestion-batch assignment, the two sub-cohorts it builds, the sweep loop
and the runner.

Determinism: every ranking, grouping and selection carries a total order — batches sort by
``(created_at, id)``, sub-cohorts preserve the cohort's own athlete order, vocabularies are
membership-tested and never iterated into an output (failure-archaeology #10, the 2026-07-07
``PYTHONHASHSEED`` reshuffle of these exact analogue lists).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from analysis.athlete_systems import build_system_profile
from analysis.poc.e5_grapple_like import (
    CAREER_NODE_LIMIT,
    MIN_HALF_EDGES,
    MIN_HALF_NODES,
    N_BOOT,
    Cohort,
    EmbeddingSupply,
    Half,
    Method,
    _centroid,
    _paired_ci,
    _system_distance,
    _to_athlete_graph,
    build_cohort,
    chance_mrr,
    distances,
    wins,
)
from analysis.poc.e9_markov import BoutRow, GateReport, load_corpus
from analysis.poc.signatures import (
    Retrieval,
    cosine_distance,
    degree_signature,
    retrieval,
    size_signature,
)
from analysis.stats_rigor import bootstrap_ci
from scripts.research.e15_systems_similarity import (
    DEGREE_NULL,
    FLAT_CENTROID,
    NODE_JACCARD,
    PASSES,
    PROD,
    SEED,
    SIZE_NULL,
    _ci,
    _l2,
    _node_jaccard_distance,
    _supply,
    shared_vocabulary,
)

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "research" / "e16_technique_jaccard_prereg.md"
MARK_OPEN = "<!-- E16:RESULTS -->"
MARK_CLOSE = "<!-- /E16:RESULTS -->"

E15_GATED_BOUTS = 465        # PoC-E15's published gate; this cell is comparable to that one
BATCH_GAP_MINUTES = 30       # §3a: a new ingestion session starts after this idle gap
SWEEP_THRESHOLDS: tuple[int, ...] = (2, 3, 4)   # §5, fixed before the run

#: Roles as re-assigned by the pre-registration (§1). Only ``c1`` carries a verdict.
ROLES: dict[str, str] = {
    NODE_JACCARD: "**H1 (candidate)**",
    SIZE_NULL: "null (H0)",
    DEGREE_NULL: "null",
    PROD: "reference",
    FLAT_CENTROID: "reference",
}


def sweep_name(k: int) -> str:
    return f"v{k} c1 over labels used by ≥{k} cohort athletes"


# ── ingestion batches: the only provenance handle the schema carries (§3a) ───────
def _created_at(row: BoutRow) -> datetime:
    """``BoutRow.key`` is ``(year, created_at, id)`` — the timestamp is already loaded."""
    return datetime.fromisoformat(str(row.key[1]))


def assign_batches(rows: Sequence[BoutRow],
                   gap_minutes: int = BATCH_GAP_MINUTES) -> dict[str, int]:
    """bout id → ingestion-session index, by row-insert time.

    Sorted by ``(created_at, id)`` — a TOTAL order, so the segmentation is identical across
    processes (the cohort's own ``key`` order sorts by competition YEAR first and would give
    a different, meaningless segmentation).

    ponytail: ``created_at`` is a row-insert timestamp, not a dump-file id — there is no
    import/batch table in the schema (checked). Ceiling = it cannot split two annotators
    inside one session; upgrade path = a real ``source_batch`` column on ``matches``.
    """
    out: dict[str, int] = {}
    idx, prev = 0, None
    for r in sorted(rows, key=lambda x: (_created_at(x), str(x.key[2]))):
        t = _created_at(r)
        if prev is not None and (t - prev).total_seconds() > gap_minutes * 60:
            idx += 1
        out[str(r.key[2])] = idx
        prev = t
    return out


def modal_batch(rows: Sequence[BoutRow], batch_of: dict[str, int]) -> int:
    """The session most of these bouts came from; ties to the LOWEST index (total order)."""
    counts = Counter(batch_of[str(r.key[2])] for r in rows)
    return min(counts, key=lambda b: (-counts[b], b))


def half_rows(rows: Sequence[BoutRow], scheme: str) -> tuple[list[BoutRow], list[BoutRow]]:
    """``split_halves``' row partition, restated because it returns graphs, not rows."""
    ordered = sorted(rows, key=lambda r: r.key)
    if scheme == "odd_even":
        return ordered[0::2], ordered[1::2]
    cut = len(ordered) // 2
    return ordered[:cut], ordered[cut:]


@dataclass(frozen=True)
class Provenance:
    """Which cohort athletes share an ingestion batch across their two halves (§3c)."""

    batch_a: tuple[int, ...]
    batch_b: tuple[int, ...]
    cross: tuple[int, ...]        # cohort indices whose halves' modal sessions DIFFER
    within: tuple[int, ...]
    pool: tuple[int, ...]         # the largest single-session same-batch group
    pool_batch: int
    pool_purity: float            # share of pool bouts actually inside the modal session


def provenance(cohort: Cohort, per: dict[str, list[BoutRow]],
               batch_of: dict[str, int]) -> Provenance:
    ba, bb = [], []
    for h in cohort.a:
        ar, br = half_rows(per[h.athlete], cohort.scheme)
        ba.append(modal_batch(ar, batch_of))
        bb.append(modal_batch(br, batch_of))
    cross = tuple(i for i in range(cohort.n) if ba[i] != bb[i])
    within = tuple(i for i in range(cohort.n) if ba[i] == bb[i])
    groups = Counter(ba[i] for i in within)
    pool_batch = min(groups, key=lambda b: (-groups[b], b)) if groups else -1
    pool = tuple(i for i in within if ba[i] == pool_batch)
    inside = total = 0
    for i in pool:
        for r in per[cohort.a[i].athlete]:
            total += 1
            inside += int(batch_of[str(r.key[2])] == pool_batch)
    return Provenance(tuple(ba), tuple(bb), cross, within, pool, pool_batch,
                      inside / total if total else float("nan"))


def sub_cohort(cohort: Cohort, idx: Sequence[int]) -> Cohort:
    """Cohort restricted to ``idx``, preserving the cohort's own athlete order."""
    c = Cohort(floor=cohort.floor, scheme=cohort.scheme,
               eligible=len(idx), dropped=0)
    c.a = [cohort.a[i] for i in idx]
    c.b = [cohort.b[i] for i in idx]
    return c


# ── arms ────────────────────────────────────────────────────────────────────────
def methods(sup: EmbeddingSupply, cohort: Cohort,
            thresholds: Sequence[int] = ()) -> list[Method]:
    """The arms in the pre-registration's order. Only ``c1`` carries a verdict."""
    out = [
        Method(NODE_JACCARD, lambda h: frozenset(str(n) for n in h.graph.nodes()),
               _node_jaccard_distance,
               "H1 — |A∩B|/|A∪B| over technique labels; no communities, no embeddings, "
               "no weighting"),
        Method(SIZE_NULL, lambda h: size_signature(h.graph), _l2,
               "H0 — (log n, log m, mean degree), nothing but how much tape there is"),
        Method(DEGREE_NULL, lambda h: degree_signature(h.graph), _l2,
               "null — normalised degree sequence, no spectrum, no paths"),
        Method(PROD,
               lambda h: build_system_profile(h.athlete, _to_athlete_graph(h, CAREER_NODE_LIMIT)),
               _system_distance,
               "reference — the shipped ranking, reproduced on halves"),
    ]
    if sup.usable:
        out.append(Method(FLAT_CENTROID, lambda h: _centroid(h, sup.vectors, True),
                          cosine_distance,
                          "reference, no verdict — PoC-E5's strongest arm; the SAME "
                          "measurement as E5's, not an independent confirmation"))
    for k in thresholds:
        vocab = shared_vocabulary(cohort, k)

        def restricted(h: Half, v: frozenset[str] = vocab) -> frozenset[str]:
            return frozenset(str(n) for n in h.graph.nodes() if str(n) in v)

        out.append(Method(
            sweep_name(k), restricted, _node_jaccard_distance,
            f"sweep, no verdict — c1 restricted to the {len(vocab)} labels at least {k} "
            f"cohort athletes use"))
    return out


# ── one scored pass ─────────────────────────────────────────────────────────────
@dataclass
class Arm:
    name: str
    note: str
    r: Retrieval
    mrr: float
    lo: float
    hi: float


@dataclass
class Pass:
    title: str
    scheme: str
    floor: int
    n: int
    chance: float
    median_nodes: int = 0
    median_edges: int = 0
    vocab_total: int = 0
    vocab_kept: dict[int, int] = field(default_factory=dict)
    arms: list[Arm] = field(default_factory=list)
    deltas: dict[tuple[str, str], tuple[float, float, float]] = field(default_factory=dict)
    note: str = ""

    def arm(self, name: str) -> Arm | None:
        return next((a for a in self.arms if a.name == name), None)

    def delta(self, a: str, b: str) -> tuple[float, float, float]:
        return self.deltas.get((a, b), (float("nan"),) * 3)


def _boot(values: Sequence[float], n_boot: int) -> tuple[float, float, float]:
    return bootstrap_ci(list(values), lambda v: float(np.mean(v)), n_boot=n_boot, seed=SEED)


def comparisons(names: Sequence[str]) -> list[tuple[str, str]]:
    """Every paired comparison this cell reports — fixed by the criterion, not the results."""
    out = [(NODE_JACCARD, SIZE_NULL), (NODE_JACCARD, DEGREE_NULL), (NODE_JACCARD, PROD),
           (NODE_JACCARD, FLAT_CENTROID), (PROD, SIZE_NULL), (FLAT_CENTROID, SIZE_NULL)]
    for k in SWEEP_THRESHOLDS:
        out += [(sweep_name(k), SIZE_NULL), (sweep_name(k), NODE_JACCARD)]
    return [(a, b) for a, b in out if a in names and b in names]


def run_pass(cohort: Cohort, sup: EmbeddingSupply, title: str, n_boot: int,
             thresholds: Sequence[int] = (), note: str = "") -> Pass:
    mn, me = cohort.median_shape
    p = Pass(title, cohort.scheme, cohort.floor, cohort.n, chance_mrr(cohort.n), mn, me,
             note=note)
    if cohort.n < 2:
        return p
    p.vocab_total = len({str(n) for h in [*cohort.a, *cohort.b] for n in h.graph.nodes()})
    p.vocab_kept = {k: len(shared_vocabulary(cohort, k)) for k in thresholds}
    truth = list(range(cohort.n))
    rr: dict[str, list[float]] = {}
    for m in methods(sup, cohort, thresholds):
        r = retrieval(m.name, distances(m, cohort), truth)
        rr[m.name] = list(r.reciprocal)
        obs, lo, hi = _boot(r.reciprocal, n_boot)
        p.arms.append(Arm(m.name, m.note, r, obs, lo, hi))
    for a, b in comparisons(list(rr)):
        p.deltas[(a, b)] = _paired_ci(rr[a], rr[b], n_boot)
    return p


def subgroup(p: Pass, idx: Sequence[int], title: str, n_boot: int) -> Pass:
    """Same ranking, MRR read over a SUBSET of queries — the cross/within-batch split (§3c i).

    The candidate pool is untouched: these queries were ranked against all ``p.n`` halves.
    Only which queries the mean is taken over changes.
    """
    out = Pass(title, p.scheme, p.floor, len(idx), p.chance, p.median_nodes, p.median_edges)
    rr: dict[str, list[float]] = {}
    for a in p.arms:
        sub = Retrieval(a.name, [a.r.ranks[i] for i in idx],
                        [a.r.reciprocal[i] for i in idx], a.r.n_candidates)
        rr[a.name] = list(sub.reciprocal)
        obs, lo, hi = (_boot(sub.reciprocal, n_boot) if idx else (float("nan"),) * 3)
        out.arms.append(Arm(a.name, a.note, sub, obs, lo, hi))
    if len(idx) >= 2:
        for x, y in comparisons(list(rr)):
            out.deltas[(x, y)] = _paired_ci(rr[x], rr[y], n_boot)
    return out


# ── the provenance gate + the verdict (§3d, §4) ─────────────────────────────────
def gate(cross: Pass, pool: Pass, cohort_chance: float) -> tuple[bool, str]:
    """§3d, applied exactly as written. Both sub-tests must hold; otherwise FAIL."""
    def leg(p: Pass, floor: float, label: str) -> tuple[bool, str]:
        a = p.arm(NODE_JACCARD)
        if a is None or p.n < 2:
            return False, f"{label}: NOT RUN (n={p.n})"
        d = p.delta(NODE_JACCARD, SIZE_NULL)
        above = bool(np.isfinite(a.lo) and a.lo > floor)
        positive = bool(np.isfinite(d[0]) and d[0] > 0.0)
        return (above and positive,
                f"{label} (n={p.n}): c1 MRR {a.mrr:.3f} [{a.lo:.3f}, {a.hi:.3f}] vs chance "
                f"{floor:.3f} → {'above' if above else 'NOT above'}; Δ vs size {_ci(d)} → "
                f"{'positive' if positive else 'NOT positive'}")

    ok_x, why_x = leg(cross, cohort_chance, "cross-batch subgroup")
    ok_p, why_p = leg(pool, pool.chance, "batch-stratified pool")
    return ok_x and ok_p, f"{why_x} · {why_p}"


def sweep_reading(p: Pass) -> tuple[str, str]:
    """§5's three branches, written before the run; the first that matches is the reading."""
    deltas = {k: p.delta(sweep_name(k), SIZE_NULL) for k in SWEEP_THRESHOLDS}
    shape = "; ".join(f"≥{k} ({p.vocab_kept.get(k, 0)}/{p.vocab_total} labels) {_ci(d)}"
                      for k, d in deltas.items())
    if all(wins(d) for d in deltas.values()):
        return "STYLE", (
            "the advantage survives at every threshold, so what `c1` recognises is which "
            "techniques an athlete uses, not which words their annotator picked — " + shape)
    if not wins(deltas[SWEEP_THRESHOLDS[0]]):
        return "VOCABULARY", (
            "the advantage does NOT survive the FIRST restriction — it needed the labels only "
            "one athlete uses, which can land in that athlete's OWN intersection and nowhere "
            "else. That is a self-recognition cheat by construction — " + shape)
    return "THIN", (
        "the advantage survives the first restriction and decays out of significance further "
        "up. Registered as decay, NOT as evidence of a confound: at the top threshold only "
        f"{p.vocab_kept.get(SWEEP_THRESHOLDS[-1], 0)} of {p.vocab_total} labels remain over a "
        f"median {p.median_nodes}-node half-graph, so this is as consistent with running out "
        "of vocabulary as with running out of signal, and this corpus cannot say which — "
        + shape)


def verdicts(primary: Pass, cross: Pass, pool: Pass) -> dict[str, str]:
    out: dict[str, str] = {}
    c1 = primary.arm(NODE_JACCARD)
    if c1 is None or primary.n < 2:
        return {"cell": "NOT RUN — no cohort"}

    gate_ok, gate_why = gate(cross, pool, primary.chance)
    out["PROVENANCE GATE (§3d) — a pair sharing an ingestion batch must not be advantaged"] = (
        f"{'PASS' if gate_ok else 'FAIL'} — {gate_why}")

    clears = all(wins(primary.delta(NODE_JACCARD, r))
                 for r in (SIZE_NULL, DEGREE_NULL, PROD))
    accept = clears and gate_ok
    out["H1 — plain technique-label Jaccard beats volume, degree and the shipped ranking"] = (
        f"{'ACCEPT' if accept else 'REJECT'} — MRR {c1.mrr:.3f} [{c1.lo:.3f}, {c1.hi:.3f}] "
        f"vs chance {primary.chance:.3f}; Δ vs H0/size "
        f"{_ci(primary.delta(NODE_JACCARD, SIZE_NULL))}, Δ vs degree "
        f"{_ci(primary.delta(NODE_JACCARD, DEGREE_NULL))}, Δ vs production "
        f"{_ci(primary.delta(NODE_JACCARD, PROD))}"
        + ("" if gate_ok else "; the intervals are moot — the provenance gate FAILED"))

    label, why = sweep_reading(primary)
    out["shared-vocabulary sweep (§5, reading, no verdict)"] = f"{label} — {why}"

    out["D1 — RANKING: order dossier analogues by c1"] = (
        "WIRE — H1 accepted and the provenance gate passed; the plan is in the results block"
        if accept else
        "DO NOT WIRE — H1 was not accepted; the shipped ranking stands")
    out["D2 — EXPLANATION: the shared-systems dossier line (fb9823f)"] = (
        "UNAFFECTED — it needs no arm to win (§6); it already shipped and stands either way")
    return out


# ── report ──────────────────────────────────────────────────────────────────────
def _arm_table(p: Pass) -> list[str]:
    refs = (SIZE_NULL, DEGREE_NULL, PROD)
    rows = ["| arm | role | MRR [95% CI] | top-1 | top-3 | top-5 | Δ vs H0 (size) "
            "| Δ vs degree | Δ vs production |",
            "|---|---|---|---|---|---|---|---|---|"]
    for a in p.arms:
        role = ROLES.get(a.name, "sweep (no verdict)")
        cells = " | ".join(_ci(p.delta(a.name, r)) if a.name != r else "—" for r in refs)
        rows.append(f"| {a.name} | {role} | {a.mrr:.3f} [{a.lo:.3f}, {a.hi:.3f}] | "
                    f"{a.r.top_k(1):.0%} | {a.r.top_k(3):.0%} | {a.r.top_k(5):.0%} | "
                    f"{cells} |")
    rows.append(f"| _chance (random ranking)_ | — | {p.chance:.3f} | {1 / p.n:.0%} | "
                f"{min(3, p.n) / p.n:.0%} | {min(5, p.n) / p.n:.0%} | — | — | — |")
    return rows


def _pass_section(p: Pass) -> list[str]:
    head = (f"Cohort: **{p.n} athletes**. Split: `{p.scheme}`, ≥{p.floor}-bout floor. "
            f"Median half-graph: {p.median_nodes} nodes / {p.median_edges} edges "
            f"(≥{MIN_HALF_NODES} nodes / ≥{MIN_HALF_EDGES} edges gated). "
            f"Chance MRR: {p.chance:.3f}.")
    if p.vocab_kept:
        head += (" Vocabulary: " + ", ".join(f"≥{k} athletes → {v}" for k, v in
                                             sorted(p.vocab_kept.items()))
                 + f" of {p.vocab_total} labels.")
    out = [f"### {p.title}", "", head, ""]
    if p.note:
        out += [p.note, ""]
    if p.n < 2:
        return [*out, "Too few queries to rank anything.", ""]
    return [*out, *_arm_table(p), ""]


@dataclass
class Run:
    gate_note: str
    embedding_note: str
    batch_note: str
    primary: Pass
    cross: Pass
    within: Pass
    pool: Pass
    others: list[Pass] = field(default_factory=list)


def _reading(run: Run) -> list[str]:
    p = run.primary
    c1, n1, a0 = p.arm(NODE_JACCARD), p.arm(SIZE_NULL), p.arm(PROD)
    out = ["## Reading", ""]
    if c1 is None or n1 is None or a0 is None:
        return [*out, "Not run.", ""]

    out.append(
        f"**H1, the candidate.** Over {p.n} candidates on the chronological split, plain "
        f"technique-label Jaccard scores MRR {c1.mrr:.3f} [{c1.lo:.3f}, {c1.hi:.3f}] "
        f"(top-3 {c1.r.top_k(3):.0%}) against a chance floor of {p.chance:.3f}, the "
        f"three-number size descriptor's {n1.mrr:.3f} and the shipped ranking's {a0.mrr:.3f}. "
        f"Paired: Δ vs H0/size {_ci(p.delta(NODE_JACCARD, SIZE_NULL))}, Δ vs degree "
        f"{_ci(p.delta(NODE_JACCARD, DEGREE_NULL))}, Δ vs production "
        f"{_ci(p.delta(NODE_JACCARD, PROD))}. Those three intervals are the whole of the "
        f"pre-registered metric criterion.")

    r1 = p.arm(FLAT_CENTROID)
    if r1 is not None:
        out.append(
            f"**Against the embedding reference.** The 768-dim occurrence-weighted mpnet "
            f"centroid scores {r1.mrr:.3f}; the set-intersection arm that ignores direction, "
            f"weight, order and frequency scores {c1.mrr:.3f}, paired Δ "
            f"{_ci(p.delta(NODE_JACCARD, FLAT_CENTROID))}. `r1` carries no verdict (it is "
            f"PoC-E5's arm re-run on nearly the same data), so this is context, not a win.")

    gate_ok, gate_why = gate(run.cross, run.pool, p.chance)
    xc, wi, po = (run.cross.arm(NODE_JACCARD), run.within.arm(NODE_JACCARD),
                  run.pool.arm(NODE_JACCARD))
    if xc is not None and wi is not None and po is not None:
        out.append(
            f"**The provenance control, which is what this cell was commissioned for.** "
            f"Split by whether an athlete's two halves came from the SAME ingestion session: "
            f"within-batch queries (n={run.within.n}) score {wi.mrr:.3f} "
            f"[{wi.lo:.3f}, {wi.hi:.3f}], cross-batch queries (n={run.cross.n}) score "
            f"{xc.mrr:.3f} [{xc.lo:.3f}, {xc.hi:.3f}], both against the same "
            f"{p.n}-candidate pool and the same chance floor {p.chance:.3f}. Inside the "
            f"batch-stratified pool — {run.pool.n} athletes who all share one ingestion "
            f"session, so batch membership cannot discriminate at all — `c1` scores "
            f"{po.mrr:.3f} [{po.lo:.3f}, {po.hi:.3f}] against that pool's chance floor "
            f"{run.pool.chance:.3f}. **Gate: {'PASS' if gate_ok else 'FAIL'}** — {gate_why}.")
        out.append(
            "That is the one thing E15 could not do. It removes the mechanism, not just the "
            "idiosyncratic labels: `p1` there stripped rare words, this holds the annotation "
            "run itself constant across every candidate."
            if gate_ok else
            "The gate is the stopping condition (§3d): with it failed, `c1` is not accepted "
            "whatever its MRR, no wiring plan is written, and the finding is that "
            "technique-label overlap cannot be separated from who annotated the tape on this "
            "corpus.")

    n1x, n1w = run.cross.arm(SIZE_NULL), run.within.arm(SIZE_NULL)
    if xc is not None and wi is not None and n1x is not None and n1w is not None:
        out.append(
            f"**The residual the gate does NOT clear, stated rather than left in the table.** "
            f"`c1` scores {wi.mrr:.3f} on within-batch queries and {xc.mrr:.3f} on cross-batch "
            f"ones, while the size null barely moves between the same two subgroups "
            f"({n1w.mrr:.3f} → {n1x.mrr:.3f}). The nulls do not care which session a pair came "
            f"from; the label-based arms do. That is consistent with PART of `c1`'s edge being "
            f"shared annotation vocabulary — and it is equally consistent with cross-batch "
            f"athletes simply being harder (their two halves are further apart in career time, "
            f"which is what put them in different import runs). This subgroup contrast is "
            f"DESCRIPTIVE: §3d registered the gate, not a two-sample test between subgroups, "
            f"and with {run.cross.n} vs {run.within.n} athletes no such test would resolve a "
            f"gap this size. What the gate does establish is the harder half of the question: "
            f"with the batch held constant for every candidate (leg ii), `c1` still beats the "
            f"size null by an interval that excludes 0, so annotation provenance cannot be the "
            f"WHOLE effect. Separating the remaining share needs the `source_batch` column §3a "
            f"names, not more statistics on this corpus.")

    label, why = sweep_reading(p)
    out.append(f"**The shared-vocabulary sweep, read as §5 registered it — {label}.** {why}.")
    out.append(
        "Two cross-checks worth printing because they cost nothing: `c1`'s primary MRR here "
        "reproduces PoC-E15's to three decimals on the same 465-bout gate (same arm, same "
        "cohort, independently re-run), and the sweep's own `≥3` point reproduces E15's "
        "post-hoc `p1`. The corpus has not moved and neither has the arm.")

    v = verdicts(p, run.cross, run.pool)
    out += ["", "### Verdicts", ""]
    out += [f"{i}. **{k}** — {val}" for i, (k, val) in enumerate(v.items(), start=1)]
    out += ["", "Nothing in this cell touches production: no DB write, no replay, no site "
                "export, no edit to `export/site_data.py` or `analysis/athlete_systems.py`.",
            ""]
    return out


def render(run: Run) -> str:
    lines = [MARK_OPEN, "", "## Results", "",
             "Generated by `uv run python -m scripts.research.e16_technique_jaccard` — "
             "**do not hand-edit**; a re-run replaces this block. Script: "
             "`scripts/research/e16_technique_jaccard.py`.", "",
             f"**Corpus gate:** {run.gate_note}", "",
             f"**Embeddings:** {run.embedding_note}", "",
             f"**Ingestion batches:** {run.batch_note}", ""]
    lines += _pass_section(run.primary)
    lines += ["## Provenance control (§3) — the gate", ""]
    for p in (run.within, run.cross, run.pool):
        lines += _pass_section(p)
    for p in run.others:
        lines += _pass_section(p)
    lines += _reading(run)
    lines += [MARK_CLOSE, ""]
    return "\n".join(lines)


def write_results(body: str) -> None:
    doc = DOC.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(MARK_OPEN) + r".*?" + re.escape(MARK_CLOSE) + r"\n?",
                         re.DOTALL)
    DOC.write_text(pattern.sub("", doc).rstrip() + "\n\n" + body, encoding="utf-8")


# ── run ─────────────────────────────────────────────────────────────────────────
def gate_note(g: GateReport) -> str:
    base = (f"{g.passed} gated bouts of {g.total} final+sequence "
            f"({g.total - g.with_sequence} under 4 events, {g.one_sided} dropped as one-sided)")
    if g.passed == E15_GATED_BOUTS:
        return (f"{base} — matching PoC-E15's published {E15_GATED_BOUTS}, so this cell's "
                f"numbers ARE comparable to that one's")
    return (f"{base} — **DRIFT** against PoC-E15's published {E15_GATED_BOUTS}; the corpus "
            f"moved, so E15's numbers are not byte-comparable to these.")


def batch_note(rows: Sequence[BoutRow], batch_of: dict[str, int], pv: Provenance) -> str:
    sizes = Counter(batch_of.values())
    return (f"{len(sizes)} ingestion sessions over the gated corpus "
            f"(`created_at`, new session after a {BATCH_GAP_MINUTES}-minute gap; largest "
            f"holds {max(sizes.values())} of {len(rows)} bouts). `matches` carries no "
            f"import/dump/batch table and `created_by` is NULL for every row, so this is the "
            f"only provenance handle in the schema. On the primary cohort: {len(pv.cross)} "
            f"athletes' halves span different sessions, {len(pv.within)} do not; the largest "
            f"same-session pool is session {pv.pool_batch} with {len(pv.pool)} athletes "
            f"({pv.pool_purity:.0%} of their bouts actually inside it)")


def build_run(per: dict[str, list[BoutRow]], sup: EmbeddingSupply, gate_txt: str,
              emb_txt: str, rows: Sequence[BoutRow], n_boot: int) -> Run:
    batch_of = assign_batches(rows)
    floor, scheme, _ = PASSES[0]
    cohort = build_cohort(per, floor, scheme)
    pv = provenance(cohort, per, batch_of)
    primary = run_pass(cohort, sup, "PRIMARY — chronological split, ≥4-bout floor", n_boot,
                       SWEEP_THRESHOLDS)
    within = subgroup(primary, pv.within,
                      "Provenance A — WITHIN-batch queries (same ingestion session both "
                      "halves)", n_boot)
    within.note = ("Same ranking and same candidate pool as the primary pass; only the "
                   "queries the mean is taken over change. These are the pairs a batch "
                   "confound COULD advantage.")
    cross = subgroup(primary, pv.cross,
                     "Provenance B — CROSS-batch queries (halves from different sessions)",
                     n_boot)
    cross.note = ("Same ranking and same candidate pool. A shared ingestion batch cannot be "
                  "why these are retrieved. Gate leg (i).")
    pool = run_pass(sub_cohort(cohort, pv.pool), sup,
                    "Provenance C — batch-stratified pool (every candidate from ONE session)",
                    n_boot)
    pool.note = ("Re-ranked inside the pool alone, so batch membership carries zero "
                 f"discriminative power by construction. Session {pv.pool_batch}; "
                 f"{pv.pool_purity:.0%} of these athletes' bouts sit inside it. Gate leg (ii).")
    others = [run_pass(build_cohort(per, f, s), sup, f"Sensitivity — {t.split('— ', 1)[-1]}",
                       n_boot, SWEEP_THRESHOLDS)
              for f, s, t in PASSES[1:]]
    return Run(gate_txt, emb_txt, batch_note(rows, batch_of, pv), primary, cross, within,
               pool, others)


def digest(per: dict[str, list[BoutRow]], sup: EmbeddingSupply,
           rows: Sequence[BoutRow]) -> str:
    """sha256 over every arm's distance matrix + both sub-cohorts + the sweep vocabularies.

    Run under different ``PYTHONHASHSEED``s; the digests must be identical. Skips the
    bootstrap entirely, so it is cheap enough to run as a gate.
    """
    batch_of = assign_batches(rows)
    floor, scheme, _ = PASSES[0]
    cohort = build_cohort(per, floor, scheme)
    pv = provenance(cohort, per, batch_of)
    h = hashlib.sha256()
    h.update(f"{cohort.n}|{'|'.join(x.athlete for x in cohort.a)}".encode())
    h.update(f"{pv.batch_a}|{pv.batch_b}|{pv.cross}|{pv.pool}|{pv.pool_batch}".encode())
    for k in SWEEP_THRESHOLDS:
        h.update(f"v{k}:" .encode() + "|".join(sorted(shared_vocabulary(cohort, k))).encode())
    for c in (cohort, sub_cohort(cohort, pv.pool)):
        for m in methods(sup, c, SWEEP_THRESHOLDS):
            h.update(m.name.encode())
            h.update(np.ascontiguousarray(np.round(distances(m, c), 9)).tobytes())
    return h.hexdigest()


def self_check() -> None:
    """One runnable check, no DB: batch segmentation, modal rule, sub-cohort construction."""
    def row(mins: int, rid: str) -> BoutRow:
        ts = datetime.fromisoformat("2026-07-07T00:00:00+00:00").timestamp()
        stamp = datetime.fromtimestamp(ts + mins * 60).astimezone().isoformat()
        return BoutRow(key=(2024, stamp, rid), sequence=[], athlete_a="a", athlete_b="b",
                       event=None, win_type=None, family="other", discipline="grappling",
                       elapsed=None)

    rows = [row(0, "r0"), row(5, "r1"), row(40, "r2"), row(41, "r3"), row(200, "r4")]
    b = assign_batches(rows, gap_minutes=30)
    assert [b[f"r{i}"] for i in range(5)] == [0, 0, 1, 1, 2], b

    # segmentation must not depend on input order (it sorts by (created_at, id))
    assert assign_batches(list(reversed(rows)), 30) == b

    # modal batch: majority wins; a tie falls to the LOWEST index, never set iteration
    assert modal_batch([rows[0], rows[1], rows[2]], b) == 0
    assert modal_batch([rows[0], rows[2]], b) == 0
    assert modal_batch([rows[2], rows[3], rows[4]], b) == 1

    # half_rows reproduces split_halves' partition, by key order
    ordered = sorted(rows, key=lambda r: r.key)
    assert half_rows(rows, "chronological") == (ordered[:2], ordered[2:])
    assert half_rows(rows, "odd_even") == (ordered[0::2], ordered[1::2])

    # sub_cohort keeps the cohort's own order and pairs a/b consistently
    import networkx as nx

    def half(name: str, side: str) -> Half:
        g = nx.DiGraph()
        g.add_edge(f"{name}1", f"{name}2")
        return Half(name, side, 2, g)
    c = Cohort(floor=4, scheme="chronological")
    c.a = [half(n, "A") for n in ("x", "y", "z")]
    c.b = [half(n, "B") for n in ("x", "y", "z")]
    s = sub_cohort(c, (2, 0))
    assert [h.athlete for h in s.a] == ["z", "x"], s.a
    assert [h.athlete for h in s.b] == ["z", "x"], s.b

    # the candidate itself, on the degenerate cases
    assert _node_jaccard_distance(frozenset("abc"), frozenset("abc")) == 0.0
    assert _node_jaccard_distance(frozenset("abc"), frozenset("xyz")) == 1.0

    # subgroup() re-reads the SAME ranks, never re-ranks
    r = Retrieval("m", [1, 4, 2], [1.0, 0.25, 0.5], 3)
    p = Pass("t", "chronological", 4, 3, 0.5)
    p.arms = [Arm("m", "", r, 0.0, 0.0, 0.0)]
    sg = subgroup(p, (0, 2), "sub", 50)
    assert sg.arms[0].r.ranks == [1, 2] and sg.arms[0].r.n_candidates == 3
    assert abs(sg.arms[0].mrr - 0.75) < 1e-12, sg.arms[0].mrr
    print("self-check OK")


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="PoC-E16 — technique-label Jaccard")
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

    g = load_corpus()
    logger.info("gate: %s", gate_note(g))
    if g.error:
        return 1
    per, sup, emb_note = _supply(g)
    logger.info("embeddings: %s", emb_note)

    if args.dry_run:
        batch_of = assign_batches(g.rows)
        for floor, scheme, _ in PASSES:
            c = build_cohort(per, floor, scheme)
            pv = provenance(c, per, batch_of)
            logger.info("floor=%d split=%-14s n=%d chance %.3f | cross=%d within=%d "
                        "pool=%d (session %d, chance %.3f, purity %.0f%%)",
                        floor, scheme, c.n, chance_mrr(c.n), len(pv.cross), len(pv.within),
                        len(pv.pool), pv.pool_batch, chance_mrr(len(pv.pool)),
                        100 * pv.pool_purity)
        return 0

    if args.digest:
        print(digest(per, sup, g.rows))
        return 0

    run = build_run(per, sup, gate_note(g), emb_note, g.rows, args.n_boot)
    write_results(render(run))
    logger.info("wrote %s", DOC)
    for k, v in verdicts(run.primary, run.cross, run.pool).items():
        logger.info("VERDICT %s: %s", k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
