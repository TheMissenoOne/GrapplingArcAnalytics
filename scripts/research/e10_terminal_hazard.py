"""PoC-E10 — does a time-inhomogeneous POINTS terminal beat the homogeneous chain?

    uv run python -m scripts.research.e10_terminal_hazard               # full run → doc
    uv run python -m scripts.research.e10_terminal_hazard --quick       # 200 boots, no readings
    uv run python -m scripts.research.e10_terminal_hazard --self-check  # no DB, pure asserts

**Read-only.** The only read is ``matches``, through ``analysis.poc.e9_markov.load_corpus`` —
the same gate as PoC-E9, not a restatement. No ``graphs`` query, so ``owner_kind`` cannot leak
by construction. No write, no replay, no export, no production file touched.

Pre-registration — the only place the criterion lives, frozen before any held-out number:
``docs/research/2026-09-14_e10_terminal_hazard_prereg.md``. The runner re-emits it verbatim
above its results into ``docs/research/2026-09-14_e10_terminal_hazard.md``.

What this tests. E9 arm 5 found the two cause-specific hazards CROSS OVER on the clock
(submission 3.4% vs points 1.6% in the first two minutes; points 66.7% vs submission 29.0%
past ten) while terminal type was NOT predictable from state history (T1 null). Production
``analysis/path_to_victory.py`` has one absorbing terminal (submission only, ``_terminal_rate``)
and no clock. This cell asks the single empirical question any clock-aware PtV rests on: does
elapsed time carry HELD-OUT information about which terminal a bout reaches, beyond a
time-homogeneous competing-risks chain?

Method. Discrete-time cause-specific hazards, piecewise constant (standard competing-risks
form; arXiv:2408.03602 for why an *estimated* cut needs its own penalty — this cell takes E9's
600 s bin boundary and estimates nothing). Evaluated by LANDMARKING (van Houwelingen 2007,
doi:10.1111/j.1467-9469.2006.00529.x; competing-risks form Nicolaie & van Houwelingen 2013),
because integrated from t=0 both models return a marginal and the contrast would be a null by
construction. Domain grounding: Lamas 2024 (doi:10.1177/17479541231210979) for the BJJ chain,
Sci Rep 2026 (s41598-026-52938-1) for the semi-Markov critique this is an instance of.

ponytail: two pieces, one cut, one grid. No proportional-hazards regression, no covariates, no
smoothing beyond Jeffreys — the smallest object that can falsify the premise. The rework itself
is not designed unless this passes; §8 of the prereg names where it would enter.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analysis.stats_rigor import bootstrap_ci, wilson

logger = logging.getLogger("e10")

REPO = Path(__file__).resolve().parents[2]
PREREG = REPO / "docs" / "research" / "2026-09-14_e10_terminal_hazard_prereg.md"
DOC = REPO / "docs" / "research" / "2026-09-14_e10_terminal_hazard.md"

# ── pre-registered constants (prereg §2, §3, §4) ────────────────────────────────
GRID = 30.0                     # seconds per discrete cell
HORIZON = 2400.0                # = MAX_SPAN, so grid and integrity filter agree
CUT = 600.0                     # E9's own bin boundary; not estimated, not tuned
MAX_SPAN = 2400.0               # ts-integrity ceiling: 2× ADCC's longest format
LANDMARKS: tuple[float, ...] = (120.0, 240.0, 360.0, 480.0, 600.0, 720.0)
CUT_SWEEP: tuple[float, ...] = (300.0, 420.0, 600.0, 780.0, 900.0)
N_BOOT = 5000
SEED = 20260820
PERM_SEED = 20260914
N_PERM = 200
EPS = 1e-12

POINTS = "points"
SUB = "submission"

E9_GATED = 429                  # what e9.md published; reported, never asserted (prereg §2)


# ── bouts ───────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TermBout:
    """One bout reduced to what a competing-risks chain needs: when it stopped, and why."""

    key: tuple[Any, ...]
    duration: float
    cause: str


@dataclass
class Prep:
    """Every bout the filters removed, counted. Nothing is defaulted."""

    gated: int = 0
    no_ts: int = 0
    no_terminal: int = 0
    draw: int = 0
    over_span: int = 0
    non_monotonic: int = 0
    kept: int = 0


def to_bouts(rows: Sequence[Any], max_span: float = MAX_SPAN,
             merge_draw: bool = False) -> tuple[list[TermBout], Prep]:
    """``e9_markov.BoutRow`` → ``TermBout``, applying prereg §2's filters in order."""
    prep = Prep(gated=len(rows))
    out: list[TermBout] = []
    for r in rows:
        elapsed = r.elapsed
        if elapsed is None:
            prep.no_ts += 1
            continue
        term = r.terminal
        if term is None:
            prep.no_terminal += 1
            continue
        if term == "END/draw":
            if not merge_draw:
                prep.draw += 1
                continue
            cause = POINTS
        else:
            cause = POINTS if term == "END/points" else SUB
        if any(b < a for a, b in zip(elapsed, elapsed[1:], strict=False)):
            prep.non_monotonic += 1
        duration = max(elapsed)
        if duration <= 0:
            prep.no_ts += 1
            continue
        if duration > max_span:
            prep.over_span += 1
            continue
        out.append(TermBout(key=r.key, duration=duration, cause=cause))
    prep.kept = len(out)
    return out, prep


