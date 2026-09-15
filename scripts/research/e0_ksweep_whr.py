"""PoC-E0 extension — Elo K sweep past 80, and Whole-History Rating (PoC-E3).

    uv run python -m scripts.research.e0_ksweep_whr --self-check      # no DB, asserts
    uv run python -m scripts.research.e0_ksweep_whr --quick           # coarse grids, scouting
    uv run python -m scripts.research.e0_ksweep_whr --source all      # the reported run

Two backlog items, one runner, because they answer to the SAME instrument and the
same criterion: `analysis/poc/e0_rating_eval.py`'s prequential (predict-then-update)
walk-forward, scored by mean log loss with a percentile bootstrap interval.

1. ``k-sweep-alem-80`` — the 2026-08-24 E0 run swept Elo K over {10..80} and log loss
   fell monotonically to the edge of the grid on BOTH corpora. A minimum at the last
   grid point is not a minimum, it is a truncated grid. This extends the grid
   geometrically to K=400 and looks for the turn.

2. ``poc-e3-whr`` — Whole-History Rating (Rémi Coulom, "Whole-History Rating: A
   Bayesian Rating System for Players of Time-Varying Strength", Computers and Games
   2008, LNCS 5131, pp. 113-124). Bradley-Terry likelihood over the whole history
   with a Wiener-process prior on each player's rating trajectory; MAP by Newton's
   method, one player at a time, over that player's own tridiagonal Hessian. One
   knob: ``w`` (drift, Elo per sqrt(year)).

Pre-registration lives in ``docs/research/2026-09-14_e0_ksweep_whr.md`` and was
written before any arm ran. This runner REPLACES the block between the
``<!-- E0KWHR:RESULTS -->`` markers in that file, so a re-run shows real change in
``git diff`` rather than churn.

**Read-only.** Public corpora only: the scouting records JSON and the ``matches``
table (``owner_kind`` cannot leak — the ``graphs`` table is never queried). No write,
no replay, no export, and nothing here touches production K.

Determinism: engines iterate in list order, players in sorted order, the bootstrap is
seeded in ``stats_rigor.bootstrap_ci``. Numpy solves a dense (tiny) system rather than
a hand-rolled tridiagonal one — same answer, less code to get wrong.
"""

from __future__ import annotations

import argparse
import math
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from analysis.poc.e0_rating_eval import (
    REPO,
    Bout,
    ConstantBaseline,
    EloEngine,
    Glicko2PerBout,
    Glicko2Yearly,
    Scored,
    WinRateBaseline,
    _metrics,
    load_db_bouts,
    load_scouting_bouts,
    report_from_rows,
    stream_scores,
)
from analysis.stats_rigor import bootstrap_ci

DOC = REPO / "docs" / "research" / "2026-09-14_e0_ksweep_whr.md"
MARKER = "<!-- E0KWHR:RESULTS -->"

# 400/ln(10): the Elo <-> natural-Bradley-Terry scale. Identical to Glicko-2's
# SCALE constant, which is why the Glicko g() deflation below can be applied to a
# WHR posterior sd without any further conversion.
ELO_SCALE = 400.0 / math.log(10.0)

# The published E0 grid, then the geometric extension this run exists to measure.
K_GRID_PUBLISHED = (10.0, 20.0, 30.0, 40.0, 60.0, 80.0)
K_GRID_EXTENDED = (80.0, 100.0, 125.0, 160.0, 200.0, 250.0, 320.0, 400.0)
K_GRID = tuple(sorted(set(K_GRID_PUBLISHED) | set(K_GRID_EXTENDED)))
K_REFERENCE = 80.0  # the incumbent best-in-grid the extension has to beat
PROD_K = 40.0       # elo_calibration.py's shipped default, for the post-hoc read only

# WHR drift, Elo per sqrt(year). Grappling careers are not chess careers; the grid is
# deliberately wide because the honest answer to "how fast does grappling strength
# drift" is that nobody here has measured it (athlete_elo guesses a 36-month K
# half-life). Fitting w IS one of the deliverables.
W_GRID = (20.0, 40.0, 80.0, 160.0, 320.0)

