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
    coverage,
    grade,
    heterogeneity,
    inverse_hhi_concentration,
    permutation_p,
    spearman,
    survives,
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
    """Ten tests at alpha .05 yield a 'finding' by construction; BH is what stops the report
    from printing it as one."""
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.6, 0.7, 0.8, 0.9, 0.95]
    qs = benjamini_hochberg(ps)
    keep = survives(qs, 0.05)
    assert keep[0] is True and keep[1] is True
    assert keep[-1] is False
    assert sum(keep) < sum(p < 0.05 for p in ps), "BH must reject fewer than raw alpha"


def test_q_values_are_reported_not_just_a_verdict() -> None:
    """A boolean makes the reader trust whichever alpha the report happened to pick. The
    q-value is the smallest FDR at which the finding survives, so it can be argued with."""
    qs = benjamini_hochberg([0.001, 0.008, 0.039, 0.041, 0.042, 0.6, 0.7, 0.8, 0.9, 0.95])
    assert qs[0] == pytest.approx(0.01, abs=1e-9)     # 0.001 * 10 / 1
    assert qs[1] == pytest.approx(0.04, abs=1e-9)     # 0.008 * 10 / 2
    assert all(0.0 <= q <= 1.0 for q in qs)


def test_q_values_are_monotone_in_p() -> None:
    """The step-up enforcement: a larger p can never earn a smaller q."""
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.6, 0.7, 0.8, 0.9, 0.95]
    qs = benjamini_hochberg(ps)
    ordered = [q for _, q in sorted(zip(ps, qs, strict=True))]
    assert ordered == sorted(ordered)


def test_benjamini_hochberg_keeps_caller_order_and_handles_empty() -> None:
    assert benjamini_hochberg([]) == []
    assert survives(benjamini_hochberg([0.9, 0.0001])) == [False, True]


def test_bootstrap_is_deterministic_and_brackets_the_estimate() -> None:
    vals = [float(x) for x in range(1, 51)]
    a = bootstrap_ci(vals, lambda v: sum(v) / len(v), n_boot=800)
    b = bootstrap_ci(vals, lambda v: sum(v) / len(v), n_boot=800)
    assert a == b, "a report that renders different intervals per run is not reproducible"
    obs, lo, hi = a
    assert lo < obs < hi


def test_concentration_says_how_many_sources_the_evidence_is_worth() -> None:
    one = inverse_hhi_concentration([100, 1, 1])
    assert one["top1"] > 0.97
    assert one["effective_n"] < 1.1, "one dominant source is worth about one source"
    even = inverse_hhi_concentration([25, 25, 25, 25])
    assert even["effective_n"] == pytest.approx(4.0, abs=0.01)
    assert inverse_hhi_concentration([])["sources"] == 0


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


# ── coverage: the gate that precision cannot answer ─────────────────────────────
def test_coverage_refuses_a_category_estimate_from_too_few_sources() -> None:
    c = coverage([61, 42])
    assert c.clusters == 2
    assert c.estimable is False
    assert c.reason and "2 source(s)" in c.reason


def test_coverage_refuses_when_one_source_dominates_despite_enough_of_them() -> None:
    """Counting sources is not enough. Five athletes where one produced 92% of the events is
    one athlete's profile with four witnesses."""
    c = coverage([158, 5, 4, 3, 2])
    assert c.clusters == 5
    assert c.effective_n < 2.0
    assert c.estimable is False
    assert c.reason and "equally-weighted" in c.reason


def test_coverage_passes_when_the_evidence_is_actually_spread() -> None:
    c = coverage([20, 18, 22, 19, 21, 20])
    assert c.estimable is True
    assert c.reason is None
    assert c.grade == "adequate"


def test_coverage_of_nothing_is_not_an_estimate() -> None:
    c = coverage([])
    assert (c.clusters, c.estimable, c.grade) == (0, False, "none")


def test_precision_and_coverage_disagree_and_that_is_the_point() -> None:
    """The failure this whole gate exists for: a narrow interval on evidence from one source.
    171 events, 54 of them control, is precise arithmetic about an unrepresentative sample."""
    est = wilson(54, 171)
    assert est.grade == "adequate"          # precision: the interval is narrow
    assert coverage([158, 8, 3, 2]).estimable is False   # coverage: it is one athlete


# ── cluster bootstrap ───────────────────────────────────────────────────────────
def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def test_cluster_bootstrap_resamples_athletes_not_rows() -> None:
    """Two athletes who disagree completely. Resampling rows averages them away; resampling
    athletes keeps the disagreement, which is the uncertainty that actually exists."""
    values = [1.0] * 50 + [0.0] * 50
    groups = ["a"] * 50 + ["b"] * 50
    _, naive_lo, naive_hi = bootstrap_ci(values, _mean, n_boot=500)
    _, clus_lo, clus_hi = bootstrap_ci(values, _mean, n_boot=500, groups=groups)
    assert naive_hi - naive_lo < 0.3
    assert (clus_hi - clus_lo) > (naive_hi - naive_lo)
    assert clus_lo == 0.0 and clus_hi == 1.0


