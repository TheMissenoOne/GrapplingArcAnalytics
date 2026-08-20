"""Interval arithmetic for categorical sport data, so no point estimate is published alone.

Every function here returns an estimate WITH its uncertainty, and every comparison returns an
effect size WITH an interval and a p-value. That is the whole purpose: a proportion computed
off 12 bouts and one computed off 400 look identical as percentages and mean entirely
different things, and the difference only becomes visible when the interval travels with the
number.

**Why Wilson and not the normal approximation.** Most proportions in this corpus sit near 0
or 1 with n under 40 -- "wins by leg lock", "loses by decision". The Wald interval runs
outside [0, 1] there and is badly anti-conservative exactly where the data is thinnest, which
is the opposite of what a confidence display is for.

**Why Agresti-Caffo for differences and the delta method for ratios.** A difference of two
proportions and their ratio carry BOTH input errors; reporting "6% against 10%" as though the
gap were established is the specific mistake this module exists to make impossible. The ratio
interval is computed on log(RR), where the sampling distribution is far closer to normal.

**Why multiplicity is handled here and not left to the caller.** A category report runs
dozens of comparisons. At alpha 0.05, twenty independent nulls produce one "finding" by
construction, and a reader has no way to see that from the table. `benjamini_hochberg` is
supplied so a family of tests can be reported with its false-discovery rate controlled.

Nothing in this module knows about grappling. It takes counts.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from scipy import stats

Z95 = 1.959963984540054

# Confidence grading. ONE rule, applied everywhere, so a "low confidence" badge means the same
# thing on every table in every report. The cuts are editorial, not statistical -- they are
# stated here so they can be argued with rather than rediscovered from behaviour.
GRADE_CUTS = ((0.08, "adequate"), (0.15, "moderate"))
MIN_N_FOR_ANY_GRADE = 5


@dataclass(frozen=True)
class Estimate:
    """A proportion and its interval. ``grade`` is derived from the interval's half-width, so
    it cannot drift away from the number it describes."""

    k: int
    n: int
    p: float | None
    lo: float | None
    hi: float | None
    half: float | None
    grade: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grade(n: int, half: float | None) -> str:
    if n == 0 or half is None:
        return "none"
    if n < MIN_N_FOR_ANY_GRADE:
        return "insufficient"
    for cut, name in GRADE_CUTS:
        if half <= cut:
            return name
    return "low"


def wilson(k: int, n: int, z: float = Z95) -> Estimate:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return Estimate(k, n, None, None, None, None, "none")
    if k < 0 or k > n:
        raise ValueError(f"k={k} outside 0..n={n}")
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return Estimate(k, n, p, max(0.0, centre - half), min(1.0, centre + half), half,
                    grade(n, half))


@dataclass(frozen=True)
class Contrast:
    """Two proportions compared. Carries the difference AND the ratio because they answer
    different questions: the difference is what changes in absolute terms, the ratio is how
    many times more likely -- and a small difference can be a large ratio when both rates are
    low, which is precisely the case in submission data."""

    a: Estimate
    b: Estimate
    diff: float | None
    diff_lo: float | None
    diff_hi: float | None
    ratio: float | None
    ratio_lo: float | None
    ratio_hi: float | None
    p_value: float | None
    test: str
    significant: bool

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["a"] = self.a.to_dict()
        d["b"] = self.b.to_dict()
        return d


def compare_proportions(k1: int, n1: int, k2: int, n2: int, z: float = Z95) -> Contrast:
    """Difference (Agresti-Caffo) and ratio (delta method on log RR) for two proportions.

    Agresti-Caffo adds one success and one failure to each arm before forming the Wald
    interval. That sounds like a fudge and is not: it is the interval with the best measured
    coverage at small n, where the plain Wald interval is worst.

    ``p_value`` uses Fisher's exact test when any expected cell falls below 5, and a
    chi-square with continuity correction otherwise. ``significant`` reports the ratio
    interval excluding 1 -- deliberately the same criterion a reader applies by eye, so the
    flag and the printed interval can never disagree.
    """
    a, b = wilson(k1, n1, z), wilson(k2, n2, z)
    if n1 <= 0 or n2 <= 0:
        return Contrast(a, b, None, None, None, None, None, None, None, "none", False)

    # Agresti-Caffo
    p1, p2 = (k1 + 1) / (n1 + 2), (k2 + 1) / (n2 + 2)
    se = math.sqrt(p1 * (1 - p1) / (n1 + 2) + p2 * (1 - p2) / (n2 + 2))
    d, dlo, dhi = (k1 / n1) - (k2 / n2), (p1 - p2) - z * se, (p1 - p2) + z * se

    rr = rlo = rhi = None
    if k1 > 0 and k2 > 0:
        rr = (k1 / n1) / (k2 / n2)
        se_log = math.sqrt((1 - k1 / n1) / k1 + (1 - k2 / n2) / k2)
        rlo, rhi = rr * math.exp(-z * se_log), rr * math.exp(z * se_log)

    table = [[k1, n1 - k1], [k2, n2 - k2]]
    exp_min = _min_expected(table)
    if exp_min < 5:
        pval, test = stats.fisher_exact(table)[1], "fisher exact"
    else:
        pval, test = stats.chi2_contingency(table, correction=True)[1], "chi-square (Yates)"

    sig = rlo is not None and rhi is not None and (rlo > 1.0 or rhi < 1.0)
    return Contrast(a, b, d, dlo, dhi, rr, rlo, rhi, float(pval), test, sig)


def _min_expected(table: Sequence[Sequence[int]]) -> float:
    rows = [sum(r) for r in table]
    cols = [sum(c) for c in zip(*table)]
    total = sum(rows)
    if total == 0:
        return 0.0
    return float(min(r * c / total for r in rows for c in cols))


@dataclass(frozen=True)
class Heterogeneity:
    """Is a k x m contingency table anything other than one common distribution?"""

    chi2: float
    p_value: float
    dof: int
    cramers_v: float
    min_expected: float
    reliable: bool
    n: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def heterogeneity(table: Sequence[Sequence[int]]) -> Heterogeneity:
    """Chi-square of independence with Cramer's V as the effect size.

    ``reliable`` is False when the smallest expected count drops below 5, which is where the
    chi-square approximation stops holding. It is reported rather than silently swallowed:
    an unreliable test on a sparse table is the normal case in this data, not an exception.
    """
    arr = [list(map(int, r)) for r in table]
    n = sum(sum(r) for r in arr)
    if n == 0 or len(arr) < 2 or len(arr[0]) < 2:
        return Heterogeneity(0.0, 1.0, 0, 0.0, 0.0, False, n)
    chi2, p, dof, _ = stats.chi2_contingency(arr, correction=False)
    k = min(len(arr), len(arr[0]))
    v = math.sqrt(float(chi2) / (n * (k - 1))) if n and k > 1 else 0.0
    mexp = _min_expected(arr)
    return Heterogeneity(float(chi2), float(p), int(dof), v, mexp, mexp >= 5, n)


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> list[bool]:
    """Which of a family of tests survive at false-discovery rate ``alpha``.

    Returned in the caller's order. Run this over every p-value a report prints, not over the
    ones that looked interesting -- selecting first and correcting after is the same error the
    correction exists to prevent.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    keep = [False] * m
    largest = -1
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= alpha * rank / m:
            largest = rank
    for rank, i in enumerate(order, start=1):
        if rank <= largest:
            keep[i] = True
    return keep


