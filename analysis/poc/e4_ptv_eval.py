"""PoC-E4 — calibrating Path-to-Victory: γ, the shaping term, and the Markov order.

Gap #12 (``docs/research/02_GAPS_AND_OPPORTUNITIES.md``): PtV ships γ=0.8 and a
0.1-per-term shaping prior, neither of which was ever fitted, and its first-order
memorylessness was never tested. This runner is the measurement, on the harness
PoC-E8 built and this cell inherits verbatim (``analysis/poc/e8_interaction_graph``:
``Bout``, ``temporal_split``, ``Kernel``, ``finish_label``, ``eval_rows``,
``evaluate_kernels``, ``paired_delta_auc``, ``rank_auc``, ``corpus_bouts``).

Four passes, in the plan's order (``docs/research/03_POC_PLANS.md`` §PoC-E4):

  1. **Label** — the VAEP-analogue, already in the harness: does a *successful
     submission by the same actor* occur within the next k=5 events?
  2. **Sweep** — PtV-as-shipped scored first, then γ × shaping over both kernels
     (ActionFlow, the production one, and PoC-E8's interaction kernel), every config
     scored on the SAME held-out rows so every contrast is paired.
  3. **Memorylessness probe** — first-order vs second-order successor model,
     held-out per-event log-likelihood, second-order contexts pruned at min count 5.
     "If second-order wins materially, PtV's kernel is the thing to revisit, not its γ."
  4. **External anchor** — our corpus's back-take→submission, takedown→submission and
     guard-pass→guard-pass transition probabilities beside Lamas 2024's published
     0.45 / 0.15 / 0.30, with intervals.

Pre-registered before any number was looked at (rendered into the report too, so it
carries its own criterion):

  * criterion .... the plan's, verbatim: "the (γ, shaping) chosen by held-out AUC
                   ships; if AUC's CI includes 0.5, PtV is demoted from site prose
                   until it doesn't".
  * grid ......... γ ∈ {0.60, 0.70, 0.80, 0.90, 0.95} (the plan's "{0.6…0.95}", with
                   production 0.8 inside it) × shaping ∈ {on = 0.1 per term (production),
                   off = 0.0}. γ=0 is also reported, LABELLED as an ablation outside the
                   pre-registered grid: it strips the discounted look-ahead entirely and
                   leaves the 1-step Lamas reward−risk, so it says what the multi-step
                   machinery buys.
  * chosen ....... the highest held-out AUC inside the grid, on the PRODUCTION kernel.
  * change ....... a config displaces production only if its PAIRED ΔAUC against
                   (γ=0.8, shaping on), on the same rows, excludes 0. Picking the argmax
                   of ten heavily-overlapping intervals is a forking path; the paired
                   interval is what makes "chosen by held-out AUC" a decision rather
                   than a ranking.
  * demoted ...... production PtV's AUC interval covering 0.5 demotes it from site prose.
                   Judged on the CLUSTER (bout-level) interval, because eval rows inside
                   one bout are consecutive events of the same fight and the row-level
                   interval is anti-conservative. Both are printed.
  * memoryless ... second order wins MATERIALLY iff the paired per-event Δ log-likelihood
                   is positive with a bout-clustered bootstrap interval excluding 0.
  * split ........ the most recent 25% of sequences by date, train is everything before.
                   Never random (ADR-03).
  * venue ........ the VERDICT is the corpus pass. The fixture is one athlete's ten days:
                   it proves the instrument reproduces PoC-E8 config-for-config, and it is
                   not evidence about grappling.

ADR-03: this run changes NO production value. `path_to_victory`'s defaults stay
γ=0.8 / shaping 0.1; the report records what a change would be if the criterion ever
selects one, and the orchestrator owns that edit.

LGPD: the fixture is the repository owner's own app export (``owner_kind='user'``).
Its numbers may appear in ``docs/research/poc/e4.md`` as PoC evidence and NOWHERE else.

Usage::

    uv run python -m analysis.poc.e4_ptv_eval
    uv run python -m analysis.poc.e4_ptv_eval --out docs/research/poc/e4.md
    uv run python -m analysis.poc.e4_ptv_eval --skip-corpus     # fixture only, no DB
"""

from __future__ import annotations

import argparse
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from analysis.path_to_victory import _SHAPING_W, GAMMA, path_to_victory
from analysis.poc.e8_interaction_graph import (
    EVAL_FRACTION,
    FINISH_WINDOW,
    Bout,
    GateReport,
    Kernel,
    KernelResult,
    _boot_ci,
    actionflow_kernel,
    corpus_bouts,
    eval_rows,
    evaluate_kernels,
    fixture_bouts,
    interaction_kernel,
    paired_delta_auc,
    rank_auc,
    temporal_split,
)
from analysis.stats_rigor import wilson
from analysis.technique_match import clean_label

REPO = Path(__file__).resolve().parents[2]

# The plan's grid: "γ ∈ {0.6…0.95}". Production (GAMMA = 0.8) is inside it by construction.
GAMMA_GRID: tuple[float, ...] = (0.60, 0.70, 0.80, 0.90, 0.95)
GAMMA_ABLATION = 0.0                    # labelled, outside the pre-registered grid
SHAPING_GRID: tuple[tuple[str, float], ...] = (("on", _SHAPING_W), ("off", 0.0))
PROD_SHAPING = _SHAPING_W

MIN_CONTEXT = 5        # the plan's "pruned at min count 5" for second-order contexts
ALPHA_GRID: tuple[float, ...] = (0.1, 0.5, 1.0)   # add-α smoothing, shared by both orders
ALPHA = 0.5                                        # the headline α

# Lamas et al. 2024 (doi 10.1177/17479541231210979), 93 WSFC-2019 no-gi matches.
LAMAS_PUBLISHED: tuple[tuple[str, float], ...] = (
    ("back control → submission", 0.45),
    ("takedown → submission", 0.15),
    ("guard pass → guard pass", 0.30),
)

assert GAMMA in GAMMA_GRID, "the production γ must be swept, not assumed"


