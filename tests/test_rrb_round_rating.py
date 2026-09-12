"""Unit tests for the RRB round-rating study's pure scoring.

Tiny hand-written fixtures only — no DB, no artefact read, no owner data. What is asserted here
is exactly what the pre-registration (``docs/research/rrb_round_rating_prereg.md``) fixes BEFORE
any arm is scored: the statistic's definition, the self-cancellation identity, the length
artefact's direction, and the K functions.
"""

from __future__ import annotations

import math

import pytest

from scripts.research.rrb_round_rating import (
    K_SHAPES,
    action_values,
    budget_lambda,
    competitiveness,
    elo_offset,
    k_mult,
    log_loss,
    marginal_submission_share,
    p_own,
    self_cancellation_residual,
    signed_log_odds,
    spearman_rho,
)

BLOCK = {"CDP": 0.5, "SUBA": 0.6, "SUB": 0.8, "BTK": 0.4}
DOC = {
    "provenance": {
        "actions": {
            "global": {
                "SUBA": {"weight": 0.5178, "n": 1082},
                "SUB": {"weight": 0.8065, "n": 176},
            }
        }
    }
}


def test_marginal_submission_share_reproduces_the_pilots_number() -> None:
    # The pilot quotes 0.5582; it must fall out of the artefact's own counts, not be pasted in.
    assert round(marginal_submission_share(DOC), 4) == 0.5582


def test_action_values_three_terminal_settings() -> None:
    m = 0.5582
    assert action_values(BLOCK, terminal="landed", marginal=m)["SUB"] == 0.8
    marg = action_values(BLOCK, terminal="marginal", marginal=m)
    assert marg["SUB"] == marg["SUBA"] == m
    assert marg["CDP"] == 0.5  # non-submission codes untouched
    drop = action_values(BLOCK, terminal="drop", marginal=m)
    assert "SUB" not in drop and "SUBA" not in drop and "CDP" in drop
    with pytest.raises(ValueError):
        action_values(BLOCK, terminal="nope", marginal=m)


def test_signed_log_odds_is_the_mean_of_actor_signed_logits() -> None:
    steps = [("SUBA", True), ("BTK", False)]
    z, n = signed_log_odds(steps, BLOCK, gamma=1.0)
    expected = (math.log(0.6 / 0.4) - math.log(0.4 / 0.6)) / 2
    assert n == 2
    assert z == pytest.approx(expected)
    # A neutral action (0.5) contributes exactly zero, whichever side plays it.
    assert signed_log_odds([("CDP", True), ("CDP", False)], BLOCK)[0] == pytest.approx(0.0)


def test_unmapped_and_absent_codes_are_dropped_not_averaged() -> None:
    steps = [("SUBA", True), (None, True), ("NOT_IN_BLOCK", True)]
    z, n = signed_log_odds(steps, BLOCK)
    assert n == 1
    assert z == pytest.approx(math.log(0.6 / 0.4))
    assert signed_log_odds([(None, True)], BLOCK) == (pytest.approx(float("nan"), nan_ok=True), 0)


def test_gamma_controls_the_length_artefact() -> None:
    """γ=0 is a sum (grows with n); γ=1 is a mean (flat in n). This is the whole point of §3."""
    one = [("SUBA", True)]
    ten = one * 10
    z1_sum, _ = signed_log_odds(one, BLOCK, gamma=0.0)
    z10_sum, _ = signed_log_odds(ten, BLOCK, gamma=0.0)
    assert z10_sum == pytest.approx(10 * z1_sum)
    z1_mean, _ = signed_log_odds(one, BLOCK, gamma=1.0)
    z10_mean, _ = signed_log_odds(ten, BLOCK, gamma=1.0)
    assert z10_mean == pytest.approx(z1_mean)
    # √n middle
    assert signed_log_odds(ten, BLOCK, gamma=0.5)[0] == pytest.approx(10 * z1_sum / math.sqrt(10))


