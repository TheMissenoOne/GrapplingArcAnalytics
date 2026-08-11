"""Decision criteria — which opponent conditions actually govern the response chosen.

``analysis/decision_flow`` already windows every exchange into
``position → action → opponent condition → response`` and scores how often the response
**worked** (``DecisionPattern.confidence`` = Beta posterior mean of P(success)).

It never answers the other question: was the response *chosen because of* the condition?
That is a **selection** probability, P(response | context, condition), and nothing in the
repo computed it. This module does, and it decides which of those associations survive
contact with chance.

The one-line model, for a context A (a position + action) and response B:

    baseline  = P(B | A, ¬C)      the complement, NOT P(B | A)
    decision  = P(B | A,  C)
    effect    = decision − baseline        (risk difference; lift is display only)

Using P(B|A) as the baseline — as the original design did — is wrong: it is computed over
all A events *including* the A∧C ones, so a common condition drags the baseline toward its
own conditional and the effect vanishes exactly where the criterion is strongest.

Why the effect and not the lift: lift is unstable when the baseline is small (0.02→0.10 is
a lift of 5 but an 8-point move, while 0.40→0.72 is a lift of 1.8 and a 32-point move — the
second is what a training system should surface). All gating is on the effect's credible
interval; lift rides along for display.

Everything is estimated as a Beta(1,1) posterior rather than a raw ratio, which IS the
shrinkage: a 1-of-1 observation lands near 0.5, not at 1.0. That is what makes the module
safe on the real corpus, where a whole compiled flowchart is ~21 exchanges over 6 matches
and per-triad counts are single digits.

Survival requires three independent things, all of which must hold:
  * the effect's 95% credible interval excludes zero,
  * a permutation test survives Benjamini-Hochberg FDR across every triad tested,
  * the effect is stable under resampling whole matches.

    uv run python -m analysis.decision_criteria --athlete "Gordon Ryan" --out c.json
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from analysis.network_metrics import _beta_ci

logger = logging.getLogger(__name__)

# Gates. Deliberately few and each independently meaningful — the original design multiplied
# four unnormalized factors into a single score, which is four free parameters wearing a hat.
FDR_Q = 0.10           # Benjamini-Hochberg across the whole triad family
MIN_STABILITY = 0.70   # fraction of match-level bootstrap resamples keeping effect > 0
MIN_CONTEXT_N = 8      # a context with fewer observations cannot separate anything

# Volume floors — a decision criterion is derived from a body of evidence, never one instance.
# Applied as a PRE-filter: a triad below these never enters the permutation family at all, so
# it cannot inflate the FDR correction against the candidates that do have support.
# Deliberately modest. The original design asked for 12 occurrences / 6 matches / 5 opponents,
# which this corpus cannot supply anywhere; the job of these numbers is to exclude one-offs,
# not to demand evidence that does not exist.
MIN_TRIAD_N = 3          # times the exact (context, condition, response) was observed
MIN_TRIAD_MATCHES = 2    # confined to one bout = that bout, not the athlete's game
MIN_TRIAD_OPPONENTS = 2  # confined to one opponent = that matchup, not a decision rule
N_PERMUTATIONS = 1000
N_BOOTSTRAP = 500
SEED = 20260810        # fixed: these reports are committed, they must be reproducible


@dataclass(frozen=True)
class Observation:
    """One windowed exchange. The unit of analysis."""

    match_id: str
    athlete_id: str
    context: str                 # A — source position + action
    condition: str | None        # C — opponent condition bundle ("" / None = none observed)
    response: str | None         # B — the athlete's next move (None = terminal failure)
    success: bool | None = None
    opponent_id: str = ""        # for the min-opponents floor; "" = not recorded


@dataclass
class Criterion:
    context: str
    condition: str
    response: str

    n_with: int = 0          # (A, C, B)
    n_condition: int = 0     # (A, C, *)
    n_without: int = 0       # (A, ¬C, B)
    n_no_condition: int = 0  # (A, ¬C, *)

    p_with: float = 0.0
    p_without: float = 0.0
    effect: float = 0.0      # p_with − p_without
    effect_lo: float = 0.0   # conservative bound; ranking key
    effect_hi: float = 0.0
    lift: float | None = None

    p_value: float = 1.0
    q_value: float = 1.0
    stability: float = 0.0

    match_count: int = 0
    success_rate: float | None = None   # P(success | A, C, B) — did it also WORK
    survives: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "context": self.context,
            "condition": self.condition,
            "response": self.response,
            "nWith": self.n_with,
            "nCondition": self.n_condition,
            "nWithout": self.n_without,
            "nNoCondition": self.n_no_condition,
            "pWith": round(self.p_with, 4),
            "pWithout": round(self.p_without, 4),
            "effect": round(self.effect, 4),
            "effectLo": round(self.effect_lo, 4),
            "effectHi": round(self.effect_hi, 4),
            "lift": None if self.lift is None else round(self.lift, 3),
            "pValue": round(self.p_value, 5),
            "qValue": round(self.q_value, 5),
            "stability": round(self.stability, 3),
            "matchCount": self.match_count,
            "successRate": None if self.success_rate is None else round(self.success_rate, 4),
            "survives": self.survives,
        }


@dataclass
class ContextReport:
    context: str
    n: int
    criteria: list[Criterion] = field(default_factory=list)
    default_response: str | None = None
    permutation_blocked: bool = True
    note: str = ""
    # funnel: how many candidate triads existed, and how many the volume floors removed
    # before any test ran. Without this, "0 survive" cannot be told apart from "0 tested".
    candidates: int = 0
    below_floor: int = 0


# ---------------------------------------------------------------- primitives


def benjamini_hochberg(pvalues: list[float]) -> list[float]:
    """BH step-up FDR. Returns q-values in the input order.

    Needed because a corpus yields thousands of candidate triads: at α=0.05 the uncorrected
    test manufactures a confident-looking criterion out of pure noise roughly one time in
    twenty, which across thousands of triads is hundreds of false rules.
    """
    n = len(pvalues)
    if n == 0:
        return []
    order = np.argsort(pvalues)
    ranked = np.asarray(pvalues, dtype=float)[order]
    q = ranked * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]  # enforce monotonicity
    out = np.empty(n, dtype=float)
    out[order] = np.clip(q, 0.0, 1.0)
    return out.tolist()


def conditional_mutual_information(table: np.ndarray) -> float:
    """I(C;B) in bits from a condition × response contingency table.

    Screening only, and reported as a percentile against the permutation null rather than
    raw: MI from a sparse table is biased upward by roughly (|C|−1)(|B|−1)/2N, which at these
    counts can exceed the signal. The null shares the table's shape and N, so the bias cancels.
    """
    total = table.sum()
    if total <= 0:
        return 0.0
    p = table / total
    pc = p.sum(axis=1, keepdims=True)
    pb = p.sum(axis=0, keepdims=True)
    denom = pc @ pb
    ratio = np.divide(p, denom, out=np.ones_like(p), where=denom > 0)
    terms = np.where(p > 0, p * np.log2(ratio, out=np.zeros_like(p), where=ratio > 0), 0.0)
    return float(np.nansum(terms))


def _posterior_effects(counts: np.ndarray) -> np.ndarray:
    """Vectorized effect matrix from a condition × response count table.

    Beta(1,1) posterior means throughout — mean = (s+1)/(n+2) — matching ``_beta_ci``.
    """
    n_cond = counts.sum(axis=1, keepdims=True)      # (A, C, *)
    per_response = counts.sum(axis=0, keepdims=True)  # (A, *, B)
    total = counts.sum()
    n_with = counts
    n_without = per_response - counts
    n_no_condition = total - n_cond
    p_with = (n_with + 1.0) / (n_cond + 2.0)
    p_without = (n_without + 1.0) / (n_no_condition + 2.0)
    return np.asarray(p_with - p_without, dtype=float)


def _blocks(match_codes: np.ndarray) -> tuple[list[np.ndarray], bool]:
    """Permutation blocks: one per match with ≥2 observations, singletons pooled.

    Shuffling condition labels globally would ignore that exchanges repeat within a match,
    producing a null with less clustering than the data and therefore too many "significant"
    results. Blocking by match fixes that, but blocks of size 1 are unshufflable — pooling
    the singletons into one residual block keeps the test from losing all its power on a
    corpus where most matches contribute a single exchange per context.

    Returns ``(blocks, clustered)``. ``clustered`` is False when every block came from the
    singleton pool: the test still runs, but it preserved no within-match structure, so its
    p-values carry a weaker guarantee and callers must say so rather than imply otherwise.
    """
    out: list[np.ndarray] = []
    singles: list[int] = []
    for m in np.unique(match_codes):
        idx = np.flatnonzero(match_codes == m)
        if idx.size >= 2:
            out.append(idx)
        else:
            singles.append(int(idx[0]))
    clustered = bool(out)
    if len(singles) >= 2:
        out.append(np.asarray(singles))
    return out, clustered


def _table(cond_codes: np.ndarray, resp_codes: np.ndarray, n_c: int, n_b: int) -> np.ndarray:
    flat = np.bincount(cond_codes * n_b + resp_codes, minlength=n_c * n_b)
    return flat.reshape(n_c, n_b).astype(float)


# ---------------------------------------------------------------- analysis


def analyze_context(
    observations: list[Observation],
    *,
    n_permutations: int = N_PERMUTATIONS,
    n_bootstrap: int = N_BOOTSTRAP,
    seed: int = SEED,
) -> ContextReport:
    """All candidate criteria for one context. p-values are raw here; FDR is applied globally."""
    context = observations[0].context if observations else ""
    usable = [o for o in observations if o.response is not None]
    report = ContextReport(context=context, n=len(usable))
    if len(usable) < MIN_CONTEXT_N:
        report.note = f"only {len(usable)} observations (need {MIN_CONTEXT_N})"
        return report

    conds = sorted({(o.condition or "") for o in usable})
    resps = sorted({o.response or "" for o in usable})
    c_index = {c: i for i, c in enumerate(conds)}
    b_index = {b: i for i, b in enumerate(resps)}
    matches = sorted({o.match_id for o in usable})
    m_index = {m: i for i, m in enumerate(matches)}

    cond_codes = np.array([c_index[o.condition or ""] for o in usable])
    resp_codes = np.array([b_index[o.response or ""] for o in usable])
    match_codes = np.array([m_index[o.match_id] for o in usable])

    counts = _table(cond_codes, resp_codes, len(conds), len(resps))
    # the response the athlete reaches for most often regardless of condition
    report.default_response = resps[int(counts.sum(axis=0).argmax())]

    if len(conds) < 2:
        report.note = "no condition contrast — every exchange carries the same condition"
        return report

    observed = _posterior_effects(counts)

    # --- permutation null: break C↔B while preserving match structure ----------
    rng = np.random.default_rng(seed)
    blocks, clustered = _blocks(match_codes)
    report.permutation_blocked = clustered
    if not clustered and blocks:
        report.note = "every match contributes one observation — null preserves no clustering"
    ge = np.zeros_like(observed)
    if blocks:
        for _ in range(n_permutations):
            permuted = cond_codes.copy()
            for blk in blocks:
                permuted[blk] = rng.permutation(permuted[blk])
            null = _posterior_effects(_table(permuted, resp_codes, len(conds), len(resps)))
            ge += null >= observed
        pvals = (ge + 1.0) / (n_permutations + 1.0)  # add-one: never report p = 0
    else:
        pvals = np.ones_like(observed)
        report.note = "not permutable — every match contributes one observation"

    # --- match-level bootstrap: does the effect survive resampling whole matches?
    stability = np.zeros_like(observed)
    if n_bootstrap:
        by_match = [np.flatnonzero(match_codes == i) for i in range(len(matches))]
        for _ in range(n_bootstrap):
            pick = rng.integers(0, len(by_match), len(by_match))
            idx = np.concatenate([by_match[i] for i in pick])
            tbl = _table(cond_codes[idx], resp_codes[idx], len(conds), len(resps))
            # The cell must still HAVE evidence in the resample. Counting "effect > 0" alone
            # scores a 1-of-1 triad as perfectly stable: when its only match is resampled
            # away the count drops to zero, the Beta(1,1) prior floors the estimate at 0.5,
            # and that is still above a small baseline — so the effect stays "positive" on
            # the strength of the prior alone. Requiring a non-empty cell makes stability
            # measure what it claims: does this survive not seeing some of the matches.
            stability += (_posterior_effects(tbl) > 0) & (tbl > 0)
        stability /= n_bootstrap

    n_cond_tot = counts.sum(axis=1)
    per_resp = counts.sum(axis=0)
    total = counts.sum()

    for ci, cond in enumerate(conds):
        if not cond:
            continue  # "no condition observed" is the contrast, not a criterion
        for bi, resp in enumerate(resps):
            n_with = int(counts[ci, bi])
            if n_with == 0:
                continue
            report.candidates += 1
            rows_all = [
                o for o in usable
                if (o.condition or "") == cond and (o.response or "") == resp
            ]
            n_matches = len({o.match_id for o in rows_all})
            # "" means the opponent was never recorded — that is unknown, not "one opponent",
            # so the floor is skipped rather than failed on data we do not have.
            known_opps = {o.opponent_id for o in rows_all if o.opponent_id}
            if (
                n_with < MIN_TRIAD_N
                or n_matches < MIN_TRIAD_MATCHES
                or (known_opps and len(known_opps) < MIN_TRIAD_OPPONENTS)
            ):
                report.below_floor += 1
                continue
            n_condition = int(n_cond_tot[ci])
            n_without = int(per_resp[bi] - counts[ci, bi])
            n_no_condition = int(total - n_cond_tot[ci])
            mean_w, lo_w, hi_w = _beta_ci(n_with, n_condition)
            mean_o, lo_o, hi_o = _beta_ci(n_without, n_no_condition)
            known = [o.success for o in rows_all if o.success is not None]
            crit = Criterion(
                context=context, condition=cond, response=resp,
                n_with=n_with, n_condition=n_condition,
                n_without=n_without, n_no_condition=n_no_condition,
                p_with=mean_w, p_without=mean_o,
                effect=mean_w - mean_o,
                effect_lo=lo_w - hi_o,   # worst case, as in reward_risk_with_ci
                effect_hi=hi_w - lo_o,
                lift=(mean_w / mean_o) if mean_o > 0 else None,
                p_value=float(pvals[ci, bi]),
                stability=float(stability[ci, bi]),
                match_count=n_matches,
                success_rate=(sum(1 for s in known if s) / len(known)) if known else None,
            )
            report.criteria.append(crit)
    return report


def analyze(
    observations: list[Observation],
    *,
    n_permutations: int = N_PERMUTATIONS,
    n_bootstrap: int = N_BOOTSTRAP,
    seed: int = SEED,
    fdr_q: float = FDR_Q,
    min_stability: float = MIN_STABILITY,
) -> list[ContextReport]:
    """Full pass: per-context criteria, then ONE global FDR correction across every triad."""
    by_context: dict[str, list[Observation]] = {}
    for o in observations:
        by_context.setdefault(o.context, []).append(o)

    reports = [
        analyze_context(
            obs, n_permutations=n_permutations, n_bootstrap=n_bootstrap, seed=seed
        )
        for _, obs in sorted(by_context.items())
    ]

    flat = [c for r in reports for c in r.criteria]
    for crit, q in zip(flat, benjamini_hochberg([c.p_value for c in flat]), strict=True):
        crit.q_value = q
        crit.survives = (
            crit.effect_lo > 0.0 and q <= fdr_q and crit.stability >= min_stability
        )
    for r in reports:
        r.criteria.sort(key=lambda c: (-c.effect_lo, -c.n_with))
    return reports


# ---------------------------------------------------------------- level selection


@dataclass
class LevelChoice:
    """Which abstraction level of a condition actually explains the response."""

    condition: str
    chosen: str
    chain: list[str] = field(default_factory=list)
    reason: str = "no-hierarchy"       # specific | generalized | insufficient | no-hierarchy
    effect: float = 0.0                # vs SIBLINGS, not vs the parent
    effect_lo: float = 0.0
    n_level: int = 0
    n_siblings: int = 0


def sibling_contrast(
    observations: list[Observation],
    response: str,
    level_members: set[str],
    sibling_members: set[str],
) -> tuple[float, float, int, int]:
    """P(B | level) − P(B | siblings of that level), as (effect, ci_lo, n_level, n_siblings).

    The contrast is against SIBLINGS, never the parent. Child events are a subset of parent
    events, so "is Ashi Barai more predictive than Ashi Waza" compares a set against one that
    contains it — the parent statistic is partly made of the child's own observations, and the
    comparison cannot answer the question. The real question is whether this child differs from
    the OTHER children of the same parent.
    """
    inside = [o for o in observations if (o.condition or "") in level_members]
    outside = [o for o in observations if (o.condition or "") in sibling_members]
    if not inside or not outside:
        return 0.0, 0.0, len(inside), len(outside)
    hits_in = sum(1 for o in inside if (o.response or "") == response)
    hits_out = sum(1 for o in outside if (o.response or "") == response)
    mean_i, lo_i, hi_i = _beta_ci(hits_in, len(inside))
    mean_o, lo_o, hi_o = _beta_ci(hits_out, len(outside))
    return mean_i - mean_o, lo_i - hi_o, len(inside), len(outside)


def select_level(
    observations: list[Observation],
    condition: str,
    response: str,
    *,
    levels_of: Any,
    members_of: Any,
    min_level_n: int = 4,
) -> LevelChoice:
    """Pick the most specific level of ``condition`` that is justified by the data.

    ``levels_of(condition) -> [condition, parent, grandparent, ...]`` (specific first) and
    ``members_of(level) -> set of condition keys rolling up to it`` make the hierarchy source
    pluggable: the technique taxonomy for technique-derived conditions, some other grouping
    for postural ones. With no hierarchy the answer is trivially the condition itself.

    Walks specific → general and stops at the first level that differs from its own siblings.
    When every sibling behaves alike the specific label is not what drives the decision, so
    the general one is the truer description — "specific when justified, abstract when
    generalized".
    """
    chain = list(levels_of(condition) or [])
    choice = LevelChoice(condition=condition, chosen=condition, chain=chain)
    if len(chain) < 2:
        return choice

    for i, level in enumerate(chain[:-1]):
        parent = chain[i + 1]
        members = set(members_of(level) or set())
        siblings = set(members_of(parent) or set()) - members
        effect, lo, n_lvl, n_sib = sibling_contrast(observations, response, members, siblings)
        if n_lvl < min_level_n or n_sib < min_level_n:
            choice.reason = "insufficient"
            continue
        if lo > 0.0:
            choice.chosen, choice.reason = level, "specific"
            choice.effect, choice.effect_lo = effect, lo
            choice.n_level, choice.n_siblings = n_lvl, n_sib
            return choice

    # nothing distinguished itself from its siblings -> the family IS the explanation
    choice.chosen = chain[-1]
    if choice.reason != "insufficient":
        choice.reason = "generalized"
    return choice


def condition_hierarchy(conditions: set[str]) -> tuple[Any, Any]:
    """``(levels_of, members_of)`` for :func:`select_level`, over both ladders.

    Conditions come from two different worlds and each needs its own ladder:

    * curated postural conditions (``cond:sprawls``, ``cond:hand-fight``) climb the
      **condition families** in ``analysis.opponent_conditions`` — the technique taxonomy has
      nothing to say about a posture;
    * technique-derived conditions (``cond:closed-guard``) climb the **technique taxonomy**;
    * composite bundles (``a-and-b``) belong to two families at once, so they stay flat rather
      than being forced into whichever half happens to sort first.

    ``conditions`` is the observed condition vocabulary, needed to answer "which conditions
    roll up to this taxonomy level".
    """
    from analysis.opponent_conditions import condition_family, family_members

    chains = {c: (
        [c, fam] if (fam := condition_family(c)) else taxonomy_levels(c)
    ) for c in conditions}

    def levels_of(condition: str) -> list[str]:
        return chains.get(condition, [])

    def members_of(level: str) -> set[str]:
        if level.startswith("fam:"):
            return family_members(level) & conditions
        if level.startswith("tax:"):
            return {c for c, chain in chains.items() if level in chain}
        return {level} if level in conditions else set()

    return levels_of, members_of


def taxonomy_levels(condition: str) -> list[str]:
    """Hierarchy for a ``cond:<technique-key>`` condition, via the technique taxonomy.

    Returns ``[]`` for the curated postural conditions (``cond:sprawls``, ``cond:hand-fight``,
    ``cond:elbow-free`` …): those describe a posture or a reaction, not a technique, so the
    technique taxonomy has nothing to say about them and pretending otherwise would invent a
    hierarchy that does not exist. They stay flat until a condition ontology exists.
    """
    from analysis.taxonomy import load_taxonomy

    if not condition.startswith("cond:"):
        return []
    tax = load_taxonomy()
    node = tax.resolve(condition[len("cond:"):].replace("-", " "))
    if node is None:
        return []
    return [condition, *(f"tax:{a}" for a in tax.chain(node)[1:])]


def observations_from_patterns(patterns: list[Any]) -> list[Observation]:
    """Raw (UNaggregated) DecisionPattern list -> observations.

    Must be the RAW output of a decision-flow extractor: ``aggregate_patterns`` folds
    counts and caps evidence at 8, which destroys the per-match detail the permutation and
    bootstrap both depend on.
    """
    out: list[Observation] = []
    for p in patterns:
        ev = p.evidence[0] if p.evidence else None
        action = p.action_key or ""
        source = p.source_position_key or "-"
        success: bool | None = None
        if p.success_count:
            success = True
        elif p.failure_count:
            success = False
        out.append(Observation(
            match_id=ev.match_id if ev else "",
            athlete_id=ev.athlete_id if ev else "",
            context=f"{source}|{action}",
            condition=p.condition_key,
            response=p.response_key,
            success=success,
            opponent_id=getattr(ev, "opponent_id", "") if ev else "",
        ))
    return out


def to_json(reports: list[ContextReport]) -> dict[str, Any]:
    survivors = [c for r in reports for c in r.criteria if c.survives]
    return {
        "contexts": len(reports),
        # the funnel — "0 survive" and "0 were ever eligible" are different diagnoses
        "candidateTriads": sum(r.candidates for r in reports),
        "belowVolumeFloor": sum(r.below_floor for r in reports),
        "criteriaTested": sum(len(r.criteria) for r in reports),
        "criteriaSurviving": len(survivors),
        "gates": {
            "fdrQ": FDR_Q, "minStability": MIN_STABILITY, "minContextN": MIN_CONTEXT_N,
            "minTriadN": MIN_TRIAD_N, "minTriadMatches": MIN_TRIAD_MATCHES,
            "minTriadOpponents": MIN_TRIAD_OPPONENTS,
            "permutations": N_PERMUTATIONS, "bootstrap": N_BOOTSTRAP, "seed": SEED,
        },
        "reports": [
            {
                "context": r.context,
                "n": r.n,
                "defaultResponse": r.default_response,
                "permutationBlocked": r.permutation_blocked,
                "note": r.note,
                "criteria": [c.to_json() for c in r.criteria],
            }
            for r in reports
        ],
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Decision criteria for one athlete (read-only)")
    ap.add_argument("--athlete", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--permutations", type=int, default=N_PERMUTATIONS)
    args = ap.parse_args()

    from analysis.decision_flow import extract_chain_patterns
    from analysis.names import _normalize_name, athlete_key
    from analysis.perspective_sequence import perspective_events, sequence_boundaries
    from db.base import db_session
    from db.models import Athlete, Reaction
    from db.repository import get_matches_for_athlete

    with db_session() as session:
        target = next(
            (a for a in session.query(Athlete).all()
             if athlete_key(a.name) == athlete_key(args.athlete)), None)
        if target is None:
            logger.error("athlete not found: %s", args.athlete)
            return 1
        reactions = list(session.query(Reaction).all())
        raw: list[Any] = []
        for m in get_matches_for_athlete(target.id, session):
            if m.status != "final" or not (getattr(m, "sequence", None) or []):
                continue
            sub = getattr(m, "submission", None)
            raw.extend(extract_chain_patterns(
                perspective_events(m, target.id),
                # only match_id matters here — it is the clustering unit for the permutation
                # blocks and the bootstrap. Slugs are display, and nothing here displays.
                match_id=str(m.id), athlete_id=str(target.id),
                boundaries=sequence_boundaries(m, target.id), reaction_catalog=reactions,
                winning_submission_key=_normalize_name(str(sub)) if sub else None,
            ))
        reports = analyze(observations_from_patterns(raw), n_permutations=args.permutations)

    payload = to_json(reports)
    payload["athlete"] = target.name
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "%s: %d contexts, %d triads tested, %d survive → %s",
        target.name, payload["contexts"], payload["criteriaTested"],
        payload["criteriaSurviving"], args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
