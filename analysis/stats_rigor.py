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
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from scipy import stats

Z95 = 1.959963984540054

# Confidence grading. ONE rule, applied everywhere, so a "low confidence" badge means the same
# thing on every table in every report. The cuts are editorial, not statistical -- they are
# stated here so they can be argued with rather than rediscovered from behaviour.
GRADE_CUTS = ((0.08, "adequate"), (0.15, "moderate"))
MIN_N_FOR_ANY_GRADE = 5

# PRECISION is not COVERAGE. The cuts above answer "how wide is this interval", which is a
# question about arithmetic. They say nothing about how many independent sources the rows came
# from, and a hundred events from one athlete will earn a narrow interval and an "adequate"
# badge while describing one person wearing the category's name.
#
# So a category-level claim is gated separately, on the number of independent clusters and on
# how evenly the evidence is spread across them. Below the gate the honest output is no
# interval at all -- not a wider one. Measured on this corpus: with two contributing athletes a
# cluster bootstrap returns an interval NARROWER than the naive one, because it is resampling
# two things. Clustering rescues an estimate that has enough sources; it cannot manufacture
# sources that are not there.
MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE = 5
MIN_EFFECTIVE_N_FOR_CATEGORY_ESTIMATE = 2.0
COVERAGE_CUTS = ((5.0, "adequate"), (3.0, "moderate"))


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


def _chi2(table: Sequence[Sequence[int]]) -> float:
    """Pearson chi-square, no correction. Small tables only -- this is called tens of
    thousands of times inside the permutation loop, where a scipy round-trip per draw would
    dominate the runtime."""
    rows = [sum(r) for r in table]
    cols = [sum(c) for c in zip(*table, strict=True)]
    n = sum(rows)
    if not n:
        return 0.0
    total = 0.0
    for i, r in enumerate(table):
        for j, obs in enumerate(r):
            exp = rows[i] * cols[j] / n
            if exp:
                total += (obs - exp) ** 2 / exp
    return total


