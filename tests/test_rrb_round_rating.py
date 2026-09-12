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
    Bout,
    _e3_credit,
    _isotonic_interp,
    action_values,
    budget_lambda,
    calibration_apply,
    calibration_fit,
    competitiveness,
    contribution_shares,
    elo_offset,
    k_mult,
    log_loss,
    marginal_submission_share,
    p_own,
    run_owner_dominance_glicko,
    run_section_e1,
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


# ── §B: the absorption solver ────────────────────────────────────────────────────

from scripts.research.rrb_round_rating import (  # noqa: E402
    absorption_values,
    frequency_values,
    mirror_error,
    node_key_of,
)


def test_absorption_on_a_tiny_chain_with_a_known_answer() -> None:
    """4-node chain, α→0, hand-computable.

    Two bouts, mirrored: ``x → y`` then A's finish; ``p → q`` then B's finish. Under the
    two-reference walk each bout is seen from both corners, so from A's perspective ``x``/``y``
    are own-side nodes that always precede A's own finish (v → 1) while ``p``/``q`` are own-side
    nodes that always precede the OPPONENT's finish (v → 0).
    """
    chains = [
        ([("x", "a"), ("y", "a")], "a"),
        ([("p", "a"), ("q", "a")], "b"),
    ]
    v, seen = absorption_values(chains, alpha=1e-9)
    assert v["x"] == pytest.approx(1.0, abs=1e-6)
    assert v["y"] == pytest.approx(1.0, abs=1e-6)
    assert v["p"] == pytest.approx(0.0, abs=1e-6)
    assert v["q"] == pytest.approx(0.0, abs=1e-6)
    # Each bout is walked twice (once per reference); the reported count is the real one.
    assert seen == {"x": 1, "y": 1, "p": 1, "q": 1}


def test_absorption_conditions_on_reaching_a_finish() -> None:
    """A node that only ever ends in a DECISION carries no finish mass and gets no value.

    ``END_OTHER`` is a third absorbing state, not a half-win: dividing it out is the same
    conditional ``lamas_chain.rrb``'s ``sub_share`` publishes.
    """
    v, _ = absorption_values([([("d", "a"), ("d", "a")], None)], alpha=1e-9)
    # With no finish reachable at all, B_own and B_opp are both ~0 and the node is dropped.
    assert "d" not in v or 0.4 < v["d"] < 0.6


def test_smoothing_shrinks_a_singleton_toward_one_half() -> None:
    """Death-rule guard: a node seen once must not inherit a singleton's outcome."""
    chains = [([("rare", "a")], "a")]
    v_small, _ = absorption_values(chains, alpha=1e-9)
    v_big, _ = absorption_values(chains, alpha=1.0)
    assert v_small["rare"] > 0.99
    assert abs(v_big["rare"] - 0.5) < abs(v_small["rare"] - 0.5)


def test_mirror_identity_holds_by_construction() -> None:
    """v(node, own) = 1 − v(node, opp): each bout is walked from BOTH corners, so flipping every
    side label must return the same table. Asserted, not assumed (prereg §B1)."""
    chains = [
        ([("x", "a"), ("y", "b"), ("z", "a")], "a"),
        ([("y", "a"), ("z", "b")], "b"),
        ([("x", "b"), ("z", "a")], None),
    ]
    assert mirror_error(chains, alpha=1.0) < 1e-12


def test_frequency_control_is_a_pure_ranking() -> None:
    f = frequency_values({"rare": 1, "mid": 5, "common": 50})
    assert f["rare"] < f["mid"] < f["common"]
    assert all(0.0 < x < 1.0 for x in f.values())  # logit is defined everywhere


def test_node_key_folds_pt_and_en_onto_one_key() -> None:
    """The §A defect, pinned: the owner's pt-BR label and the corpus's English one are ONE node."""
    assert node_key_of("Costas", "control") == node_key_of("Back Control", "control")
    assert node_key_of("Mata-Leão", "submission") == node_key_of("Rear Naked Choke", "submission")


# ── §C: the hierarchical personal layers ─────────────────────────────────────────

from scripts.research.rrb_round_rating import (  # noqa: E402
    HierState,
    OwnerRound,
    ece,
    hier_observe,
    hier_score,
    walk_forward_scores,
)


def _rd(steps, finish):
    return OwnerRound(order=("s", 0), outcome="", difficulty=5.0, intensity=5.0,
                      steps=tuple(steps), prefix=tuple(steps), finish_side=finish)