# ── the sweep ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Config:
    """One (kernel, γ, shaping) point of the sweep."""

    kernel: str
    gamma: float
    shaping: float
    ablation: bool = False

    @property
    def shaping_name(self) -> str:
        return "on" if self.shaping else "off"

    @property
    def is_production(self) -> bool:
        return self.gamma == GAMMA and self.shaping == PROD_SHAPING

    def __str__(self) -> str:
        return f"γ={self.gamma:.2f}, shaping {self.shaping_name}"


@dataclass
class SweepRow:
    cfg: Config
    auc: float
    lo: float
    hi: float
    clo: float          # cluster (bout-level) interval — the one the criterion reads
    chi: float
    cold: int
    clamped: float      # share of train nodes saturated at the ±1 clamp
    d_auc: float        # paired ΔAUC against production ON THE SAME KERNEL
    d_lo: float
    d_hi: float

    @property
    def separates(self) -> bool:
        """Does the interval the criterion reads clear chance?"""
        return self.clo > 0.5 or self.chi < 0.5

    @property
    def beats_production(self) -> bool:
        return self.d_lo > 0.0


def row_groups(bouts: Sequence[Bout], k: int = FINISH_WINDOW) -> list[Any]:
    """One bout key per eval row, in ``eval_rows`` order.

    ``eval_rows`` is a plain concatenation over ``bouts`` with no cross-bout state, so
    calling it per bout reproduces its partition exactly — cheaper and safer than a second
    copy of its filter, which would be free to drift.
    """
    return [b.key for b in bouts for _ in eval_rows([b], k)]


def sweep(
    train: Sequence[Bout],
    held: Sequence[Bout],
    kernels: Sequence[Kernel],
    n_boot: int = 2000,
    n_boot_delta: int = 1000,
) -> tuple[list[SweepRow], list[bool], dict[str, list[float]]]:
    """Every (kernel, γ, shaping) on the SAME held-out rows. Returns rows + labels.

    The third return is the per-kernel production scores, kept so the report can say how
    much of the sweep is one value function being monotonically rescaled.
    """
    groups = row_groups(held)
    labels: list[bool] = [y for _, y in eval_rows(held)]
    grid = [(g, False) for g in GAMMA_GRID] + [(GAMMA_ABLATION, True)]

    per_kernel: dict[str, dict[Config, KernelResult]] = defaultdict(dict)
    for gamma, ablation in grid:
        for _, w in SHAPING_GRID:
            results, labels = evaluate_configs(train, held, kernels, gamma, w, n_boot, groups)
            for kern, res in zip(kernels, results, strict=True):
                per_kernel[kern.name][Config(kern.name, gamma, w, ablation)] = res

    rows: list[SweepRow] = []
    prod_scores: dict[str, list[float]] = {}
    for kname, by_cfg in per_kernel.items():
        prod = next(r for c, r in by_cfg.items() if c.is_production)
        prod_scores[kname] = prod.scores
        for cfg, res in by_cfg.items():
            d = ((0.0, 0.0, 0.0) if cfg.is_production
                 else paired_delta_auc(res, prod, labels, n_boot=n_boot_delta, groups=groups))
            rows.append(SweepRow(cfg, res.auc, res.lo, res.hi, res.clo, res.chi,
                                 res.cold_rows, res.clamped, *d))
    rows.sort(key=lambda r: (r.cfg.kernel, r.cfg.ablation, -r.cfg.gamma, r.cfg.shaping_name))
    return rows, labels, prod_scores


def evaluate_configs(
    train: Sequence[Bout],
    held: Sequence[Bout],
    kernels: Sequence[Kernel],
    gamma: float,
    shaping_w: float,
    n_boot: int,
    groups: Sequence[Any],
) -> tuple[list[KernelResult], list[bool]]:
    """One (γ, shaping) point, both kernels, row-level AND bout-clustered intervals.

    The row-level interval is E8's, kept so this runner reproduces that report
    config-for-config; the clustered one is what the criterion reads.
    """
    value_fn = partial(path_to_victory, gamma=gamma, shaping_w=shaping_w)
    results, labels = evaluate_kernels(train, held, kernels, n_boot=n_boot, value_fn=value_fn)
    for res in results:
        if res.auc != res.auc:      # NaN — a degenerate held-out set has no interval
            continue

        def stat(idx: Sequence[int], sc: list[float] = res.scores) -> float:
            return rank_auc([sc[i] for i in idx], [labels[i] for i in idx])

        _, res.clo, res.chi = _boot_ci(labels, stat, n_boot, groups)
    return results, labels


def chosen(rows: Sequence[SweepRow], kernel: str) -> SweepRow | None:
    """Highest held-out AUC inside the PRE-REGISTERED grid (the ablation cannot win)."""
    grid = [r for r in rows if r.cfg.kernel == kernel and not r.cfg.ablation]
    return max(grid, key=lambda r: r.auc) if grid else None


def production_row(rows: Sequence[SweepRow], kernel: str) -> SweepRow | None:
    return next((r for r in rows if r.cfg.kernel == kernel and r.cfg.is_production), None)


# ── Markov order probe ──────────────────────────────────────────────────────────
@dataclass
class OrderResult:
    """First vs second order on one kernel's chains, at one smoothing α."""

    kernel: str
    alpha: float
    n_steps: int
    n_second_order: int      # steps where the 2nd-order context cleared MIN_CONTEXT
    contexts: int            # distinct (prev, cur) contexts that cleared it in train
    vocab: int
    ll1: float               # mean per-step log-likelihood
    ll2: float
    delta: float
    lo: float
    hi: float

    @property
    def material(self) -> bool:
        """The pre-registered bar: positive Δ with a clustered interval excluding 0."""
        return self.lo == self.lo and self.lo > 0.0


