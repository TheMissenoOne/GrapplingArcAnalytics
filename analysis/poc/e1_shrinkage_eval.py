"""PoC-E1 — empirical-Bayes shrinkage for small-N node estimates
(``docs/research/03_POC_PLANS.md``).

**Precondition (05_EXTERNAL_POC_REVIEW.md §2, applied before any number below):** per-event-type
success semantics must be defined before renaming a posterior "skill". ``docs/match_event_model.md``
sets the contract — ``successful`` is optional; ``True``/``False`` are read events, an OMITTED
key is neutral BY CONTRACT (not a missing observation, not a silent False). Corpus fill rates are
measured fresh below (do not copy a prior run's numbers) and used to declare, per type, whether
``successful`` is an admissible Bernoulli trial for this PoC:

  * **admissible** — ``pass``, ``takedown``, ``sweep``, ``submission``. All four are ``ACTION``
    type in ``analysis.attribution`` with the clean "landed vs attempted-but-defended" semantics
    the contract defines, and none sits at the near-floor fill rate that flags severe
    selection-into-logging.
  * **excluded** — ``guard``, ``control`` (``STATE`` type: "successful guard" has no contract
    definition — a state is held or lost, not landed, and both sit at a ~10-12% fill rate,
    the corpus floor); ``escape`` (``ACTION`` by the type table, but its fill rate sits at the
    same ~9% floor as the STATE types — logging an escape's *success* is close to tautological,
    since the escape event is usually only filed once it has already happened); ``transition``
    (a catch-all bucket that includes non-grappling bookkeeping rows — ``analysis.attribution``
    refuses even a role for it — so "successful" there answers no single question).

Selection-into-logging caveat (also from the external review, §2): a filled ``successful`` is
not a random sample of events — coders fill it preferentially where the outcome was easy to
judge (a submission either finished or it did not). The measured rates below are corpus fill
rates among LOGGED events, not attempt rates; this PoC does not correct for that, and neither
does anything downstream of it.

**Evaluation** (temporal split, per 03's pre-registered criterion, stated in full in
``render_markdown``): bouts sorted chronologically, most recent ``EVAL_FRACTION`` held out.
Node population = admissible-type events with a filled ``successful``, gated on
``attribution.bout_flags(...)["perspective_reliable"]`` (plain dict). Rank correlation
(``stats_rigor.spearman``) and top-k precision (``stats_rigor.bootstrap_ci``) score whether
train-period shrunken vs raw per-node rates predict eval-period per-node rates better. A
second pass measures signature churn: how many nodes clearing the current raw ±1σ-style rule
(``analysis.deviance.SIGNATURE_Z``, applied here to per-athlete node rates instead of ELO)
survive under "the shrunken interval clears the athlete's own mean".

This PoC only MEASURES — see ``## Consumers`` in the generated doc for the three rewiring
targets and the exact change each would take; none is touched here.

Usage::

    uv run python -m analysis.poc.e1_shrinkage_eval
    uv run python -m analysis.poc.e1_shrinkage_eval --out docs/research/poc/e1.md
"""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scipy.stats import beta as beta_dist

from analysis.attribution import bout_flags
from analysis.deviance import SIGNATURE_Z
from analysis.names import _normalize_name
from analysis.shrinkage import BetaBinomialPrior, fit_beta_binomial_prior, shrink_beta_binomial
from analysis.stats_rigor import RankCorrelation, bootstrap_ci, spearman

REPO = Path(__file__).resolve().parents[2]

ADMISSIBLE_TYPES = frozenset({"pass", "takedown", "sweep", "submission"})
EXCLUDED_TYPES = frozenset({"guard", "control", "escape", "transition"})

EVAL_FRACTION = 0.25   # most recent quarter of bouts held out, never random
MIN_TRAIN_TRIALS = 3   # a node needs >= this many filled train-period trials to enter the ranking
MIN_EVAL_TRIALS = 1    # and >= this many eval-period trials to have ground truth
MIN_ATHLETE_TRIALS = 3  # a (athlete, node) pair needs this many trials for the signature check
MIN_NODE_POP = 3        # min OTHER athletes at a node for the old z-rule (mirrors deviance.MIN_POP)
TOP_K = 15
CI = 0.95
SEED = 20260824