def test_cluster_bootstrap_can_return_a_narrower_interval_and_that_is_not_a_bug() -> None:
    """The real -65 kg `no_gi+adcc` cut: 36 wins, 17 finishes, two contributing athletes at
    9/16 and 8/20. Because a cluster draw can only ever produce one of three mixtures, the
    interval is BOUNDED BY THE TWO ATHLETES' OWN RATES -- it comes out at [0.400, 0.562],
    roughly half the width of the naive one, and cannot reach a value neither athlete showed.

    That is the whole reason `coverage` gates publication rather than the bootstrap fixing it
    by itself. If this assertion ever fails, the fix is not to widen the bootstrap; it is to
    check that the gate still refuses this cut.
    """
    frankland = [1.0] * 9 + [0.0] * 7
    black = [1.0] * 8 + [0.0] * 12
    values = frankland + black
    groups = ["frankland"] * 16 + ["black"] * 20

    _, nlo, nhi = bootstrap_ci(values, _mean, n_boot=2000)
    _, clo, chi = bootstrap_ci(values, _mean, n_boot=2000, groups=groups)

    assert (chi - clo) < (nhi - nlo) / 1.5
    assert clo == pytest.approx(8 / 20, abs=0.01)   # cannot go below the worse athlete
    assert chi == pytest.approx(9 / 16, abs=0.01)   # nor above the better one
    assert coverage([16, 20]).estimable is False


def test_the_gate_admits_the_cut_where_clustering_actually_helps() -> None:
    """The +65 kg counterpart, six athletes, same cut. Here the clustered interval is properly
    WIDER than the naive one, and the gate lets it through -- which is what makes the refusal
    above a judgement about the evidence rather than a blanket distrust of the method.
    """
    per = {"soares": (3, 4), "guedes": (1, 6), "lopez": (1, 4),
           "garcia": (6, 14), "crevar": (11, 13), "mitrovic": (9, 13)}
    values = [x for k, n in per.values() for x in ([1.0] * k + [0.0] * (n - k))]
    groups = [name for name, (_, n) in per.items() for _ in range(n)]

    _, nlo, nhi = bootstrap_ci(values, _mean, n_boot=2000)
    _, clo, chi = bootstrap_ci(values, _mean, n_boot=2000, groups=groups)

    assert (chi - clo) > (nhi - nlo)
    assert coverage([n for _, n in per.values()]).estimable is True


def test_cluster_bootstrap_rejects_a_label_per_row_mismatch() -> None:
    with pytest.raises(ValueError, match="groups has 2 labels for 3 values"):
        bootstrap_ci([1.0, 2.0, 3.0], _mean, groups=["a", "b"])


def test_cluster_bootstrap_is_deterministic() -> None:
    values, groups = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0], ["a", "a", "b", "b", "c", "c"]
    first = bootstrap_ci(values, _mean, n_boot=300, groups=groups)
    assert first == bootstrap_ci(values, _mean, n_boot=300, groups=groups)


# ── permutation, for the tables the approximation cannot handle ─────────────────
def test_a_sparse_table_gets_a_permutation_p_instead_of_only_a_warning() -> None:
    h = heterogeneity([[5, 0], [0, 4]])
    assert h.reliable is False
    assert h.p_permutation is not None
    assert 0.0 < h.p_permutation < 0.05


def test_a_healthy_table_needs_no_permutation() -> None:
    h = heterogeneity([[40, 60], [45, 55]])
    assert h.reliable is True
    assert h.p_permutation is None


def test_permutation_p_matches_the_exact_test_it_stands_in_for() -> None:
    """Validated against Fisher rather than against the chi-square, on purpose.

    On [[40, 60], [45, 55]] -- a table with a smallest expected count of 47.5, comfortably
    "reliable" -- the permutation converges on 0.569 and stays there as draws increase, while
    the uncorrected chi-square says 0.474. The permutation is not drifting: Fisher's exact
    two-sided gives 0.567 and Yates 0.567. The chi-square is the one that is off, by 0.09 on a
    table nobody would flag, which is worth knowing before reading any p on this page too
    closely.
    """
    from scipy import stats as _stats

    table = [[40, 60], [45, 55]]
    perm = permutation_p(table, n_draws=20000)
    assert perm is not None
    assert perm == pytest.approx(_stats.fisher_exact(table)[1], abs=0.01)


def test_permutation_p_never_claims_more_than_the_draws_support() -> None:
    """A Monte-Carlo p of exactly zero would assert certainty the sampling cannot deliver."""
    p = permutation_p([[60, 0], [0, 60]], n_draws=500)
    assert p is not None and p > 0.0
    assert p == pytest.approx(1 / 501, abs=1e-9)
