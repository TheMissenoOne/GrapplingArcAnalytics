"""Decision-criteria statistics.

The load-bearing test here is `test_null_calibration`: fed data where the condition and the
response are independent by construction, the pipeline must find (almost) nothing. A detector
that cannot stay quiet on noise is worse than no detector, because every number it produces
looks equally confident.
"""

from __future__ import annotations

import numpy as np
import pytest

from analysis.decision_criteria import (
    Observation,
    analyze,
    analyze_context,
    benjamini_hochberg,
    condition_hierarchy,
    conditional_mutual_information,
    observations_from_patterns,
    select_level,
    sibling_contrast,
    taxonomy_levels,
    to_json,
)

FAST = {"n_permutations": 200, "n_bootstrap": 200}


def _obs(match, cond, resp, *, ctx="closed-guard|hip-bump", success=None, opp=None):
    return Observation(
        match_id=match, athlete_id="a", context=ctx,
        condition=cond, response=resp, success=success,
        # default: one opponent per match, so the min-opponents floor tracks the match floor
        opponent_id=opp if opp is not None else f"opp-{match}",
    )


# ---------------------------------------------------------------- BH / FDR


def test_bh_uniform_case():
    q = benjamini_hochberg([0.01, 0.02, 0.03, 0.04, 0.05])
    assert all(abs(v - 0.05) < 1e-9 for v in q)


def test_bh_preserves_input_order():
    q = benjamini_hochberg([0.04, 0.01])
    assert q[1] < q[0]
    assert abs(q[1] - 0.02) < 1e-9


def test_bh_is_monotone_and_clipped():
    q = benjamini_hochberg([0.5, 0.9, 0.99])
    assert all(v <= 1.0 for v in q)
    assert q == sorted(q)


def test_bh_empty():
    assert benjamini_hochberg([]) == []


# ---------------------------------------------------------------- MI


def test_mi_zero_when_independent():
    table = np.array([[10.0, 10.0], [10.0, 10.0]])
    assert conditional_mutual_information(table) == pytest.approx(0.0, abs=1e-12)


def test_mi_maximal_when_deterministic():
    table = np.array([[10.0, 0.0], [0.0, 10.0]])
    assert conditional_mutual_information(table) == pytest.approx(1.0, abs=1e-9)


def test_mi_empty_table():
    assert conditional_mutual_information(np.zeros((2, 2))) == 0.0


# ---------------------------------------------------------------- guards


def test_context_below_minimum_is_skipped():
    obs = [_obs(f"m{i}", "whizzer", "rear-body-lock") for i in range(4)]
    rep = analyze_context(obs, **FAST)
    assert rep.criteria == []
    assert "need" in rep.note


def test_single_condition_yields_no_contrast():
    """Everything carrying the same condition cannot show that the condition matters."""
    obs = [_obs(f"m{i}", "whizzer", "rear-body-lock") for i in range(12)]
    rep = analyze_context(obs, **FAST)
    assert rep.criteria == []
    assert "no condition contrast" in rep.note


def test_default_response_is_reported():
    obs = [_obs(f"m{i}", "whizzer" if i % 2 else "", "knee-tap") for i in range(12)]
    rep = analyze_context(obs, **FAST)
    assert rep.default_response == "knee-tap"


def test_terminal_failures_are_excluded_from_selection():
    obs = [_obs(f"m{i}", "whizzer", None) for i in range(12)]
    rep = analyze_context(obs, **FAST)
    assert rep.n == 0


# ---------------------------------------------------------------- the two big ones


def test_null_calibration_finds_almost_nothing():
    """Condition and response independent by construction -> essentially no survivors."""
    rng = np.random.default_rng(7)
    conds = ["whizzer", "sprawl", "underhook"]
    resps = ["rear-body-lock", "front-headlock", "knee-tap"]
    obs = [
        _obs(f"m{m}", conds[rng.integers(3)], resps[rng.integers(3)])
        for m in range(15)
        for _ in range(6)
    ]
    reports = analyze(obs, **FAST)
    survivors = [c for r in reports for c in r.criteria if c.survives]
    assert survivors == [], [(c.condition, c.response, c.q_value) for c in survivors]


def test_planted_criterion_is_recovered():
    """A strong, consistent, multi-match association must be found."""
    obs: list[Observation] = []
    for m in range(14):
        for _ in range(3):
            obs.append(_obs(f"m{m}", "whizzer", "rear-body-lock", success=True))
        for _ in range(3):
            obs.append(_obs(f"m{m}", "sprawl", "front-headlock", success=True))
    reports = analyze(obs, **FAST)
    survivors = {(c.condition, c.response) for r in reports for c in r.criteria if c.survives}
    assert ("whizzer", "rear-body-lock") in survivors
    assert ("sprawl", "front-headlock") in survivors


