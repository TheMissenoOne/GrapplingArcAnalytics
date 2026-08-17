"""Glicko-2 core — pure math, zero I/O.

Direct port of the plan bundle's reference core (see ``models.py`` docstring), gate-verified
against the published worked example: 1500/200/0.06 vs (1400/30 win), (1550/100 loss),
(1700/300 loss) -> ~1464.06 rating / 151.52 RD / 0.059996 volatility. Do not "improve" this
math without re-deriving against that gate — ``tests/test_rating_v2.py`` enforces it.
"""

from __future__ import annotations

import math

from analysis.rating_v2.models import Observation, RatingState

SCALE = 173.7178


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / math.pi**2)


def update_period(
    state: RatingState,
    observations: list[Observation],
    *,
    tau: float = 0.5,
    center: float = 1500.0,
    epsilon: float = 1e-6,
) -> RatingState:
    """Advance one athlete's state through one rating period.

    No observations -> RD widens for inactivity (volatility unchanged), rating unchanged.
    """
    mu = (state.rating - center) / SCALE
    phi = state.deviation / SCALE
    sigma = state.volatility

    if not observations:
        phi_star = math.sqrt(phi * phi + sigma * sigma)
        return RatingState(state.rating, SCALE * phi_star, sigma)

    inv_v = 0.0
    score_sum = 0.0
    for obs in observations:
        muj = (obs.opponent_rating - center) / SCALE
        phij = obs.opponent_deviation / SCALE
        gj = _g(phij)
        e = 1.0 / (1.0 + math.exp(-gj * (mu - muj)))
        inv_v += obs.weight * gj * gj * e * (1.0 - e)
        score_sum += obs.weight * gj * (obs.score - e)

    v = 1.0 / inv_v
    delta = v * score_sum
    a = math.log(sigma * sigma)

    def f(x: float) -> float:
        ex = math.exp(x)
        return (
            ex * (delta * delta - phi * phi - v - ex)
            / (2.0 * (phi * phi + v + ex) ** 2)
            - (x - a) / (tau * tau)
        )

    lo = a
    if delta * delta > phi * phi + v:
        hi = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        hi = a - k * tau

    f_lo, f_hi = f(lo), f(hi)
    for _ in range(100):
        if abs(hi - lo) <= epsilon:
            break
        mid = lo + (lo - hi) * f_lo / (f_hi - f_lo)
        f_mid = f(mid)
        if f_mid * f_hi <= 0:
            lo, f_lo = hi, f_hi
        else:
            f_lo /= 2.0
        hi, f_hi = mid, f_mid

    sigma_new = math.exp(lo / 2.0)
    phi_star = math.sqrt(phi * phi + sigma_new * sigma_new)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * score_sum

    return RatingState(
        rating=center + SCALE * mu_new,
        deviation=SCALE * phi_new,
        volatility=sigma_new,
    )