# ── the two models (prereg §3) ──────────────────────────────────────────────────
def piece_of(cell: int, cuts: Sequence[float], grid: float = GRID) -> int:
    """Which piece a grid cell belongs to. Cells are assigned by their START time."""
    t = cell * grid
    idx = 0
    for c in cuts:
        if t < c:
            break
        idx += 1
    return idx


@dataclass(frozen=True)
class Hazards:
    """Piecewise-constant discrete-time cause-specific hazards, ``(h_points, h_sub)`` per piece.

    ``cuts=()`` is M_hom — one piece, hazards constant for all time. Registered identity
    (prereg §3, asserted by ``tests/test_research_e10.py``): with one piece
    ``p_points(t) == h_p/(h_p+h_s)`` for every ``t``, i.e. exactly the train marginal over
    terminal types. Any time-homogeneous competing-risks chain IS the train marginal here.
    """

    cuts: tuple[float, ...]
    h: tuple[tuple[float, float], ...]
    grid: float = GRID

    def at(self, cell: int) -> tuple[float, float]:
        return self.h[piece_of(cell, self.cuts, self.grid)]


def fit_hazards(bouts: Sequence[TermBout], cuts: Sequence[float] = (CUT,),
                grid: float = GRID) -> Hazards:
    """ĥ_τ(piece) = (events of cause τ + ½) / (at-risk cell-observations + 1).

    Jeffreys smoothing: a guard against an empty cell, not a fit — every piece on this corpus
    carries thousands of cell-observations.
    """
    n = len(cuts) + 1
    ev_p = [0.0] * n
    ev_s = [0.0] * n
    risk = [0.0] * n
    for b in bouts:
        last = int(b.duration // grid)
        for j in range(last + 1):
            risk[piece_of(j, cuts, grid)] += 1.0
        p = piece_of(last, cuts, grid)
        if b.cause == POINTS:
            ev_p[p] += 1.0
        else:
            ev_s[p] += 1.0
    return Hazards(
        cuts=tuple(cuts),
        h=tuple(((ev_p[i] + 0.5) / (risk[i] + 1.0), (ev_s[i] + 0.5) / (risk[i] + 1.0))
                for i in range(n)),
        grid=grid,
    )


def p_points(hz: Hazards, t: float, horizon: float = HORIZON) -> float:
    """P(terminal = points | bout still running at ``t``) by forward recursion on the grid.

    Mass surviving past the horizon is renormalised proportionally across the two causes —
    the only horizon convention, identical for both models.
    """
    j0 = int(round(t / hz.grid))
    n_cells = int(round(horizon / hz.grid))
    surv, mass_p, mass_s = 1.0, 0.0, 0.0
    for j in range(j0, n_cells):
        hp, hs = hz.at(j)
        mass_p += surv * hp
        mass_s += surv * hs
        surv *= max(0.0, 1.0 - hp - hs)
    total = mass_p + mass_s
    return mass_p / total if total > 0 else 0.5


def duration_loglik(hz: Hazards, b: TermBout, horizon: float = HORIZON) -> float:
    """log P(bout stops in its observed cell, of its observed cause) — the SECONDARY target.

    Non-decisive by pre-registration (prereg §6.4/§7.2): the quantity is the time of the last
    RECORDED event, not the final bell, and the open-ended tail inflates it.
    """
    n_cells = int(round(horizon / hz.grid))
    j_star = min(int(b.duration // hz.grid), n_cells - 1)
    total = 0.0
    for j in range(j_star):
        hp, hs = hz.at(j)
        total += math.log(max(EPS, 1.0 - hp - hs))
    hp, hs = hz.at(j_star)
    return total + math.log(max(EPS, hp if b.cause == POINTS else hs))


# ── primary criterion (prereg §4) ───────────────────────────────────────────────
def landmark_probs(hz: Hazards, landmarks: Sequence[float] = LANDMARKS,
                   horizon: float = HORIZON) -> dict[float, float]:
    """``P(points | T > t)`` per landmark. It depends on the model and ``t`` only, never on the
    bout — computing it once per model is what keeps the permutation control affordable."""
    return {t: p_points(hz, t, horizon) for t in landmarks}


def bout_losses(hz: Hazards, b: TermBout, landmarks: Sequence[float] = LANDMARKS,
                horizon: float = HORIZON) -> tuple[float, float, int] | None:
    """(mean log-loss, mean Brier, n contributing landmarks) — ONE value per bout, or None."""
    return losses_from(landmark_probs(hz, landmarks, horizon), b)


def losses_from(probs: dict[float, float], b: TermBout) -> tuple[float, float, int] | None:
    live = [t for t in sorted(probs) if b.duration > t]
    if not live:
        return None
    y = 1.0 if b.cause == POINTS else 0.0
    ll, br = 0.0, 0.0
    for t in live:
        p = min(max(probs[t], EPS), 1.0 - EPS)
        ll += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
        br += (p - y) ** 2
    n = len(live)
    return ll / n, br / n, n


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


@dataclass
class LandmarkRow:
    t: float
    n: int
    obs_points: int
    p_hom: float
    p_piece: float
    ll_hom: float
    ll_piece: float


@dataclass
class Primary:
    n_train: int
    n_eval: int
    n_no_landmark: int
    hom: Hazards
    piece: Hazards
    ll_hom: float
    ll_piece: float
    br_hom: float
    br_piece: float
    d_ll: tuple[float, float, float]
    d_br: tuple[float, float, float]
    perm: Perm
    landmarks: list[LandmarkRow] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        """Prereg §4 (+ amendment A1): all four conditions, and nothing else decides it."""
        obs, lo, hi = self.d_ll
        return obs > 0.0 and lo > 0.0 and hi > lo and self.perm.passes


def _deltas(train: Sequence[TermBout], held: Sequence[TermBout], cuts: Sequence[float],
            landmarks: Sequence[float], horizon: float,
            grid: float) -> tuple[Hazards, Hazards, list[TermBout],
                                  list[float], list[float], list[float], list[float]]:
    hom = fit_hazards(train, cuts=(), grid=grid)
    piece = fit_hazards(train, cuts=cuts, grid=grid)
    ph = landmark_probs(hom, landmarks, horizon)
    pp = landmark_probs(piece, landmarks, horizon)
    used: list[TermBout] = []
    llh: list[float] = []
    llp: list[float] = []
    brh: list[float] = []
    brp: list[float] = []
    for b in held:
        a = losses_from(ph, b)
        c = losses_from(pp, b)
        if a is None or c is None:
            continue
        used.append(b)
        llh.append(a[0])
        brh.append(a[1])
        llp.append(c[0])
        brp.append(c[1])
    return hom, piece, used, llh, llp, brh, brp


def run_primary(train: Sequence[TermBout], held: Sequence[TermBout],
                cuts: Sequence[float] = (CUT,), landmarks: Sequence[float] = LANDMARKS,
                horizon: float = HORIZON, grid: float = GRID,
                n_boot: int = N_BOOT, n_perm: int = N_PERM) -> Primary:
    hom, piece, used, llh, llp, brh, brp = _deltas(
        train, held, cuts, landmarks, horizon, grid)
    d_ll = [a - b for a, b in zip(llh, llp, strict=True)]
    d_br = [a - b for a, b in zip(brh, brp, strict=True)]

    rows: list[LandmarkRow] = []
    for t in landmarks:
        live = [b for b in used if b.duration > t]
        if not live:
            continue
        ph, pp = p_points(hom, t, horizon), p_points(piece, t, horizon)
        rows.append(LandmarkRow(
            t=t, n=len(live), obs_points=sum(1 for b in live if b.cause == POINTS),
            p_hom=ph, p_piece=pp,
            ll_hom=_mean([_nll(ph, b) for b in live]),
            ll_piece=_mean([_nll(pp, b) for b in live]),
        ))

    ci_ll = bootstrap_ci(d_ll, _mean, n_boot=n_boot, seed=SEED)
    return Primary(
        n_train=len(train), n_eval=len(used), n_no_landmark=len(held) - len(used),
        hom=hom, piece=piece,
        ll_hom=_mean(llh), ll_piece=_mean(llp), br_hom=_mean(brh), br_piece=_mean(brp),
        d_ll=ci_ll,
        d_br=bootstrap_ci(d_br, _mean, n_boot=n_boot, seed=SEED),
        perm=permutation_null(train, used, ci_ll[0], cuts, landmarks, horizon, grid, n_perm),
        landmarks=rows,
    )


def _nll(p: float, b: TermBout) -> float:
    q = min(max(p, EPS), 1.0 - EPS)
    return -math.log(q) if b.cause == POINTS else -math.log(1.0 - q)


@dataclass(frozen=True)
class Perm:
    """The §5 null control. ``p`` is the criterion; the distribution is reported beside it."""

    p: float
    mean: float
    lo: float
    hi: float
    n_draws: int

    @property
    def passes(self) -> bool:
        return self.n_draws > 0 and self.p <= 0.05


def permutation_null(train: Sequence[TermBout], held: Sequence[TermBout], observed: float,
                     cuts: Sequence[float], landmarks: Sequence[float], horizon: float,
                     grid: float, n_perm: int = N_PERM) -> Perm:
    """Prereg §5 + amendment A1 — a PASS CONDITION, not a reading.

    Terminal labels are permuted JOINTLY over train and eval and both models are RE-FITTED on
    the permuted train, so the null is "if terminal type were unrelated to duration, could this
    harness still produce a positive Δ?" — the question the control is actually for. Permuting
    eval only was the first design and is wrong: it asks a well-fitted model to predict random
    labels, which it must lose, so Δ_perm is strongly negative and proves nothing (A1).

    ``p = (1 + #{Δ_perm ≥ Δ_obs}) / (1 + n_draws)``, one-sided.
    """
    if n_perm <= 0 or not held or not train:
        return Perm(float("nan"), float("nan"), float("nan"), float("nan"), 0)
    rng = random.Random(PERM_SEED)
    n_tr = len(train)
    causes = [b.cause for b in (*train, *held)]
    means: list[float] = []
    for _ in range(n_perm):
        rng.shuffle(causes)
        shuffled = [TermBout(b.key, b.duration, c)
                    for b, c in zip((*train, *held), causes, strict=True)]
        _, _, _, llh, llp, _, _ = _deltas(shuffled[:n_tr], shuffled[n_tr:], cuts,
                                          landmarks, horizon, grid)
        means.append(_mean([a - b for a, b in zip(llh, llp, strict=True)]))
    ge = sum(1 for m in means if m >= observed)
    means.sort()
    return Perm(
        p=(1 + ge) / (1 + len(means)),
        mean=_mean(means),
        lo=means[max(0, int(0.025 * len(means)))],
        hi=means[min(len(means) - 1, int(0.975 * len(means)))],
        n_draws=len(means),
    )


# ── post-hoc diagnostics (never a criterion — added after the FAIL, labelled as such) ──
@dataclass(frozen=True)
class Shift:
    """Train-vs-eval composition. Post-hoc and purely descriptive: it explains a verdict, it
    never contributes to one."""

    n_train: int
    n_eval: int
    points_train: float
    points_eval: float
    median_train: float
    median_eval: float


def composition(train: Sequence[TermBout], held: Sequence[TermBout]) -> Shift:
    def rate(xs: Sequence[TermBout]) -> float:
        return sum(1.0 for b in xs if b.cause == POINTS) / len(xs) if xs else float("nan")

    def med(xs: Sequence[TermBout]) -> float:
        d = sorted(b.duration for b in xs)
        return d[len(d) // 2] if d else float("nan")

    return Shift(len(train), len(held), rate(train), rate(held), med(train), med(held))


def _shifted(probs: dict[float, float], delta: float) -> dict[float, float]:
    return {t: 1.0 / (1.0 + math.exp(-(math.log(p / (1.0 - p)) + delta)))
            for t, p in probs.items()}


def _mean_prob(probs: dict[float, float], used: Sequence[TermBout]) -> float:
    return _mean([_mean([probs[t] for t in sorted(probs) if b.duration > t]) for b in used])


def oracle_intercept_delta(train: Sequence[TermBout], held: Sequence[TermBout],
                           cuts: Sequence[float] = (CUT,),
                           landmarks: Sequence[float] = LANDMARKS, horizon: float = HORIZON,
                           grid: float = GRID,
                           n_boot: int = N_BOOT) -> tuple[float, float, float]:
    """**ORACLE. NEVER A CRITERION.** Each model's LEVEL is moved to the held-out base rate by a
    single logit intercept fitted on the eval labels; only then are the two compared.

    It exists to separate two readings a bare FAIL cannot tell apart — "the clock carries
    nothing" versus "the clock's SHAPE is right and the model's LEVEL moved" — because those
    two close the backlog cell in opposite ways. It uses held-out labels and is therefore
    unusable as evidence for anything shipping.
    """
    hom = fit_hazards(train, cuts=(), grid=grid)
    piece = fit_hazards(train, cuts=cuts, grid=grid)
    used = [b for b in held if any(b.duration > t for t in landmarks)]
    if not used:
        return (float("nan"),) * 3
    target = sum(1.0 for b in used if b.cause == POINTS) / len(used)

    def fit(probs: dict[float, float]) -> dict[float, float]:
        lo, hi = -12.0, 12.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if _mean_prob(_shifted(probs, mid), used) < target:
                lo = mid
            else:
                hi = mid
        return _shifted(probs, (lo + hi) / 2)

    ph = fit(landmark_probs(hom, landmarks, horizon))
    pp = fit(landmark_probs(piece, landmarks, horizon))
    deltas: list[float] = []
    for b in used:
        a, c = losses_from(ph, b), losses_from(pp, b)
        if a is not None and c is not None:
            deltas.append(a[0] - c[0])
    return bootstrap_ci(deltas, _mean, n_boot=n_boot, seed=SEED)


def run_secondary(train: Sequence[TermBout], held: Sequence[TermBout],
                  cuts: Sequence[float] = (CUT,), horizon: float = HORIZON,
                  grid: float = GRID,
                  n_boot: int = N_BOOT) -> tuple[float, float, tuple[float, float, float]]:
    """Held-out time-to-terminal log-likelihood, M_piece − M_hom. NON-DECISIVE (prereg §6.4)."""
    hom = fit_hazards(train, cuts=(), grid=grid)
    piece = fit_hazards(train, cuts=cuts, grid=grid)
    lh = [duration_loglik(hom, b, horizon) for b in held]
    lp = [duration_loglik(piece, b, horizon) for b in held]
    return (_mean(lh), _mean(lp),
            bootstrap_ci([a - b for a, b in zip(lp, lh, strict=True)], _mean,
                         n_boot=n_boot, seed=SEED))


# ── run ─────────────────────────────────────────────────────────────────────────
@dataclass
class Variant:
    name: str
    prep: Prep
    primary: Primary


@dataclass
class Run:
    gate_note: str
    prep: Prep
    primary: Primary
    secondary: tuple[float, float, tuple[float, float, float]]
    shift: Shift
    oracle: tuple[float, float, float]
    sweep: list[tuple[float, tuple[float, float, float]]] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)


def _split(bouts: Sequence[TermBout], train_keys: set[Any]
           ) -> tuple[list[TermBout], list[TermBout]]:
    return ([b for b in bouts if b.key in train_keys],
            [b for b in bouts if b.key not in train_keys])


def build_run(n_boot: int = N_BOOT, n_perm: int = N_PERM, readings: bool = True) -> Run | None:
    from analysis.poc.e9_markov import load_corpus, split_rows

    gate = load_corpus()
    if gate.error:
        logger.error("corpus read failed: %s", gate.error)
        return None
    rows_train, rows_eval = split_rows(gate.rows)
    train_keys = {r.key for r in rows_train}
    note = (f"{gate.total} matches / {gate.passed} gated "
            f"({gate.one_sided} one-sided dropped) — e9.md published {E9_GATED} gated; "
            f"split {len(rows_train)} train / {len(rows_eval)} eval bouts")

    bouts, prep = to_bouts(gate.rows)
    tr, ev = _split(bouts, train_keys)
    run = Run(
        gate_note=note, prep=prep,
        primary=run_primary(tr, ev, n_boot=n_boot, n_perm=n_perm),
        secondary=run_secondary(tr, ev, n_boot=n_boot),
        shift=composition(tr, ev),
        oracle=oracle_intercept_delta(tr, ev, n_boot=n_boot),
    )
    if not readings:
        return run

    for cut in CUT_SWEEP:
        _, _, _, llh, llp, _, _ = _deltas(tr, ev, (cut,), LANDMARKS, HORIZON, GRID)
        run.sweep.append((cut, bootstrap_ci([a - b for a, b in zip(llh, llp, strict=True)],
                                            _mean, n_boot=n_boot, seed=SEED)))

    for name, vb, vp in (
        ("draw merged into points", *to_bouts(gate.rows, merge_draw=True)),
        ("no ts-integrity cap", *to_bouts(gate.rows, max_span=float("inf"))),
    ):
        vtr, vev = _split(vb, train_keys)
        horizon = max(HORIZON, max((b.duration for b in vb), default=HORIZON) + GRID)
        run.variants.append(Variant(name, vp, run_primary(
            vtr, vev, horizon=horizon, n_boot=n_boot, n_perm=n_perm)))
    return run


# ── report ──────────────────────────────────────────────────────────────────────
def _ci(d: tuple[float, float, float]) -> str:
    obs, lo, hi = d
    return f"{obs:+.4f} [{lo:+.4f}, {hi:+.4f}]"


def _haz(hz: Hazards) -> str:
    parts = []
    bounds = [0.0, *hz.cuts, float("inf")]
    for i, (hp, hs) in enumerate(hz.h):
        lo, hi = bounds[i], bounds[i + 1]
        span = f"{lo:.0f}–{'∞' if math.isinf(hi) else f'{hi:.0f}'}s"
        parts.append(f"{span}: points {hp:.4f}/cell, sub {hs:.4f}/cell")
    return " · ".join(parts)


def _primary_block(p: Primary) -> list[str]:
    out = [
        f"* Fitted hazards — **M_hom** {_haz(p.hom)}",
        f"* Fitted hazards — **M_piece** {_haz(p.piece)}",
        f"* {p.n_train} train bouts · {p.n_eval} eval bouts scored "
        f"({p.n_no_landmark} eval bouts dropped: no landmark survived)",
        "",
        "| score | M_hom | M_piece | paired Δ (hom − piece), 95% bootstrap over bouts |",
        "|---|---|---|---|",
        f"| log-loss | {p.ll_hom:.4f} | {p.ll_piece:.4f} | **{_ci(p.d_ll)}** |",
        f"| Brier | {p.br_hom:.4f} | {p.br_piece:.4f} | {_ci(p.d_br)} |",
        "",
        f"* Permutation null (prereg §5 + A1 — labels shuffled jointly over train+eval, both "
        f"models re-fitted, {p.perm.n_draws} draws): Δ_perm mean {p.perm.mean:+.4f} "
        f"[{p.perm.lo:+.4f}, {p.perm.hi:+.4f}], one-sided **p = {p.perm.p:.4f}** — "
        f"{'passes (≤ 0.05) ✔' if p.perm.passes else 'FAILS (> 0.05) ✘'}",
        "",
        "| landmark | eval bouts still running | observed P(points) | M_hom p | M_piece p | "
        "M_hom log-loss | M_piece log-loss |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in p.landmarks:
        e = wilson(r.obs_points, r.n)
        obs = (f"{100 * e.p:.1f}% [{100 * (e.lo or 0):.1f}%, {100 * (e.hi or 0):.1f}%]"
               if e.p is not None else "—")
        out.append(f"| {r.t:.0f}s | {r.n} | {obs} | {r.p_hom:.3f} | {r.p_piece:.3f} | "
                   f"{r.ll_hom:.4f} | {r.ll_piece:.4f} |")
    return out


def _reading(run: Run) -> list[str]:
    """The synthesis, written from the numbers above — mechanism and honest bounds.

    ponytail: the prose is interpolated but its *shape* was written against a FAIL whose
    mechanism is the base-rate shift below. A re-run that flips the verdict needs this function
    re-read, not just re-run — the same ceiling ``e9_markov._reading`` carries.
    """
    p = run.primary
    late = [r for r in p.landmarks if r.t >= CUT] or p.landmarks[-1:]
    late_wins = sum(1 for r in late if r.ll_piece < r.ll_hom)
    early = [r for r in p.landmarks if r.t < CUT] or p.landmarks[:1]
    sweep_all_negative = all(d[2] < 0 for _, d in run.sweep) if run.sweep else False
    return [
        f"1. **The pre-registered criterion FAILS, and it fails in the wrong direction.** The "
        f"clock-aware chain is not merely indistinguishable from the homogeneous one — it is "
        f"significantly WORSE on held-out terminal type, Δ (hom − piece) {_ci(p.d_ll)} over "
        f"{p.n_eval} eval bouts, with Brier agreeing ({_ci(p.d_br)}). Nothing about a "
        f"pre-registration lets that be read as a null.",
        "",
        f"2. **The harness is not what lost it.** Permuting terminal labels jointly over train "
        f"and eval and re-fitting both models returns Δ_perm {p.perm.mean:+.4f} "
        f"[{p.perm.lo:+.4f}, {p.perm.hi:+.4f}] — centred on zero, exactly as a control should "
        f"be. The extra pair of parameters buys nothing on noise, so the observed loss is the "
        f"model's, not the design's. The one-sided p = {p.perm.p:.4f} is that same fact "
        f"restated — the observed Δ sits below the whole permutation distribution — and it is "
        f"the §4 condition failing, not an independent piece of evidence.",
        "",
        f"3. **The mechanism is a base-rate shift, and it is enormous.** The most recent "
        f"quarter of the corpus is a different population: P(points) falls from "
        f"{100 * run.shift.points_train:.1f}% in train to {100 * run.shift.points_eval:.1f}% "
        f"in eval and the median bout shortens from {run.shift.median_train:.0f}s to "
        f"{run.shift.median_eval:.0f}s. Both models inherit the train base rate, so both "
        f"predict ≈{early[0].p_hom:.2f} points at the first landmark against an observed "
        f"{100 * early[0].obs_points / early[0].n:.1f}%. M_piece is wrong in the SAME "
        f"direction and by MORE at the early landmarks (its late-piece points hazard pulls "
        f"every early prediction up), and the early landmarks carry nearly all the eval mass "
        f"— {early[0].n} bouts at {early[0].t:.0f}s against {p.landmarks[-1].n} at "
        f"{p.landmarks[-1].t:.0f}s. That arithmetic is the whole verdict.",
        "",
        f"4. **The clock's SHAPE is right; its LEVEL is not transportable — and that "
        f"distinction is the finding.** At the landmarks at or past the pre-registered cut, "
        f"M_piece BEATS M_hom in {late_wins} of {len(late)} — the observed eval rate jumps to "
        f"{100 * late[-1].obs_points / late[-1].n:.1f}% at {late[-1].t:.0f}s and only M_piece "
        f"moves with it. The oracle-intercept diagnostic makes it explicit: once each model's "
        f"level is moved onto the held-out base rate, the piecewise model WINS, "
        f"{_ci(run.oracle)}. So E9's crossover is real and it survives a temporal holdout — "
        f"but it is worth ~{abs(run.oracle[0]):.3f} nats against a base-rate error of "
        f"~{abs(p.d_ll[0]):.3f}, an order of magnitude smaller than the thing that would ship "
        f"alongside it. A production constant fitted on this corpus would import the level "
        f"too, and the level moved inside this very corpus.",
        "",
        f"5. **No cut rescues it, which rules out the obvious rebuttal.** The sweep over "
        f"{', '.join(f'{c:.0f}s' for c, _ in run.sweep)} is "
        f"{'negative everywhere' if sweep_all_negative else 'mixed'}"
        f"{'' if not run.sweep else f', the pre-registered 600s at {_ci(dict(run.sweep)[CUT])}'}"
        f". The verdict is not an artefact of where the boundary was placed, and E9's own bin "
        f"edge is not a bad choice — there is no good one while the level is wrong.",
        "",
        "6. **The sensitivities agree, which is the least interesting and most necessary "
        "line here.** " + " · ".join(
            f"{v.name}: {_ci(v.primary.d_ll)}" for v in run.variants) +
        (". Merging draws into points moves the train base rate up and the loss down a little, "
         "and changes nothing about the sign. Dropping the ts-integrity cap makes it slightly "
         "worse — the twelve multi-hour bouts are timestamp defects and letting them set the "
         "late-piece hazard is not a more honest run, it is a noisier one." if run.variants
         else ""),
        "",
        f"7. **What bounds all of it.** {p.n_eval} eval bouts is thin and every interval says "
        f"so. The clock is `ts − min(ts)` over RECORDED events, so a bout's 'duration' is a "
        f"lower bound on its real length — prereg §7.1 registered that this can inflate the "
        f"effect's magnitude and shift the crossover, and the measured effect came in SMALLER "
        f"than the base-rate noise anyway, so the artefact was never the binding problem. "
        f"The secondary time-to-terminal limb ({_ci(run.secondary[2])}) sits on that same "
        f"artefact and decides nothing, as registered. And the corpus is no longer E9's: "
        f"{run.gate_note}.",
        "",
        "8. **What the backlog cell becomes.** Not 'add a time-dependent points terminal to "
        "`path_to_victory`' — that is refused. The measurement relocates the problem: a "
        "single global terminal base rate does not hold across this corpus' own event mix, "
        "so the next question is whether the hazard CONDITIONED on something a bout knows "
        "about itself (ruleset family, division, competition format — `analysis/"
        "ruleset_scoring.family_of` already exists) transports where a global one does not. "
        "E9 arm 4 already measured the tension that question runs into: a specialised ADCC "
        "kernel diverged from the global one in structure and still LOST to it on sample "
        "size. Any successor cell has to clear that bar first, and it is a different cell.",
    ]


def render(run: Run) -> str:
    prereg = PREREG.read_text(encoding="utf-8") if PREREG.exists() else "(prereg file missing)"
    p = run.primary
    verdict = "**PASS**" if p.passes else "**FAIL**"
    lines = [
        "# PoC-E10 — a time-inhomogeneous POINTS terminal",
        "",
        "Generated by `uv run python -m scripts.research.e10_terminal_hazard` — "
        "**do not hand-edit**. Module: `scripts/research/e10_terminal_hazard.py`; "
        "test: `tests/test_research_e10.py`; pre-registration: "
        "`docs/research/2026-09-14_e10_terminal_hazard_prereg.md` (re-emitted verbatim below).",
        "",
        "## Verdict",
        "",
        f"- **terminal hazard** — {verdict}. Paired held-out log-loss Δ (M_hom − M_piece) "
        f"{_ci(p.d_ll)} over {p.n_eval} eval bouts; permutation null p = {p.perm.p:.4f} "
        f"(Δ_perm mean {p.perm.mean:+.4f}).",
        "",
        f"**Corpus:** {run.gate_note}.",
        "",
        "---",
        "",
        prereg.strip(),
        "",
        "---",
        "",
        "## Results",
        "",
        "### Corpus preparation (every drop counted)",
        "",
        "| step | bouts |",
        "|---|---|",
        f"| gated by `e9_markov.load_corpus` | {run.prep.gated} |",
        f"| dropped — any event missing `ts` (never defaulted) | {run.prep.no_ts} |",
        f"| dropped — `win_type` NULL | {run.prep.no_terminal} |",
        f"| dropped — `END/draw` (primary is submission vs points) | {run.prep.draw} |",
        f"| dropped — span > {MAX_SPAN:.0f}s, ts-integrity | {run.prep.over_span} |",
        f"| **kept** | **{run.prep.kept}** |",
        "",
        f"Non-monotonic `ts` inside a bout: {run.prep.non_monotonic} bouts "
        "(duration is `max(elapsed)`, so event order does not enter it).",
        "",
        "### Primary — held-out terminal type at landmarks",
        "",
        *_primary_block(p),
        "",
        "### Post-hoc diagnostic — why (NOT a criterion, added after the verdict)",
        "",
        "Neither model is calibrated on the held-out window, and the reason is not the clock:",
        "",
        "| | train | eval (most recent 25%) |",
        "|---|---|---|",
        f"| bouts | {run.shift.n_train} | {run.shift.n_eval} |",
        f"| P(terminal = points) | {100 * run.shift.points_train:.1f}% | "
        f"{100 * run.shift.points_eval:.1f}% |",
        f"| median duration | {run.shift.median_train:.0f}s | {run.shift.median_eval:.0f}s |",
        "",
        "Both models inherit the TRAIN base rate, so both are wrong by roughly that gap at "
        "every landmark. **Oracle-intercept diagnostic** — each model's level moved onto the "
        "held-out base rate by a single logit intercept fitted on the eval labels, then "
        f"compared: Δ log-loss (hom − piece) {_ci(run.oracle)}. It uses held-out labels, it is "
        "unusable as evidence for anything shipping, and it exists only to separate \"the "
        "clock carries nothing\" from \"the clock's shape is right and the level moved\".",
        "",
        "### Secondary — time-to-terminal (NON-DECISIVE, prereg §6.4/§7.2)",
        "",
        f"Per-bout held-out log-likelihood of `(duration, cause)`: M_hom "
        f"{run.secondary[0]:.4f}, M_piece {run.secondary[1]:.4f}, paired Δ (piece − hom) "
        f"{_ci(run.secondary[2])}. Its target is the last RECORDED event, not the bell — "
        "it decides nothing.",
        "",
    ]
    if run.sweep:
        lines += [
            "### Reading — cut sweep (prereg §6.1)",
            "",
            "| cut | paired Δ log-loss (hom − piece) |",
            "|---|---|",
            *[f"| {c:.0f}s{' ← pre-registered' if c == CUT else ''} | {_ci(d)} |"
              for c, d in run.sweep],
            "",
        ]
    for v in run.variants:
        lines += [
            f"### Reading — sensitivity: {v.name}",
            "",
            f"{v.prep.kept} bouts kept ({v.prep.draw} draws dropped, "
            f"{v.prep.over_span} over-span dropped).",
            "",
            *_primary_block(v.primary),
            "",
        ]
    lines += ["## Reading", ""] + _reading(run) + [
        "",
        "## What changes in production",
        "",
        ("**Nothing** — the criterion was not met. `analysis/path_to_victory.py` keeps its "
         "single submission terminal and no clock; E9's crossover stays a descriptive finding "
         "and the backlog cell closes."
         if not p.passes else
         "The premise holds, and prereg §8 is the licence — nothing wider. The hazard would "
         "enter `analysis/path_to_victory.py` as one keyword, `points_hazard: float = 0.0`, "
         "folded into the existing `stay` term "
         "(`stay = max(0, 1 − p_r − p_k − p_t − points_hazard)`), with production calling "
         "`path_to_victory` twice — early (ĥ_p1) and late (ĥ_p2). The points terminal absorbs "
         "with **value 0**, never a signed reward: PtV is one-sided and carries no score "
         "state, so a sign would be a score model smuggled in as a constant. Downstream that "
         "moves `analysis/counter_moves.py` (`edge_ptv`), `path_to_victory.dilemmas` and "
         "`analysis/systems.py` → `export/site_data.py:_forks_block` (the dossier's dilemmas / "
         "counter-moves / defense block) and `export/ontology.py` + `export/narrative.py` — "
         "i.e. a full site regeneration and a `GrapplingArc` commit. Neither the re-fit on the "
         "whole corpus nor the regeneration is a subagent's to run."),
        "",
    ]
    return "\n".join(lines) + "\n"


# ── self-check (no DB) ──────────────────────────────────────────────────────────
def self_check() -> int:
    """Asserts the properties the criterion rests on, on data with a known answer."""
    # piece_of: cells are assigned by START time, boundary cell belongs to the LATE piece.
    assert piece_of(19, (600.0,)) == 0 and piece_of(20, (600.0,)) == 1

    # M_hom is the train marginal, at EVERY landmark (prereg §3's registered identity).
    rng = random.Random(7)
    train = [TermBout((i,), rng.uniform(60, 1200), POINTS if i % 3 else SUB)
             for i in range(300)]
    hom = fit_hazards(train, cuts=())
    hp, hs = hom.h[0]
    marginal = hp / (hp + hs)
    for t in LANDMARKS:
        assert abs(p_points(hom, t) - marginal) < 1e-9, (t, p_points(hom, t), marginal)

    # A corpus where the clock IS the terminal: short → submission, long → points.
    planted = ([TermBout(("s", i), 60.0 + 1.0 * i, SUB) for i in range(240)]
               + [TermBout(("p", i), 700.0 + 3.0 * i, POINTS) for i in range(120)])
    pc = fit_hazards(planted, cuts=(CUT,))
    assert p_points(pc, 120.0) < 0.5 < p_points(pc, 720.0), (
        p_points(pc, 120.0), p_points(pc, 720.0))
    d = run_primary(planted, planted, n_boot=300, n_perm=50)
    assert d.d_ll[0] > 0 and d.d_ll[1] > 0, d.d_ll
    assert d.perm.p <= 0.05, (d.perm.p, d.perm.mean)
    assert d.passes

    # A corpus where the clock carries NOTHING: the criterion must refuse it.
    flat = [TermBout((i,), 90.0 + 5.0 * i, POINTS if i % 2 else SUB) for i in range(200)]
    n = run_primary(flat, flat, n_boot=300, n_perm=50)
    assert not n.passes, (n.d_ll, n.perm)

    # A degenerate (zero-width) interval is never a win — E9 amendment 1.
    deg = Primary(0, 1, 0, hom, hom, 0.0, 0.0, 0.0, 0.0,
                  (0.005, 0.005, 0.005), (0.0, 0.0, 0.0),
                  Perm(p=0.001, mean=0.0, lo=-0.01, hi=0.01, n_draws=50))
    assert not deg.passes

    print("self-check OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="PoC-E10 — time-inhomogeneous points terminal")
    ap.add_argument("--self-check", action="store_true", help="pure asserts, no DB")
    ap.add_argument("--quick", action="store_true",
                    help="200 boots / 30 permutations, no readings, no document write")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    if args.self_check:
        return self_check()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    run = build_run(n_boot=200 if args.quick else args.n_boot,
                    n_perm=30 if args.quick else N_PERM,
                    readings=not args.quick)
    if run is None:
        return 1
    logger.info("gate: %s", run.gate_note)
    logger.info("primary Δ log-loss %s | perm p=%.4f (mean %+.4f)",
                _ci(run.primary.d_ll), run.primary.perm.p, run.primary.perm.mean)
    logger.info("VERDICT terminal hazard: %s", "PASS" if run.primary.passes else "FAIL")
    if args.quick or args.no_write:
        print("(quick/no-write run — document not updated)")
        return 0
    DOC.write_text(render(run), encoding="utf-8")
    logger.info("wrote %s", DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
