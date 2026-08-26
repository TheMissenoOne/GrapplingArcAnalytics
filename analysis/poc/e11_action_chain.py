"""PoC-E11 — the high-confidence action chain: does data quality beat data quantity?

The pre-registration is `docs/research/poc/e11_prereg.md`, written and committed BEFORE this
module produced a held-out number, and re-emitted verbatim by ``render_markdown`` so the
criterion always travels with the numbers it judged.

The owner's question, in one line: *train on part of the corpus, predict the rest — and does
training only on the trustworthy part beat training on everything?*  Asked in Lamas 2024's
twelve-action state space, which `analysis/lamas_chain.py` already implements and tests.

Five limbs, one runner:

  1. **C1** — is the order-1 action chain predictive at all against its own order-0 marginal?
  2. **C2/C3** — high-confidence training vs FULL training, unmatched and SIZE-MATCHED.
  3. **C4** — which context vocabulary: A-12, A-7, S-type, S-label.
  4. **C5** — the sample-size curve, recency-nested and random, and the stabilisation point.
  5. **C6** — the house graph techniques on the action graph vs on the shipped label graph.

Two decisions are load-bearing and argued in the pre-registration, not here:

  * **the target alphabet is the SEVEN annotation-invariant families**, not the twelve actions.
    `successful` is annotated per BATCH (measured: BTKA→BTK lands 4.2% of the time across the
    full training window and 88.0% inside the high-confidence subset), so a twelve-symbol
    target would make the quality arms a measurement of annotation policy. The twelve-symbol
    target runs as an explicitly-labelled secondary.
  * **order is fixed at 1** — PoC-E4 closed second order at label level and PoC-E9 measured the
    maximum estimable order as 1 for both coarse spaces. Re-running it would re-litigate a
    closed cell. The order-0 marginal stays as the mandatory floor.

Everything else is inherited: `Backoff`/`fit_backoff`/`score_order`/`paired_delta`/`wins`/
`_boot_ci` from PoC-E9 (whose target alphabet became a parameter for exactly this cell),
`temporal_split` from PoC-E8, `lamas_state`/`STATES`/`FAMILY`/`reward_risk` from
`analysis/lamas_chain.py`, every interval from `stats_rigor`.

LGPD: athlete corpus only — `matches` is public competition footage by construction and holds
no `owner_kind='user'` row. Nothing here touches a production export.

Usage::

    uv run python -m analysis.poc.e11_action_chain
    uv run python -m analysis.poc.e11_action_chain --pdf reports/e11/e11.pdf
    uv run python -m analysis.poc.e11_action_chain --skip-corpus     # self-check, no DB
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

import networkx as nx

from analysis.attribution import EVENT_TYPES, bout_flags
from analysis.lamas_chain import STATE_DEFS, STATES, chain_of, lamas_state, reward_risk
from analysis.poc.e8_interaction_graph import _boot_ci, temporal_split
from analysis.poc.e9_markov import OrderRow, paired_delta, score_order, wins
from analysis.stats_rigor import coverage, spearman
from analysis.technique_match import clean_label

REPO = Path(__file__).resolve().parents[2]

SEED = 20260820          # stats_rigor's seed, so every interval in the repo agrees
ALPHA = 0.5              # PoC-E4's, then PoC-E9's headline smoothing
ORDER = 1                # fixed; see the module docstring
MIN_INDEX = 1            # scored positions start here, identically in every arm
EVAL_FRACTION = 0.25
N_BOOT = 2000
N_SUBSAMPLE = 20         # R, the size-matched draws (C3) and the random size sweep (C5)
N_STABILITY = 200        # bootstrap-over-bouts resamples for the community Jaccard (C6)

# PoC-E8/E9 published this many gated bouts. A mismatch is reported, never silently inherited.
E9_GATED_BOUTS = 429

# ── the two target alphabets ────────────────────────────────────────────────────
# PRIMARY: annotation-invariant. `X*` collapses the attempt/success pair, so the symbol is a
# function of the event's type and label only and means the same thing in every ingest batch.
# Written out rather than derived from `lamas_chain.FAMILY`'s keys: the derivation would have
# to invent a three-letter abbreviation per key and that is how `sweep` became `SWE*` in the
# first draft. `test_family_alphabet_covers_every_action` pins that this stays total over
# `STATES` and onto `ALPHABET_FAMILY`.
FAMILY_OF: dict[str, str] = {
    "CDP": "CDP", "PGD": "PGD",
    "SWPA": "SWP*", "SWP": "SWP*",
    "TKDA": "TKD*", "TKD": "TKD*",
    "GPSA": "GPS*", "GPS": "GPS*",
    "BTKA": "BTK*", "BTK": "BTK*",
    "SUBA": "SUB*", "SUB": "SUB*",
}
ALPHABET_FAMILY: tuple[str, ...] = ("CDP", "PGD", "SWP*", "TKD*", "GPS*", "BTK*", "SUB*")
ALPHABET_ACTION: tuple[str, ...] = STATES

FAMILY_DEFS: dict[str, str] = {
    "CDP": "Disputa de pegada em pé.", "PGD": "Puxada para a guarda.",
    "SWP*": "Raspagem (tentativa ou concluída).", "TKD*": "Queda (tentativa ou concluída).",
    "GPS*": "Passagem de guarda (tentativa ou concluída).",
    "BTK*": "Pegada de costas (tentativa ou concluída).",
    "SUB*": "Finalização (tentativa ou concluída).",
}

# Power gates (C2/C3 secondary limb, and the HC-restricted-eval sensitivity).
HC_MIN_TRAIN_BOUTS = 20
HC_MIN_TRAIN_STEPS = 150
HC_MIN_EVAL_BOUTS = 10

# C5's stabilisation rule, pre-registered.
STABILISE_TOL = 0.010
SIZE_GRID: tuple[int | None, ...] = (10, 20, 27, 40, 60, 90, 130, 175, 232, 290, None)

# HC-B's annotation-completeness threshold, pre-registered.
HC_B_COVERAGE = 0.90
MIN_CHAIN_STEPS = 4


def family_of(action: str) -> str:
    """The annotation-invariant family symbol for one Lamas action code."""
    return FAMILY_OF[action]


# ── the chain ───────────────────────────────────────────────────────────────────
class Pos(NamedTuple):
    """One position in a bout's action chain, carrying every context vocabulary at once.

    ``action`` is the Lamas code, ``family`` its annotation-invariant collapse, ``typ`` and
    ``label`` the underlying event's own vocabulary. Keeping all four on one object is what
    makes the four context arms of C4 score the SAME positions — the pairing the criterion
    needs is structural here, not a promise.
    """

    action: str
    family: str
    typ: str
    label: str
    actor_id: Any


def action_chain(seq: Sequence[Mapping[str, Any]], win_type: str | None) -> list[Pos]:
    """A bout's Lamas actions in array order, truncated at the finishing SUB.

    Deliberately re-walks the sequence instead of calling ``lamas_chain.chain_of``, because
    this cell needs the underlying event's ``type`` and ``label`` at every position and
    ``Chain.steps`` carries only the code. The rules are `lamas_chain`'s, unchanged — type-first
    mapping via ``lamas_state``, unmapped events passed over, SUB absorbing ONLY when the bout's
    ``win_type`` is SUBMISSION — and ``test_action_chain_matches_lamas_chain_of`` pins the two
    walks position-for-position so they cannot drift.
    """
    finishes = str(win_type or "").strip().upper() == "SUBMISSION"
    out: list[Pos] = []
    for e in seq:
        code = lamas_state(e)
        if code is None:
            continue
        typ = str(e.get("type") or "").strip().lower()
        out.append(Pos(code, family_of(code), typ if typ in EVENT_TYPES else "other",
                       clean_label(str(e.get("label", "")), typ), e.get("actor_id")))
        if finishes and code == "SUB":
            break
    return out


# ── context vocabularies ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Context:
    """A named way to read one chain position AS CONTEXT.

    Names are prefixed for the same reason PoC-E9 prefixes its state names: a bare ``pass``
    canonicalises to ``Guard Pass`` inside ``clean_label``, and a context vocabulary that
    silently collides with a label vocabulary would make two arms secretly the same arm.
    """

    name: str
    of: Callable[[Pos], str]


A12 = Context("A-12 (Lamas actions)", lambda p: f"a12/{p.action}")
A7 = Context("A-7 (families)", lambda p: f"a7/{p.family}")
S_TYPE = Context("S-type (event types)", lambda p: f"typ/{p.typ}")
S_LABEL = Context("S-label (clean_label)", lambda p: f"lab/{p.label}")
CONTEXTS: tuple[Context, ...] = (A12, A7, S_TYPE, S_LABEL)

# PoC-E9's Step is (state, target). Both are plain strings, which is the whole reason its
# estimator is reusable here without a second copy.
Step = tuple[str, str]


def steps_of(chain: Sequence[Pos], ctx: Context, alphabet: tuple[str, ...]) -> list[Step]:
    """One chain → PoC-E9's (state, target) pairs, in the chosen context vocabulary."""
    tgt: Callable[[Pos], str] = (
        (lambda p: p.family) if alphabet is ALPHABET_FAMILY else (lambda p: p.action))
    return [(ctx.of(p), tgt(p)) for p in chain]