def test_empty_personal_state_is_exactly_the_prior() -> None:
    """With no personal history the label layer must BE the global prior — otherwise the
    walk-forward's first rounds are scored by an invented number."""
    prior = {"x": 0.8, "y": 0.2}
    z, n, ne = hier_score([("x", "a"), ("y", "b")], HierState.empty(), prior, alpha=1.0,
                          lam=1.5, gate=1)
    assert n == 2 and ne == 0
    assert z == pytest.approx((math.log(0.8 / 0.2) - math.log(0.2 / 0.8)) / 2)


def test_personal_evidence_moves_the_label_value_toward_the_observed_outcome() -> None:
    prior = {"x": 0.5}
    st = HierState.empty()
    for _ in range(20):
        hier_observe(_rd([("x", "a")], "a"), st)   # x always precedes MY finish
    z, _n, _ne = hier_score([("x", "a")], st, prior, alpha=1.0, lam=1.5, gate=10**9)
    assert z > 1.5  # (20 + 0.5)/(20 + 1) ≈ 0.976 -> logit ≈ 3.7


def test_edge_residual_is_gated_and_is_a_residual() -> None:
    """Below the gate it contributes nothing; above it, it moves the score off the label layer."""
    prior = {"x": 0.5, "y": 0.5}
    st = HierState.empty()
    for _ in range(4):
        hier_observe(_rd([("x", "a"), ("y", "a")], "a"), st)
    labels, _n, _ne = hier_score([("x", "a"), ("y", "a")], st, prior, alpha=1.0, lam=1.5,
                                 gate=10**9, use_edges=False)
    below, _n2, ne_below = hier_score([("x", "a"), ("y", "a")], st, prior, alpha=1.0,
                                      lam=1.5, gate=10)
    above, _n3, ne_above = hier_score([("x", "a"), ("y", "a")], st, prior, alpha=1.0,
                                      lam=1.5, gate=3)
    assert ne_below == 0 and below == pytest.approx(labels)
    assert ne_above == 1 and above != pytest.approx(labels)


def test_cross_actor_pairs_are_not_edges() -> None:
    """The within-actor rule: A's move followed by B's is not one athlete's transition."""
    st = HierState.empty()
    for _ in range(9):
        hier_observe(_rd([("x", "a"), ("y", "b")], "a"), st)
    assert st.edge_n == {}


def test_walk_forward_never_lets_a_round_see_itself() -> None:
    """The first round must score on the prior alone, whatever the later rounds contain."""
    prior = {"x": 0.5}
    rounds = [_rd([("x", "a")], "a") for _ in range(5)]
    out = walk_forward_scores(rounds, prior, alpha=1.0, lam=0.0, gate=10**9, use_edges=False)
    assert out[0][1] == pytest.approx(0.0)      # prior 0.5 -> logit 0
    assert out[-1][1] > out[0][1]               # later rounds have personal evidence


def test_ece_is_zero_on_a_perfectly_calibrated_set() -> None:
    ps = [0.0, 0.0, 1.0, 1.0]
    ys = [0, 0, 1, 1]
    e, rows = ece(ps, ys, bins=2)
    assert e == pytest.approx(0.0)
    assert sum(r["n"] for r in rows) == 4


def test_prequential_offsets_reach_every_round_not_just_the_first() -> None:
    """Regression: the per-arm offset dict used to SHADOW the ``offsets`` parameter, so from the
    second round onward the A4 arm silently read 0.0 and collapsed onto the "substitute nothing"
    arm. The symptom was A4 matching A0n_zero to four decimals — a plausible-looking null."""
    from scripts.research.rrb_round_rating import run_owner_prequential

    doc = {
        "sessions": [
            {
                "created_at": f"2026-01-0{d + 1}",
                "rounds": [
                    {
                        "difficulty": 5,
                        "intensity": 5,
                        "outcome": "succeeded" if d % 2 else "failed",
                        "entries": [{"type": "submission", "label": "Armbar",
                                     "actor": "you", "successful": True}],
                    }
                ],
            }
            for d in range(6)
        ]
    }
    block = {"SUBA": 0.5, "SUB": 0.5}
    flat = run_owner_prequential(doc, block, 0.5, library=True)
    pushed = run_owner_prequential(doc, block, 0.5, library=True,
                                   offsets=dict.fromkeys(range(6), 300.0))
    # A large positive offset on EVERY round must move the arm; if only round 0 saw it, the two
    # runs would be indistinguishable.
    assert pushed["arms"]["A4_rrb"]["final_rating"] != pytest.approx(
        flat["arms"]["A4_rrb"]["final_rating"], rel=1e-6
    )
    assert pushed["arms"]["A4_rrb"]["final_rating"] > flat["arms"]["A0n_zero"]["final_rating"]