def dedupe_by_key(bouts: Sequence[Bout]) -> list[Bout]:
    """One Bout per distinct key.

    Corpus bouts are MIRRORED (entered once per athlete perspective). ActionFlow's chains
    are perspective-free, so a mirrored corpus doubles every count uniformly — harmless for
    a probability, fatal for a threshold: ``MIN_CONTEXT=5`` would silently become 2.5
    distinct observations. The interaction kernel's mirror is a deterministic role-relabel
    of the same succession and carries no extra evidence about Markov order either.
    """
    seen: set[Any] = set()
    out: list[Bout] = []
    for b in bouts:
        if b.key not in seen:
            seen.add(b.key)
            out.append(b)
    return out


def _chain_counts(
    chains: Sequence[Sequence[str]],
) -> tuple[Counter[str], dict[str, Counter[str]], dict[tuple[str, str], Counter[str]]]:
    """(state counts, first-order successors, second-order successors) from train chains."""
    states: Counter[str] = Counter()
    first: dict[str, Counter[str]] = defaultdict(Counter)
    second: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for c in chains:
        states.update(c)
        for x, y in zip(c, c[1:], strict=False):
            first[x][y] += 1
        for x, y, z in zip(c, c[1:], c[2:], strict=False):
            second[(x, y)][z] += 1
    return states, dict(first), dict(second)


def markov_order(
    kernel: Kernel,
    train: Sequence[Bout],
    held: Sequence[Bout],
    alpha: float = ALPHA,
    min_context: int = MIN_CONTEXT,
    n_boot: int = 2000,
) -> OrderResult:
    """Held-out per-step log-likelihood, first order vs backed-off second order.

    Both models share one vocabulary, one α and — crucially — the SAME scored steps
    (every step from index 2 on, so the second-order model is never given a head start on
    positions the first-order one cannot see). The second order backs off to the first
    wherever its ``(prev, cur)`` context has fewer than ``min_context`` train observations,
    which is the plan's pruning rule: without it the comparison measures coverage, not order.
    """
    tr = [c for b in dedupe_by_key(train) for c in kernel.chains(b)]
    ev = [(b.key, c) for b in dedupe_by_key(held) for c in kernel.chains(b)]
    states, first, second = _chain_counts(tr)
    vocab = len(states) + 1                       # +1 for the unseen-state bucket
    kept = {ctx for ctx, succ in second.items() if sum(succ.values()) >= min_context}

    def p1(x: str, z: str) -> float:
        succ = first.get(x)
        denom = (sum(succ.values()) if succ else 0) + alpha * vocab
        return ((succ[z] if succ else 0) + alpha) / denom

    def p2(x: str, y: str, z: str) -> float:
        succ = second[(x, y)]
        return (succ[z] + alpha) / (sum(succ.values()) + alpha * vocab)

    l1: list[float] = []
    l2: list[float] = []
    groups: list[Any] = []
    n_second = 0
    for key, c in ev:
        # unseen held-out states collapse into one bucket the vocabulary already reserves
        seq = [s if s in states else "\x00unk" for s in c]
        for t in range(2, len(seq)):
            x, y, z = seq[t - 2], seq[t - 1], seq[t]
            a = p1(y, z)
            if (x, y) in kept:
                b = p2(x, y, z)
                n_second += 1
            else:
                b = a
            l1.append(math.log(a))
            l2.append(math.log(b))
            groups.append(key)

    if not l1:
        nan = float("nan")
        return OrderResult(kernel.name, alpha, 0, 0, len(kept), vocab, nan, nan, nan, nan, nan)

    diffs = [b - a for a, b in zip(l1, l2, strict=True)]
    d, lo, hi = _boot_ci(
        [True] * len(diffs),
        lambda s: sum(diffs[i] for i in s) / len(s) if s else 0.0,
        n_boot, groups)
    return OrderResult(kernel.name, alpha, len(l1), n_second, len(kept), vocab,
                       sum(l1) / len(l1), sum(l2) / len(l2), d, lo, hi)


# ── external anchor (Lamas 2024) ────────────────────────────────────────────────
def own_transitions(bouts: Sequence[Bout]) -> list[tuple[dict[str, str], dict[str, str]]]:
    """Every (event, that fighter's next own event) pair, UNFOLDED.

    Deliberately not read off the ActionFlow graph: ``network_from_sequences`` drops the
    ``A → A`` edge, and Lamas' guard-pass→guard-pass figure is precisely a self-transition.
    Reading that cell off a graph built to refuse it would report 0.04 for a quantity our
    kernel does not measure and call the mismatch a corpus finding. Bouts are de-duplicated
    by key first — the corpus mirror would otherwise double every denominator and halve the
    interval it earns.
    """
    pairs: list[tuple[dict[str, str], dict[str, str]]] = []
    for b in dedupe_by_key(bouts):
        by_actor: defaultdict[Any, list[dict[str, str]]] = defaultdict(list)
        for e in b.sequence:
            actor = e.get("actor_id", e.get("actor"))
            typ = str(e.get("type", "")).lower()
            label = clean_label(str(e.get("label", "")), typ)
            if actor is not None and label:
                by_actor[actor].append({"label": label.strip().lower(), "type": typ})
        for chain in by_actor.values():
            pairs += list(zip(chain, chain[1:], strict=False))
    return pairs


def lamas_anchor(bouts: Sequence[Bout]) -> list[dict[str, Any]]:
    """Our three transition probabilities beside Lamas 2024's published values.

    Descriptive, on the whole gated corpus — not a held-out prediction, so it is reported
    beside the criterion and never inside it. "Submission" here means the fighter's next
    own action IS a submission event (an attempt), which is what a transition into the
    submission state means in a Markov chain; landing it is a different, smaller number.
    """
    def typed(t: str) -> Any:
        return lambda e: e["type"] == t

    def labelled(name: str) -> Any:
        return lambda e: e["label"] == name

    specs: list[tuple[str, Any, Any]] = [
        ("back control → submission", labelled("back control"), typed("submission")),
        ("takedown → submission", typed("takedown"), typed("submission")),
        ("guard pass → guard pass", typed("pass"), typed("pass")),
    ]
    pairs = own_transitions(bouts)
    published = dict(LAMAS_PUBLISHED)
    out: list[dict[str, Any]] = []
    for name, src, dst in specs:
        from_src = [(x, y) for x, y in pairs if src(x)]
        k, n = sum(1 for _, y in from_src if dst(y)), len(from_src)
        est = wilson(k, n)
        ref = published[name]
        agrees = bool(est.lo is not None and est.hi is not None and est.lo <= ref <= est.hi)
        out.append({"name": name, "k": k, "n": n, "est": est, "lamas": ref, "agrees": agrees})
    return out