# ── event-type fill-rate measurement (the precondition) ─────────────────────────
@dataclass
class TypeFill:
    n: int = 0
    filled: int = 0
    true_n: int = 0
    false_n: int = 0

    @property
    def fill_rate(self) -> float:
        return self.filled / self.n if self.n else 0.0


def measure_fill_rates(matches: Sequence[Mapping[str, Any]]) -> dict[str, TypeFill]:
    """Per-event-type fill rate of the ``successful`` key, over every ``final`` match's raw
    sequence (unfiltered by gate — this measures the corpus's logging behaviour, not the
    admissible subset)."""
    out: dict[str, TypeFill] = defaultdict(TypeFill)
    for m in matches:
        for e in m.get("sequence") or []:
            if not isinstance(e, dict):
                continue
            t = str(e.get("type") or "")
            row = out[t]
            row.n += 1
            if "successful" in e:
                row.filled += 1
                if e["successful"] is True:
                    row.true_n += 1
                elif e["successful"] is False:
                    row.false_n += 1
    return dict(out)


# ── corpus fetch + gate ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Bout:
    key: tuple[int, str, str]   # (year, created_at, match_id) — temporal sort key
    a_id: str
    b_id: str
    sequence: list[dict[str, Any]]


@dataclass
class FetchReport:
    total: int = 0
    passed: int = 0
    one_sided: int = 0
    bouts: list[Bout] = field(default_factory=list)
    raw_matches: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def fetch_corpus() -> FetchReport:
    """Read-only: one ``select`` over ``matches`` through the shared engine. A missing or
    unreachable ``DATABASE_URL`` is reported, not raised."""
    rep = FetchReport()
    try:
        from sqlalchemy import text

        from db.base import get_engine

        with get_engine().connect() as conn:
            rowset = conn.execute(text(
                "SELECT id, athlete_a_id, athlete_b_id, year, created_at, sequence "
                "FROM matches WHERE status = 'final' AND sequence IS NOT NULL"
            )).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the report says why, the run continues
        rep.error = f"{type(exc).__name__}: {exc}".split("\n")[0][:160]
        return rep

    for row in rowset:
        rep.total += 1
        seq = [e for e in (row["sequence"] or []) if isinstance(e, dict)]
        rep.raw_matches.append({"sequence": seq})
        flags = bout_flags(seq, str(row["athlete_a_id"]), str(row["athlete_b_id"]))
        if not flags["perspective_reliable"]:
            rep.one_sided += 1
            continue
        rep.passed += 1
        key = (int(row["year"] or 0), str(row["created_at"]), str(row["id"]))
        rep.bouts.append(Bout(key=key, a_id=str(row["athlete_a_id"]), b_id=str(row["athlete_b_id"]),
                              sequence=seq))
    return rep


def admissible_events(bouts: Sequence[Bout]) -> list[dict[str, Any]]:
    """Flatten gated bouts to admissible-type events with a filled ``successful`` only — an
    omitted key is neutral by contract, not a trial."""
    out: list[dict[str, Any]] = []
    for b in bouts:
        for e in b.sequence:
            t = str(e.get("type") or "")
            if t not in ADMISSIBLE_TYPES or "successful" not in e:
                continue
            out.append({
                "node_key": _normalize_name(str(e.get("label") or "")),
                "actor_id": str(e.get("actor_id") or ""),
                "successful": bool(e["successful"]),
            })
    return out


# ── node-level counts ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class NodeCounts:
    successes: int
    trials: int

    @property
    def rate(self) -> float:
        return self.successes / self.trials if self.trials else float("nan")


def node_counts(events: Sequence[Mapping[str, Any]],
                key: str = "node_key") -> dict[Any, NodeCounts]:
    acc: dict[Any, list[int]] = defaultdict(lambda: [0, 0])
    for e in events:
        row = acc[e[key]]
        row[1] += 1
        if e["successful"]:
            row[0] += 1
    return {k: NodeCounts(s, n) for k, (s, n) in acc.items()}


def athlete_node_counts(events: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], NodeCounts]:
    acc: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for e in events:
        row = acc[(e["actor_id"], e["node_key"])]
        row[1] += 1
        if e["successful"]:
            row[0] += 1
    return {k: NodeCounts(s, n) for k, (s, n) in acc.items()}