# ── §D: inferred per-action success ──────────────────────────────────────────────

from scripts.research.rrb_round_rating import (  # noqa: E402
    entry_score,
    entry_success_table,
    infer_entry_success,
)

_TABLE = {
    "action_exit_orientation": {
        "sweep": "top", "pass": "top", "guard": "bottom",
        "submission": "neutral", "transition": "neutral", "*": "neutral",
    }
}


def test_owner_rule_half_guard_sweep_top_reads_as_a_success() -> None:
    """The owner's own example: the sweep landed where a sweep is supposed to land."""
    score, src = infer_entry_success(("Half Guard", "guard"), ("Butterfly Sweep", "sweep"),
                                     ("Mount", "control"), terminal=False, table=_TABLE)
    assert src == "inferred"
    assert score == 1.0


def test_owner_rule_back_control_rnc_back_control_reads_as_a_failure() -> None:
    """The other example: the chain came back to where it started, so nothing landed."""
    score, src = infer_entry_success(("Back Control", "control"),
                                     ("Rear Naked Choke", "submission"),
                                     ("Back Control", "control"), terminal=False, table=_TABLE)
    assert src == "inferred"
    assert score == 0.0


def test_unresolvable_is_null_never_auto_success_or_auto_failure() -> None:
    """ADR-06 one level down: a missing outcome is lost coverage, never a manufactured result."""
    # No target state at all — the chain does not say what happened.
    assert infer_entry_success(("Half Guard", "guard"), ("Butterfly Sweep", "sweep"), None,
                               terminal=False, table=_TABLE) == (None, "unresolved")
    # A `neutral` exit orientation makes NO claim about where the action lands, so it may only
    # ever resolve by the return-to-source rule — never upward into a success.
    assert infer_entry_success(("Closed Guard", "guard"), ("Hip Escape", "transition"),
                               ("Half Guard", "guard"), terminal=False, table=_TABLE) == (
        None, "unresolved")


def test_terminal_submission_closing_the_chain_is_a_success() -> None:
    score, src = infer_entry_success(("Back Control", "control"),
                                     ("Rear Naked Choke", "submission"), None,
                                     terminal=True, table=_TABLE)
    assert (score, src) == (1.0, "inferred")


def test_entry_success_table_is_index_aligned_with_its_input() -> None:
    ents = [
        {"type": "guard", "label": "Meia Guarda"},
        {"type": "sweep", "label": "Raspagem de Gancho"},
        {"type": "control", "label": "Montada"},
    ]
    rows = entry_success_table(ents)
    assert len(rows) == len(ents)
    assert rows[0][1] == "unresolved"          # a STATE is never scored
    assert rows[1][0] == 1.0                   # pt-BR half guard -> sweep -> mount, inferred


def test_the_three_scoring_modes_differ_exactly_where_they_should() -> None:
    e_false = {"successful": False}
    assert entry_score(e_false, (None, "unresolved"), "flag") == (0.0, "flag")
    # inferred_only refuses to fall back — an unresolved entry produces NO observation
    assert entry_score(e_false, (None, "unresolved"), "inferred_only") == (None, "unresolved")
    # ...and the fallback mode takes the flag exactly there
    assert entry_score(e_false, (None, "unresolved"), "inferred_then_flag") == (0.0, "flag")
    # where the sequence DOES resolve, both inferred modes ignore a contradicting flag
    assert entry_score(e_false, (1.0, "inferred"), "inferred_only") == (1.0, "inferred")
    assert entry_score(e_false, (1.0, "inferred"), "inferred_then_flag") == (1.0, "inferred")
    with pytest.raises(ValueError):
        entry_score(e_false, (None, "unresolved"), "nope")


