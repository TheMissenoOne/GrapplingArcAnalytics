"""PoC-E0 extension — sweep the BASE K of the engine production actually ships.

    uv run python -m scripts.research.athlete_elo_base_k --self-check   # no DB, asserts
    uv run python -m scripts.research.athlete_elo_base_k --quick        # 3 levels, db
    set -a; source .env; set +a                                         # read-only DATABASE_URL
    uv run python -m scripts.research.athlete_elo_base_k --source all   # the reported run

Backlog item ``athlete-elo-base-k-sweep``, the follow-up named in
``docs/research/2026-09-14_e0_ksweep_whr.md`` §4.4:

> If the owner wants one follow-up, it is this one: sweep ``athlete_elo``'s OWN base K
> through this harness via the existing ``AthleteEloEngine`` adapter. That engine is what
> ships, it scored worst in the E0 db run, and it is the only K on which a sweep result
> would be directly actionable.

**Which K.** The 2026-09-14 sweep moved ``elo_calibration``'s plain-Elo K (base 40 x
win-type x stage). That is not production's K. ``analysis/athlete_elo.py`` ships

    K = _base_k(n) x gap_factor x competitive_mult x temporal_decay
        _base_k(n)      = 40 (n<=10), 32 (n<=30), then log-decay -> 10
        gap_factor      = clamp(|target - graph_elo| / 400, 0.1, 1.0)
        competitive_mult= 2.5 for a ranked/leaderboard athlete, 1.0 for a casual user
        temporal_decay  = 2 ** (-months_since / 36)

so there is no scalar "the base K" to sweep -- there is a LADDER and three multipliers on
it. Every one of those terms is multiplicative and none of them reads ``_base_k``, so the
LEVEL of the schedule is one number however you factor it: scaling the ladder by c is
arithmetically identical to scaling ``competitive_mult`` by c. ``tests/
test_research_athlete_elo_base_k.py`` asserts that identity against the real replay rather
than asserting it in prose, and it is the whole licence for this runner, which sweeps the
level through the ``competitive_mult`` parameter that ``replay_matches`` already exposes --
**no edit to** ``analysis/athlete_elo.py``.

The level is reported as ``L = K_BASE_EARLY x competitive_mult``, i.e. the nominal K of a
first-10-matches bout before the gap and decay factors shrink it. Shipped competitor path:
L = 40 x 2.5 = **100**. Shipped casual path: L = 40. (L = 100 is also, by coincidence worth
noticing, exactly where the plain-Elo curve bottomed on both corpora.)

**Read-only.** Public corpora only (the scouting records JSON and the ``matches`` table).
No write, no replay, no export, and nothing here changes production K.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from analysis import athlete_elo
from analysis.poc.e0_rating_eval import (
    REPO,
    AthleteEloEngine,
    Bout,
    ConstantBaseline,
    EloEngine,
    Glicko2PerBout,
    Scored,
    WinRateBaseline,
    _metrics,
    load_db_bouts,
    load_rank_elo,
    load_scouting_bouts,
    report_from_rows,
    stream_scores,
)
from scripts.research.e0_ksweep_whr import (
    _best,
    _fmt_ll,
    _loss,
    _mean,
    paired_delta,
    split_years,
    window,
    write_block,
)

DOC = REPO / "docs" / "research" / "2026-09-14_athlete_elo_base_k.md"
MARKER = "<!-- ATHLETEK:RESULTS -->"

# L = K_BASE_EARLY x competitive_mult. Shipped competitor path = 40 x 2.5.
LEVEL_SHIPPED = athlete_elo.K_BASE_EARLY * athlete_elo.COMPETITIVE_K_MULT   # 100.0
LEVEL_CASUAL = athlete_elo.K_BASE_EARLY                                    # 40.0

# Geometric ladder, ratio sqrt(2), 1/8x to 8x the shipped level, containing it exactly.
# Written as a literal (not generated) so arm NAMES are stable bytes across runs and
# across machines -- a generated 17.677669529663688 would make the doc churn. The span is
# deliberately wide: the one durable lesson of the 2026-09-14 K sweep is that a grid whose
# optimum sits at an edge has not measured an optimum.
LEVEL_GRID: tuple[float, ...] = (
    12.5, 17.7, 25.0, 35.4, 50.0, 70.7, 100.0, 141.4, 200.0, 282.8, 400.0, 565.7, 800.0,
)
QUICK_LEVEL_GRID: tuple[float, ...] = (25.0, 100.0, 400.0)

# Plain-Elo reference points carried along so this study and the previous one are read on
# ONE bout stream rather than compared across two runs of a corpus that keeps growing.
ELO_REFERENCE_K = (40.0, 100.0)


def level_name(level: float) -> str:
    return f"athlete-elo-L{level:g}"


def engine_for_level(level: float, rank_targets: Mapping[str, float]) -> AthleteEloEngine:
    """One sweep arm: the unmodified production replay at K-schedule level ``level``."""
    return AthleteEloEngine(
        rank_targets,
        competitive_mult=level / athlete_elo.K_BASE_EARLY,
        name=level_name(level),
    )


class RankTargetOnly:
    """Control arm: the LEAKED STATIC PRIOR, with the learning removed.

    ``AthleteEloEngine`` (faithfully, because ``db.repository.replay_and_persist_athlete``
    does the same) feeds each athlete's present-day ADCC ``rank_elo`` in as the convergence
    target AND as every opponent's input rating, falling back to the black-belt floor (800)
    for the unranked. That snapshot is information from AFTER the bouts being predicted, so
    a prequential score for this engine family is optimistic by construction and the honest
    thing is to measure how much of it is the prior rather than the replay.

    This arm predicts ``expected(target_a, target_b)`` and never updates. Any sweep arm that
    cannot beat it is not learning anything the leaderboard did not already say.
    """

    name = "rank-target-only"

    def __init__(self, rank_targets: Mapping[str, float]) -> None:
        self.rank_targets = rank_targets

    def _target(self, athlete: str) -> float:
        return self.rank_targets.get(athlete, athlete_elo.BASE_BLACKBELT_ELO)

    def predict(self, a: str, b: str) -> float:
        return athlete_elo.expected(self._target(a), self._target(b))

    def observe(self, bout: Bout) -> None:
        pass

    def close_year(self) -> None:
        pass


def build_engines(levels: Sequence[float],
                  rank_targets: Mapping[str, float]) -> list[Any]:
    engines: list[Any] = [ConstantBaseline(), WinRateBaseline(),
                          RankTargetOnly(rank_targets)]
    engines += [engine_for_level(level, rank_targets) for level in levels]
    # The casual path (competitive_mult = 1.0) is a SHIPPED level but not a member of the
    # geometric ladder, so it rides along as context and is excluded from the sweep's
    # best-in-grid selection -- keeping the grid a clean sqrt(2) ladder while still
    # measuring the other value production actually uses.
    if LEVEL_CASUAL not in levels:
        engines.append(engine_for_level(LEVEL_CASUAL, rank_targets))
    engines += [EloEngine(k=k, use_mults=True) for k in ELO_REFERENCE_K]
    engines.append(Glicko2PerBout(tau=0.5))
    return engines


def degenerate_arms(rows: Mapping[str, list[Scored]], names: Sequence[str]) -> bool:
    """True when every swept arm produced the IDENTICAL prediction vector.

    Pre-registered void condition. The sweep knob only exists downstream of an athlete's
    own match ``sequence``; a corpus that carries none leaves every arm inert and equal,
    and thirteen identical rows are not thirteen measurements. Detected rather than
    assumed, so the claim in the doc is the runner's output.
    """
    vectors = {tuple(round(r.p, 12) for r in rows[n]) for n in names}
    return len(vectors) <= 1


def run_corpus(source: str, bouts: Sequence[Bout], dropped: Mapping[str, int],
               levels: Sequence[float], rank_targets: Mapping[str, float],
               n_boot: int) -> str:
    t0 = time.time()
    engines = build_engines(levels, rank_targets)
    rows = stream_scores(bouts, engines)
    elapsed = time.time() - t0

    names = [e.name for e in engines]
    sweep = [level_name(level) for level in levels]
    years = sorted({b.year for b in bouts})
    scored_n = len(rows[names[0]])

    rated = {a for a in ({b.a for b in bouts} | {b.b for b in bouts}) if a in rank_targets}
    both = sum(1 for b in bouts if b.a in rank_targets and b.b in rank_targets)

    out = [
        f"## Corpus: `{source}`",
        "",
        f"{len(bouts)} distinct bouts, {years[0]}–{years[-1]} · dropped `{dict(dropped)}` ·"
        f" first corpus year burned in · {scored_n} scored predictions per arm ·"
        f" {len(engines)} arms in one prequential pass ({elapsed:.0f}s).",
        "",
        f"Bouts carrying an event `sequence`: **{sum(1 for b in bouts if b.sequence)}**"
        f" of {len(bouts)} · athletes with a `rank_elo` target: **{len(rated)}** of"
        f" {len({b.a for b in bouts} | {b.b for b in bouts})} · bouts with BOTH sides"
        f" rated: **{both}**.",
        "",
    ]

    if degenerate_arms(rows, sweep):
        out += [
            "### VOID — the swept knob is inert on this corpus",
            "",
            "Every level in the grid produced a **byte-identical prediction vector**, so no"
            " verdict is issued here. The reason is structural and is printed above:"
            f" {sum(1 for b in bouts if b.sequence)} of {len(bouts)} bouts carry an event"
            " `sequence`, and `athlete_elo` grows a rating only from the athlete's own"
            " sequence events — with none, `AthleteEloEngine.observe` returns immediately,"
            " no graph is ever built, and K never multiplies anything. Every arm falls back"
            " to the rank target, which for these keys resolves to the black-belt floor for"
            " BOTH sides, so every prediction is exactly 0.5.",
            "",
            "This is not a null result about K. It is the corpus being unable to run the"
            " engine at all, and it is reported rather than silently skipped so nobody"
            " re-runs the sweep here expecting an answer.",
            "",
        ]
        return "\n".join(out)

    out += [
        "### Base-K sweep — `athlete_elo` at each schedule level, by predictive log loss",
        "",
        "| L (= 40 × competitive_mult) | competitive_mult | log loss | 95% CI | Brier"
        " | accuracy |",
        "|---|---|---|---|---|---|",
    ]
    metrics: dict[str, dict[str, Any]] = {}
    for level in levels:
        name = level_name(level)
        m = _metrics(rows[name], n_boot)
        metrics[name] = m
        tag = ""
        if level == LEVEL_SHIPPED:
            tag = " ← SHIPPED (competitor)"
        elif level == LEVEL_CASUAL:
            tag = " ← shipped (casual)"
        out.append(f"| {level:g}{tag} | {level / athlete_elo.K_BASE_EARLY:.4g} |"
                   f" {_fmt_ll(m)} | {m['brier']:.4f} | {m['accuracy']:.3f} |")

    ref_name = level_name(LEVEL_SHIPPED)
    ref_m = metrics[ref_name]
    ci_width = ref_m["log_loss_hi"] - ref_m["log_loss_lo"]
    best_name, best_ll = _best(rows, sweep)
    gain = ref_m["log_loss"] - best_ll
    primary = gain > ci_width
    pair = paired_delta(rows[best_name], rows[ref_name], n_boot=n_boot)
    secondary = pair["hi"] < 0.0

    tune, test = split_years(rows[names[0]])
    tune_best, _ = _best(rows, sweep, tune)
    test_ref = _mean([_loss(r) for r in window(rows[ref_name], test)])
    test_sel = _mean([_loss(r) for r in window(rows[tune_best], test)])
    tert_pair = paired_delta(window(rows[tune_best], test), window(rows[ref_name], test),
                             n_boot=n_boot)
    tertiary = tert_pair["hi"] < 0.0
    verdict = primary and tertiary

    curve = [(level, metrics[level_name(level)]["log_loss"]) for level in levels]
    argmin = min(curve, key=lambda kv: (kv[1], kv[0]))[0]
    interior = levels[0] < argmin < levels[-1]

    out += [
        "",
        f"TUNE years {tune[0]}–{tune[-1]} ({len(window(rows[names[0]], tune))} bouts) ·"
        f" TEST years {test[0]}–{test[-1]} ({len(window(rows[names[0]], test))} bouts).",
        "",
        "### Verdicts",
        "",
        f"- Reference `{ref_name}` (SHIPPED): {ref_m['log_loss']:.4f}, CI width"
        f" **{ci_width:.4f}**.",
        f"- Best in grid: `{best_name}` at {best_ll:.4f} (gain {gain:+.4f}).",
        f"- Curve minimum: **L={argmin:g}** — "
        f"{'INTERIOR' if interior else 'at a grid EDGE (grid is truncated)'}.",
        f"- **BK-PRIMARY** (gain > CI width {ci_width:.4f}): "
        f"**{'PASS' if primary else 'FAIL'}** ({gain:+.4f}).",
        f"- **BK-SECONDARY** (paired bootstrap best−shipped, 95% CI excludes 0): "
        f"**{'PASS' if secondary else 'FAIL'}** "
        f"({pair['delta']:+.4f} [{pair['lo']:+.4f}, {pair['hi']:+.4f}]).",
        f"- **BK-TERTIARY** (L chosen on TUNE = `{tune_best}`, scored on TEST vs shipped): "
        f"**{'PASS' if tertiary else 'FAIL'}** — {test_sel:.4f} vs {test_ref:.4f}, paired "
        f"{tert_pair['delta']:+.4f} [{tert_pair['lo']:+.4f}, {tert_pair['hi']:+.4f}].",
        "",
        f"- **VERDICT (PRIMARY and TERTIARY, as pre-registered): "
        f"{'PASS' if verdict else 'FAIL'}**",
        "",
        "### The sweep against its controls and the other engines",
        "",
        "| engine | log loss | 95% CI | Brier | accuracy |",
        "|---|---|---|---|---|",
    ]
    context = ["rank-target-only", "win-rate", "constant-0.5", "glicko2-perbout-tau0.5"]
    context += [f"elo-k{k:g}" for k in ELO_REFERENCE_K]
    shown = list(dict.fromkeys([best_name, ref_name, level_name(LEVEL_CASUAL)] + context))
    table = sorted(((n, _metrics(rows[n], n_boot)) for n in shown),
                   key=lambda t: (t[1]["log_loss"], t[0]))
    for name, m in table:
        out.append(f"| `{name}` | {_fmt_ll(m)} | {m['brier']:.4f} | {m['accuracy']:.3f} |")

    prior_pair = paired_delta(rows[best_name], rows["rank-target-only"], n_boot=n_boot)
    casual_pair = paired_delta(rows[level_name(LEVEL_CASUAL)], rows[ref_name], n_boot=n_boot)
    elo_pair = paired_delta(rows[ref_name], rows["elo-k100"], n_boot=n_boot)
    out += [
        "",
        "### Secondary readings (pre-registered as descriptive — no pass/fail attached)",
        "",
        f"- **Best sweep arm vs the leaked static prior**: `{best_name}` vs"
        f" `rank-target-only`, paired {prior_pair['delta']:+.4f}"
        f" [{prior_pair['lo']:+.4f}, {prior_pair['hi']:+.4f}] — "
        f"{'the replay ADDS over the prior' if prior_pair['hi'] < 0 else ('the replay is WORSE than the prior' if prior_pair['lo'] > 0 else 'indistinguishable from the prior')}.",
        f"- **Casual path vs competitor path** (the only two levels production actually"
        f" ships): `{level_name(LEVEL_CASUAL)}` vs `{ref_name}`, paired"
        f" {casual_pair['delta']:+.4f} [{casual_pair['lo']:+.4f}, {casual_pair['hi']:+.4f}].",
        f"- **Production engine at its shipped level vs plain Elo at the level the previous"
        f" sweep liked**: `{ref_name}` vs `elo-k100`, paired {elo_pair['delta']:+.4f}"
        f" [{elo_pair['lo']:+.4f}, {elo_pair['hi']:+.4f}].",
        "",
        "### Slices for the finalists",
        "",
        "| engine | slice | n | log loss | 95% CI | accuracy |",
        "|---|---|---|---|---|---|",
    ]
    for name in dict.fromkeys([ref_name, best_name, "rank-target-only"]):
        rep = report_from_rows(name, rows[name], n_boot)
        for slice_name in ("experienced", "cold_start"):
            m = rep.slices[slice_name]
            if not m.get("n"):
                continue
            out.append(f"| `{name}` | {slice_name} | {m['n']} | {_fmt_ll(m)} |"
                       f" {m['accuracy']:.3f} |")
    out.append("")
    return "\n".join(out)


def self_check() -> int:
    """No DB, no corpus files: the identity the whole sweep rests on, plus plumbing."""
    # 1. Scaling the base-K LADDER == scaling competitive_mult. If this ever stops holding,
    #    this runner is sweeping something other than base K and every table is mislabelled.
    for n in (5, 20, 100):
        a = athlete_elo.k_factor(n, 800.0, 1200.0, competitive_mult=2.5 * 3.0)
        b = 3.0 * athlete_elo.k_factor(n, 800.0, 1200.0, competitive_mult=2.5)
        assert abs(a - b) < 1e-9, (n, a, b)

    # 2. The grid is geometric (ratio sqrt 2), contains the shipped level exactly, and
    #    brackets it symmetrically.
    assert LEVEL_SHIPPED in LEVEL_GRID
    assert LEVEL_GRID[0] * 8 == LEVEL_SHIPPED and LEVEL_GRID[-1] == LEVEL_SHIPPED * 8

    # 3. The knob is live: two levels must disagree on a corpus that HAS sequences.
    seq = ({"label": "armbar", "type": "submission", "actor_id": "a1"},
           {"label": "mount", "type": "position", "actor_id": "a1"})
    bouts = [Bout("a1", "b1", 1.0, y, "POINTS", "F", "E1", sequence=seq)
             for y in (2019, 2020, 2021, 2022)]
    targets = {"a1": 1200.0, "b1": 900.0}
    lo, hi = engine_for_level(12.5, targets), engine_for_level(800.0, targets)
    rows = stream_scores(bouts, [lo, hi, RankTargetOnly(targets)])
    assert [r.p for r in rows[lo.name]] != [r.p for r in rows[hi.name]], "knob is inert"
    assert not degenerate_arms(rows, [lo.name, hi.name])

    # 4. The void detector fires on the corpus shape that motivated it (no sequences).
    bare = [Bout("a1", "b1", 1.0, y, "POINTS", "F", "E1") for y in (2019, 2020, 2021)]
    bare_rows = stream_scores(bare, [engine_for_level(level, {}) for level in (12.5, 800.0)])
    assert degenerate_arms(bare_rows, [level_name(12.5), level_name(800.0)])
    print("self-check OK")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=None, choices=["scouting", "db", "all"],
                    help="default: 'all' ('db' under --quick)")
    ap.add_argument("--quick", action="store_true",
                    help="3 levels and 200 bootstrap draws — shape, not verdicts")
    ap.add_argument("--levels", default=None,
                    help="comma-separated levels replacing the pre-registered grid. Any run "
                         "using this is POST-HOC by definition and must say so; it implies "
                         "--no-write so an exploratory grid cannot overwrite the report.")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--self-check", action="store_true", help="asserts only, no corpus")
    ap.add_argument("--no-write", action="store_true", help="print only, leave the doc alone")
    args = ap.parse_args(argv)

    if args.self_check:
        return self_check()

    levels: Sequence[float] = QUICK_LEVEL_GRID if args.quick else LEVEL_GRID
    if args.levels:
        levels = tuple(float(x) for x in args.levels.split(","))
        if LEVEL_SHIPPED not in levels:
            raise SystemExit(f"--levels must contain the shipped level {LEVEL_SHIPPED:g}")
    n_boot = 200 if args.quick else args.n_boot
    source = args.source or ("db" if args.quick else "all")
    sources = ["scouting", "db"] if source == "all" else [source]

    rank_targets = load_rank_elo()
    blocks = []
    for src in sources:
        bouts, dropped = (load_scouting_bouts() if src == "scouting" else load_db_bouts())
        blocks.append(run_corpus(src, bouts, dropped, levels, rank_targets, n_boot))
    body = "\n".join(blocks)
    print(body)
    if args.quick or args.no_write or args.levels:
        print("\n(quick/no-write run — document not updated)")
        return 0
    write_block(body, doc=Path(DOC), marker=MARKER)
    print(f"written: {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