QUICK_K_GRID = (40.0, 80.0, 200.0, 400.0)
QUICK_W_GRID = (40.0, 160.0)


# ── WHR core (Coulom 2008) ──────────────────────────────────────────────────────
class WhrModel:
    """Whole-History Rating MAP fit, maintained incrementally.

    State: for every player, the sorted list of time points at which they competed
    (this corpus dates bouts to the YEAR — see the E0 docstring — so a year is one
    time point and all of a player's bouts that year share one rating node), the
    natural-scale rating at each node, and the games attached to it.

    Objective (log posterior, maximised):

        sum over games   log  1 / (1 + exp(r_opp - r_self))      [Bradley-Terry]
      + sum over consecutive nodes  -(r_{k+1} - r_k)^2 / (2 w^2 dt_k)   [Wiener prior]

    Newton per player, holding every other player fixed, exactly as in the paper
    (§4): the Hessian of one player's rating vector is tridiagonal, so each step is a
    tiny solve. Opponent ratings enter through the gradient only.

    Anchoring: an undefeated player's MAP rating is +infinity, so Coulom adds a
    virtual win AND a virtual loss against a rating-0 opponent. Here they are attached
    to the player's FIRST node — that is what pins the scale and keeps the Hessian
    strictly negative definite even for a one-game player.
    """

    def __init__(self, w_elo: float, *, max_sweeps: int = 30, tol: float = 1e-4,
                 step_clip: float = 1.0) -> None:
        self.w2 = (w_elo / ELO_SCALE) ** 2      # natural-scale drift variance per year
        self.max_sweeps = max_sweeps
        self.tol = tol
        self.step_clip = step_clip
        self.nodes: dict[str, list[int]] = defaultdict(list)          # sorted times
        self.rating: dict[str, dict[int, float]] = defaultdict(dict)  # player -> t -> r
        self.var: dict[str, dict[int, float]] = defaultdict(dict)     # player -> t -> sd^2
        self.games: dict[str, dict[int, list[tuple[str, float]]]] = defaultdict(
            lambda: defaultdict(list))

    # -- state ------------------------------------------------------------------
    def _touch(self, player: str, t: int) -> None:
        """Ensure a rating node exists at time ``t``, warm-started from the player's
        most recent earlier node (the Wiener prior's conditional mean is that value)."""
        ts = self.nodes[player]
        if t in self.rating[player]:
            return
        prev = [x for x in ts if x < t]
        self.rating[player][t] = self.rating[player][prev[-1]] if prev else 0.0
        self.var[player][t] = 1.0
        ts.append(t)
        ts.sort()

    def add_game(self, a: str, b: str, score_a: float, t: int) -> None:
        self._touch(a, t)
        self._touch(b, t)
        self.games[a][t].append((b, score_a))
        self.games[b][t].append((a, 1.0 - score_a))

    # -- fit --------------------------------------------------------------------
    def _sweep_player(self, player: str) -> float:
        ts = self.nodes[player]
        n = len(ts)
        r = np.array([self.rating[player][t] for t in ts], dtype=float)
        grad = np.zeros(n)
        hess = np.zeros((n, n))

        for k, t in enumerate(ts):
            for opp, s in self.games[player][t]:
                ro = self.rating[opp][t]
                p = 1.0 / (1.0 + math.exp(max(-60.0, min(60.0, ro - r[k]))))
                grad[k] += s - p
                hess[k, k] -= p * (1.0 - p)
            if k == 0:  # virtual win + virtual loss vs a rating-0 opponent
                p0 = 1.0 / (1.0 + math.exp(max(-60.0, min(60.0, -r[k]))))
                grad[k] += (1.0 - p0) + (0.0 - p0)
                hess[k, k] -= 2.0 * p0 * (1.0 - p0)

        for k in range(n - 1):
            s2 = self.w2 * (ts[k + 1] - ts[k])
            d = (r[k + 1] - r[k]) / s2
            grad[k] += d
            grad[k + 1] -= d
            hess[k, k] -= 1.0 / s2
            hess[k + 1, k + 1] -= 1.0 / s2
            hess[k, k + 1] += 1.0 / s2
            hess[k + 1, k] += 1.0 / s2

        # ponytail: dense solve/inverse on an n x n with n = years-this-player-competed
        # (<= 19 on both corpora). The tridiagonal Thomas recursions Coulom describes are
        # the right call at Go-server scale; here they would be 40 lines to save
        # microseconds. Swap them in if a corpus ever carries per-day time points.
        if n == 1:
            h = hess[0, 0]
            delta = np.array([-grad[0] / h])
            cov_diag = np.array([-1.0 / h])
        else:
            delta = -np.linalg.solve(hess, grad)
            cov_diag = np.diagonal(np.linalg.inv(-hess)).copy()

        delta = np.clip(delta, -self.step_clip, self.step_clip)
        r = np.clip(r + delta, -10.0, 10.0)
        for k, t in enumerate(ts):
            self.rating[player][t] = float(r[k])
            self.var[player][t] = float(max(cov_diag[k], 1e-6))
        return float(np.max(np.abs(delta)))

    def fit(self, max_sweeps: int | None = None) -> int:
        """Cyclic Newton over every player until the largest step falls under ``tol``.
        Players in sorted order so the fixed-point reached is reproducible."""
        sweeps = self.max_sweeps if max_sweeps is None else max_sweeps
        players = sorted(self.nodes)
        for i in range(sweeps):
            worst = 0.0
            for player in players:
                worst = max(worst, self._sweep_player(player))
            if worst < self.tol:
                return i + 1
        return sweeps

    # -- prediction -------------------------------------------------------------
    def state_at(self, player: str, t: int) -> tuple[float, float]:
        """Prior mean and variance of ``player``'s rating at time ``t``.

        The Wiener process has zero drift, so the mean is the last fitted node's
        rating and the variance is that node's posterior variance plus ``w^2 * dt``
        of accumulated drift. An unseen player sits at the anchor with the drift of
        one period's worth of ignorance.
        """
        ts = self.nodes.get(player)
        if not ts:
            return 0.0, 1.0 + self.w2
        prev = [x for x in ts if x <= t]
        last = prev[-1] if prev else ts[0]
        return self.rating[player][last], self.var[player][last] + self.w2 * abs(t - last)


