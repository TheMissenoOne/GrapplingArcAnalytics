"""The interval arithmetic has to be right, because every published number leans on it.

Checked against values a reader can verify by hand or against a published worked example,
not against this module's own output.
"""
from __future__ import annotations

import math

import pytest

from analysis.stats_rigor import (
    auc,
    benjamini_hochberg,
    bootstrap_ci,
    compare_proportions,
    grade,
    heterogeneity,
    shannon_concentration,
    spearman,
    wilson,
)


def f(x: float | None) -> float:
    """Narrow an Optional at the assertion site. A None here is itself a failure, and saying
    so once beats scattering `is not None` through every test."""
    assert x is not None
    return x


def test_wilson_matches_an_independent_recomputation() -> None:
    """15 of 148, the Newcombe (1998) worked case. Plain Wilson is (0.0624, 0.1605); the
    (0.0625, 0.1620) often quoted for this case is the CONTINUITY-CORRECTED variant, which is
    a different, wider interval. This module reports plain Wilson, so the test pins that."""
    e = wilson(15, 148)
    k, n, z = 15, 148, 1.959963984540054
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    assert f(e.lo) == pytest.approx(centre - half, abs=1e-12)
    assert f(e.hi) == pytest.approx(centre + half, abs=1e-12)
    assert (round(f(e.lo), 4), round(f(e.hi), 4)) == (0.0624, 0.1605)


def test_wilson_never_leaves_the_unit_interval() -> None:
    """The reason this module does not use the Wald interval: at k=0 Wald gives [0, 0] and at
    k=n it gives [1, 1], both claiming certainty from a handful of observations."""
    for k, n in ((0, 7), (7, 7), (1, 3), (0, 1)):
        e = wilson(k, n)
        assert 0.0 <= f(e.lo) <= f(e.hi) <= 1.0
    assert f(wilson(0, 7).hi) > 0, "zero successes is not proof the rate is zero"
    assert f(wilson(7, 7).lo) < 1, "seven for seven is not proof the rate is one"