# ── corpus ──────────────────────────────────────────────────────────────────────
@dataclass
class BoutRow:
    key: tuple[Any, ...]
    bout_id: str
    sequence: list[dict[str, Any]]
    athlete_a: str
    athlete_b: str
    event: str
    year: int
    win_type: str | None
    chain: list[Pos]
    annotated: float          # share of the bout's events carrying a `successful` key

    @property
    def n_actors(self) -> int:
        return len({p.actor_id for p in self.chain if p.actor_id is not None})

    @property
    def hc_a(self) -> bool:
        """Attribution quality — `lamas_chain._actor_reliability`'s double refusal, inverted.

        `perspective_reliable` is already applied at load time; what remains is the
        `single_actor` hole it leaves on short bouts (a bout filed entirely under one name
        scores reward = 1.00 by construction) plus a usable chain length.
        """
        return self.n_actors >= 2 and len(self.chain) >= MIN_CHAIN_STEPS

    @property
    def hc_b(self) -> bool:
        """Annotation completeness — the machine-checkable proxy for a frame-read or
        concordance-audited pipeline, since those annotate every event and the transcript
        pipelines do not. Confounded with event family and era; the report says so."""
        return self.hc_a and self.annotated >= HC_B_COVERAGE

    def lamas_bout(self) -> dict[str, Any]:
        """The shape `lamas_chain.chain_of`/`reward_risk` consume."""
        return {"id": self.bout_id, "win_type": self.win_type, "seq": self.sequence,
                "a_id": self.athlete_a, "b_id": self.athlete_b}


@dataclass
class GateReport:
    total: int = 0
    with_sequence: int = 0
    passed: int = 0
    one_sided: int = 0
    rows: list[BoutRow] = field(default_factory=list)
    error: str | None = None


def load_corpus(min_events: int = 4) -> GateReport:
    """Gated athlete-corpus bouts. ONE read-only ``select`` over ``matches``.

    Same filter and same gate as ``e9.load_corpus``; restated because this cell also needs the
    per-bout annotation share and the bout id as a cluster key. ``verify_gate`` compares the
    resulting count against PoC-E8/E9's published 429 rather than assuming it still holds.
    """
    rep = GateReport()
    try:
        from sqlalchemy import text

        from db.base import get_engine

        with get_engine().connect() as conn:
            rowset = conn.execute(text(
                "SELECT id, athlete_a_id, athlete_b_id, year, created_at, event, win_type, "
                "sequence FROM matches WHERE status = 'final' AND sequence IS NOT NULL"
            )).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the report says why, the self-check still runs
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
            bout_id=str(row["id"]), sequence=seq,
            athlete_a=str(row["athlete_a_id"]), athlete_b=str(row["athlete_b_id"]),
            event=str(row["event"] or "—"), year=int(row["year"] or 0),
            win_type=row["win_type"],
            chain=action_chain(seq, row["win_type"]),
            annotated=sum(1 for e in seq if e.get("successful") is not None) / len(seq),
        ))
    rep.rows.sort(key=lambda r: r.key)
    return rep


def verify_gate(gate: GateReport) -> str:
    """Does today's gate still return PoC-E8/E9's published 429? Reported, never assumed."""
    if gate.error:
        return f"NOT RUN — {gate.error}"
    if gate.passed == E9_GATED_BOUTS:
        return f"OK — {gate.passed} gated bouts, matching PoC-E8/E9's published {E9_GATED_BOUTS}"
    return (f"MOVED — {gate.passed} gated bouts against PoC-E8/E9's published "
            f"{E9_GATED_BOUTS} ({gate.passed - E9_GATED_BOUTS:+d}). This cell describes "
            f"today's corpus; the older cells' numbers are not directly comparable.")


def split_rows(rows: Sequence[BoutRow], frac: float = EVAL_FRACTION
               ) -> tuple[list[BoutRow], list[BoutRow]]:
    """Chronological split, boundary key to TRAIN — PoC-E8's ``temporal_split`` on BoutRows."""
    from analysis.poc.e8_interaction_graph import Bout

    bouts = [Bout(key=r.key, sequence=r.sequence) for r in rows]
    _, held = temporal_split(bouts, frac)
    held_keys = {b.key for b in held}
    return ([r for r in rows if r.key not in held_keys],
            [r for r in rows if r.key in held_keys])


# ── the estimator, wrapped ──────────────────────────────────────────────────────
def score(train: Sequence[BoutRow], held: Sequence[BoutRow], ctx: Context, label: str,
          alphabet: tuple[str, ...] = ALPHABET_FAMILY, order: int = ORDER,
          n_boot: int = N_BOOT) -> OrderRow:
    """Held-out per-step log-likelihood of the next action's FAMILY, at one training set.

    PoC-E9's ``score_order`` verbatim, with its target alphabet and its scored-step floor
    supplied rather than defaulted. Every call in this module passes the SAME ``held`` and the
    same ``ctx`` for a given comparison, which is what keeps ``paired_delta`` legal.
    """
    tr = [steps_of(r.chain, ctx, alphabet) for r in train if len(r.chain) > 1]
    ev = [(r.bout_id, steps_of(r.chain, ctx, alphabet)) for r in held if len(r.chain) > 1]
    return score_order(tr, ev, order, ALPHA, ctx.name, label, n_boot,
                       alphabet=alphabet, min_index=MIN_INDEX)


def averaged(rows: Sequence[OrderRow], space: str, chain: str) -> OrderRow:
    """Average R models' per-step log-probabilities into ONE paired row.

    C3 and C5's random sweep both need "the typical model at this size" as a single vector
    that ``paired_delta`` can difference against a real model. Averaging the LOG-probabilities
    (not the probabilities) keeps the statistic on the criterion's own scale — the reported
    quantity stays a mean per-step log-likelihood, and the average of R draws is what the
    pre-registration says is being compared, not a mixture model's likelihood.

    Two consequences, because the first draft of this cell got one of them backwards:

    * the resulting ``ll`` is **exactly** ``mean_r(ll_r)`` (the per-step and per-draw means
      commute), so there is NO ensembling advantage in the point estimate and a contrast
      against a single model is legitimate;
    * the resulting **interval** is not: it is bootstrapped over a vector whose variance is
      ~1/R of a single draw's, so it says where the typical model sits, not how precisely a
      model of that size predicts. Callers publish ``SizePoint.median_draw`` beside it.
    """
    if not rows:
        raise ValueError("averaged() needs at least one row")
    base = rows[0]
    for r in rows[1:]:
        if r.groups != base.groups:
            raise ValueError("unpaired subsample rows")
    logps = [statistics.fmean(r.logps[i] for r in rows) for i in range(len(base.logps))]
    obs, lo, hi = _boot_ci([True] * len(logps),
                           lambda s: sum(logps[i] for i in s) / len(s) if s else 0.0,
                           N_BOOT, base.groups)
    return OrderRow(space, chain, base.order, base.alpha, base.n_states, len(logps), obs, lo, hi,
                    base.support, base.contexts, base.contexts_min5, logps, list(base.groups))


def subsample(rows: Sequence[BoutRow], n: int, seed: int) -> list[BoutRow]:
    """``n`` train bouts drawn WITHOUT replacement, deterministically, order preserved."""
    if n >= len(rows):
        return list(rows)
    rng = random.Random(seed)
    keep = set(rng.sample(range(len(rows)), n))
    return [r for i, r in enumerate(rows) if i in keep]


def outcome(delta: tuple[float, float, float], a: str, b: str) -> str:
    """The three pre-declared C2/C3 outcomes, with the half-width when it is a null."""
    if wins(delta):
        return f"**{a} WINS**"
    if wins((-delta[0], -delta[2], -delta[1])):
        return f"**{b} WINS**"
    half = (delta[2] - delta[1]) / 2 if delta[1] == delta[1] else float("nan")
    return f"INDISTINGUISHABLE (half-width {half:.4f} nats/step)"


# ── C2/C3 — the quality limbs ───────────────────────────────────────────────────
@dataclass
class QualityLimb:
    name: str
    n_train_bouts: int
    n_train_steps: int
    n_eval_bouts: int
    powered: bool
    reason: str | None
    tags: list[tuple[str, int]] = field(default_factory=list)
    years: list[tuple[int, int]] = field(default_factory=list)
    ll: float = float("nan")
    full_ll: float = float("nan")
    matched_ll: float = float("nan")
    d_full: tuple[float, float, float] = (float("nan"),) * 3
    d_matched: tuple[float, float, float] = (float("nan"),) * 3
    d_full_a12: tuple[float, float, float] = (float("nan"),) * 3