def _g(phi2: float) -> float:
    """Glickman's variance-deflation factor on a natural-scale variance."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi2 / math.pi**2)


class WhrEngine:
    """E0 engine adapter over :class:`WhrModel`.

    WHR is a batch method; the harness is prequential. The adapter keeps the walk-forward
    honest by refitting only on data already observed, then predicting the next bout —
    so a prediction never sees its own result, and never sees any later result either.

    ``refit='bout'`` refits (warm-started, so a handful of sweeps) after every observed
    bout, matching ``Glicko2PerBout``'s cadence. ``refit='year'`` refits once per closed
    calendar year, matching ``Glicko2Yearly``. E0's 2026-08-24 reading was that cadence,
    not the rating model, decided the Glicko comparison — so WHR is measured at both.

    ``deflate`` turns the MAP point estimate into an uncertainty-aware probability using
    the posterior variances the Newton step already produced (Glickman's g-factor on the
    combined variance). Off = plain Bradley-Terry on MAP ratings.
    """

    def __init__(self, w_elo: float, *, refit: str = "bout", deflate: bool = True,
                 warm_sweeps: int = 2) -> None:
        self.model = WhrModel(w_elo)
        self.refit = refit
        self.deflate = deflate
        self.warm_sweeps = warm_sweeps
        self.cur_year = 0
        self.name = f"whr-w{w_elo:g}" + ("" if deflate else "-map") + (
            "" if refit == "bout" else f"-{refit}")

    def predict(self, a: str, b: str) -> float:
        ra, va = self.model.state_at(a, self.cur_year)
        rb, vb = self.model.state_at(b, self.cur_year)
        scale = _g(va + vb) if self.deflate else 1.0
        return 1.0 / (1.0 + math.exp(max(-60.0, min(60.0, -scale * (ra - rb)))))

    def observe(self, bout: Bout) -> None:
        self.cur_year = bout.year
        self.model.add_game(bout.a, bout.b, bout.score_a, bout.year)
        if self.refit == "bout":
            self.model.fit(max_sweeps=self.warm_sweeps)

    def close_year(self) -> None:
        self.cur_year += 1
        if self.refit == "year":
            self.model.fit()


class SharedPrediction:
    """A second read of an engine that has ALREADY been fitted this pass.

    ``stream_scores`` predicts with every engine before observing with every engine, so
    a no-op ``observe`` here is not a leak: this arm reads exactly the state the owner
    arm held at prediction time. Exists so the MAP and deflated WHR variants cost one
    fit between them instead of two.
    """

    def __init__(self, owner: WhrEngine, *, deflate: bool, name: str) -> None:
        self.owner = owner
        self.deflate = deflate
        self.name = name

    def predict(self, a: str, b: str) -> float:
        ra, va = self.owner.model.state_at(a, self.owner.cur_year)
        rb, vb = self.owner.model.state_at(b, self.owner.cur_year)
        scale = _g(va + vb) if self.deflate else 1.0
        return 1.0 / (1.0 + math.exp(max(-60.0, min(60.0, -scale * (ra - rb)))))

    def observe(self, bout: Bout) -> None:
        pass

    def close_year(self) -> None:
        pass


# ── comparison machinery ────────────────────────────────────────────────────────
def _loss(row: Scored) -> float:
    clip = 1e-12
    return -(row.score * math.log(max(row.p, clip))
             + (1 - row.score) * math.log(max(1 - row.p, clip)))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def paired_delta(rows_a: Sequence[Scored], rows_b: Sequence[Scored],
                 n_boot: int = 2000) -> dict[str, float]:
    """Paired bootstrap of (log loss of A) - (log loss of B), bout by bout.

    The arms are evaluated on the identical bout stream in the identical order, so the
    per-bout losses pair exactly and the difference has far less variance than the gap
    between two independently bootstrapped means. Negative = A is better.
    """
    diffs = [_loss(a) - _loss(b) for a, b in zip(rows_a, rows_b, strict=True)]
    d, lo, hi = bootstrap_ci(diffs, _mean, n_boot=n_boot)
    return {"delta": d, "lo": lo, "hi": hi, "n": len(diffs)}


def window(rows: Sequence[Scored], years: Sequence[int]) -> list[Scored]:
    keep = set(years)
    return [r for r in rows if r.year in keep]


def split_years(rows: Sequence[Scored]) -> tuple[list[int], list[int]]:
    """Scored years split into an earlier TUNE half and a later TEST half.

    Selecting a hyper-parameter on the same bouts you then report it on is optimistic
    by construction; this is the selection-free reading. Split on the MEDIAN BOUT, not
    the median year, so both halves carry comparable weight on a corpus whose volume
    grows year over year.
    """
    years = sorted({r.year for r in rows})
    counts = {y: sum(1 for r in rows if r.year == y) for y in years}
    half = len(rows) / 2.0
    run = 0
    for i, y in enumerate(years):
        run += counts[y]
        if run >= half:
            cut = max(i + 1, 1)
            break
    else:  # pragma: no cover - non-empty rows always break above
        cut = len(years) - 1
    cut = min(cut, len(years) - 1)
    return years[:cut], years[cut:]


# ── run ─────────────────────────────────────────────────────────────────────────
def build_engines(k_grid: Sequence[float], w_grid: Sequence[float]) -> list[Any]:
    engines: list[Any] = [ConstantBaseline(), WinRateBaseline()]
    for k in k_grid:
        engines.append(EloEngine(k=k, use_mults=True))
        engines.append(EloEngine(k=k, use_mults=False))
    engines.append(Glicko2Yearly(tau=0.5))
    engines.append(Glicko2PerBout(tau=0.5))
    for w in w_grid:
        owner = WhrEngine(w, refit="bout", deflate=True)
        engines.append(owner)
        engines.append(SharedPrediction(owner, deflate=False, name=f"whr-w{w:g}-map"))
        engines.append(WhrEngine(w, refit="year", deflate=True))
    return engines


def _fmt_ll(m: Mapping[str, Any]) -> str:
    if not m.get("n"):
        return "— | —"
    return f"{m['log_loss']:.4f} | [{m['log_loss_lo']:.4f}, {m['log_loss_hi']:.4f}]"


def _best(rows: Mapping[str, list[Scored]], names: Sequence[str],
          years: Sequence[int] | None = None) -> tuple[str, float]:
    def ll(name: str) -> float:
        sel = rows[name] if years is None else window(rows[name], years)
        return _mean([_loss(r) for r in sel]) if sel else float("inf")
    # total order: loss, then name — no dict iteration decides a tie (failure-archaeology #10)
    best = min(sorted(names), key=lambda n: (ll(n), n))
    return best, ll(best)


def run_corpus(source: str, bouts: Sequence[Bout], dropped: Mapping[str, int],
               k_grid: Sequence[float], w_grid: Sequence[float],
               n_boot: int) -> str:
    t0 = time.time()
    engines = build_engines(k_grid, w_grid)
    rows = stream_scores(bouts, engines)
    elapsed = time.time() - t0

    names = [e.name for e in engines]
    k_names = {(k, v): f"elo-k{k:g}" + ("" if v == "mult" else "-flat")
               for k in k_grid for v in ("mult", "flat")}
    whr_names = [n for n in names if n.startswith("whr-")]
    tune, test = split_years(rows[names[0]])
    years = sorted({b.year for b in bouts})

    out = [
        f"## Corpus: `{source}`",
        "",
        f"{len(bouts)} distinct bouts, {years[0]}–{years[-1]} · dropped `{dict(dropped)}` ·"
        f" first corpus year burned in · {len(rows[names[0]])} scored predictions per arm ·"
        f" {len(engines)} arms in one prequential pass ({elapsed:.0f}s).",
        "",
        f"TUNE years {tune[0]}–{tune[-1]} ({len(window(rows[names[0]], tune))} bouts) ·"
        f" TEST years {test[0]}–{test[-1]} ({len(window(rows[names[0]], test))} bouts).",
        "",
        "### K sweep — Elo by predictive log loss",
        "",
        "| K | variant | log loss | 95% CI | Brier | accuracy |",
        "|---|---|---|---|---|---|",
    ]
    k_metrics: dict[str, dict[str, Any]] = {}
    for k in k_grid:
        for variant in ("mult", "flat"):
            name = k_names[(k, variant)]
            m = _metrics(rows[name], n_boot)
            k_metrics[name] = m
            marker = " ←" if k == K_REFERENCE else ""
            out.append(f"| {k:g}{marker} | {variant} | {_fmt_ll(m)} |"
                       f" {m['brier']:.4f} | {m['accuracy']:.3f} |")

    all_k_names = [k_names[(k, v)] for k in k_grid for v in ("mult", "flat")]
    ref_name = k_names[(K_REFERENCE, "mult")]
    ref_m = k_metrics[ref_name]
    ci_width = ref_m["log_loss_hi"] - ref_m["log_loss_lo"]
    best_k_name, best_k_ll = _best(rows, all_k_names)
    gain = ref_m["log_loss"] - best_k_ll
    k_primary = gain > ci_width
    k_pair = paired_delta(rows[best_k_name], rows[ref_name], n_boot=n_boot)
    k_secondary = k_pair["hi"] < 0.0

    tune_best_k, _ = _best(rows, all_k_names, tune)
    test_ref = _mean([_loss(r) for r in window(rows[ref_name], test)])
    test_sel = _mean([_loss(r) for r in window(rows[tune_best_k], test)])
    k_tert_pair = paired_delta(window(rows[tune_best_k], test), window(rows[ref_name], test),
                               n_boot=n_boot)
    k_tertiary = k_tert_pair["hi"] < 0.0

    # where the curve actually turns, mult variant only (it beats flat at every K in E0)
    mult_curve = [(k, k_metrics[k_names[(k, "mult")]]["log_loss"]) for k in k_grid]
    argmin_k = min(mult_curve, key=lambda kv: (kv[1], kv[0]))[0]
    interior = k_grid[0] < argmin_k < k_grid[-1]

    out += [
        "",
        "### K verdicts",
        "",
        f"- Reference `{ref_name}`: {ref_m['log_loss']:.4f}, CI width **{ci_width:.4f}**.",
        f"- Best in grid: `{best_k_name}` at {best_k_ll:.4f} (gain {gain:+.4f}).",
        f"- Curve minimum (mult variant): **K={argmin_k:g}** — "
        f"{'INTERIOR (the grid now contains the turn)' if interior else 'still at a grid EDGE'}.",
        f"- **K-PRIMARY** (gain > CI width {ci_width:.4f}): "
        f"**{'PASS' if k_primary else 'FAIL'}** ({gain:+.4f}).",
        f"- **K-SECONDARY** (paired bootstrap of best−reference, 95% CI excludes 0): "
        f"**{'PASS' if k_secondary else 'FAIL'}** "
        f"({k_pair['delta']:+.4f} [{k_pair['lo']:+.4f}, {k_pair['hi']:+.4f}]).",
        f"- **K-TERTIARY** (K chosen on TUNE = `{tune_best_k}`, scored on TEST vs "
        f"`{ref_name}`): **{'PASS' if k_tertiary else 'FAIL'}** — "
        f"{test_sel:.4f} vs {test_ref:.4f}, paired {k_tert_pair['delta']:+.4f} "
        f"[{k_tert_pair['lo']:+.4f}, {k_tert_pair['hi']:+.4f}].",
        "",
        "### WHR and the E0 incumbents",
        "",
        "| engine | log loss | 95% CI | Brier | accuracy |",
        "|---|---|---|---|---|",
    ]
    incumbents = ["glicko2-perbout-tau0.5", "glicko2-tau0.5", ref_name, best_k_name,
                  "win-rate", "constant-0.5"]
    shown = list(dict.fromkeys(whr_names + incumbents))
    table = sorted(((n, _metrics(rows[n], n_boot)) for n in shown),
                   key=lambda t: (t[1]["log_loss"], t[0]))
    for name, m in table:
        out.append(f"| `{name}` | {_fmt_ll(m)} | {m['brier']:.4f} | {m['accuracy']:.3f} |")

    e0_names = ["glicko2-perbout-tau0.5", "glicko2-tau0.5"] + all_k_names
    e0_best, e0_best_ll = _best(rows, e0_names)
    e0_m = _metrics(rows[e0_best], n_boot)
    e0_ci_width = e0_m["log_loss_hi"] - e0_m["log_loss_lo"]
    whr_best, whr_best_ll = _best(rows, whr_names)
    whr_gain = e0_best_ll - whr_best_ll
    w_primary = whr_gain > e0_ci_width
    w_pair = paired_delta(rows[whr_best], rows[e0_best], n_boot=n_boot)
    w_secondary = w_pair["hi"] < 0.0

    tune_whr, _ = _best(rows, whr_names, tune)
    tune_e0, _ = _best(rows, e0_names, tune)
    w_tert_pair = paired_delta(window(rows[tune_whr], test), window(rows[tune_e0], test),
                               n_boot=n_boot)
    w_tertiary = w_tert_pair["hi"] < 0.0
    test_whr = _mean([_loss(r) for r in window(rows[tune_whr], test)])
    test_e0 = _mean([_loss(r) for r in window(rows[tune_e0], test)])

    out += [
        "",
        "### WHR verdicts",
        "",
        f"- E0 incumbent best: `{e0_best}` {e0_best_ll:.4f}, CI width **{e0_ci_width:.4f}**.",
        f"- WHR best: `{whr_best}` {whr_best_ll:.4f} (gain {whr_gain:+.4f}).",
        f"- **WHR-PRIMARY** (gain > incumbent CI width): "
        f"**{'PASS' if w_primary else 'FAIL'}** ({whr_gain:+.4f} vs {e0_ci_width:.4f}).",
        f"- **WHR-SECONDARY** (paired bootstrap WHR−incumbent, 95% CI excludes 0): "
        f"**{'PASS' if w_secondary else 'FAIL'}** "
        f"({w_pair['delta']:+.4f} [{w_pair['lo']:+.4f}, {w_pair['hi']:+.4f}]).",
        f"- **WHR-TERTIARY** (both chosen on TUNE — `{tune_whr}` vs `{tune_e0}` — scored on "
        f"TEST): **{'PASS' if w_tertiary else 'FAIL'}** — {test_whr:.4f} vs {test_e0:.4f}, "
        f"paired {w_tert_pair['delta']:+.4f} "
        f"[{w_tert_pair['lo']:+.4f}, {w_tert_pair['hi']:+.4f}].",
        "",
        "### Post-hoc (NOT pre-registered — read as exploratory)",
        "",
    ]
    prod_name = k_names[(PROD_K, "mult")]
    argmin_name = k_names[(argmin_k, "mult")]
    prod_pair = paired_delta(rows[argmin_name], rows[prod_name], n_boot=n_boot)
    prod_m = _metrics(rows[prod_name], n_boot)
    argmin_m = _metrics(rows[argmin_name], n_boot)
    gl_pair = paired_delta(rows[whr_best], rows["glicko2-perbout-tau0.5"], n_boot=n_boot)
    out += [
        f"- Curve minimum vs the SHIPPED production K: `{argmin_name}` "
        f"{argmin_m['log_loss']:.4f} vs `{prod_name}` {prod_m['log_loss']:.4f}, paired "
        f"{prod_pair['delta']:+.4f} [{prod_pair['lo']:+.4f}, {prod_pair['hi']:+.4f}] — "
        f"{'CI excludes 0' if prod_pair['hi'] < 0 else 'CI includes 0'}.",
        f"- WHR best vs the other per-bout Bayesian arm: `{whr_best}` vs "
        f"`glicko2-perbout-tau0.5`, paired {gl_pair['delta']:+.4f} "
        f"[{gl_pair['lo']:+.4f}, {gl_pair['hi']:+.4f}].",
        "",
        "### Slices for the finalists",
        "",
        "| engine | slice | n | log loss | 95% CI | accuracy |",
        "|---|---|---|---|---|---|",
    ]
    for name in dict.fromkeys([best_k_name, ref_name, e0_best, whr_best]):
        rep = report_from_rows(name, rows[name], n_boot)
        for slice_name in ("experienced", "cold_start"):
            m = rep.slices[slice_name]
            if not m.get("n"):
                continue
            out.append(f"| `{name}` | {slice_name} | {m['n']} | {_fmt_ll(m)} |"
                       f" {m['accuracy']:.3f} |")
    out.append("")
    return "\n".join(out)


def write_block(body: str, doc: Path = DOC, marker: str = MARKER) -> None:
    """Replace the text between the two RESULTS markers; the pre-registration above
    the first marker is never touched by a run. ``marker`` is a parameter so a sibling
    study (``athlete_elo_base_k.py``) writes its own doc through this same rule instead
    of copying it."""
    text = doc.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(marker) + r".*?" + re.escape(marker), re.DOTALL)
    if not pattern.search(text):
        raise SystemExit(f"{doc} has no {marker} pair — pre-registration must exist first")
    doc.write_text(pattern.sub(f"{marker}\n\n{body}\n{marker}", text), encoding="utf-8")


def self_check() -> int:
    """No DB, no corpus files: the WHR math against cases with known answers."""
    # 1. Two players, one game, symmetric prior -> winner above loser, mirror-symmetric.
    m = WhrModel(80.0)
    m.add_game("a", "b", 1.0, 2020)
    m.fit()
    ra, _ = m.state_at("a", 2020)
    rb, _ = m.state_at("b", 2020)
    assert ra > 0 > rb, (ra, rb)
    assert abs(ra + rb) < 1e-4, "anchor is symmetric, so the pair must mirror"

    # 2. More wins -> higher rating; the virtual loss keeps it finite.
    m2 = WhrModel(80.0)
    for i in range(20):
        m2.add_game("champ", f"opp{i}", 1.0, 2020)
    m2.fit()
    rc, _ = m2.state_at("champ", 2020)
    assert 0 < rc < 10, rc
    assert rc > ra, "20 wins must rate above 1 win"

    # 3. Wiener prior: a player who stops competing keeps their rating but gains variance.
    _, v_now = m2.state_at("champ", 2020)
    _, v_later = m2.state_at("champ", 2030)
    assert v_later > v_now
    assert m2.state_at("champ", 2030)[0] == m2.state_at("champ", 2020)[0]

    # 4. Deflation can only pull a probability toward 0.5, never past it.
    e = WhrEngine(80.0)
    e.observe(Bout(a="a", b="b", score_a=1.0, year=2020, method="", stage="", comp=""))
    p_def = e.predict("a", "b")
    s = SharedPrediction(e, deflate=False, name="x")
    p_map = s.predict("a", "b")
    assert 0.5 < p_def < p_map < 1.0, (p_def, p_map)

    # 5. A drift-free model (w -> 0) must hold one rating across years.
    m3 = WhrModel(0.5)
    m3.add_game("x", "y", 1.0, 2010)
    m3.add_game("x", "z", 0.0, 2020)
    m3.fit()
    assert abs(m3.rating["x"][2010] - m3.rating["x"][2020]) < 0.05

    # 6. Harness plumbing: engines run through a 3-bout stream without leaking.
    bouts = [Bout(a="a", b="b", score_a=1.0, year=y, method="", stage="", comp="")
             for y in (2019, 2020, 2021)]
    scored = stream_scores(bouts, [WhrEngine(80.0), EloEngine(k=400.0)])
    assert len(scored["whr-w80"]) == 2  # first year burned in
    print("self-check OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=None, choices=["scouting", "db", "all"],
                    help="default: 'all', or 'scouting' under --quick so a smoke run "
                         "needs no DATABASE_URL")
    ap.add_argument("--quick", action="store_true",
                    help="coarse K/w grids and 200 bootstrap draws — shape, not verdicts")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--self-check", action="store_true", help="WHR math asserts, no corpus")
    ap.add_argument("--no-write", action="store_true", help="print only, leave the doc alone")
    args = ap.parse_args(argv)

    if args.self_check:
        return self_check()

    k_grid: Sequence[float] = QUICK_K_GRID if args.quick else K_GRID
    w_grid: Sequence[float] = QUICK_W_GRID if args.quick else W_GRID
    n_boot = 200 if args.quick else args.n_boot
    source = args.source or ("scouting" if args.quick else "all")
    sources = ["scouting", "db"] if source == "all" else [source]

    blocks = []
    for source in sources:
        bouts, dropped = (load_scouting_bouts() if source == "scouting" else load_db_bouts())
        blocks.append(run_corpus(source, bouts, dropped, k_grid, w_grid, n_boot))
    body = "\n".join(blocks)
    print(body)
    if args.quick or args.no_write:
        print("\n(quick/no-write run — document not updated)")
        return 0
    write_block(body)
    print(f"written: {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
