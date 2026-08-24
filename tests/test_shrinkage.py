"""Tests for analysis/shrinkage.py (PoC-E1)."""

from __future__ import annotations

import random

from analysis.shrinkage import (
    fit_beta_binomial_prior,
    fit_normal_normal_prior,
    shrink_beta_binomial,
    shrink_normal_normal,
)


def test_beta_binomial_prior_recovery() -> None:
    # 200 nodes, true rate 0.3, trials large enough that per-node noise is small — the
    # fitted prior mean should land close to 0.3.
    rng = random.Random(20260824)
    rates = []
    for _ in range(200):
        n = 500
        s = sum(1 for _ in range(n) if rng.random() < 0.3)
        rates.append((s, n))
    prior = fit_beta_binomial_prior(rates)
    assert abs(prior.mean - 0.3) < 0.02


def test_beta_binomial_shrinkage_direction_small_n_pulled_harder() -> None:
    prior = fit_beta_binomial_prior([(30, 100)] * 50)  # population rate ~0.3
    # Two nodes both observed at 90% (way above the population), but one on n=5, one on n=200.
    small = shrink_beta_binomial(4, 5, prior)     # raw 0.80
    large = shrink_beta_binomial(180, 200, prior)  # raw 0.90
    assert abs(small - 0.80) > abs(large - 0.90)  # small-n moved further from its raw value


def test_beta_binomial_degenerate_single_node_falls_back_to_weak_prior() -> None:
    prior = fit_beta_binomial_prior([(3, 10)])
    assert (prior.alpha, prior.beta) == (1.0, 1.0)


def test_beta_binomial_degenerate_zero_variance_falls_back() -> None:
    # every node has the identical rate -> zero population variance
    prior = fit_beta_binomial_prior([(5, 10)] * 20)
    assert (prior.alpha, prior.beta) == (1.0, 1.0)


def test_normal_normal_prior_recovery() -> None:
    rng = random.Random(20260824)
    true_mu = 1500.0
    tau = 50.0  # true between-node sd
    within_var = 25.0  # small measurement noise per node
    estimates = [rng.gauss(true_mu, tau) + rng.gauss(0.0, within_var**0.5) for _ in range(300)]
    prior = fit_normal_normal_prior(estimates, [within_var] * 300)
    assert abs(prior.mu - true_mu) < 5.0
    assert abs(prior.tau2 - tau**2) < tau**2  # order of magnitude, not exact


def test_normal_normal_shrinkage_direction_small_n_pulled_harder() -> None:
    prior = fit_normal_normal_prior([100.0, 110.0, 90.0, 105.0, 95.0], [4.0] * 5)
    raw = 130.0
    noisy = shrink_normal_normal(raw, within_var=400.0, prior=prior)   # high within-var (small n)
    precise = shrink_normal_normal(raw, within_var=1.0, prior=prior)   # low within-var (large n)
    assert abs(noisy - raw) > abs(precise - raw)
    assert prior.mu < noisy < raw  # pulled toward mu, not past it
    assert prior.mu < precise < raw


def test_normal_normal_degenerate_single_node() -> None:
    prior = fit_normal_normal_prior([42.0], [10.0])
    assert prior.mu == 42.0
    assert prior.tau2 == 0.0
    # tau2=0 -> fully shrunk to mu regardless of within_var.
    assert shrink_normal_normal(999.0, 5.0, prior) == 42.0


def test_normal_normal_degenerate_zero_within_var_returns_raw() -> None:
    prior = fit_normal_normal_prior([1.0, 2.0, 3.0], [0.5, 0.5, 0.5])
    assert shrink_normal_normal(7.0, 0.0, prior) == 7.0


def test_normal_normal_mismatched_lengths_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="same length"):
        fit_normal_normal_prior([1.0, 2.0], [1.0])