def test_temperature_is_a_monotone_rescaling_only() -> None:
    steps = [("SUBA", True), ("CDP", False)]
    hot, _ = signed_log_odds(steps, BLOCK, temperature=0.75)
    cold, _ = signed_log_odds(steps, BLOCK, temperature=1.5)
    assert hot == pytest.approx(2 * cold)


def test_self_cancellation_is_an_exact_identity() -> None:
    """Prereg §2b: score AND opponent from the same round ⇒ s − E == 0 for every p.

    This is why A1-as-a-rating-update is dead before it runs (Chen et al. 2017, opponent
    indifference). It holds because 400·log10 and Glicko-2's 173.7178 scale are the same map —
    but only to the precision Glickman PUBLISHED that constant at: 400/ln(10) = 173.717792761…,
    and ``glicko2.SCALE`` is the rounded 173.7178. The residual is therefore ~2e-9 at the
    extremes, not machine zero. Measured, stated, and bounded rather than papered over.
    """
    for p in (0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99):
        assert abs(self_cancellation_residual(p)) < 1e-8


def test_elo_offset_sign_and_inverse() -> None:
    assert elo_offset(0.5) == pytest.approx(0.0)
    assert elo_offset(0.9) < 0  # dominant round ⇒ partner reads WEAKER
    assert elo_offset(0.1) > 0
    assert elo_offset(0.75) == pytest.approx(-elo_offset(0.25))
    # p_own inverts through the Glicko/Elo scale
    assert p_own(math.log(3.0)) == pytest.approx(0.75)


def test_k_functions() -> None:
    assert competitiveness(0.5) == 1.0
    assert competitiveness(1.0) == pytest.approx(0.0)
    assert competitiveness(0.75) == pytest.approx(0.5)
    for shape in K_SHAPES:
        assert k_mult(0.5, shape=shape, lam=1.0) == pytest.approx(1.0)
        assert k_mult(1.0, shape=shape, lam=1.0) == pytest.approx(0.0)
    assert k_mult(0.75, shape="quadratic", lam=1.0) == pytest.approx(0.25)
    assert k_mult(0.75, shape="sqrt", lam=1.0) == pytest.approx(math.sqrt(0.5))
    assert k_mult(0.75, shape="linear", lam=0.4) == pytest.approx(0.2)
    with pytest.raises(ValueError):
        k_mult(0.5, shape="cubic")


def test_budget_lambda_keeps_the_k_budget_at_mean_one() -> None:
    ps = [0.2, 0.4, 0.5, 0.6, 0.85]
    for shape in K_SHAPES:
        lam = budget_lambda(ps, shape=shape)
        mean = sum(k_mult(p, shape=shape, lam=lam) for p in ps) / len(ps)
        assert mean == pytest.approx(1.0)


def test_length_artefact_metric_detects_what_it_is_for() -> None:
    """ρ(|Z|, n) — the death-rule-4 statistic.

    Under γ=0 a longer one-sided sequence must show the artefact.
    """
    lens = [1, 2, 3, 4, 5, 6]
    zs_sum = [signed_log_odds([("SUBA", True)] * n, BLOCK, gamma=0.0)[0] for n in lens]
    zs_mean = [signed_log_odds([("SUBA", True)] * n, BLOCK, gamma=1.0)[0] for n in lens]
    assert spearman_rho([abs(z) for z in zs_sum], [float(n) for n in lens]) == pytest.approx(1.0)
    # γ=1 removes the artefact by construction: |Z| is CONSTANT in n. ρ on that series is pure
    # float noise (summation order differs in the last bit), so the assertion is on the spread,
    # not on ρ — asserting ρ==nan would be asserting a rounding accident.
    spread = max(abs(z) for z in zs_mean) - min(abs(z) for z in zs_mean)
    assert spread < 1e-12


def test_log_loss_matches_the_hand_value() -> None:
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))
    assert log_loss([0.9], [1]) == pytest.approx(-math.log(0.9))