def bootstrap_ci(values: Sequence[float], statistic: Any, n_boot: int = 5000,
                 z: float = Z95, seed: int = 20260820) -> tuple[float, float, float]:
    """Percentile bootstrap for a statistic with no closed-form interval.

    Deterministic by default: a report that renders a different interval on each run is not
    reproducible, and reproducibility matters more here than a fresh random draw.
    """
    import random

    if not values:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    obs = float(statistic(values))
    n = len(values)
    draws = sorted(float(statistic([values[rng.randrange(n)] for _ in range(n)]))
                   for _ in range(n_boot))
    tail = (1 - _two_sided_mass(z)) / 2
    lo = draws[max(0, int(tail * n_boot))]
    hi = draws[min(n_boot - 1, int((1 - tail) * n_boot))]
    return obs, lo, hi


def _two_sided_mass(z: float) -> float:
    return float(stats.norm.cdf(z) - stats.norm.cdf(-z))


def shannon_concentration(counts: Sequence[int]) -> dict[str, float]:
    """How much of a category's evidence comes from how few sources.

    HHI and top-1 share, because a category profile assembled mostly from one athlete is that
    athlete's profile wearing the category's name. ``effective_n`` is the inverse HHI: the
    number of equally-weighted contributors this evidence is actually worth.
    """
    total = sum(counts)
    if total <= 0:
        return {"hhi": 0.0, "top1": 0.0, "effective_n": 0.0, "sources": 0}
    shares = [c / total for c in counts if c > 0]
    hhi = sum(s * s for s in shares)
    return {"hhi": hhi, "top1": max(shares), "effective_n": 1 / hhi if hhi else 0.0,
            "sources": len(shares)}