def test_last_flag_only_keeps_the_manual_flag_on_the_final_node_alone() -> None:
    """Owner refinement: the final action has no following transition (D7 anchor rule), so the
    sequence cannot resolve it — that one entry keeps its flag, every internal action does not."""
    e_false = {"successful": False}
    # internal, unresolved -> NULL (never the flag)
    assert entry_score(e_false, (None, "unresolved"), "last_flag_only") == (None, "unresolved")
    # internal, resolved -> the inference wins over a contradicting flag
    assert entry_score(e_false, (1.0, "inferred"), "last_flag_only") == (1.0, "inferred")
    # last node -> the flag, even where the sequence happens to resolve it
    assert entry_score(e_false, (1.0, "inferred"), "last_flag_only", is_last=True) == (0.0, "flag")


# ══ §E — global calibration, dominance-driven Glicko-2, technique credit ═══════════


def test_calibration_apply_temperature_and_platt_formulas() -> None:
    z = 0.6
    temp = calibration_apply([z], {"method": "temperature", "T": 2.0})[0]
    assert temp == pytest.approx(p_own(z / 2.0))
    platt = calibration_apply([z], {"method": "platt", "a": 2.0, "b": -0.5})[0]
    assert platt == pytest.approx(p_own(2.0 * z - 0.5))
    with pytest.raises(ValueError):
        calibration_apply([z], {"method": "nope"})


def test_isotonic_interp_is_monotone_and_clamps_outside_the_thresholds() -> None:
    xt, yt = [0.2, 0.5, 0.8], [0.1, 0.5, 0.9]
    assert _isotonic_interp(0.0, xt, yt) == 0.1     # below range -> clamps to the first
    assert _isotonic_interp(1.0, xt, yt) == 0.9     # above range -> clamps to the last
    assert _isotonic_interp(0.35, xt, yt) == pytest.approx(0.3)  # midpoint, linear interpolation
    assert _isotonic_interp(0.5, xt, yt) == pytest.approx(0.5)   # exactly on a threshold
    assert _isotonic_interp(0.5, [], []) == 0.5     # no thresholds -> identity, never a crash


def test_calibration_fit_temperature_never_scores_worse_than_raw_on_its_own_training_data() -> None:
    """Temperature scaling minimises log-loss over T by direct search on a bounded interval that
    CONTAINS T=1 (raw `σ(Z)`) — so by construction it can never score worse than raw on the SAME
    data it was fit on. Deliberately mixed/overlapping labels, not perfectly separable — a
    perfectly separable toy set pushes T to a boundary and is not a useful fixture here."""
    zs = [-2.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 2.0]
    ys = [0, 1, 0, 0, 1, 1, 0, 1]
    params = calibration_fit(zs, ys, "temperature")
    assert params["method"] == "temperature"
    assert 0.0 < params["T"] < 20.0
    calibrated = calibration_apply(zs, params)
    raw = [p_own(z) for z in zs]
    assert log_loss(calibrated, ys) <= log_loss(raw, ys) + 1e-9


def test_contribution_shares_sum_of_absolute_values_is_one() -> None:
    # own does a mildly favourable action (TKD, 0.55); the OPPONENT lands a high-value one (SUB,
    # 0.8) — that should read as bad news for own, i.e. a NEGATIVE share, not a blanket
    # own/not-own sign rule (a code with value exactly 0.5 has zero signed weight regardless of
    # side, which is the case this test avoids by picking values away from 0.5).
    vals = {"SUB": 0.8, "TKD": 0.55}
    steps = [("TKD", True), ("SUB", False)]
    shares = contribution_shares(steps, vals)
    assert len(shares) == 2
    assert sum(abs(c) for _, _, c in shares) == pytest.approx(1.0)
    by_code = {code: c for code, _own, c in shares}
    assert by_code["TKD"] > 0  # own's own favourable action
    assert by_code["SUB"] < 0  # the opponent's dominant action, read from own's perspective


def test_contribution_shares_empty_when_nothing_is_mapped() -> None:
    assert contribution_shares([(None, True), (None, False)], {"SUB": 0.8}) == []