def permutation_p(table: Sequence[Sequence[int]], n_draws: int = 10000,
                  seed: int = 20260820) -> float | None:
    """Monte-Carlo exact p for a contingency table, for when the chi-square approximation is
    not usable.

    The alternative in place was to print the chi-square p anyway with a red "approximation
    unreliable" label beside it. If a number is known to be wrong, labelling it does not make
    it readable -- the reader still has to decide how wrong, which is the part nobody can do
    by eye. Shuffling the column labels across the observations preserves both margins and
    answers the same question without the approximation.
    """
    import random

    obs = [(i, j) for i, row in enumerate(table) for j, c in enumerate(row) for _ in range(c)]
    if len(obs) < 2 or len(table) < 2 or len(table[0]) < 2:
        return None
    target = _chi2(table)
    cols = [j for _, j in obs]
    rng = random.Random(seed)
    shape = (len(table), len(table[0]))
    hits = 0
    for _ in range(n_draws):
        rng.shuffle(cols)
        grid = [[0] * shape[1] for _ in range(shape[0])]
        for (i, _), j in zip(obs, cols, strict=True):
            grid[i][j] += 1
        if _chi2(grid) >= target:
            hits += 1
    # +1 in both parts: a Monte-Carlo p of exactly zero claims more than the draws support.
    return (hits + 1) / (n_draws + 1)


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
    p_permutation: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def heterogeneity(table: Sequence[Sequence[int]]) -> Heterogeneity:
    """Chi-square of independence with Cramer's V as the effect size.

    ``reliable`` is False when the smallest expected count drops below 5, which is where the
    chi-square approximation stops holding. It is reported rather than silently swallowed:
    an unreliable test on a sparse table is the normal case in this data, not an exception.

    When it is unreliable, a Monte-Carlo permutation p is computed alongside so the reader has
    a number that does not depend on the approximation, instead of a suspect one wearing a
    warning label.
    """
    arr = [list(map(int, r)) for r in table]
    n = sum(sum(r) for r in arr)
    if n == 0 or len(arr) < 2 or len(arr[0]) < 2:
        return Heterogeneity(0.0, 1.0, 0, 0.0, 0.0, False, n, None)
    chi2, p, dof, _ = stats.chi2_contingency(arr, correction=False)
    k = min(len(arr), len(arr[0]))
    v = math.sqrt(float(chi2) / (n * (k - 1))) if n and k > 1 else 0.0
    mexp = _min_expected(arr)
    perm = None if mexp >= 5 else permutation_p(arr)
    return Heterogeneity(float(chi2), float(p), int(dof), v, mexp, mexp >= 5, n, perm)


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.05) -> list[float]:
    """BH step-up FDR. Returns Q-VALUES in the caller's order, not a pass/fail flag.

    A boolean answers "did this survive at the alpha I happened to pick", which forces the
    reader to trust the pick. A q-value is the smallest FDR at which the finding survives, so
    it can be read, compared across panels, and argued with. `alpha` is accepted only so
    `survives_bh` can be derived at the same call site.

    Monotonicity is enforced downward from the largest rank, which is what makes the q-values
    a step-up procedure rather than a per-test rescaling.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, p_values[i] * m / rank)
        q[i] = min(1.0, max(0.0, running))
    return q


def survives(q_values: Sequence[float], alpha: float = 0.05) -> list[bool]:
    """Which q-values clear the false-discovery rate the report declares."""
    return [q <= alpha for q in q_values]


def bootstrap_ci(values: Sequence[float], statistic: Any, n_boot: int = 5000,
                 z: float = Z95, seed: int = 20260820,
                 groups: Sequence[Any] | None = None) -> tuple[float, float, float]:
    """Percentile bootstrap for a statistic with no closed-form interval.

    Deterministic by default: a report that renders a different interval on each run is not
    reproducible, and reproducibility matters more here than a fresh random draw.

    ``groups`` gives one cluster label per value and switches this to a CLUSTER bootstrap:
    whole clusters are drawn with replacement and their members carried along, so the
    resampling unit becomes the athlete (or the bout) rather than the row. That is the right
    unit whenever rows within a cluster are not independent -- a hundred events from one
    athlete are not a hundred independent observations of a division.

    It is not a rescue. With a handful of clusters the interval is unstable in both directions,
    and on this corpus a two-athlete division returns a NARROWER interval clustered than naive.
    Gate the estimate on ``coverage`` first; use this only once the gate passes.
    """
    import random

    if not values:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    obs = float(statistic(values))
    tail = (1 - _two_sided_mass(z)) / 2

    draw: Callable[[], list[float]]
    if groups is None:
        n = len(values)
        draw = lambda: [values[rng.randrange(n)] for _ in range(n)]  # noqa: E731
    else:
        if len(groups) != len(values):
            raise ValueError(f"groups has {len(groups)} labels for {len(values)} values")
        buckets: dict[Any, list[float]] = {}
        for label, v in zip(groups, values, strict=True):
            buckets.setdefault(label, []).append(v)
        members = list(buckets.values())
        g = len(members)
        draw = lambda: [v for _ in range(g) for v in members[rng.randrange(g)]]  # noqa: E731

    draws = sorted(float(statistic(sample)) for _ in range(n_boot)
                   if (sample := draw()))
    if not draws:
        return obs, float("nan"), float("nan")
    lo = draws[max(0, int(tail * len(draws)))]
    hi = draws[min(len(draws) - 1, int((1 - tail) * len(draws)))]
    return obs, lo, hi


def _two_sided_mass(z: float) -> float:
    return float(stats.norm.cdf(z) - stats.norm.cdf(-z))


def inverse_hhi_concentration(counts: Sequence[int]) -> dict[str, float]:
    """How much of a category's evidence comes from how few sources.

    HHI and top-1 share, because a category profile assembled mostly from one athlete is that
    athlete's profile wearing the category's name. ``effective_n`` is the inverse HHI: the
    number of equally-weighted contributors this evidence is actually worth.

    This is the Hill number of order 2, not order 1 -- it was called ``shannon_concentration``
    for a while, which named a different quantity than the one it returns.
    """
    total = sum(counts)
    if total <= 0:
        return {"hhi": 0.0, "top1": 0.0, "effective_n": 0.0, "sources": 0}
    shares = [c / total for c in counts if c > 0]
    hhi = sum(s * s for s in shares)
    return {"hhi": hhi, "top1": max(shares), "effective_n": 1 / hhi if hhi else 0.0,
            "sources": len(shares)}


@dataclass(frozen=True)
class Coverage:
    """How many independent sources an estimate rests on, and whether that is enough to make a
    claim about the population rather than about the rows.

    ``estimable`` is the publication gate. When it is false the caller must emit ``reason``
    and the observed counts INSTEAD OF an interval, because every interval available at that
    point -- naive or clustered -- is measuring the wrong thing.
    """

    clusters: int
    effective_n: float
    top_share: float
    grade: str
    estimable: bool
    # `reason` is developer-facing English for logs and tests. `reason_code` is what a UI
    # renders from, together with the numbers already on this object -- a report in another
    # language cannot translate a sentence it was handed, and printing this one raw is how
    # English ended up on a Portuguese page.
    reason: str | None
    reason_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def coverage(counts: Sequence[int]) -> Coverage:
    """Grade the source spread behind an estimate. ``counts`` is one entry per source."""
    conc = inverse_hhi_concentration(counts)
    clusters, eff = int(conc["sources"]), float(conc["effective_n"])
    if clusters == 0:
        return Coverage(0, 0.0, 0.0, "none", False, "no sources", "no_sources")
    reason = code = None
    if clusters < MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE:
        code = "few_clusters"
        reason = (f"{clusters} source(s) contribute; a category estimate needs "
                  f"{MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE}")
    elif eff < MIN_EFFECTIVE_N_FOR_CATEGORY_ESTIMATE:
        code = "dominated"
        reason = (f"evidence is worth {eff:.2f} equally-weighted sources; the largest single "
                  f"source is {conc['top1']:.0%} of it")
    for cut, name in COVERAGE_CUTS:
        if eff >= cut:
            break
    else:
        name = "low"
    return Coverage(clusters, eff, float(conc["top1"]), name, reason is None, reason, code)


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
    # A stable key for the verdict above. Consumers render in their own language, and a
    # renderer that string-compares the English sentence silently leaks it the day the
    # wording changes.
    verdict: str

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
                          "none", "one class is empty", "empty_class")

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
                      "not distinguishable from chance" if crosses else "separates the classes",
                      "chance" if crosses else "separates")