@dataclass(frozen=True)
class RankCorrelation:
    rho: float
    p_value: float
    n: int
    lo: float
    hi: float
    grade: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> RankCorrelation:
    """Rank correlation with a Fisher-z interval.

    Rank-based rather than Pearson because nothing here is plausibly linear or normal --
    ratings, event counts and shares are all bounded or skewed, and a Pearson r on them
    reports a relationship whose shape was assumed rather than observed.

    The interval comes from Fisher's z with SE 1/sqrt(n-3), which is an approximation for
    Spearman; it is reported because a rho with no interval is the same failure this module
    exists to prevent, and it is graded off its own width so a thin sample says so.
    """
    n = len(xs)
    if n != len(ys):
        raise ValueError("spearman needs equal-length inputs")
    if n < 4:
        return RankCorrelation(float("nan"), 1.0, n, float("nan"), float("nan"), "insufficient")
    rho, p = stats.spearmanr(xs, ys)
    rho = float(rho)
    z = 0.5 * math.log((1 + rho) / (1 - rho)) if abs(rho) < 1 else math.copysign(18.0, rho)
    se = 1 / math.sqrt(n - 3)
    lo, hi = (math.tanh(z - Z95 * se), math.tanh(z + Z95 * se))
    return RankCorrelation(rho, float(p), n, lo, hi, grade(n, (hi - lo) / 2))


@dataclass(frozen=True)
class Separation:
    """How well a continuous measure separates two outcomes."""

    auc: float
    lo: float
    hi: float
    n_pos: int
    n_neg: int
    grade: str
    interpretation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def auc(scores: Sequence[float], labels: Sequence[bool], n_boot: int = 4000,
        seed: int = 20260820) -> Separation:
    """Area under the ROC curve, with a percentile-bootstrap interval.

    AUC is the probability that a randomly chosen positive scores above a randomly chosen
    negative. 0.5 is a coin flip, and the interval is what says whether a measured 0.68 is
    distinguishable from one -- with samples this size it usually is not, which is the point
    of computing it rather than quoting the number alone.
    """
    import random

    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return Separation(float("nan"), float("nan"), float("nan"), len(pos), len(neg),
                          "none", "one class is empty")

    def _a(p: Sequence[float], q: Sequence[float]) -> float:
        wins = sum(1.0 if x > y else 0.5 if x == y else 0.0 for x in p for y in q)
        return wins / (len(p) * len(q))

    obs = _a(pos, neg)
    rng = random.Random(seed)
    draws = sorted(_a([pos[rng.randrange(len(pos))] for _ in pos],
                      [neg[rng.randrange(len(neg))] for _ in neg]) for _ in range(n_boot))
    lo, hi = draws[int(0.025 * n_boot)], draws[min(n_boot - 1, int(0.975 * n_boot))]
    crosses = lo <= 0.5 <= hi
    return Separation(obs, lo, hi, len(pos), len(neg),
                      grade(len(pos) + len(neg), (hi - lo) / 2),
                      "not distinguishable from chance" if crosses else "separates the classes")