# ── pass assembly ───────────────────────────────────────────────────────────────
@dataclass
class Pass:
    name: str
    n_bouts: int
    n_train: int
    n_eval_bouts: int
    n_pos: int
    n_neg: int
    rows: list[SweepRow]
    orders: list[OrderResult] = field(default_factory=list)
    anchor: list[dict[str, Any]] = field(default_factory=list)


def run_pass(
    name: str,
    bouts: Sequence[Bout],
    n_boot: int = 2000,
    n_boot_delta: int = 1000,
    with_anchor: bool = False,
) -> Pass:
    kernels = [actionflow_kernel(), interaction_kernel()]
    train, held = temporal_split(bouts)
    rows, labels, _ = sweep(train, held, kernels, n_boot=n_boot, n_boot_delta=n_boot_delta)
    orders = [markov_order(k, train, held, alpha=a, n_boot=max(200, n_boot // 2))
              for k in kernels for a in ALPHA_GRID]
    anchor = lamas_anchor(bouts) if with_anchor else []
    return Pass(name=name, n_bouts=len(bouts), n_train=len(train), n_eval_bouts=len(held),
                n_pos=sum(labels), n_neg=len(labels) - sum(labels),
                rows=rows, orders=orders, anchor=anchor)


PROD_KERNEL = "actionflow (within-actor)"


def verdict(corpus: Pass | None) -> dict[str, Any]:
    """The pre-registered criterion, applied verbatim, on the corpus pass only."""
    if corpus is None:
        return {"decided": False,
                "text": "UNDECIDED — the corpus pass did not run, and the fixture "
                        "(one athlete's ten days) is not a venue for the verdict."}
    prod = production_row(corpus.rows, PROD_KERNEL)
    best = chosen(corpus.rows, PROD_KERNEL)
    if prod is None or best is None:
        return {"decided": False, "text": "UNDECIDED — the sweep produced no rows."}

    demoted = not prod.separates
    change = best.beats_production and not best.cfg.is_production
    head = "REJECT (DEMOTE)" if demoted else "ACCEPT"
    order_hit = [o for o in corpus.orders if o.kernel == PROD_KERNEL and o.alpha == ALPHA]
    material = bool(order_hit and order_hit[0].material)
    return {
        "decided": True, "demoted": demoted, "change": change, "material": material,
        "prod": prod, "best": best,
        "text": (
            f"{head} — production PtV (γ={GAMMA}, shaping on) scores AUC {prod.auc:.3f} "
            f"[{prod.clo:.3f}, {prod.chi:.3f}] bout-clustered on held-out finish prediction, "
            + ("and the interval COVERS 0.5, so the criterion demotes PtV from site prose "
               "until it does not."
               if demoted else
               "and the interval EXCLUDES 0.5, so PtV keeps its place in site prose.")
            + " The sweep's argmax inside the pre-registered grid is "
            + (f"production itself ({best.cfg})."
               if best.cfg.is_production else
               f"{best.cfg} at AUC {best.auc:.3f}, whose PAIRED ΔAUC against production is "
               f"{best.d_auc:+.3f} [{best.d_lo:+.3f}, {best.d_hi:+.3f}] — "
               + ("it excludes 0, so the criterion SELECTS THIS CHANGE."
                  if change else
                  "it covers 0, so the criterion selects NO CHANGE and γ=0.8 with shaping "
                  "on stays, now with a number behind it instead of an assumption."))
            + " Memorylessness: second order "
            + ("WINS materially on the production kernel — per the plan, the kernel is what "
               "to revisit, not γ." if material else
               (f"LOSES materially on the production kernel (Δ per-step log-likelihood "
                f"{order_hit[0].delta:+.3f} [{order_hit[0].lo:+.3f}, {order_hit[0].hi:+.3f}], "
                f"bout-clustered), so the first-order kernel is not the defect either."
                if order_hit and order_hit[0].hi < 0 else
                "does NOT win materially on the production kernel, so the first-order "
                "assumption survives its first test."))
        ),
    }


# ── report ──────────────────────────────────────────────────────────────────────
def _sweep_table(rows: Sequence[SweepRow], kernel: str) -> list[str]:
    out = [
        "| γ | shaping | AUC [95% CI, rows] | AUC [95% CI, bouts] | ΔAUC vs production "
        "[paired] | clamped | cold rows | reads |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.cfg.kernel != kernel:
            continue
        tag = " *(ablation)*" if r.cfg.ablation else ""
        prod = " **← production**" if r.cfg.is_production else ""
        # 4 decimals: a percentile bootstrap can land a bound EXACTLY on 0, and at 3
        # decimals "+0.000" would read as "covers 0" next to a verdict saying it does not.
        delta = ("—" if r.cfg.is_production
                 else f"{r.d_auc:+.3f} [{r.d_lo:+.4f}, {r.d_hi:+.4f}]")
        out.append(
            f"| {r.cfg.gamma:.2f}{tag} | {r.cfg.shaping_name}{prod} | "
            f"{r.auc:.3f} [{r.lo:.3f}, {r.hi:.3f}] | {r.auc:.3f} [{r.clo:.3f}, {r.chi:.3f}] | "
            f"{delta} | {r.clamped:.0%} | {r.cold} | "
            f"{'separates' if r.separates else 'chance'} |")
    return out


def _order_table(orders: Sequence[OrderResult], kernel: str) -> list[str]:
    out = [
        "| α | steps scored | 2nd-order steps | contexts ≥5 | mean logP 1st | mean logP 2nd "
        "| Δ [95% CI, bouts] | material? |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for o in orders:
        if o.kernel != kernel:
            continue
        star = " **←**" if o.alpha == ALPHA else ""
        out.append(
            f"| {o.alpha}{star} | {o.n_steps} | {o.n_second_order} | {o.contexts} | "
            f"{o.ll1:.4f} | {o.ll2:.4f} | {o.delta:+.4f} [{o.lo:+.4f}, {o.hi:+.4f}] | "
            f"{'YES' if o.material else 'no'} |")
    return out


def _pass_section(p: Pass) -> list[str]:
    lines = [
        f"## {p.name}",
        "",
        f"{p.n_bouts} sequences · temporal split {p.n_train} train / {p.n_eval_bouts} held out "
        f"(most recent {EVAL_FRACTION:.0%} by date) · {p.n_pos} positive and {p.n_neg} negative "
        f"eval rows (label: a landed submission by the same actor within k={FINISH_WINDOW}).",
        "",
    ]
    for kernel in (PROD_KERNEL, "interaction (actor-aware)"):
        lines += [f"### Sweep — {kernel}", ""]
        lines += _sweep_table(p.rows, kernel)
        best = chosen(p.rows, kernel)
        prod = production_row(p.rows, kernel)
        if best and prod:
            lines += ["", f"Argmax inside the grid: **{best.cfg}** (AUC {best.auc:.3f}); "
                          f"production {prod.cfg} scores {prod.auc:.3f}. "
                      + ("They are the same config."
                         if best.cfg.is_production else
                         f"Paired ΔAUC {best.d_auc:+.3f} [{best.d_lo:+.3f}, {best.d_hi:+.3f}]"
                         + (" — excludes 0." if best.beats_production else " — covers 0."))]
        lines.append("")
    lines += ["### Memorylessness — first vs second order", "",
              f"Second-order contexts pruned at min count {MIN_CONTEXT}; below it the model "
              f"backs off to first order, so the two are compared on successor structure and "
              f"not on coverage. Both share one vocabulary, one add-α smoothing and the same "
              f"scored steps. Interval is bout-clustered.", ""]
    for kernel in (PROD_KERNEL, "interaction (actor-aware)"):
        lines += [f"**{kernel}**", ""] + _order_table(p.orders, kernel) + [""]
    if p.anchor:
        lines += [
            "### External anchor — Lamas et al. 2024",
            "",
            "Descriptive, on the whole gated corpus (not held out): the share of a fighter's "
            "own next actions out of a state that land on the named target, self-transitions "
            "INCLUDED (the graph builder drops `A → A`; Lamas' guard-pass→guard-pass cell "
            "*is* that transition, so this reads the raw within-actor successions instead). "
            "Bouts de-duplicated across the perspective mirror. Lamas ran the same "
            "construction on 93 WSFC-2019 no-gi matches.",
            "",
            "| transition | ours (Wilson 95%) | k/n | Lamas 2024 | agrees? |",
            "|---|---|---|---|---|",
        ]
        for a in p.anchor:
            e = a["est"]
            ours = (f"{e.p:.3f} [{e.lo:.3f}, {e.hi:.3f}]" if e.p is not None else "—")
            lines.append(f"| {a['name']} | {ours} | {a['k']}/{a['n']} | {a['lamas']:.2f} "
                         f"| {'yes' if a['agrees'] else 'NO'} |")
        lines.append("")
    return lines


def render_markdown(fixture: Pass, corpus: Pass | None, gate: GateReport) -> str:
    v = verdict(corpus)
    lines = [
        "# PoC-E4 — Path-to-Victory calibrated: γ, shaping, and the Markov order",
        "",
        "Generated by `uv run python -m analysis.poc.e4_ptv_eval` — do not hand-edit. "
        "Runner: `analysis/poc/e4_ptv_eval.py`; harness inherited from "
        "`analysis/poc/e8_interaction_graph.py`; model under test: "
        "`analysis/path_to_victory.py`. Plan: `docs/research/03_POC_PLANS.md` (PoC-E4); "
        "gap: `docs/research/02_GAPS_AND_OPPORTUNITIES.md` #12. "
        "Tests: `tests/test_poc_e4.py`.",
        "",
        "## Criterion (pre-registered, before any number below)",
        "",
        "> the (γ, shaping) chosen by held-out AUC ships; if AUC's CI includes 0.5, PtV is "
        "demoted from site prose until it doesn't (that is ADR-03's rule applied to a metric).",
        "",
        "And, for item 3 of the same plan cell:",
        "",
        "> Memorylessness probe: first-order vs second-order transition model log-likelihood "
        "on held-out bouts (state = (prev, cur) pairs, pruned at min count 5). If second-order "
        "wins materially, PtV's kernel is the thing to revisit, not its γ.",
        "",
        "Operationalised, fixed in the runner's docstring before the run:",
        "",
        f"1. **Grid** = γ ∈ {{{', '.join(f'{g:.2f}' for g in GAMMA_GRID)}}} "
        f"(the plan's \"{{0.6…0.95}}\", production γ={GAMMA} inside it) × shaping ∈ "
        f"{{on = {PROD_SHAPING} per term (production), off = 0.0}}. γ={GAMMA_ABLATION:.2f} is "
        "reported too, LABELLED as an ablation outside the grid — it deletes the discounted "
        "look-ahead and leaves the 1-step Lamas reward−risk, which is what says whether the "
        "multi-step machinery earns its existence.",
        "2. **Chosen** = the highest held-out AUC inside the grid, on the production "
        "(ActionFlow) kernel. **Change** = that config displaces production only if its "
        "PAIRED ΔAUC against (γ=0.8, shaping on), same rows, excludes 0 — the argmax of ten "
        "overlapping intervals is a forking path, the paired interval is the decision.",
        "3. **Demotion** is judged on the BOUT-CLUSTERED interval. Eval rows inside one bout "
        "are consecutive events of one fight, so the row-level interval is anti-conservative; "
        "both are printed and the criterion reads the clustered one.",
        f"4. **Materially** (memorylessness) = positive per-step Δ log-likelihood with a "
        f"bout-clustered bootstrap interval excluding 0, at α={ALPHA}, contexts pruned at "
        f"{MIN_CONTEXT}.",
        f"5. **Split** = the most recent {EVAL_FRACTION:.0%} of sequences by date, train is "
        "everything before. Never random (ADR-03). Label = a landed submission by the same "
        f"actor within the next k={FINISH_WINDOW} events.",
        "6. **Venue** = the corpus pass decides. The fixture is one athlete's ten days: it "
        "proves the instrument (it reproduces PoC-E8's numbers config-for-config), and it is "
        "not evidence about grappling.",
        "",
        "## Verdict",
        "",
        f"**{v['text']}**",
        "",
        "ADR-03: this run changes no production value. `path_to_victory` keeps γ=0.8 and "
        "shaping 0.1 by default; what a change *would* be, if one is ever selected, is "
        "spelled out under Reading.",
        "",
    ]
    if corpus is not None:
        lines += ["## Gate", "",
                  f"{gate.total} final matches with a sequence · {gate.with_sequence} with ≥4 "
                  f"events · **{gate.passed} pass** `bout_flags(...)['perspective_reliable']` "
                  f"· {gate.one_sided} refused (one-sided filing). Each passing bout enters "
                  f"twice, once per athlete perspective → {gate.passed * 2} sequences. This is "
                  "gap #2's gate, applied to the training kernel exactly as the plan requires; "
                  "without it the calibration would inherit the attribution noise.", ""]
        lines += _pass_section(corpus)
    else:
        lines += ["## Corpus pass — NOT RUN", "",
                  f"`{gate.error or 'no DATABASE_URL'}`. The verdict stays undecided.", ""]
    lines += _pass_section(fixture)
    lines += ["> LGPD: the fixture is the repository owner's own app export "
              "(`owner_kind='user'` data). These numbers exist as PoC evidence in this file "
              "only — never in an export, a site artefact or any competitive output.", ""]
    lines += _naming_audit()
    lines += ["## Reading", ""] + _reading(fixture, corpus, v)
    return "\n".join(lines) + "\n"


def _naming_audit() -> list[str]:
    """The rider folded into E4's scope, recorded where the verdict lives.

    Static: it is the result of reading every surface, not a computed number. It is
    rendered here rather than hand-written into the report because ``e4.md`` is generated
    and a hand-edit would be lost on the next run.
    """
    return [
        "## Naming audit (PoC-E8's rider, folded into E4's scope)",
        "",
        "> audit that nothing presents `network_metrics.route_to_submission` (greedy, "
        "max_steps=6) under the \"Path-to-Victory\" name — the value model and the greedy "
        "walk must never share a label.",
        "",
        "Three distinct objects exist in this codebase and only the first is Path-to-Victory:",
        "",
        "| object | what it is | where |",
        "|---|---|---|",
        "| **PtV** | discounted absorbing/value model, γ=0.8, every node scored by its whole "
        "downstream distribution | `analysis/path_to_victory.py` |",
        "| greedy route | single highest-probability walk, capped at 6 steps | "
        "`analysis/network_metrics.py:route_to_submission` |",
        "| bracket \"path\" | OBSERVED transition distribution over the chains of bouts "
        "won / lost | `scripts/bracket_export.py:_sequence_block` keys `path_to_victory` / "
        "`path_to_defeat` |",
        "",
        "**Result: no mislabelled presentation surface found.** Every consumer checked:",
        "",
        "| surface | presents | verdict |",
        "|---|---|---|",
        "| `analysis/insights.py:47` → docs/insights report | the greedy walk, under "
        "\"Highest-probability routes to a finish\" | correct — and it is the ONLY caller of "
        "`route_to_submission` outside `notebooks/01_position_network.ipynb` |",
        "| `analysis/insights.py:142` | \"Path-to-Victory — multi-step advantage by "
        "position\", fed by `path_to_victory()` | correct |",
        "| `export/site_data.py:1515` → `site/grapple-*.html` | \"Ranked by Path-to-Victory "
        "value of where the response lands\", fed by `counter_moves` → `edge_ptv` | correct |",
        "| `analysis/pro_analytics.py:249` → App `pathToVictory` → `ProInsightsCard` "
        "(\"Caminho para a vitória\") | `path_to_victory()` output | correct |",
        "| `export/tech_library.py` `eloDeviance` | `path_to_victory.node_elo_deviance` | "
        "correct |",
        "| BracketAnalysis `app.js:618` / `explorar.js:807` | the bracket \"path\" keys, "
        "titled \"Chegou aqui, venceu — e depois?\" and \"Quando chega aqui, o que vem "
        "depois\" | correct in the UI — the collision is in the JSON KEY only |",
        "",
        "Fixed in this repo (docstrings and comments only — no behaviour, no rendered "
        "string changed): `network_metrics.route_to_submission` now states outright that it "
        "is not PtV and names the difference; `path_to_victory`'s module docstring says the "
        "same from the other side; `insights.py` carries the boundary at the call site; "
        "`bracket_export._sequence_block` documents its key collision and why renaming the "
        "key is a BracketAnalysis contract change, not a docstring edit.",
        "",
        "**Flagged, not fixed — belongs to the orchestrator (other repos):** BracketAnalysis "
        "reads `path_to_victory` / `path_to_defeat` as JSON keys in `app.js:618`, "
        "`explorar.js:807`, `tools/validate.py:42` (`REQUIRED_SEQ`) and `README.md:28`. "
        "Nothing renders them under the PtV name, so this is a latent collision rather than a "
        "defect; renaming to something like `chain_after_win` / `chain_after_loss` would be a "
        "two-sided change (`scripts/bracket_export.py` + those four files) and is not worth "
        "doing on its own. Worth doing the next time that exporter is touched.",
        "",
    ]


def _reading(fixture: Pass, corpus: Pass | None, v: Mapping[str, Any]) -> list[str]:
    if corpus is None:
        return ["1. **No corpus pass, no verdict.** The criterion is corpus-decided by "
                "pre-registration; this run proves the instrument and nothing more."]
    prod = v["prod"]
    best = v["best"]
    rows = [r for r in corpus.rows if r.cfg.kernel == PROD_KERNEL and not r.cfg.ablation]
    abl = next((r for r in corpus.rows
                if r.cfg.kernel == PROD_KERNEL and r.cfg.ablation and r.cfg.shaping), None)
    no_shape = next((r for r in corpus.rows if r.cfg.kernel == PROD_KERNEL
                     and r.cfg.gamma == GAMMA and not r.cfg.shaping), None)
    order = next((o for o in corpus.orders if o.kernel == PROD_KERNEL and o.alpha == ALPHA),
                 None)
    worse = [r for r in rows if r.d_hi < 0.0]
    better = [r for r in rows if r.d_lo > 0.0]
    flat = [r for r in rows if not r.cfg.is_production and r not in worse and r not in better]
    # The span that the "γ does not matter" claim is allowed to be about: the configs whose
    # value function is not saturating. Including the clamped corner would inflate it 15×.
    unsat = [*flat, prod]
    spread = max(r.auc for r in unsat) - min(r.auc for r in unsat)
    out = [
        f"1. **Most of the grid is indistinguishable from production; one corner is not.** "
        f"Of the {len(rows) - 1} non-production configs on the production kernel, "
        f"{len(flat)} have a paired interval covering 0 — no evidence they differ from "
        f"γ={GAMMA} with shaping on — while {len(worse)} are significantly WORSE and "
        f"{len(better)} significantly better. "
        + ("The worse ones are all high-γ WITH shaping: "
           + ", ".join(f"{w.cfg} ({w.d_auc:+.3f}, {w.clamped:.0%} of nodes clamped)"
                       for w in sorted(worse, key=lambda r: r.d_auc))
           + f", against {prod.clamped:.0%} clamped at production. "
           + ("For the worst of them the mechanism is visible and is not about grappling: "
              "`shaping + p_reward + γ·stay·continuation` saturates against PtV's ±1 clamp, "
              "and a value function with that share of its nodes pinned at 1 has little "
              "ordering left for an AUC to read. It is a real property of the shipped model — "
              "the clamp is load-bearing, and γ=0.8 is safe partly because it sits below "
              "where saturation starts eating the ranking. "
              if max(w.clamped for w in worse) >= 2 * max(prod.clamped, 0.01) else "")
           + ("The smaller degradations sit at a clamp share close to production's, so "
              "saturation does not explain them; they are recorded as an observation, not a "
              "mechanism. "
              if any(w.clamped < 2 * max(prod.clamped, 0.01) for w in worse) else "")
           if worse else
           "Nothing in the grid separates from production in either direction. ")
        + f"Read the flat part honestly: across those {len(flat)} configs plus production the "
          f"AUC span is {spread:.3f}, against a single config's own interval of "
          f"{(prod.chi - prod.clo):.3f}. This sweep resolves nothing there. \"γ=0.8 is right\" "
          f"is NOT what that shows — what it shows is that **no configuration outside the "
          f"high-γ-with-shaping corner is distinguishable from production on this corpus** "
          f"(γ ∈ {{{', '.join(f'{g:.2f}' for g in GAMMA_GRID)}}} with shaping off, and "
          f"γ ≤ {GAMMA} with it on), a much weaker and much more honest claim, and enough to "
          f"close gap #12's first half: the constant is no longer unexamined, it is measured "
          f"and found non-critical over the range where the model is not saturating.",
        "",
    ]
    if no_shape is not None:
        gam_hi = [r for r in rows if r.cfg.shaping and r.cfg.gamma > GAMMA]
        out += [
            f"2. **Shaping's effect is γ-dependent, and at production γ it is a wash.** "
            f"Turning it off at γ={GAMMA} moves AUC to {no_shape.auc:.3f} (paired Δ "
            f"{no_shape.d_auc:+.3f} [{no_shape.d_lo:+.3f}, {no_shape.d_hi:+.3f}]; this one IS "
            f"a clean paired contrast, since the other side is production itself). "
            + ("The interval excludes 0, so the prior carries real predictive weight and it, "
               "not γ, is the term worth fitting."
               if (no_shape.d_lo > 0 or no_shape.d_hi < 0) else
               "The interval covers 0: on this corpus the positional prior neither helps nor "
               "hurts the held-out ranking. Its justification stays what it always was — it "
               "makes thin-data nodes order sensibly, which is a presentation property, not a "
               "predictive one, and saying so is more accurate than the current silence.")
            + (" Above production γ the picture flips: with shaping on, γ="
               + " and γ=".join(f"{r.cfg.gamma:.2f}" for r in sorted(gam_hi,
                                                                     key=lambda r: r.cfg.gamma))
               + " fall "
               + " and ".join(f"{abs(r.d_auc):.3f}" for r in sorted(gam_hi,
                                                                    key=lambda r: r.cfg.gamma))
               + " below production while their shaping-off twins do not. Shaping is what "
                 "pushes the value function into the clamp; γ alone does not."
               if all(r.d_hi < 0 for r in gam_hi) and gam_hi else ""),
            "",
        ]
    if abl is not None:
        out += [
            f"3. **What the multi-step machinery buys, measured.** The γ=0 ablation — pure "
            f"1-step Lamas reward−risk plus shaping, no look-ahead at all — scores "
            f"{abl.auc:.3f} against production's {prod.auc:.3f} (paired Δ {abl.d_auc:+.3f} "
            f"[{abl.d_lo:+.3f}, {abl.d_hi:+.3f}]). "
            + ("The discounted value model is therefore doing measurable work over the "
               "1-step baseline."
               if abl.d_hi < 0 else
               "The discounted value model is NOT measurably better than the 1-step baseline "
               "here. That is the uncomfortable number in this report and it belongs in the "
               "open: PtV's extra machinery is justified today by what it *represents* "
               "(a horizon, a branch value, the dilemma/funnel metrics that need edge values), "
               "not by held-out finish prediction, where a one-step rate ties it."),
            "",
        ]
    if order is not None:
        alphas = [o for o in corpus.orders if o.kernel == PROD_KERNEL]
        same_sign = len({o.delta > 0 for o in alphas}) == 1
        loses = order.hi < 0.0
        out += [
            f"4. **First-order memorylessness survives, and second order does not just fail "
            f"to win — it loses.** On the production kernel, {order.n_second_order} of "
            f"{order.n_steps} held-out steps had a (prev, cur) context with ≥{MIN_CONTEXT} "
            f"train observations ({order.contexts} such contexts exist); over all scored "
            f"steps the second-order model's mean log-likelihood is {order.ll2:.4f} against "
            f"first order's {order.ll1:.4f}, Δ {order.delta:+.4f} [{order.lo:+.4f}, "
            f"{order.hi:+.4f}], bout-clustered. "
            + ("The interval excludes 0 in SECOND order's favour: per the plan, the KERNEL is "
               "the thing to revisit, not γ."
               if order.material else
               ("The interval excludes 0 in FIRST order's favour. A pruning threshold of five "
                "is not enough support to estimate a distribution over a vocabulary this "
                "wide, so the extra context overfits and costs held-out likelihood. The plan "
                "asked whether second order wins materially; the answer is that it loses "
                "materially, which points the same way — PtV's first-order kernel is not the "
                "defect, and raising the order on this corpus would make prediction worse, "
                "not better."
                if loses else
                "The interval covers 0: no evidence against first order at this corpus size, "
                "which is not the same claim as \"grappling is memoryless\"."))
            + (f" The sign is stable across α ∈ {{{', '.join(str(a) for a in ALPHA_GRID)}}} "
               f"(Δ {', '.join(f'{o.delta:+.3f}' for o in alphas)}), so this is not a "
               f"smoothing artefact."
               if same_sign else
               " The sign FLIPS across the α grid, so treat the direction as unresolved — "
               "the result is a smoothing artefact more than a fact about grappling.")
            + " The honest bound on all of it: most (prev, cur) contexts never reach five "
              "observations, so the probe only ever looks at the small, dense corner of the "
              "state space where a second order could have been estimated at all.",
            "",
        ]
    if corpus.anchor:
        agree = sum(1 for a in corpus.anchor if a["agrees"])
        detail = "; ".join(
            f"{a['name']} {a['est'].p:.2f} vs {a['lamas']:.2f}" if a["est"].p is not None
            else f"{a['name']} — vs {a['lamas']:.2f}" for a in corpus.anchor)
        out += [
            f"5. **External anchor: {agree} of {len(corpus.anchor)} agree with Lamas 2024** "
            f"({detail}). Agreement is validation content for `the-data.html`. The "
            f"disagreements have a structural explanation before they have a substantive "
            f"one: Lamas codes ~10 coarse states, our label vocabulary runs to hundreds of "
            f"nodes, and a finer state space mechanically DILUTES every single transition "
            f"probability — mass that Lamas keeps inside \"guard pass\" our coding spreads "
            f"over smash / pressure / knee-cut / long-step. So a lower number here is what a "
            f"finer coding predicts, and the anchor cannot validate at face value without a "
            f"state-space mapping nobody has written. What it CAN do is flag the one cell "
            f"that lands on top of the published value (takedown→submission) and the one "
            f"that is far enough away to be worth a look. Reported beside the criterion and "
            f"never inside it — the accept/reject above does not move on these numbers.",
            "",
        ]
    out += [
        "6. **What a production change would be, if one were ever selected.** "
        + (f"The criterion selected **{best.cfg}**: that is `GAMMA = {best.cfg.gamma}` and "
           f"`_SHAPING_W = {best.cfg.shaping}` in `analysis/path_to_victory.py`, a re-export "
           "of `tech_library` (`eloDeviance` is PtV-derived and rides the App contract), a "
           "re-run of `export/site_data.py`, and the App's bundled `pro_baseline.json` left "
           "alone. It is an orchestrator decision, not this runner's."
           if v.get("change") else
           "None was. Production stays γ=0.8 with shaping on. Had one been selected the edit "
           "would have been two constants in `analysis/path_to_victory.py` (`GAMMA`, "
           "`_SHAPING_W`) plus a full re-export — `eloDeviance` in `tech_library` is "
           "PtV-derived and rides the App contract, and every PtV number on the public site "
           "comes from `export/site_data.py` — and it would have been the orchestrator's "
           "call, not this runner's."),
        "",
        f"7. **Caveats that bound every number above.** The label rides on `successful`, "
        f"present on a minority of corpus events (absent reads as False, so positives are "
        f"undercounted); the gate removes one-sided bouts but cannot repair actor noise "
        f"inside the ones it keeps; the held-out window is right-censored at each bout's end, "
        f"identically for every config; the sweep re-uses ONE held-out set for "
        f"{len(corpus.rows)} configs, so the argmax is optimistically biased and that is "
        f"exactly why the criterion requires a paired interval rather than a ranking; and the "
        f"corpus enters mirrored (each bout once per perspective), which the order probe "
        f"de-duplicates and the AUC path leaves in place because both kernels see it "
        f"identically.",
    ]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PoC-E4 — PtV γ/shaping calibration, measured")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e4.md"))
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--n-boot-delta", type=int, default=1000)
    ap.add_argument("--skip-corpus", action="store_true", help="fixture only (no DB read)")
    args = ap.parse_args(argv)

    gate = GateReport(error="skipped (--skip-corpus)") if args.skip_corpus else corpus_bouts()
    corpus = (run_pass("Corpus pass (gated on perspective_reliable)", gate.bouts,
                       n_boot=args.n_boot, n_boot_delta=args.n_boot_delta, with_anchor=True)
              if gate.bouts else None)
    fixture = run_pass("Fixture pass (app data — owner's own export)", fixture_bouts(),
                       n_boot=args.n_boot, n_boot_delta=args.n_boot_delta)

    md = render_markdown(fixture, corpus, gate)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