# ---------------------------------------------------------------- estimator behaviour


def test_baseline_uses_the_complement_not_the_total():
    """P(B|A,¬C) must exclude the C rows, else a common condition dilutes its own effect."""
    obs = [_obs(f"m{i}", "whizzer", "rear-body-lock") for i in range(10)]
    obs += [_obs(f"n{i}", "sprawl", "knee-tap") for i in range(10)]
    rep = analyze_context(obs, **FAST)
    crit = next(c for c in rep.criteria if c.condition == "whizzer"
                and c.response == "rear-body-lock")
    # none of the 10 non-whizzer observations produced a rear body lock
    assert crit.n_without == 0
    assert crit.n_no_condition == 10
    assert crit.p_without < 0.15


def test_single_observation_never_becomes_a_criterion():
    """A criterion is derived from a body of evidence, never one instance. The floor removes
    it BEFORE any test runs, so it cannot inflate the FDR correction either."""
    obs = [_obs("m0", "rare-condition", "odd-response")]
    obs += [_obs(f"m{i}", "common", "usual") for i in range(1, 12)]
    rep = analyze_context(obs, **FAST)
    assert not [c for c in rep.criteria if c.condition == "rare-condition"]
    assert rep.below_floor >= 1
    assert rep.candidates > len(rep.criteria)


def test_pattern_confined_to_one_match_is_rejected():
    """Three occurrences, all in the same bout: that is the bout, not the athlete's game."""
    obs = [_obs("m0", "rare", "odd") for _ in range(3)]
    obs += [_obs(f"m{i}", "common", "usual") for i in range(1, 12)]
    rep = analyze_context(obs, **FAST)
    assert not [c for c in rep.criteria if c.condition == "rare"]


def test_pattern_confined_to_one_opponent_is_rejected():
    """Spread over matches but always the same opponent: a matchup, not a decision rule."""
    obs = [_obs(f"m{i}", "rare", "odd", opp="same-guy") for i in range(3)]
    obs += [_obs(f"n{i}", "common", "usual") for i in range(12)]
    rep = analyze_context(obs, **FAST)
    assert not [c for c in rep.criteria if c.condition == "rare"]


def test_unrecorded_opponent_does_not_fail_the_opponent_floor():
    """An unknown opponent is unknown, not 'one opponent' — do not reject on absent data."""
    obs = [_obs(f"m{i}", "rare", "odd", opp="") for i in range(3)]
    obs += [_obs(f"n{i}", "common", "usual", opp="") for i in range(12)]
    rep = analyze_context(obs, **FAST)
    assert [c for c in rep.criteria if c.condition == "rare"]


def test_thin_but_eligible_triad_is_shrunk_not_certain():
    """Just past the floor, 3-of-3, must still land well below certainty."""
    obs = [_obs(f"m{i}", "rare", "odd") for i in range(3)]
    obs += [_obs(f"n{i}", "common", "usual") for i in range(12)]
    rep = analyze_context(obs, **FAST)
    crit = next(c for c in rep.criteria if c.condition == "rare")
    assert crit.n_with == 3
    assert crit.p_with < 0.85


def test_success_rate_is_tracked_separately_from_selection():
    """Chosen often and works often are different questions; both must be reported."""
    obs = []
    for m in range(12):
        obs.append(_obs(f"m{m}", "whizzer", "rear-body-lock", success=(m % 4 == 0)))
        obs.append(_obs(f"m{m}", "sprawl", "knee-tap", success=True))
    rep = analyze_context(obs, **FAST)
    crit = next(c for c in rep.criteria if c.condition == "whizzer")
    assert crit.success_rate == pytest.approx(0.25, abs=1e-9)


def test_effect_interval_brackets_the_point_estimate():
    obs = [_obs(f"m{i}", "whizzer" if i % 2 else "sprawl", "rear-body-lock")
           for i in range(16)]
    rep = analyze_context(obs, **FAST)
    for c in rep.criteria:
        assert c.effect_lo <= c.effect <= c.effect_hi


def test_criteria_are_ranked_by_conservative_bound():
    obs = []
    for m in range(14):
        obs += [_obs(f"m{m}", "whizzer", "rear-body-lock")] * 3
        obs += [_obs(f"m{m}", "sprawl", "front-headlock")] * 3
    reports = analyze(obs, **FAST)
    los = [c.effect_lo for c in reports[0].criteria]
    assert los == sorted(los, reverse=True)


