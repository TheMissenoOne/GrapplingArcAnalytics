"""Empirical-Bayes shrinkage for small-N proportions and rating-like estimates (PoC-E1,
``docs/research/03_POC_PLANS.md``).

Two estimators, both fitted method-of-moments over a POPULATION of nodes (never a single
node in isolation — that is what makes it empirical Bayes and not a prior pulled from thin
air):

* **beta-binomial**, for proportions (successes/trials per node). The prior is the Beta
  distribution whose mean/variance match the population of raw per-node rates; each node's
  posterior mean is ``(successes + alpha) / (trials + alpha + beta)``.
* **normal-normal**, for continuous per-node estimates (e.g. a node's rating or deviance
  z). ``B = within / (within + between)`` is the shrinkage weight; a small-n node has large
  ``within`` variance so ``B`` is large and it gets pulled hard toward the population mean
  ``mu``; a large-n node has small ``within`` and stays close to its raw value.

Pure functions, typed, no DB import — callers assemble the population from whatever corpus
(node-level graph stats, App session replay) they are measuring.
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

_WEAK_PRIOR = (1.0, 1.0)  # uniform Beta(1,1) — the fallback when the population can't fit one


@dataclass(frozen=True)
class BetaBinomialPrior:
    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


def fit_beta_binomial_prior(rates: Sequence[tuple[int, int]]) -> BetaBinomialPrior:
    """Method-of-moments Beta prior over the population of per-node raw rates ``p_i =
    successes_i / trials_i``. Falls back to a weak uniform prior (alpha=beta=1) when there
    are fewer than 2 nodes, the population variance is non-positive, or the variance exceeds
    what a Beta can express (``var >= mean*(1-mean)``, i.e. node rates are near-degenerate
    at 0/1 with essentially no between-node spread left to explain).
    """
    pop = [(s, n) for s, n in rates if n > 0]
    if len(pop) < 2:
        return BetaBinomialPrior(*_WEAK_PRIOR)
    ps = [s / n for s, n in pop]
    m, v = statistics.fmean(ps), statistics.pvariance(ps)
    if v <= 0 or v >= m * (1 - m):
        return BetaBinomialPrior(*_WEAK_PRIOR)
    common = m * (1 - m) / v - 1
    return BetaBinomialPrior(alpha=m * common, beta=(1 - m) * common)


def shrink_beta_binomial(successes: int, trials: int, prior: BetaBinomialPrior) -> float:
    """Posterior mean of a node's rate under ``prior``."""
    return (successes + prior.alpha) / (trials + prior.alpha + prior.beta)


@dataclass(frozen=True)
class NormalNormalPrior:
    mu: float     # population mean
    tau2: float   # between-node variance (>= 0)


def fit_normal_normal_prior(
    estimates: Sequence[float], within_vars: Sequence[float]
) -> NormalNormalPrior:
    """Method-of-moments normal-normal prior: population mean ``mu``, and between-node
    variance ``tau2 = max(0, var(estimates) - mean(within_vars))`` (Efron-Morris). Clamped
    at 0 rather than allowed negative, which is the usual outcome when sampling noise alone
    explains the spread across nodes. Fewer than 2 nodes fall back to ``tau2=0`` (no
    between-node signal to fit; every node shrinks fully to its own value, since ``mu`` is
    then just that value).
    """
    if len(estimates) != len(within_vars):
        raise ValueError("estimates and within_vars must be the same length")
    if not estimates:
        return NormalNormalPrior(mu=0.0, tau2=0.0)
    mu = statistics.fmean(estimates)
    if len(estimates) < 2:
        return NormalNormalPrior(mu=mu, tau2=0.0)
    total_var = statistics.pvariance(estimates)
    tau2 = max(0.0, total_var - statistics.fmean(within_vars))
    return NormalNormalPrior(mu=mu, tau2=tau2)


def shrink_normal_normal(x: float, within_var: float, prior: NormalNormalPrior) -> float:
    """``shrunk = mu + (1-B)(x-mu)``, ``B = within_var / (within_var + tau2)``.

    ``tau2 <= 0`` (no between-node spread) shrinks every node fully to ``mu``. ``within_var
    <= 0`` (an exact, infinitely-precise observation) returns ``x`` unshrunk — there is
    nothing to average away.
    """
    if prior.tau2 <= 0:
        return prior.mu
    if within_var <= 0:
        return x
    b = within_var / (within_var + prior.tau2)
    return prior.mu + (1 - b) * (x - prior.mu)