def test_e3_credit_arms_are_mean_one_over_the_round() -> None:
    """prereg §E3: `equal_split`/`contribution` weight the round's mapped steps so the AVERAGE
    weight is 1; `last_action_only` puts the round's whole evidence on its single observation."""
    vals = {"SUB": 0.8, "TKD": 0.55, "GPS": 0.5, "BTK": 0.6}
    steps = [("TKD", True), ("SUB", True), ("GPS", False), ("BTK", True)]
    for arm in ("equal_split", "contribution"):
        credit = _e3_credit(steps, vals, arm)
        assert len(credit) == 4
        assert sum(w for _, _, w in credit) / len(credit) == pytest.approx(1.0)
    last = _e3_credit(steps, vals, "last_action_only")
    assert last == [("BTK", True, 1.0)]
    assert _e3_credit([], vals, "equal_split") == []
    with pytest.raises(ValueError):
        _e3_credit(steps, vals, "nope")


def _owner_doc(round3_entries: list[dict]) -> dict:
    """Two fixed early rounds + a third round whose ENTRIES vary by caller."""
    fixed = [
        {
            "difficulty": 5,
            "intensity": 5,
            "outcome": "succeeded",
            "entries": [
                {
                    "type": "submission",
                    "label": "Rear Naked Choke",
                    "actor": "you",
                    "successful": True,
                }
            ],
        },
        {
            "difficulty": 5,
            "intensity": 5,
            "outcome": "failed",
            "entries": [
                {"type": "pass", "label": "Leg Drag", "actor": "partner", "successful": True}
            ],
        },
    ]
    return {
        "sessions": [
            {"created_at": "2026-01-01", "rounds": fixed},
            {
                "created_at": "2026-01-02",
                "rounds": [
                    {
                        "difficulty": 5,
                        "intensity": 5,
                        "outcome": "succeeded",
                        "entries": round3_entries,
                    }
                ],
            },
        ]
    }


def test_e2_forecast_independent_of_same_round_actions() -> None:
    """prereg §E2's identity, one level up from §2b: the forecast issued for round *t* reads
    state that saw only EARLIER rounds, so it cannot depend on round *t*'s own actions — even
    though those actions DO change what the state becomes AFTER round *t* updates."""
    vals = {"SUB": 0.8, "GPS": 0.5, "BTK": 0.6, "TKD": 0.55}
    dominant = [
        {"type": "submission", "label": "Rear Naked Choke", "actor": "you", "successful": True}
    ]
    weak = [{"type": "pass", "label": "Leg Drag", "actor": "partner", "successful": True}]

    result_a = run_owner_dominance_glicko(_owner_doc(dominant), vals, None)
    result_b = run_owner_dominance_glicko(_owner_doc(weak), vals, None)

    assert len(result_a["_ps"]) == len(result_b["_ps"]) == 3
    # rounds 1-2's forecasts are identical by construction (unrelated to round 3 at all)...
    assert result_a["_ps"][:2] == result_b["_ps"][:2]
    # ...and so is round 3's OWN forecast, despite round 3's entries differing between the runs.
    assert result_a["_ps"][2] == pytest.approx(result_b["_ps"][2])
    # but the state AFTER round 3 (which DOES see its entries) diverges.
    assert result_a["final_rating"] != pytest.approx(result_b["final_rating"])


def test_run_section_e1_death_rule_verdict_is_one_of_the_three() -> None:
    """No DB: synthetic corpus bouts + a tiny owner doc, exercising the whole §E1 pipeline."""
    vals = {"SUB": 0.8, "GPS": 0.5, "BTK": 0.6, "TKD": 0.55, "SWP": 0.5, "PGD": 0.5}
    bouts = []
    win_types = {}
    for i in range(40):
        year = 2022 + (i % 4)
        a_wins = i % 3 != 0  # a skewed but non-degenerate label split
        steps = (("TKD", a_wins), ("BTK", a_wins), ("SUB", a_wins))
        bid = f"b{i}"
        bouts.append(
            Bout(
                bout_id=bid,
                year=year,
                created_at=f"{year}-01-01",
                a="athleteA",
                b="athleteB",
                winner="athleteA" if a_wins else "athleteB",
                family="other",
                steps=steps,
            )
        )
        win_types[bid] = "SUBMISSION"

    owner_doc = _owner_doc(
        [{"type": "submission", "label": "Rear Naked Choke", "actor": "you", "successful": True}]
    )
    res = run_section_e1(bouts, win_types, vals, owner_doc, cutoffs=(2022, 2023, 2024))
    assert res["death_rule"]["verdict"] in ("PASS", "FAIL", "NULL")
    assert res["chosen_method"] in ("temperature", "platt", "isotonic")
    assert entry_score({}, (None, "unresolved"), "last_flag_only", is_last=True) == (1.0, "flag")