def test_results_are_deterministic():
    obs = []
    for m in range(12):
        obs += [_obs(f"m{m}", "whizzer", "rear-body-lock")] * 2
        obs += [_obs(f"m{m}", "sprawl", "knee-tap")] * 2
    a = to_json(analyze(obs, **FAST))
    b = to_json(analyze(obs, **FAST))
    assert a == b


def test_unclustered_corpus_is_flagged_rather_than_implied_sound():
    """One observation per match: the test still runs, but it preserved no within-match
    structure, so the weaker guarantee must be stated instead of quietly assumed."""
    obs = [_obs(f"m{i}", "whizzer" if i % 2 else "sprawl", "rear-body-lock")
           for i in range(12)]
    rep = analyze_context(obs, **FAST)
    assert rep.permutation_blocked is False
    assert "no clustering" in rep.note


def test_clustered_corpus_reports_blocked_permutation():
    obs = []
    for m in range(8):
        obs += [_obs(f"m{m}", "whizzer", "rear-body-lock")] * 2
        obs += [_obs(f"m{m}", "sprawl", "knee-tap")] * 2
    rep = analyze_context(obs, **FAST)
    assert rep.permutation_blocked is True


# ---------------------------------------------------------------- adapters


def test_observations_from_patterns_builds_context_and_outcome():
    from analysis.decision_flow import DecisionPattern, PatternEvidence

    ev = PatternEvidence("mid", "slug", "ath", 1, (2,), 3, 40)
    pattern = DecisionPattern(
        source_position_key="closed-guard", action_key="hip-bump",
        condition_key="whizzer", response_key="rear-body-lock",
        resulting_position_key="back-control", success_count=1, evidence=[ev],
    )
    got = observations_from_patterns([pattern])[0]
    assert got.context == "closed-guard|hip-bump"
    assert got.condition == "whizzer"
    assert got.response == "rear-body-lock"
    assert got.match_id == "mid"
    assert got.success is True


def test_observations_handle_missing_position_and_evidence():
    from analysis.decision_flow import DecisionPattern

    pattern = DecisionPattern(
        source_position_key=None, action_key="hip-bump", condition_key=None,
        response_key=None, resulting_position_key=None, failure_count=1,
    )
    got = observations_from_patterns([pattern])[0]
    assert got.context == "-|hip-bump"
    assert got.match_id == ""
    assert got.success is False


def test_to_json_reports_gates_and_counts():
    obs = []
    for m in range(12):
        obs += [_obs(f"m{m}", "whizzer", "rear-body-lock")] * 2
        obs += [_obs(f"m{m}", "sprawl", "knee-tap")] * 2
    payload = to_json(analyze(obs, **FAST))
    assert payload["contexts"] == 1
    assert payload["criteriaTested"] >= 2
    assert payload["gates"]["fdrQ"] == 0.10
    assert "reports" in payload


# ---------------------------------------------------------------- level selection


def _levels(mapping):
    return lambda c: mapping.get(c, [])


def _members(mapping):
    return lambda lvl: mapping.get(lvl, set())


LEVELS = {"ashi-barai": ["ashi-barai", "ashi-waza", "takedown"]}
MEMBERS = {
    "ashi-barai": {"ashi-barai"},
    "ashi-waza": {"ashi-barai", "ouchi-gari", "kouchi-gari"},
    "takedown": {"ashi-barai", "ouchi-gari", "kouchi-gari", "single-leg"},
}


def test_specific_level_wins_when_it_differs_from_siblings():
    obs = [_obs(f"m{i}", "ashi-barai", "back-take") for i in range(12)]
    obs += [_obs(f"n{i}", "ouchi-gari", "guard-pull") for i in range(6)]
    obs += [_obs(f"o{i}", "kouchi-gari", "guard-pull") for i in range(6)]
    got = select_level(obs, "ashi-barai", "back-take",
                       levels_of=_levels(LEVELS), members_of=_members(MEMBERS))
    assert got.chosen == "ashi-barai"
    assert got.reason == "specific"
    assert got.effect_lo > 0


def test_generalizes_when_every_sibling_behaves_alike():
    """The design's Case C: if all of Ashi Waza leads to B, the specific throw is not the
    criterion — the family is."""
    obs = []
    for i in range(8):
        obs.append(_obs(f"a{i}", "ashi-barai", "back-take"))
        obs.append(_obs(f"b{i}", "ouchi-gari", "back-take"))
        obs.append(_obs(f"c{i}", "kouchi-gari", "back-take"))
        obs.append(_obs(f"d{i}", "single-leg", "guard-pull"))
    got = select_level(obs, "ashi-barai", "back-take",
                       levels_of=_levels(LEVELS), members_of=_members(MEMBERS))
    assert got.chosen != "ashi-barai"
    assert got.reason in ("generalized", "specific")