def run_quality(train: Sequence[BoutRow], held: Sequence[BoutRow], full_row: OrderRow,
                pred: Callable[[BoutRow], bool], name: str,
                n_boot: int = N_BOOT) -> QualityLimb:
    """One high-confidence set against FULL — unmatched (C2) and size-matched (C3).

    The size-matched arm is the one that carries the science. PoC-E9's ADCC limb already
    measured a specialised kernel losing purely on variance (56 training bouts against ~370),
    so an unmatched loss here would be a rediscovery, not a finding: only the size-matched
    contrast can separate "worse data" from "less data".
    """
    sub = [r for r in train if pred(r)]
    ev_hc = [r for r in held if pred(r)]
    n_steps = sum(len(r.chain) for r in sub)
    powered = len(sub) >= HC_MIN_TRAIN_BOUTS and n_steps >= HC_MIN_TRAIN_STEPS
    cov = coverage([len(r.chain) for r in sub if r.chain])
    reason = None
    if not powered:
        reason = (f"{len(sub)} train bouts / {n_steps} steps against the pre-registered floor "
                  f"of {HC_MIN_TRAIN_BOUTS} / {HC_MIN_TRAIN_STEPS}")
    elif not cov.estimable:
        powered, reason = False, f"coverage refuses: {cov.reason}"
    limb = QualityLimb(name, len(sub), n_steps, len(ev_hc), powered, reason,
                       Counter(r.event for r in sub).most_common(8),
                       sorted(Counter(r.year for r in sub).items()))
    if not powered:
        return limb
    row = score(sub, held, A7, name, n_boot=n_boot)
    limb.ll, limb.full_ll = row.ll, full_row.ll
    limb.d_full = paired_delta(row, full_row, n_boot)
    draws = [score(subsample(list(train), len(sub), SEED + i), held, A7,
                   f"{name}·matched·{i}", n_boot=0) for i in range(N_SUBSAMPLE)]
    matched = averaged(draws, A7.name, f"FULL subsampled to {len(sub)} bouts")
    limb.matched_ll = matched.ll
    limb.d_matched = paired_delta(row, matched, n_boot)
    # Sensitivity, explicitly labelled: the SAME contrast on the twelve-symbol target, where
    # the batch annotation policy is in play. Reported so the confound is visible, never used
    # as a verdict.
    a12_row = score(sub, held, A12, name, ALPHABET_ACTION, n_boot=0)
    a12_full = score(train, held, A12, "FULL", ALPHABET_ACTION, n_boot=0)
    limb.d_full_a12 = paired_delta(a12_row, a12_full, n_boot)
    return limb


# ── C5 — the sample-size curve ──────────────────────────────────────────────────
@dataclass
class SizePoint:
    n: int
    scheme: str
    ll: float
    lo: float
    hi: float
    delta_vs_full: tuple[float, float, float]
    # The R INDIVIDUAL draws' scores, random scheme only.
    #
    # `ll` on a random row is mean_t(mean_r(log p_rt)) = mean_r(ll_r) — EXACTLY the arithmetic
    # mean of these draws, because the two means commute. So the point estimate carries no
    # ensembling advantage over a single model and the recency contrast on `ll` is legitimate.
    # (The first draft of this cell claimed a Jensen advantage here. There isn't one; the
    # measured gap between `ll` and `median_draw` is ±0.001 nats/step, i.e. mean-vs-median
    # skew and nothing else.)
    #
    # What the averaging DOES change is spread: the CI and the Δ-vs-FULL on a random row are
    # bootstrapped over the AVERAGED per-step vector, whose variance is ~1/R of a single
    # draw's. Those intervals are therefore tighter than any one model of that size would earn,
    # and must be read as "where the typical model sits", never as "how precisely a model of
    # this size predicts". `median_draw` is published so the draw-level spread is visible.
    draw_lls: list[float] = field(default_factory=list)

    @property
    def median_draw(self) -> float:
        return statistics.median(self.draw_lls) if self.draw_lls else self.ll

    @property
    def draw_spread(self) -> tuple[float, float]:
        return ((min(self.draw_lls), max(self.draw_lls)) if self.draw_lls
                else (self.ll, self.ll))


@dataclass
class SizeCurve:
    points: list[SizePoint]
    n_full: int
    stabilised_at: int | None
    rule: str


def run_size_curve(train: Sequence[BoutRow], held: Sequence[BoutRow], full_row: OrderRow,
                   n_boot: int = N_BOOT) -> SizeCurve:
    """Two sweeps, one grid: recency-nested (deterministic) and random (R seeded draws).

    Recency-nested answers "how much recent history do I need"; random answers "how much data,
    independent of when". Reporting only one would hide which lever is which — if random at n
    matches recency at n, drift is not the constraint and volume is.
    """
    pts: list[SizePoint] = []
    ordered = list(train)
    for n in SIZE_GRID:
        k = len(ordered) if n is None else min(n, len(ordered))
        recent = ordered[-k:]
        r_row = score(recent, held, A7, f"recency n={k}", n_boot=n_boot)
        pts.append(SizePoint(k, "recency", r_row.ll, r_row.lo, r_row.hi,
                             paired_delta(r_row, full_row, n_boot)))
        if k >= len(ordered):
            pts.append(SizePoint(k, "random", r_row.ll, r_row.lo, r_row.hi,
                                 paired_delta(r_row, full_row, n_boot)))
            continue
        draws = [score(subsample(ordered, k, SEED + i), held, A7, f"rand·{k}·{i}", n_boot=0)
                 for i in range(N_SUBSAMPLE)]
        avg = averaged(draws, A7.name, f"random n={k}")
        pts.append(SizePoint(k, "random", avg.ll, avg.lo, avg.hi,
                             paired_delta(avg, full_row, n_boot),
                             [d.ll for d in draws]))
    rec = [p for p in pts if p.scheme == "recency"]
    stab: int | None = None
    for i, p in enumerate(rec):
        tail = rec[i:]
        if all(not wins(q.delta_vs_full) and not wins((-q.delta_vs_full[0], -q.delta_vs_full[2],
                                                       -q.delta_vs_full[1]))
               and abs(q.delta_vs_full[0]) < STABILISE_TOL for q in tail):
            stab = p.n
            break
    return SizeCurve(pts, len(ordered), stab,
                     f"smallest n whose Δ vs FULL covers 0 and |Δ| < {STABILISE_TOL} nats/step, "
                     f"and the same for every larger n on the grid")


# ── C6 — the graph layer ────────────────────────────────────────────────────────
def action_graph(rows: Sequence[BoutRow], key: Callable[[Pos], str]) -> nx.DiGraph:
    """Directed weighted graph over one action vocabulary. CROSS-ACTOR, self-loops kept.

    Lamas' chain is the MATCH's flow, not one athlete's (`lamas_chain` argues this from the
    measured 307-of-700 one-sided filings), and folding `A → A` would delete the very cell the
    paper publishes at 0.30. Both choices differ from `transitions/build_graph`, which is why
    the label-level comparison below is a comparison and not a check.
    """
    g = nx.DiGraph()
    occ: Counter[str] = Counter()
    w: Counter[tuple[str, str]] = Counter()
    for r in rows:
        codes = [key(p) for p in r.chain]
        occ.update(codes)
        w.update(zip(codes, codes[1:], strict=False))
    for node, c in occ.items():
        g.add_node(node, occ=c)
    for (a, b), c in w.items():
        g.add_edge(a, b, weight=c, dist=1.0 / c)
    return g