def test_wilson_interval_narrows_as_n_grows() -> None:
    widths = [f(wilson(n // 2, n).half) for n in (10, 40, 160, 640)]
    assert widths == sorted(widths, reverse=True)


def test_empty_and_invalid_inputs() -> None:
    assert wilson(0, 0).grade == "none"
    with pytest.raises(ValueError):
        wilson(5, 3)


def test_grade_is_derived_from_the_interval_not_from_n_alone() -> None:
    assert grade(0, None) == "none"
    assert grade(4, 0.01) == "insufficient", "n < 5 is insufficient however tight the interval"
    assert grade(100, 0.05) == "adequate"
    assert grade(100, 0.12) == "moderate"
    assert grade(100, 0.30) == "low"


def test_compare_flags_no_difference_when_the_ratio_interval_covers_one() -> None:
    """The case that mattered: 23/372 against 13/126 looks like a doubling and is not
    distinguishable from no difference at this sample size."""
    c = compare_proportions(13, 126, 23, 372)
    assert f(c.ratio) == pytest.approx(1.669, abs=0.01)
    assert f(c.ratio_lo) < 1.0 < f(c.ratio_hi)
    assert c.significant is False


def test_compare_flags_a_real_difference() -> None:
    c = compare_proportions(90, 100, 10, 100)
    assert c.significant is True
    assert f(c.ratio_lo) > 1.0
    assert f(c.p_value) < 0.001


def test_compare_switches_to_fisher_when_cells_are_sparse() -> None:
    assert compare_proportions(1, 6, 0, 6).test == "fisher exact"
    assert compare_proportions(50, 100, 30, 100).test == "chi-square (Yates)"


def test_significance_flag_agrees_with_the_printed_interval() -> None:
    """A flag that can disagree with the number beside it is worse than no flag."""
    for args in ((13, 126, 23, 372), (90, 100, 10, 100), (5, 40, 4, 38), (1, 9, 8, 9)):
        c = compare_proportions(*args)
        if c.ratio_lo is None:
            continue
        assert c.significant == (f(c.ratio_lo) > 1.0 or f(c.ratio_hi) < 1.0)


def test_heterogeneity_reports_when_the_approximation_is_unreliable() -> None:
    sparse = heterogeneity([[1, 2], [2, 1]])
    assert sparse.reliable is False and sparse.min_expected < 5
    dense = heterogeneity([[80, 20], [20, 80]])
    assert dense.reliable is True
    assert dense.p_value < 0.001
    assert 0.0 <= dense.cramers_v <= 1.0


def test_heterogeneity_of_one_common_distribution_is_not_significant() -> None:
    h = heterogeneity([[50, 50], [51, 49]])
    assert h.p_value > 0.5
    assert h.cramers_v < 0.1


def test_benjamini_hochberg_controls_a_family() -> None:
    """Twenty nulls at alpha .05 yield a 'finding' by construction; BH is what stops the
    report from printing it as one."""
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.6, 0.7, 0.8, 0.9, 0.95]
    keep = benjamini_hochberg(ps, 0.05)
    assert keep[0] is True and keep[1] is True
    assert keep[-1] is False
    assert sum(keep) < sum(p < 0.05 for p in ps), "BH must reject fewer than raw alpha"


def test_benjamini_hochberg_keeps_caller_order_and_handles_empty() -> None:
    assert benjamini_hochberg([]) == []
    keep = benjamini_hochberg([0.9, 0.0001])
    assert keep == [False, True]


def test_bootstrap_is_deterministic_and_brackets_the_estimate() -> None:
    vals = [float(x) for x in range(1, 51)]
    a = bootstrap_ci(vals, lambda v: sum(v) / len(v), n_boot=800)
    b = bootstrap_ci(vals, lambda v: sum(v) / len(v), n_boot=800)
    assert a == b, "a report that renders different intervals per run is not reproducible"
    obs, lo, hi = a
    assert lo < obs < hi


def test_concentration_says_how_many_sources_the_evidence_is_worth() -> None:
    one = shannon_concentration([100, 1, 1])
    assert one["top1"] > 0.97
    assert one["effective_n"] < 1.1, "one dominant source is worth about one source"
    even = shannon_concentration([25, 25, 25, 25])
    assert even["effective_n"] == pytest.approx(4.0, abs=0.01)
    assert shannon_concentration([])["sources"] == 0


def test_spearman_finds_a_monotonic_relationship_that_is_not_linear() -> None:
    """The reason it is rank-based: y = x^3 is a perfect monotone relationship, and rank
    correlation reports 1.0 where Pearson would report less."""
    xs = [1.0, 2, 3, 4, 5, 6, 7, 8]
    ys = [x ** 3 for x in xs]
    r = spearman(xs, ys)
    assert r.rho == pytest.approx(1.0)
    assert r.lo > 0.5


def test_spearman_on_noise_covers_zero() -> None:
    xs = [3.0, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8]
    ys = [2.0, 7, 1, 8, 2, 8, 1, 8, 2, 8, 4, 5]
    r = spearman(xs, ys)
    assert r.lo < 0 < r.hi, "an interval that excludes 0 on noise would be the bug"


def test_spearman_refuses_a_sample_too_small_to_have_an_interval() -> None:
    r = spearman([1.0, 2, 3], [1.0, 2, 3])
    assert r.grade == "insufficient"
    with pytest.raises(ValueError):
        spearman([1.0, 2], [1.0])


def test_auc_of_a_perfect_separator_is_one() -> None:
    s = auc([1.0, 2, 3, 4, 90, 91, 92, 93], [False] * 4 + [True] * 4, n_boot=600)
    assert s.auc == pytest.approx(1.0)
    assert s.interpretation == "separates the classes"


def test_auc_of_a_useless_measure_says_so() -> None:
    """The case that matters: a measured 0.6 on a small sample is not evidence of anything,
    and the interval is what says so instead of the point estimate flattering itself."""
    s = auc([1.0, 2, 3, 4, 1.5, 2.5, 3.5, 4.5], [True, False] * 4, n_boot=600)
    assert s.lo <= 0.5 <= s.hi
    assert s.interpretation == "not distinguishable from chance"


def test_auc_handles_ties_as_half_credit_and_an_empty_class() -> None:
    tied = auc([5.0, 5, 5, 5], [True, True, False, False], n_boot=200)
    assert tied.auc == pytest.approx(0.5)
    empty = auc([1.0, 2, 3], [True, True, True], n_boot=200)
    assert empty.grade == "none" and empty.n_neg == 0