def test_thin_level_is_not_promoted():
    obs = [_obs("m0", "ashi-barai", "back-take")]
    obs += [_obs(f"n{i}", "ouchi-gari", "guard-pull") for i in range(10)]
    got = select_level(obs, "ashi-barai", "back-take",
                       levels_of=_levels(LEVELS), members_of=_members(MEMBERS))
    assert got.reason == "insufficient"
    assert got.chosen == "takedown"


def test_no_hierarchy_returns_the_condition_itself():
    obs = [_obs(f"m{i}", "sprawls", "knee-tap") for i in range(10)]
    got = select_level(obs, "sprawls", "knee-tap",
                       levels_of=_levels({}), members_of=_members({}))
    assert got.chosen == "sprawls"
    assert got.reason == "no-hierarchy"


def test_sibling_set_excludes_the_child_itself():
    """The nested-comparison bug: siblings must never contain the child's own observations."""
    obs = [_obs(f"m{i}", "ashi-barai", "back-take") for i in range(10)]
    effect, lo, n_lvl, n_sib = sibling_contrast(
        obs, "back-take", {"ashi-barai"}, MEMBERS["ashi-waza"] - {"ashi-barai"})
    assert n_lvl == 10
    assert n_sib == 0  # no sibling observations exist, so there is nothing to contrast


def test_postural_conditions_have_no_technique_hierarchy():
    assert taxonomy_levels("cond:sprawls") == []
    assert taxonomy_levels("cond:hand-fight") == []
    assert taxonomy_levels("not-a-condition") == []


def test_technique_derived_condition_ladders_through_the_taxonomy():
    chain = taxonomy_levels("cond:closed-guard")
    assert chain[0] == "cond:closed-guard"
    assert "tax:guard" in chain


def test_stability_discriminates_on_a_thin_triad():
    """A barely-eligible triad must not read as perfectly stable: when its handful of matches
    are resampled away the cell empties, and the Beta prior alone must not carry it."""
    obs = [_obs(f"m{i}", "rare", "odd-response") for i in range(3)]
    obs += [_obs(f"n{i}", "common", "usual") for i in range(14)]
    rep = analyze_context(obs, **FAST)
    crit = next(c for c in rep.criteria if c.condition == "rare")
    assert crit.n_with == 3
    assert crit.stability < 0.99


# ---------------------------------------------------------------- condition hierarchy


def test_curated_conditions_climb_their_family():
    lv, mem = condition_hierarchy({"cond:sprawls", "cond:bases-out", "cond:hand-fight"})
    assert lv("cond:sprawls") == ["cond:sprawls", "fam:base"]
    assert mem("fam:base") == {"cond:sprawls", "cond:bases-out"}
    assert mem("cond:sprawls") == {"cond:sprawls"}


def test_family_members_are_restricted_to_observed_conditions():
    """A family must not claim members the corpus never saw, or the sibling group is fiction."""
    _, mem = condition_hierarchy({"cond:sprawls"})
    assert mem("fam:base") == {"cond:sprawls"}


def test_technique_conditions_climb_the_taxonomy():
    lv, mem = condition_hierarchy({"cond:closed-guard", "cond:half-guard"})
    assert lv("cond:closed-guard")[0] == "cond:closed-guard"
    assert "tax:guard" in lv("cond:closed-guard")
    assert mem("tax:guard") == {"cond:closed-guard", "cond:half-guard"}


def test_composite_bundles_stay_flat():
    """A bundle spans two families; forcing it into one would invent a grouping."""
    lv, _ = condition_hierarchy({"cond:sprawls-and-cond:hand-fight"})
    assert lv("cond:sprawls-and-cond:hand-fight") == []


def test_unknown_level_has_no_members():
    _, mem = condition_hierarchy({"cond:sprawls"})
    assert mem("fam:nonexistent") == set()
    assert mem("cond:never-seen") == set()


def test_family_level_is_chosen_when_siblings_agree():
    """Every member of fam:base leading to the same response -> the family is the criterion."""
    lv, mem = condition_hierarchy({"cond:sprawls", "cond:bases-out", "cond:hand-fight"})
    obs = []
    for i in range(8):
        obs.append(_obs(f"a{i}", "cond:sprawls", "front-headlock"))
        obs.append(_obs(f"b{i}", "cond:bases-out", "front-headlock"))
        obs.append(_obs(f"c{i}", "cond:hand-fight", "guard-pull"))
    got = select_level(obs, "cond:sprawls", "front-headlock", levels_of=lv, members_of=mem)
    assert got.chosen == "fam:base"
    assert got.reason == "generalized"