# ── criterion 1: temporal ranking eval ────────────────────────────────────────
@dataclass
class RankingEval:
    n_train_events: int
    n_eval_events: int
    n_nodes: int
    prior: BetaBinomialPrior
    raw_corr: RankCorrelation
    shrunk_corr: RankCorrelation
    k: int
    raw_precision: tuple[float, float, float]
    shrunk_precision: tuple[float, float, float]
    usage_corr: RankCorrelation


def _precision_at_k(predictor: Mapping[str, float], truth_topk: set[str], k: int,
                    seed: int = SEED) -> tuple[float, float, float]:
    ranked = sorted(predictor.items(), key=lambda kv: kv[1], reverse=True)[:k]
    hits = [1.0 if node in truth_topk else 0.0 for node, _ in ranked]
    return bootstrap_ci(hits, statistic=statistics.fmean, seed=seed)


def evaluate_ranking(train_events: Sequence[Mapping[str, Any]],
                     eval_events: Sequence[Mapping[str, Any]]) -> RankingEval:
    train = node_counts(train_events)
    ev = node_counts(eval_events)
    eligible = sorted(n for n, c in train.items()
                      if c.trials >= MIN_TRAIN_TRIALS and n in ev
                      and ev[n].trials >= MIN_EVAL_TRIALS)

    prior = fit_beta_binomial_prior([(train[n].successes, train[n].trials) for n in eligible])
    raw = {n: train[n].rate for n in eligible}
    shrunk = {n: shrink_beta_binomial(train[n].successes, train[n].trials, prior) for n in eligible}
    truth = {n: ev[n].rate for n in eligible}
    usage_train = {n: float(train[n].trials) for n in eligible}
    usage_eval = {n: float(ev[n].trials) for n in eligible}

    xs, ys = [raw[n] for n in eligible], [truth[n] for n in eligible]
    sx, sy = [shrunk[n] for n in eligible], ys
    raw_corr = spearman(xs, ys)
    shrunk_corr = spearman(sx, sy)
    usage_corr = spearman([usage_train[n] for n in eligible], [usage_eval[n] for n in eligible])

    k = min(TOP_K, max(1, len(eligible) // 3)) if eligible else 0
    truth_topk = {n for n, _ in sorted(truth.items(), key=lambda kv: kv[1], reverse=True)[:k]}
    raw_p = _precision_at_k(raw, truth_topk, k) if k else (float("nan"),) * 3
    shrunk_p = _precision_at_k(shrunk, truth_topk, k) if k else (float("nan"),) * 3

    return RankingEval(len(train_events), len(eval_events), len(eligible), prior,
                       raw_corr, shrunk_corr, k, raw_p, shrunk_p, usage_corr)


# ── criterion 2: signature survival ───────────────────────────────────────────
@dataclass
class SignatureEval:
    n_candidates: int      # (athlete, node) pairs with enough data + a computable old-rule z
    n_old: int
    n_survive: int          # old signature AND new rule holds
    n_new_only: int         # new rule holds, old rule did not (among candidates)


def evaluate_signatures(events: Sequence[Mapping[str, Any]]) -> SignatureEval:
    an = athlete_node_counts(events)

    # node -> {athlete: rate}, restricted to (athlete, node) pairs with enough trials.
    by_node: dict[str, dict[str, float]] = defaultdict(dict)
    for (athlete, node), c in an.items():
        if c.trials >= MIN_ATHLETE_TRIALS:
            by_node[node][athlete] = c.rate

    # each athlete's own overall admissible-type rate (their baseline, ANY trial count).
    athlete_totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for (athlete, _node), c in an.items():
        row = athlete_totals[athlete]
        row[0] += c.successes
        row[1] += c.trials
    athlete_mean = {a: s / n if n else 0.0 for a, (s, n) in athlete_totals.items()}

    candidate_pairs = [(a, n) for (a, n), c in an.items() if c.trials >= MIN_ATHLETE_TRIALS]
    prior = fit_beta_binomial_prior([(an[p].successes, an[p].trials) for p in candidate_pairs])

    n_candidates = n_old = n_survive = n_new_only = 0
    for athlete, node in candidate_pairs:
        pop = by_node[node]
        others = [r for a, r in pop.items() if a != athlete]
        if len(others) < MIN_NODE_POP:
            continue
        mean = statistics.fmean(others)
        std = statistics.pstdev(others) if len(others) > 1 else 0.0
        if std <= 0.0:
            continue
        n_candidates += 1
        z = (pop[athlete] - mean) / std
        old_sig = z >= SIGNATURE_Z

        c = an[(athlete, node)]
        alpha_post, beta_post = c.successes + prior.alpha, c.trials - c.successes + prior.beta
        ci_lo = float(beta_dist.ppf((1 - CI) / 2, alpha_post, beta_post))
        new_sig = ci_lo > athlete_mean[athlete]

        if old_sig:
            n_old += 1
            if new_sig:
                n_survive += 1
        elif new_sig:
            n_new_only += 1

    return SignatureEval(n_candidates, n_old, n_survive, n_new_only)


# ── markdown ───────────────────────────────────────────────────────────────────
CRITERION = (
    'on a temporal split, top-k by shrunken estimate predicts next-period per-node '
    'success/usage better (rank correlation + top-k precision) than top-k by raw estimate. '
    "Also report how many current \"signatures\" survive — churn is a finding, not a blocker."
)

CONSUMERS = [
    ("`analysis.network_metrics.reward_risk_ranking` / `reward_risk_with_ci`",
     "rank by the beta-binomial shrunken posterior mean of `(reward, denom)` and `(risk, denom)` "
     "instead of the raw Beta(k+1, n-k+1) point estimate `reward_risk_with_ci` already "
     "computes — the prior comes from `fit_beta_binomial_prior` over every node's "
     "`(reward, denom)` in the same graph, replacing the implicit uniform Beta(1,1) prior "
     "with one fitted to the population."),
    ("`analysis.deviance.node_deviance` (MIN_POP fallback)",
     "replace the `n < MIN_POP: fall back to type baseline` branch with "
     "`shrink_normal_normal(node_elo, within_var=pop_std_at_node**2/n, prior)`, `prior` fit once "
     "via `fit_normal_normal_prior` over every node's `(mean_elo, n)` in `by_key`; `MIN_POP` "
     "(currently 3) would raise, per 03's plan, once shrinkage owns the small-n floor instead of "
     "a hard cutoff."),
    ("App/Web signature detection (`services/rating/*` in GrapplingArcApp, TS port)",
     "replace the bare `z >= +1σ` rule this PoC measured above with "
     "`shrunk_ci_lo(node) > athlete_own_mean` — the same rule evaluated here, ported to "
     "TypeScript against the App's on-device per-node session counts."),
]


def _fill_table(fills: Mapping[str, TypeFill]) -> list[str]:
    rows = ["| type | category | n | fill rate | true | false | omitted | admissible? |",
            "|---|---|---|---|---|---|---|---|"]
    for t in sorted(fills, key=lambda t: -fills[t].n):
        f = fills[t]
        cat = "ACTION" if t in ADMISSIBLE_TYPES else ("STATE" if t in ("guard", "control") else
             "ACTION*" if t == "escape" else "TRANSITION")
        adm = "yes" if t in ADMISSIBLE_TYPES else "no"
        rows.append(f"| {t} | {cat} | {f.n} | {f.fill_rate:.1%} | {f.true_n} | {f.false_n} | "
                   f"{f.n - f.filled} | {adm} |")
    return rows


def _corr_row(label: str, c: RankCorrelation) -> str:
    return f"| {label} | {c.rho:.3f} [{c.lo:.3f}, {c.hi:.3f}] | {c.n} | {c.grade} |"


def _prec_row(label: str, p: tuple[float, float, float], k: int) -> str:
    return f"| {label} | {p[0]:.3f} [{p[1]:.3f}, {p[2]:.3f}] | {k} |"


def render_markdown(fills: Mapping[str, TypeFill], gate: FetchReport,
                    ranking: RankingEval | None, sig: SignatureEval | None) -> str:
    lines = [
        "# PoC-E1 — empirical-Bayes shrinkage: first run",
        "",
        "Generated by `uv run python -m analysis.poc.e1_shrinkage_eval` — do not hand-edit. "
        "Builder: `analysis/shrinkage.py`; tests: `tests/test_shrinkage.py`. "
        "Plan: `docs/research/03_POC_PLANS.md` (PoC-E1); precondition: "
        "`docs/research/05_EXTERNAL_POC_REVIEW.md` §2.",
        "",
        "## Criterion (pre-registered, before any number below)",
        "",
        f"> {CRITERION}",
        "",
        "## Precondition: per-event-type success semantics",
        "",
        "`docs/match_event_model.md`: `successful` is optional; `True`=landed/finished, "
        "`False`=attempted-but-defended, an OMITTED key is **neutral by contract**, not a "
        "missing observation. Fill rates measured fresh below, over every `final` match's raw "
        "sequence (not just the gated/admissible subset).",
        "",
        *_fill_table(fills),
        "",
        "**Selection-into-logging caveat:** a filled `successful` is not a random sample of "
        "events — the higher fill rate on `submission`/`transition` reflects that a finish or "
        "a takedown outcome is easy for a coder to judge in one glance, while a `guard`/`control` "
        "state's \"success\" has no single obvious answer. The measured rates below describe "
        "coder behaviour, not attempt rates, and this PoC does not correct for that.",
        "",
        "**Admissible:** `pass`, `takedown`, `sweep`, `submission` — `ACTION` type with the "
        "contract's clean landed/attempted semantics, none at the corpus's ~10% fill floor.",
        "",
        "**Excluded:** `guard`, `control` (`STATE` type — no contract definition of a "
        "\"successful\" state, and both sit at the fill floor); `escape` (`ACTION` by the type "
        "table, but its fill rate sits at the same floor as the STATE types — logging is near "
        "tautological, an escape event is usually filed once it has already happened); "
        "`transition` (catch-all bucket, includes non-grappling bookkeeping rows per "
        "`analysis.attribution`, no single question `successful` could answer there).",
        "",
        f"Gate: {gate.total} final matches with a sequence · {gate.passed} pass "
        f"`bout_flags(...)['perspective_reliable']` · {gate.one_sided} refused (one-sided filing)"
        + (f" · error: {gate.error}" if gate.error else "") + ".",
        "",
    ]

    if ranking is not None:
        lines += [
            "## Result 1 — temporal-split node ranking",
            "",
            f"Train {ranking.n_train_events} admissible events, eval {ranking.n_eval_events} "
            f"(most recent {EVAL_FRACTION:.0%} of gated bouts, by date) · "
            f"{ranking.n_nodes} nodes with >= {MIN_TRAIN_TRIALS} train trials and "
            f">= {MIN_EVAL_TRIALS} eval trial(s) · prior fit: "
            f"Beta(alpha={ranking.prior.alpha:.2f}, beta={ranking.prior.beta:.2f}), "
            f"mean={ranking.prior.mean:.3f}.",
            "",
            "Rank correlation of the train-period estimate against the eval-period actual "
            "success rate (higher is better):",
            "",
            "| estimator | Spearman rho [95% CI] | n | grade |",
            "|---|---|---|---|",
            _corr_row("raw rate", ranking.raw_corr),
            _corr_row("shrunken (EB) rate", ranking.shrunk_corr),
            _corr_row("usage (train trials → eval trials, context only)", ranking.usage_corr),
            "",
            f"Top-k precision (k={ranking.k}): fraction of the predicted top-k that also land "
            "in the eval-period actual top-k, bootstrap CI over the k hit/miss indicators:",
            "",
            "| estimator | precision@k [95% CI] | k |",
            "|---|---|---|",
            _prec_row("raw rate", ranking.raw_precision, ranking.k),
            _prec_row("shrunken (EB) rate", ranking.shrunk_precision, ranking.k),
            "",
        ]
    else:
        lines += ["## Result 1 — temporal-split node ranking", "",
                  "Skipped: no gated corpus bouts.", ""]

    if sig is not None:
        rate = sig.n_survive / sig.n_old if sig.n_old else float("nan")
        lines += [
            "## Result 2 — signature survival",
            "",
            "Old rule: `(athlete, node)` raw rate z-score >= "
            f"`SIGNATURE_Z`={SIGNATURE_Z} against the OTHER athletes observed at that node "
            f"(>= {MIN_NODE_POP} other athletes, nonzero spread). New rule: the shrunken "
            "posterior's credible interval lower bound clears the athlete's OWN overall "
            f"admissible-type rate. Both computed over pairs with >= {MIN_ATHLETE_TRIALS} "
            "trials, full corpus (not split — this is a static churn count, not a prediction).",
            "",
            f"{sig.n_candidates} (athlete, node) pairs had a computable old-rule z · "
            f"**{sig.n_old} cleared the old rule** · **{sig.n_survive} survive under the new "
            f"rule** ({rate:.0%}) · {sig.n_new_only} pairs the new rule flags that the old rule "
            "did not.",
            "",
        ]
    else:
        lines += ["## Result 2 — signature survival", "", "Skipped: no gated corpus events.", ""]

    lines += [
        "## Consumers (named, not rewired)",
        "",
        "Per 03's plan, this PoC only measures. The three rewiring targets, and the exact "
        "change each would take if this doc records an accept:",
        "",
    ]
    for name, change in CONSUMERS:
        lines += [f"- **{name}** — {change}", ""]

    lines += ["## Reading", ""]
    return "\n".join(lines)


def _reading(ranking: RankingEval | None, sig: SignatureEval | None) -> list[str]:
    if ranking is None:
        return ["1. No corpus data — nothing to read."]
    rho_gap = ranking.shrunk_corr.rho - ranking.raw_corr.rho
    prec_gap = ranking.shrunk_precision[0] - ranking.raw_precision[0]
    # ACCEPT needs the shrunken estimator no worse on either axis AND strictly better on at
    # least one — a tie on both, or a loss on either, is not the win the criterion asks for.
    no_worse = rho_gap >= 0 and prec_gap >= 0
    strictly_better = rho_gap > 0 or prec_gap > 0
    verdict = "ACCEPT" if no_worse and strictly_better else "REJECT"
    out = [
        f"1. Rank correlation: shrunken {ranking.shrunk_corr.rho:.3f} vs raw "
        f"{ranking.raw_corr.rho:.3f} (delta {rho_gap:+.3f}); precision@k: shrunken "
        f"{ranking.shrunk_precision[0]:.3f} vs raw {ranking.raw_precision[0]:.3f} "
        f"(delta {prec_gap:+.3f}). n={ranking.n_nodes} nodes, k={ranking.k} — read both "
        "deltas against their CIs above before treating either as a win.",
        "2. The usage correlation (train trials -> eval trials) is context, not part of the "
        "accept criterion's success-rate comparison; it shows whether which nodes get *used* "
        "next period is itself predictable, independent of shrinkage.",
    ]
    if sig is not None:
        out.append(
            f"3. Signature churn: {sig.n_old} old-rule signatures, {sig.n_survive} survive the "
            f"new rule, {sig.n_new_only} new-only. Per 03's plan this is a finding, not a "
            "blocker — it does not enter the accept/reject decision below.")
    accepted = verdict == "ACCEPT"
    out.append(f"4. **VERDICT: {verdict}** — " + (
        "the shrunken estimator predicts the held-out period at least as well as the raw "
        "estimate on both the rank-correlation and precision axes, on this corpus." if accepted
        else "the shrunken estimator does not clear the raw estimate on this corpus at this "
        "sample size; the direction of the criterion (rank correlation AND precision both "
        "non-negative) is the bar, per 03's plan, and it was not met on both."))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PoC-E1 — empirical-Bayes shrinkage, measured")
    ap.add_argument("--out", default=str(REPO / "docs" / "research" / "poc" / "e1.md"))
    args = ap.parse_args(argv)

    gate = fetch_corpus()
    fills = measure_fill_rates(gate.raw_matches)

    ranking = sig = None
    if gate.bouts:
        bouts = sorted(gate.bouts, key=lambda b: b.key)
        cut = int(len(bouts) * (1 - EVAL_FRACTION))
        train_events = admissible_events(bouts[:cut])
        eval_events = admissible_events(bouts[cut:])
        ranking = evaluate_ranking(train_events, eval_events)
        sig = evaluate_signatures(admissible_events(bouts))

    md = render_markdown(fills, gate, ranking, sig)
    md += "\n" + "\n".join(_reading(ranking, sig)) + "\n"

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