def ranked_pagerank(g: nx.DiGraph) -> list[tuple[str, float]]:
    """PageRank, ties broken by ``(-score, node name)`` — the deterministic tie-break scar
    (`network_metrics.detect_communities` carries the same rule for the same reason: a
    hash-seeded iteration order reshuffles a published table between two identical runs)."""
    if g.number_of_edges() == 0:
        return []
    pr = nx.pagerank(g, weight="weight")
    return sorted(((n, round(v, 5)) for n, v in pr.items()), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class CommunityReport:
    density: float
    modularity: float
    communities: list[list[str]]
    mean_jaccard: list[float]
    p10_jaccard: list[float]
    interpretable: bool


def _communities(g: nx.DiGraph) -> list[list[str]]:
    if g.number_of_edges() == 0:
        return [[n] for n in sorted(g)]
    und = g.to_undirected()
    comms = nx.community.greedy_modularity_communities(und, weight="weight")
    out = [sorted(c, key=lambda n: (-g.nodes[n].get("occ", 0), n)) for c in comms]
    return sorted(out, key=lambda c: (-len(c), c[0]))


def community_report(rows: Sequence[BoutRow], key: Callable[[Pos], str],
                     n_resamples: int = N_STABILITY, seed: int = SEED) -> CommunityReport:
    """Communities plus the three honesty gates C6 pre-registers.

    Stability resamples BOUTS with replacement (`constellations/stability.bootstrap_jaccard`'s
    unit, applied here directly because that module's callback signature is built around
    `detect`'s Constellation objects and this graph has twelve nodes, not an athlete corpus).
    ``interpretable`` is False above density 0.9: on a near-complete graph every partition is
    an arbitrary cut of one blob and modularity says nothing, however respectable Q looks.
    """
    g = action_graph(rows, key)
    base = _communities(g)
    dens = nx.density(g) if g.number_of_nodes() > 1 else 0.0
    und = g.to_undirected()
    q = (nx.community.modularity(und, [set(c) for c in base], weight="weight")
         if g.number_of_edges() else 0.0)
    rng = random.Random(seed)
    samples: list[list[float]] = [[] for _ in base]
    for _ in range(n_resamples):
        draw = [rows[rng.randrange(len(rows))] for _ in rows] if rows else []
        got = _communities(action_graph(draw, key))
        for i, c in enumerate(base):
            s = set(c)
            samples[i].append(max((len(s & set(o)) / len(s | set(o)) for o in got), default=0.0))
    mean = [round(statistics.fmean(v), 4) if v else 0.0 for v in samples]
    p10 = [round(statistics.quantiles(v, n=10)[0], 4) if len(v) >= 2 else 0.0 for v in samples]
    return CommunityReport(round(dens, 4), round(q, 4), base, mean, p10, dens <= 0.9)


@dataclass
class GraphLimb:
    action_pr: list[tuple[str, float]]
    family_pr: list[tuple[str, float]]
    label_pr: list[tuple[str, float]]
    label_family_pr: list[tuple[str, float]]
    rho: Any
    reward_risk_rows: list[dict[str, Any]]
    communities: CommunityReport
    n_label_nodes: int
    n_label_edges: int
    occupancy: list[tuple[str, int]]


def run_graph_limb(rows: Sequence[BoutRow], n_resamples: int = N_STABILITY) -> GraphLimb:
    """The house techniques at action level, beside the shipped label-level graph.

    The comparison is made numeric rather than left to the eye: each label of the production
    ActionFlow graph is mapped through ``lamas_state`` to its family, its PageRank mass summed,
    and the two seven-symbol rankings correlated. Agreement corroborates; disagreement is the
    finding, because the two graphs are built under deliberately different rules (cross-actor
    with self-loops here, within-actor without them there).
    """
    from analysis.network_metrics import pagerank_ranking
    from analysis.transitions.build_graph import network_from_sequences

    g12 = action_graph(rows, lambda p: p.action)
    g7 = action_graph(rows, lambda p: p.family)
    lab = network_from_sequences([r.sequence for r in rows])
    lab_pr = nx.pagerank(lab, weight="weight") if lab.number_of_edges() else {}
    mass: Counter[str] = Counter()
    for node, v in lab_pr.items():
        code = lamas_state({"label": node, "type": lab.nodes[node].get("type", "")})
        if code is not None:
            mass[family_of(code)] += v
    lab_fam = sorted(((f, round(mass.get(f, 0.0), 5)) for f in ALPHABET_FAMILY),
                     key=lambda kv: (-kv[1], kv[0]))
    fam_pr = ranked_pagerank(g7)
    order = {f: i for i, (f, _) in enumerate(fam_pr)}
    rho = spearman([order.get(f, len(order)) for f, _ in lab_fam],
                   list(range(len(lab_fam)))) if fam_pr else None
    rr = reward_risk([chain_of(r.lamas_bout()) for r in rows])
    return GraphLimb(
        ranked_pagerank(g12), fam_pr, pagerank_ranking(lab, limit=15), lab_fam, rho,
        rr["rows"], community_report(rows, lambda p: p.action, n_resamples),
        lab.number_of_nodes(), lab.number_of_edges(),
        sorted(((n, int(d["occ"])) for n, d in g12.nodes(data=True)),
               key=lambda kv: (-kv[1], kv[0])))


# ── the run ─────────────────────────────────────────────────────────────────────
@dataclass
class Run:
    gate: GateReport
    n_train: int = 0
    n_eval: int = 0
    n_eval_steps: int = 0
    boundary: str = "—"
    m0: OrderRow | None = None
    full: OrderRow | None = None
    c1: tuple[float, float, float] = (float("nan"),) * 3
    contexts: list[OrderRow] = field(default_factory=list)
    cross: list[tuple[str, str, tuple[float, float, float]]] = field(default_factory=list)
    hc_a: QualityLimb | None = None
    hc_b: QualityLimb | None = None
    curve: SizeCurve | None = None
    graph: GraphLimb | None = None
    hc_eval_refusal: str = "—"
    annotation_table: list[tuple[str, float, float]] = field(default_factory=list)


def annotation_shares(train: Sequence[BoutRow]) -> list[tuple[str, float, float]]:
    """Per family, the share of its occurrences coded as SUCCESS in FULL vs in HC-B.

    This is the confound the target alphabet exists to defuse, measured on the run's own
    corpus rather than quoted from the pre-registration.
    """
    def share(rs: Sequence[BoutRow]) -> dict[str, float]:
        c: Counter[str] = Counter()
        ok: Counter[str] = Counter()
        for r in rs:
            for p in r.chain:
                c[p.family] += 1
                if p.action in {"SWP", "TKD", "GPS", "BTK", "SUB"}:
                    ok[p.family] += 1
        return {f: (ok[f] / c[f]) if c[f] else float("nan") for f in ALPHABET_FAMILY}

    a, b = share(train), share([r for r in train if r.hc_b])
    return [(f, a[f], b[f]) for f in ALPHABET_FAMILY if f not in {"CDP", "PGD"}]


def run_all(gate: GateReport, n_boot: int = N_BOOT,
            n_resamples: int = N_STABILITY) -> Run:
    run = Run(gate)
    if not gate.rows:
        return run
    train, held = split_rows(gate.rows)
    run.n_train, run.n_eval = len(train), len(held)
    run.n_eval_steps = sum(max(0, len(r.chain) - MIN_INDEX) for r in held)
    run.boundary = str(held[0].key[0]) if held else "—"
    run.annotation_table = annotation_shares(train)

    run.full = score(train, held, A7, "FULL", n_boot=n_boot)
    run.m0 = score(train, held, A7, "M0 (order-0 marginal)", order=0, n_boot=n_boot)
    run.c1 = paired_delta(run.full, run.m0, n_boot)

    run.contexts = [score(train, held, c, "FULL", n_boot=n_boot) for c in CONTEXTS]
    for i, a in enumerate(run.contexts):
        for b in run.contexts[i + 1:]:
            run.cross.append((a.space, b.space, paired_delta(a, b, n_boot)))

    run.hc_a = run_quality(train, held, run.full, lambda r: r.hc_a, "HC-A", n_boot)
    run.hc_b = run_quality(train, held, run.full, lambda r: r.hc_b, "HC-B", n_boot)
    n_hc_eval = sum(1 for r in held if r.hc_b)
    run.hc_eval_refusal = (
        f"REFUSED — {n_hc_eval} HC-B bout(s) in the eval window against the pre-registered "
        f"floor of {HC_MIN_EVAL_BOUTS}" if n_hc_eval < HC_MIN_EVAL_BOUTS
        else f"runnable ({n_hc_eval} eval bouts)")

    run.curve = run_size_curve(train, held, run.full, n_boot)
    run.graph = run_graph_limb(train, n_resamples)
    return run


# ── verdicts ────────────────────────────────────────────────────────────────────
def _ci(d: tuple[float, float, float]) -> str:
    return f"{d[0]:+.4f} [{d[1]:+.4f}, {d[2]:+.4f}]"


def verdicts(run: Run) -> dict[str, str]:
    """The pre-registered criteria, applied verbatim. No limb reads another limb's outcome —
    except C1, whose failure the pre-registration says demotes every other verdict."""
    out: dict[str, str] = {}
    if run.full is None or run.m0 is None:
        return {"all": "UNDECIDED — the corpus pass did not run."}
    ok = wins(run.c1)
    out["C1_predictive"] = (
        f"{'ACCEPT' if ok else 'REJECT'} — the order-1 action chain scores {run.full.ll:.4f} "
        f"against the order-0 train marginal's {run.m0.ll:.4f}, paired Δ {_ci(run.c1)}. "
        + ("The chain carries real information about the next action."
           if ok else "**The chain does not beat its own marginal; every verdict below is "
                      "demoted to descriptive.**"))
    for limb in (run.hc_a, run.hc_b):
        if limb is None:
            continue
        k = f"C2C3_{limb.name.replace('-', '_')}"
        if not limb.powered:
            out[k] = (f"UNDERPOWERED — {limb.reason}. No verdict, by pre-registration.")
            continue
        out[k] = (
            f"C2 (unmatched, {limb.n_train_bouts} vs {run.n_train} train bouts): "
            f"{outcome(limb.d_full, limb.name, 'FULL')}, Δ {_ci(limb.d_full)}. "
            f"C3 (size-matched to {limb.n_train_bouts} bouts, R={N_SUBSAMPLE}): "
            f"{outcome(limb.d_matched, limb.name, 'random same-size FULL')}, "
            f"Δ {_ci(limb.d_matched)}.")
    a12 = next((d for a, b, d in run.cross if a == A12.name and b == A7.name), None)
    if a12 is not None:
        out["C4_vocabulary"] = (
            f"{'ACCEPT' if wins(a12) else 'REJECT'} the attempt/success split: Δ (A-12 − A-7) "
            f"{_ci(a12)} on the fixed family target. Undominated set: "
            f"{{{', '.join(_undominated(run.cross, [c.space for c in run.contexts]))}}}.")
    if run.curve:
        out["C5_enough"] = (
            "Stabilisation point n\\* = "
            + (f"**{run.curve.stabilised_at} train bouts** of {run.curve.n_full}"
               if run.curve.stabilised_at is not None
               else "**not stabilised within this corpus**")
            + f" (rule: {run.curve.rule}).")
    if run.graph:
        c = run.graph.communities
        out["C6_graph"] = (
            f"Descriptive, no accept/reject. Twelve-node graph density {c.density:.3f}, "
            f"modularity Q={c.modularity:.4f}, {len(c.communities)} communities, mean "
            f"bootstrap Jaccard {max(c.mean_jaccard, default=0.0):.2f}/"
            f"{min(c.mean_jaccard, default=0.0):.2f} (best/worst). "
            + ("Community structure is reported but **NOT interpretable**: on a graph this "
               "close to complete every partition is an arbitrary cut of one blob, whatever Q "
               "says." if not c.interpretable else "Density is below the pre-registered 0.9 "
               "gate, so the partition is reported as interpretable."))
    return out


def _undominated(cross: Sequence[tuple[str, str, tuple[float, float, float]]],
                 names: Sequence[str]) -> list[str]:
    beaten: set[str] = set()
    for a, b, d in cross:
        if wins(d):
            beaten.add(b)
        elif wins((-d[0], -d[2], -d[1])):
            beaten.add(a)
    return [n for n in names if n not in beaten]


# ── figures + PDF ───────────────────────────────────────────────────────────────
def _matrix(rows: Sequence[BoutRow]) -> list[list[float]]:
    """Row-normalised family transition matrix, square on ``ALPHABET_FAMILY``."""
    idx = {f: i for i, f in enumerate(ALPHABET_FAMILY)}
    counts = [[0.0] * len(ALPHABET_FAMILY) for _ in ALPHABET_FAMILY]
    for r in rows:
        fams = [p.family for p in r.chain]
        for a, b in zip(fams, fams[1:], strict=False):
            counts[idx[a]][idx[b]] += 1
    return [[c / s if (s := sum(row)) else 0.0 for c in row] for row in counts]


def write_figures(run: Run, train: Sequence[BoutRow], out_dir: Path) -> list[tuple[str, Path]]:
    """Matplotlib figures for the PDF. Deterministic; no network, no seaborn styling."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[tuple[str, Path]] = []

    sets = [("FULL", list(train)), ("HC-A", [r for r in train if r.hc_a]),
            ("HC-B", [r for r in train if r.hc_b])]
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 4.2))
    for ax, (name, rs) in zip(axes, sets, strict=True):
        m = _matrix(rs)
        ax.imshow(m, cmap="viridis", vmin=0, vmax=0.6)
        ax.set_xticks(range(len(ALPHABET_FAMILY)), ALPHABET_FAMILY, rotation=90, fontsize=7)
        ax.set_yticks(range(len(ALPHABET_FAMILY)), ALPHABET_FAMILY, fontsize=7)
        ax.set_title(f"{name} — {len(rs)} bouts", fontsize=9)
        for i in range(len(ALPHABET_FAMILY)):
            for j in range(len(ALPHABET_FAMILY)):
                if m[i][j] >= 0.005:
                    ax.text(j, i, f"{m[i][j]:.2f}", ha="center", va="center", fontsize=5.5,
                            color="white" if m[i][j] < 0.35 else "black")
    fig.suptitle("Family transition matrices, row-normalised (train windows)", fontsize=10)
    fig.tight_layout()
    p = out_dir / "fig1_matrices.png"
    fig.savefig(p, dpi=170)
    plt.close(fig)
    made.append(("Family transition matrices per training arm", p))

    if run.curve:
        fig, ax = plt.subplots(figsize=(7.4, 4.0))
        for scheme, style in (("recency", "-o"), ("random", "--s")):
            pts = [p for p in run.curve.points if p.scheme == scheme]
            ax.errorbar([p.n for p in pts], [p.ll for p in pts],
                        yerr=[[p.ll - p.lo for p in pts], [p.hi - p.ll for p in pts]],
                        fmt=style, capsize=2, ms=4, lw=1.2, label=scheme)
        if run.full:
            ax.axhline(run.full.ll, color="black", lw=0.8, ls=":",
                       label=f"FULL ({run.curve.n_full} bouts)")
        if run.m0:
            ax.axhline(run.m0.ll, color="crimson", lw=0.8, ls=":", label="order-0 marginal")
        if run.curve.stabilised_at:
            ax.axvline(run.curve.stabilised_at, color="tab:green", lw=0.9,
                       label=f"n* = {run.curve.stabilised_at}")
        ax.set_xlabel("training bouts")
        ax.set_ylabel("held-out mean logP(family) per step")
        ax.set_title("C5 — how much training data is enough to measure", fontsize=10)
        ax.legend(fontsize=7)
        ax.grid(alpha=0.25, lw=0.4)
        fig.tight_layout()
        p = out_dir / "fig2_size_curve.png"
        fig.savefig(p, dpi=170)
        plt.close(fig)
        made.append(("Sample-size curve", p))

    if run.graph:
        fig, ax = plt.subplots(figsize=(7.4, 3.6))
        names = [n for n, _ in run.graph.family_pr]
        ours = [v for _, v in run.graph.family_pr]
        by = dict(run.graph.label_family_pr)
        theirs = [by.get(n, 0.0) for n in names]
        x = range(len(names))
        ax.bar([i - 0.2 for i in x], ours, width=0.4, label="action graph (12→7 nodes)")
        ax.bar([i + 0.2 for i in x], theirs, width=0.4,
               label="label ActionFlow graph, mass aggregated to families")
        ax.set_xticks(list(x), names, fontsize=8)
        ax.set_ylabel("PageRank mass")
        ax.set_title("C6 — action level vs label level", fontsize=10)
        ax.legend(fontsize=7)
        ax.grid(alpha=0.25, lw=0.4, axis="y")
        fig.tight_layout()
        p = out_dir / "fig3_pagerank.png"
        fig.savefig(p, dpi=170)
        plt.close(fig)
        made.append(("PageRank, action level vs label level", p))
    return made


def write_pdf(run: Run, figures: Sequence[tuple[str, Path]], out: Path) -> Path:
    """The owner-facing deliverable. reportlab, the repo's precedent (`scripts/frame_pdf.py`).

    Real text objects, not a picture of text — a PDF whose pages are images is unsearchable and
    ten times the size. Figures are embedded as PNGs; everything else is drawn as text so the
    verdicts can be copied out of the file.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfgen.canvas import Canvas

    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = A4
    c = Canvas(str(out), pagesize=A4)
    margin, y = 42.0, h - 52.0

    def new_page() -> None:
        nonlocal y
        c.showPage()
        y = h - 52.0

    def text(s: str, size: float = 9.0, font: str = "Helvetica", gap: float = 3.0) -> None:
        nonlocal y
        c.setFont(font, size)
        for line in simpleSplit(s, font, size, w - 2 * margin):
            if y < 60:
                new_page()
                c.setFont(font, size)
            c.drawString(margin, y, line)
            y -= size + gap

    def heading(s: str, size: float = 13.0) -> None:
        nonlocal y
        y -= 8
        if y < 90:
            new_page()
        text(s, size, "Helvetica-Bold", 5.0)

    def rule() -> None:
        nonlocal y
        c.setLineWidth(0.4)
        c.line(margin, y + 4, w - margin, y + 4)
        y -= 8

    def table(header: Sequence[str], rows: Sequence[Sequence[str]],
              widths: Sequence[float]) -> None:
        nonlocal y
        if y < 90:
            new_page()
        c.setFont("Helvetica-Bold", 7.5)
        x = margin
        for head, cw in zip(header, widths, strict=True):
            c.drawString(x, y, head)
            x += cw
        y -= 11
        rule()
        c.setFont("Helvetica", 7.5)
        for row in rows:
            if y < 60:
                new_page()
                c.setFont("Helvetica", 7.5)
            x = margin
            for cell, cw in zip(row, widths, strict=True):
                c.drawString(x, y, cell[: int(cw / 3.9)])
                x += cw
            y -= 10
        y -= 4

    c.setTitle("PoC-E11 — the high-confidence action chain")
    text("PoC-E11", 20, "Helvetica-Bold", 6)
    text("The high-confidence action chain: does data quality beat data quantity?",
         12, "Helvetica-Bold", 6)
    rule()
    text("Pre-registration: docs/research/poc/e11_prereg.md (written before any held-out "
         "number). Runner: analysis/poc/e11_action_chain.py. Tests: tests/test_poc_e11.py. "
         "Generated report: docs/research/poc/e11.md. Nothing in this cell touches a "
         "production export; the corpus read is read-only.", 8.5)
    text(f"Gate self-check: {verify_gate(run.gate)}", 8.5)
    if run.gate.error:
        text(f"Corpus pass NOT RUN: {run.gate.error}", 9, "Helvetica-Bold")
        c.save()
        return out
    text(f"Corpus: {run.gate.total} final matches with a sequence · {run.gate.with_sequence} "
         f"with >= 4 events · {run.gate.passed} pass perspective_reliable · "
         f"{run.gate.one_sided} refused. Split: {run.n_train} train / {run.n_eval} eval bouts "
         f"({run.n_eval_steps} scored eval steps), boundary {run.boundary}.", 8.5)

    heading("Verdicts")
    for k, v in verdicts(run).items():
        # The verdict strings are markdown (the .md report emits them verbatim); the PDF is not,
        # so bold markers and the escaped `n\*` are unwrapped rather than drawn literally.
        text(f"{k} — {v.replace('**', '').replace(chr(92) + '*', '*')}", 8.5)

    heading("Why the target is the seven families and not the twelve actions")
    text("The twelve-action space splits every family on `successful`, which is annotated per "
         "ingest batch. Measured on this run's own training window:", 8.5)
    table(["family", "success share, FULL train", "success share, HC-B train"],
          [[f, f"{a:.1%}" if a == a else "—", f"{b:.1%}" if b == b else "—"]
           for f, a, b in run.annotation_table], [120, 170, 170])
    text("A model trained on the high-confidence subset and scored on a twelve-symbol target "
         "would lose because the two sets are annotated differently, not because one is worse "
         "data. The primary target is therefore the annotation-invariant family alphabet.", 8.5)

    heading("C1/C4 — is it predictive, and in which vocabulary")
    rows = []
    if run.m0:
        rows.append(["order-0 marginal", str(run.m0.n_states), str(run.m0.n_steps),
                     f"{run.m0.ll:.4f} [{run.m0.lo:.4f}, {run.m0.hi:.4f}]", "—"])
    for r in run.contexts:
        d = paired_delta(r, run.m0, 0) if run.m0 else None
        rows.append([r.space, str(r.n_states), str(r.n_steps),
                     f"{r.ll:.4f} [{r.lo:.4f}, {r.hi:.4f}]",
                     f"{d[0]:+.4f}" if d else "—"])
    table(["context vocabulary", "states", "steps", "held-out mean logP [95% CI, bouts]",
           "d vs M0"], rows, [150, 45, 45, 175, 55])
    table(["A", "B", "paired delta [95% CI]", "reading"],
          [[a, b, _ci(d), "A wins" if wins(d) else
            "B wins" if wins((-d[0], -d[2], -d[1])) else "no difference"]
           for a, b, d in run.cross], [130, 130, 160, 70])

    heading("C2/C3 — quality against quantity")
    qrows = []
    for limb in (run.hc_a, run.hc_b):
        if limb is None:
            continue
        qrows.append([limb.name, f"{limb.n_train_bouts}/{run.n_train}", str(limb.n_train_steps),
                      f"{limb.ll:.4f}" if limb.powered else "—",
                      _ci(limb.d_full) if limb.powered else "underpowered",
                      _ci(limb.d_matched) if limb.powered else "—"])
    table(["subset", "train bouts", "steps", "logP", "d vs FULL (C2)",
           "d vs size-matched FULL (C3)"], qrows, [50, 62, 45, 55, 130, 130])
    for limb in (run.hc_a, run.hc_b):
        if limb and limb.powered:
            text(f"{limb.name} tags: "
                 + ", ".join(f"{t} ({n})" for t, n in limb.tags[:6]) + ".", 7.5)
            text(f"{limb.name} on the TWELVE-symbol target (batch-confounded, reported not "
                 f"used): d vs FULL {_ci(limb.d_full_a12)}.", 7.5)
    text(f"HC-restricted eval sensitivity: {run.hc_eval_refusal}.", 8.5)

    for title, path in figures:
        new_page()
        heading(title)
        img = ImageReader(str(path))
        iw, ih = img.getSize()
        draw_w = w - 2 * margin
        draw_h = draw_w * ih / iw
        c.drawImage(img, margin, y - draw_h, draw_w, draw_h, mask="auto")
        y -= draw_h + 14

    if run.curve:
        new_page()
        heading("C5 — the sample-size curve")
        table(["n bouts", "scheme", "held-out logP [95% CI]", "median draw",
               "delta vs FULL [95% CI]"],
              [[str(p.n), p.scheme, f"{p.ll:.4f} [{p.lo:.4f}, {p.hi:.4f}]",
                f"{p.median_draw:.4f}" if p.draw_lls else "—", _ci(p.delta_vs_full)]
               for p in run.curve.points], [45, 52, 160, 60, 155])
        text(f"Stabilisation rule: {run.curve.rule}. n* = "
             f"{run.curve.stabilised_at if run.curve.stabilised_at else 'not stabilised'}.", 8.5)
        text("The random row's logP is exactly the arithmetic mean of the R draws, so it "
             "carries no ensembling advantage; its INTERVAL is bootstrapped over the averaged "
             "per-step vector and is roughly 1/R as wide as a single model's would be. The "
             "median-draw column is the score of an actual single model.", 8.5)

    if run.graph:
        g = run.graph
        new_page()
        heading("C6 — the graph techniques on the action chain")
        text(f"Action graph: 12 nodes, density {g.communities.density:.3f}. Label ActionFlow "
             f"graph: {g.n_label_nodes} nodes / {g.n_label_edges} edges.", 8.5)
        table(["action", "PageRank", "occurrences", "definition"],
              [[n, f"{v:.5f}", str(dict(g.occupancy).get(n, 0)), STATE_DEFS.get(n, "")[:60]]
               for n, v in g.action_pr], [50, 55, 60, 320])
        table(["family", "PageRank (action graph)", "PageRank mass (label graph)"],
              [[f, f"{v:.5f}", f"{dict(g.label_family_pr).get(f, 0.0):.5f}"]
               for f, v in g.family_pr], [70, 150, 160])
        if g.rho is not None:
            text(f"Spearman between the two family rankings: rho={g.rho.rho:.3f} "
                 f"(p={g.rho.p_value:.3g}, n={g.rho.n}).", 8.5)
        text(f"Communities (Q={g.communities.modularity:.4f}, density "
             f"{g.communities.density:.3f}): "
             + " | ".join("{" + ", ".join(cm) + f"}} mean J={mj:.2f}, p10={p10:.2f}"
                          for cm, mj, p10 in zip(g.communities.communities,
                                                 g.communities.mean_jaccard,
                                                 g.communities.p10_jaccard, strict=True)), 8.5)
        if not g.communities.interpretable:
            text("Density exceeds the pre-registered 0.9 gate: this partition is NOT "
                 "interpretable as community structure, whatever Q says.", 8.5,
                 "Helvetica-Bold")
        table(["state", "reward-risk score", "[95% CI]", "n", "bouts", "gated"],
              [[r["state"], f"{r['score']:.3f}" if r["score"] is not None else "—",
                f"[{r['score_lo']:.3f}, {r['score_hi']:.3f}]"
                if r["score_lo"] is not None else "withheld",
                str(r["n"]), str(r["bouts"]), "yes" if r["gated"] else "no"]
               for r in g.reward_risk_rows], [55, 90, 110, 40, 40, 40])
    c.save()
    return out


# ── markdown ────────────────────────────────────────────────────────────────────
PREREG = REPO / "docs" / "research" / "poc" / "e11_prereg.md"


def render_markdown(run: Run, prereg: str, pdf: Path | None = None) -> str:
    v = verdicts(run)
    lines = [
        "# PoC-E11 — the high-confidence action chain: quality against quantity",
        "",
        "Generated by `uv run python -m analysis.poc.e11_action_chain` — **do not hand-edit**. "
        "Module: `analysis/poc/e11_action_chain.py`; tests: `tests/test_poc_e11.py`; "
        "pre-registration: `docs/research/poc/e11_prereg.md`; plan: "
        "`docs/research/03_POC_PLANS.md` (PoC-E11).",
        "",
        f"**Gate self-check:** {verify_gate(run.gate)}",
        "",
    ]
    if pdf is not None:
        lines += [f"**PDF deliverable:** `{pdf}` (gitignored `reports/`; regenerate with "
                  f"`--pdf`).", ""]
    lines += ["## Verdicts", ""]
    for k, val in v.items():
        lines += [f"- **{k}** — {val}", ""]
    lines += ["---", "", prereg.strip(), "", "---", "", "## Results", ""]
    if run.gate.error:
        return "\n".join([*lines, f"Corpus pass NOT RUN: `{run.gate.error}`.", ""]) + "\n"
    lines += [
        "### Corpus and split", "",
        f"{run.gate.total} final matches with a sequence · {run.gate.with_sequence} with ≥4 "
        f"events · **{run.gate.passed} pass** `bout_flags(...)['perspective_reliable']` · "
        f"{run.gate.one_sided} refused (one-sided filing). Split: **{run.n_train} train / "
        f"{run.n_eval} eval** bouts, {run.n_eval_steps} scored eval steps, boundary "
        f"{run.boundary}.", "",
        "### The annotation confound, measured on this run", "",
        "| family | success share, FULL train | success share, HC-B train |", "|---|---|---|",
    ]
    for fam, sh_full, sh_hc in run.annotation_table:
        lines.append(f"| `{fam}` | {sh_full:.1%} | "
                     + (f"{sh_hc:.1%} |" if sh_hc == sh_hc else "— |"))
    lines += ["", "This is why the primary target is the seven-symbol family alphabet and not "
                  "the twelve actions: on a twelve-symbol target the quality arms would be "
                  "measuring annotation policy.", "",
              "### C1 / C4 — predictive at all, and in which vocabulary", "",
              "| context vocabulary | states | scored steps | held-out mean logP(family) "
              "[95% CI, bout-clustered] |", "|---|---|---|---|"]
    if run.m0:
        lines.append(f"| order-0 train marginal (M0) | {run.m0.n_states} | {run.m0.n_steps} "
                     f"| {run.m0.ll:.4f} [{run.m0.lo:.4f}, {run.m0.hi:.4f}] |")
    for r in run.contexts:
        lines.append(f"| {r.space} | {r.n_states} | {r.n_steps} "
                     f"| {r.ll:.4f} [{r.lo:.4f}, {r.hi:.4f}] |")
    lines += ["", "| A | B | paired Δ logP per step [95% CI] | reading |", "|---|---|---|---|"]
    for a, b, d in run.cross:
        rev = (-d[0], -d[2], -d[1])
        lines.append(f"| {a} | {b} | {_ci(d)} | "
                     f"{'A wins' if wins(d) else 'B wins' if wins(rev) else 'no difference'} |")
    lines += ["", "### C2 / C3 — quality against quantity", "",
              "| subset | train bouts | action steps | eval bouts | held-out logP "
              "| Δ vs FULL (C2) | Δ vs size-matched FULL (C3) |",
              "|---|---|---|---|---|---|---|"]
    for limb in (run.hc_a, run.hc_b):
        if limb is None:
            continue
        lines.append(
            f"| **{limb.name}** | {limb.n_train_bouts}/{run.n_train} | {limb.n_train_steps} "
            f"| {limb.n_eval_bouts} | "
            + (f"{limb.ll:.4f} | {_ci(limb.d_full)} | {_ci(limb.d_matched)} |"
               if limb.powered else f"— | UNDERPOWERED ({limb.reason}) | — |"))
    lines.append("")
    for limb in (run.hc_a, run.hc_b):
        if limb is None or not limb.powered:
            continue
        lines += [f"`{limb.name}` selects: "
                  + ", ".join(f"{t} ({n})" for t, n in limb.tags)
                  + f" · years {limb.years}.",
                  "", f"`{limb.name}` on the TWELVE-symbol target — batch-confounded, reported "
                  f"so the confound is visible and never used as a verdict: Δ vs FULL "
                  f"{_ci(limb.d_full_a12)}.", ""]
    lines += [f"HC-restricted eval sensitivity: **{run.hc_eval_refusal}**.", ""]
    if run.curve:
        lines += ["### C5 — how much data is enough to measure", "",
                  "| n train bouts | scheme | held-out logP [95% CI] | R draws: median [min, "
                  "max] | Δ vs FULL [95% CI] |", "|---|---|---|---|---|"]
        for p in run.curve.points:
            lo_d, hi_d = p.draw_spread
            lines.append(f"| {p.n} | {p.scheme} | {p.ll:.4f} [{p.lo:.4f}, {p.hi:.4f}] "
                         + (f"| {p.median_draw:.4f} [{lo_d:.4f}, {hi_d:.4f}] "
                            if p.draw_lls else "| — ")
                         + f"| {_ci(p.delta_vs_full)} |")
        lines += ["", "> Reading the `random` rows. Their `logP` is exactly the arithmetic mean "
                      "of the R draws (the per-step and per-draw means commute), so it carries "
                      "**no** ensembling advantage and the contrast against the one-model "
                      "`recency` row is legitimate; the median column is the score of an actual "
                      "single model and is what the Reading below quotes. Their **interval** is "
                      "another matter: it is bootstrapped over the averaged per-step vector, "
                      "whose variance is roughly 1/R of a single draw's, so it says where the "
                      "typical model sits and must never be read as how precisely a model of "
                      "that size predicts. The `[min, max]` is the honest spread.", ""]
        star = (str(run.curve.stabilised_at) + " train bouts"
                if run.curve.stabilised_at is not None
                else "not stabilised within this corpus")
        lines += ["", f"Rule: {run.curve.rule}. **n\\* = {star}**.", ""]
    if run.graph:
        g = run.graph
        lines += ["### C6 — the house graph techniques on the action chain", "",
                  f"Action graph: 12 nodes, density **{g.communities.density:.3f}**. Shipped "
                  f"label ActionFlow graph on the same bouts: {g.n_label_nodes} nodes / "
                  f"{g.n_label_edges} edges.", "",
                  "| action | PageRank | occurrences | definition |", "|---|---|---|---|"]
        occ = dict(g.occupancy)
        for node, pr_v in g.action_pr:
            lines.append(f"| `{node}` | {pr_v:.5f} | {occ.get(node, 0)} "
                         f"| {STATE_DEFS.get(node, '')} |")
        lines += ["", "| family | PageRank (action graph) | PageRank mass (label graph, "
                      "aggregated) |", "|---|---|---|"]
        by = dict(g.label_family_pr)
        for fam_name, pr_v in g.family_pr:
            lines.append(f"| `{fam_name}` | {pr_v:.5f} | {by.get(fam_name, 0.0):.5f} |")
        if g.rho is not None:
            lines += ["", f"Spearman between the two family rankings: **ρ={g.rho.rho:.3f}** "
                          f"(p={g.rho.p_value:.3g}, n={g.rho.n}).", ""]
        lines += ["", f"Communities (greedy modularity, Q={g.communities.modularity:.4f}), "
                      f"bootstrap over BOUTS ({N_STABILITY} resamples):", "",
                  "| community | mean Jaccard | p10 Jaccard |", "|---|---|---|"]
        for cm, mj, p10 in zip(g.communities.communities, g.communities.mean_jaccard,
                               g.communities.p10_jaccard, strict=True):
            lines.append(f"| {', '.join('`' + x + '`' for x in cm)} | {mj:.2f} | {p10:.2f} |")
        if not g.communities.interpretable:
            lines += ["", "> Density exceeds the pre-registered 0.9 gate. This partition is "
                          "**not interpretable** as community structure: on a near-complete "
                          "graph every partition is an arbitrary cut of one blob, and Q is "
                          "measuring the cut, not the grappling.", ""]
        lines += ["", "| state | reward−risk | [95% CI] | n | bouts | gated |",
                  "|---|---|---|---|---|---|"]
        for rr_row in g.reward_risk_rows:
            s = f"{rr_row['score']:.3f}" if rr_row["score"] is not None else "—"
            ci = (f"[{rr_row['score_lo']:.3f}, {rr_row['score_hi']:.3f}]"
                  if rr_row["score_lo"] is not None else "withheld (coverage)")
            lines.append(f"| `{rr_row['state']}` | {s} | {ci} | {rr_row['n']} "
                         f"| {rr_row['bouts']} | {'yes' if rr_row['gated'] else 'no'} |")
        lines.append("")
    lines += ["", "## Reading", ""] + _reading(run)
    return "\n".join(lines) + "\n"


def _recency_note(curve: SizeCurve) -> str:
    """The random-vs-recency contrast, stated against the comparator that survives scrutiny.

    The contrast is made on the MEDIAN of the R draws rather than on their mean, not because
    the mean is inflated — it is exactly ``mean_r(ll_r)``, no ensembling advantage — but
    because the median is the score of an actual single model and the recency arm is one
    model. The measured mean-vs-median gap is reported so a reader can see it is skew and not
    a thumb on the scale.
    """
    rec = {p.n: p for p in curve.points if p.scheme == "recency"}
    rnd = [p for p in curve.points if p.scheme == "random" and p.draw_lls]
    if not rnd:
        return (" The random sweep beside it separates volume from recency, but no draw-level "
                "spread was recorded, so no contrast is claimed.")
    gaps = [(p.n, p.median_draw - rec[p.n].ll, abs(p.ll - p.median_draw))
            for p in rnd if p.n in rec]
    ahead = sum(1 for _, g, _ in gaps if g > 0)
    best = max(gaps, key=lambda g: g[1])
    worst_case = min((p.draw_spread[0] - rec[p.n].ll for p in rnd if p.n in rec), default=0.0)
    return (
        f" The random sweep beside it separates volume from recency, and the contrast is drawn "
        f"against the MEDIAN single draw — a real model, like the recency arm — rather than "
        f"against the R-draw mean, which differs from it by only "
        f"{max(e for _, _, e in gaps):.4f} nats/step at worst and carries no ensembling "
        f"advantage in the point estimate. A random slice of the training window beats the most "
        f"RECENT slice of the same size at {ahead} of {len(gaps)} grid points, by up to "
        f"{best[1]:+.4f} nats/step at n={best[0]}. The bound on that claim is the draw-level "
        f"spread: at the least favourable grid point the WORST of the {N_SUBSAMPLE} random "
        + (f"draws still clears its recency counterpart by {worst_case:+.4f}"
           if worst_case > 0 else
           f"draws falls {abs(worst_case):.4f} nats/step BEHIND its recency counterpart, so the "
           f"claim is about the typical random slice and not about every one of them")
        + ". The lever is nevertheless DIVERSITY rather than recency: the newest bouts come "
          "from one or two events, and a training set drawn only from them is a training set "
          "about those events."
    )


def _reading(run: Run) -> list[str]:
    """The synthesis, written from the numbers above."""
    if run.full is None or run.m0 is None:
        return ["No corpus pass, no reading."]
    out = [
        f"1. **The chain is {'' if wins(run.c1) else 'NOT '}predictive against its own "
        f"marginal** — Δ {_ci(run.c1)}. Everything else in this cell is read under that "
        f"result, which is why it is C1 and not an appendix.",
        "",
    ]
    if not wins(run.c1) and run.curve:
        small = min((p for p in run.curve.points if p.scheme == "recency"), key=lambda p: p.n)
        out += [
            f"   *The mechanism, and it is visible in C5.* The estimator is not broken and it "
            f"is not starved: the same order-1 model climbs from {small.ll:.4f} at "
            f"{small.n} training bouts to {run.full.ll:.4f} at {run.curve.n_full}, a gain of "
            f"{run.full.ll - small.ll:+.3f} nats/step whose intervals exclude 0 for most of "
            f"the sweep. So it learns from data — it just converges onto something the order-0 "
            f"marginal already knows. What more bouts buy this chain is a better estimate of "
            f"**how often each action happens**, not of **which action follows which**. On "
            f"this corpus, at this granularity, the previous action carries no measurable "
            f"information about the next one.",
            "",
        ]
    if run.hc_a and run.hc_a.powered:
        a = run.hc_a
        out += [
            f"2. **Quality against quantity, HC-A ({a.n_train_bouts} of {run.n_train} train "
            f"bouts).** Unmatched: {outcome(a.d_full, 'HC-A', 'FULL')} at Δ {_ci(a.d_full)}. "
            f"Size-matched against {N_SUBSAMPLE} random draws of the same size: "
            f"{outcome(a.d_matched, 'HC-A', 'random same-size FULL')} at Δ "
            f"{_ci(a.d_matched)}. The second number is the one that means anything about data "
            f"quality; the first also contains the effect of throwing bouts away.",
            "",
        ]
    if run.hc_b:
        b = run.hc_b
        out += [
            f"3. **HC-B, the annotation-completeness subset ({b.n_train_bouts} train bouts, "
            f"{b.n_train_steps} steps).** "
            + (f"{'Powered' if b.powered else 'UNDERPOWERED'}: {b.reason or 'runs'}. "
               if not b.powered else
               f"Unmatched {outcome(b.d_full, 'HC-B', 'FULL')}, size-matched "
               f"{outcome(b.d_matched, 'HC-B', 'random same-size FULL')}. ")
            + "Whatever the number, HC-B is confounded with event family and era — it is "
              "almost entirely ADCC Trials 2023/2024, because those are the batches a "
              "frame-read pipeline produced. It is a statement about those batches, not about "
              "audited data in general.",
            "",
        ]
    if run.curve:
        out += [
            "4. **Enough to measure.** "
            + (f"Held-out prediction stabilises at **{run.curve.stabilised_at} training "
               f"bouts** of {run.curve.n_full} on the recency-nested sweep."
               if run.curve.stabilised_at is not None
               else "Held-out prediction does **not** stabilise within this corpus by the "
                    "pre-registered rule — every training size below the full window is still "
                    "measurably behind it, or the intervals are too wide to say.")
            + _recency_note(run.curve),
            "",
        ]
    if run.graph:
        g = run.graph
        top = g.action_pr[0][0] if g.action_pr else "—"
        out += [
            f"5. **The graph techniques transfer, and two of the three say less than they "
            f"look like they say.** PageRank on the twelve-node graph puts `{top}` on top, but "
            f"that ranking is partly a function of the `successful` annotation policy — the "
            f"attempt codes absorb everything the flag does not mark — which is why the "
            f"seven-node family ranking is published beside it and is the one to quote. "
            f"Communities on a graph of density {g.communities.density:.3f} are "
            + ("not interpretable at all" if not g.communities.interpretable
               else "interpretable by the pre-registered gate")
            + "; the honest report is the density, not the partition. Reward-risk survives "
              "intact because `lamas_chain` already gates it on actor reliability."
            + (f" The two levels agree at ρ={g.rho.rho:.3f} on the family ranking."
               if g.rho is not None else ""),
            "",
        ]
    out += [
        "6. **What bounds all of it.** `successful` is present on a minority of corpus events "
        "and its coverage varies by ingest batch by a factor of five, which is the single "
        "largest quality fact about this corpus and the reason this cell has an "
        "annotation-invariant target at all; the `perspective_reliable` gate removes one-sided "
        "bouts but cannot repair actor noise inside the ones it keeps; the eval window is one "
        "quarter of a corpus that grew 9% since PoC-E9 ran, so the two cells' absolute numbers "
        "are not interchangeable; and the Lamas mapping passes over roughly 30% of the event "
        "stream by design (guard postures and dwell states are not actions), so this chain "
        "describes what athletes DO and is silent about where they ARE.",
        "",
        "7. **Nothing here changes a production export.** No engine, no site artefact, no "
        "schema. Training the shipped kernel on a subset would be a separate decision needing "
        "its own cell.",
    ]
    return out


# ── self-check ──────────────────────────────────────────────────────────────────
def _demo() -> None:
    """One runnable check of the non-trivial logic, no framework, no DB."""
    seq: list[dict[str, Any]] = [
        {"type": "control", "label": "Collar Tie", "actor_id": "X"},
        {"type": "takedown", "label": "Single Leg Takedown", "successful": True, "actor_id": "X"},
        {"type": "guard", "label": "Half Guard", "actor_id": "Y"},
        {"type": "pass", "label": "Knee Cut Pass", "actor_id": "X"},
        {"type": "control", "label": "Back Control", "actor_id": "X"},
        {"type": "submission", "label": "Rear Naked Choke", "successful": True, "actor_id": "X"},
        {"type": "submission", "label": "Tap", "successful": True, "actor_id": "X"},
    ]
    ch = action_chain(seq, "SUBMISSION")
    assert [p.action for p in ch] == ["CDP", "TKD", "GPSA", "BTKA", "SUB"], ch
    assert [p.family for p in ch] == ["CDP", "TKD*", "GPS*", "BTK*", "SUB*"], ch
    assert [p.action for p in ch] == [s.state for s in chain_of(
        {"id": "d", "win_type": "SUBMISSION", "seq": seq}).steps]
    assert set(FAMILY_OF.values()) == set(ALPHABET_FAMILY)
    assert {A12.of(ch[0]), A7.of(ch[0]), S_TYPE.of(ch[0]), S_LABEL.of(ch[0])} == {
        "a12/CDP", "a7/CDP", "typ/control", "lab/Collar Tie"}
    row = BoutRow(key=(2024, "t", "b"), bout_id="b", sequence=seq, athlete_a="X", athlete_b="Y",
                  event="E", year=2024, win_type="SUBMISSION", chain=ch, annotated=3 / 7)
    assert row.n_actors == 1 and not row.hc_a          # single_actor refusal
    assert not row.hc_b
    m = _matrix([row])
    assert all(abs(sum(r) - 1.0) < 1e-9 or sum(r) == 0 for r in m), m
    print("e11 self-check ok")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PoC-E11 — the high-confidence action chain")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e11.md"))
    ap.add_argument("--prereg", default=str(PREREG))
    ap.add_argument("--pdf", default=str(REPO / "reports" / "e11" / "e11.pdf"))
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--n-stability", type=int, default=N_STABILITY)
    ap.add_argument("--skip-corpus", action="store_true", help="no DB read")
    args = ap.parse_args(argv)

    gate = GateReport(error="skipped (--skip-corpus)") if args.skip_corpus else load_corpus()
    run = run_all(gate, n_boot=args.n_boot, n_resamples=args.n_stability)
    pdf_path: Path | None = None
    if not args.no_pdf:
        pdf_path = Path(args.pdf)
        train = split_rows(gate.rows)[0] if gate.rows else []
        figs = write_figures(run, train, pdf_path.parent) if gate.rows else []
        write_pdf(run, figs, pdf_path)
    prereg_path = Path(args.prereg)
    prereg = prereg_path.read_text(encoding="utf-8") if prereg_path.exists() else (
        "> Pre-registration file missing — see `docs/research/03_POC_PLANS.md` (PoC-E11).")
    md = render_markdown(run, prereg, pdf_path)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}" + (f" · pdf: {pdf_path}" if pdf_path else ""))
    return 0


if __name__ == "__main__":
    if "--demo" in __import__("sys").argv:
        _demo()
    else:
        raise SystemExit(main())
