"""PoC-E9 — the state-space / order / terminal machinery, checked before it is read.

The load-bearing tests here are the ones that protect the two decisions the PoC's whole
meaning rests on:

  * ``test_state_names_survive_clean_label`` — a bare ``"pass"`` canonicalises to
    ``"Guard Pass"`` inside ``technique_match.clean_label``. If the state names stopped
    being prefixed, ``Kernel.build`` and ``Kernel.node_of`` would silently disagree on the
    finish-AUC limb and every node would score cold.
  * ``test_every_space_scores_the_same_steps`` — the primary criterion is a PAIRED delta
    across state spaces. That only means anything if the three arms score the identical
    steps, which is exactly why repeats are not folded. Break the pairing and the tables
    still render, wrong.

Everything is synthetic. No DB, no network.
"""

from __future__ import annotations

import math
from typing import Any

from analysis.attribution import EVENT_TYPES
from analysis.poc.e9_markov import (
    ALPHA,
    CAT_ALPHABET,
    MIN_SCORED_INDEX,
    ORDERS,
    S_CAT,
    S_LABEL,
    S_V3,
    SPACES,
    UNK,
    BoutRow,
    StateSpace,
    _bin_of,
    _hazard,
    _js,
    adcc_divergence,
    bout_chain,
    bout_positions,
    chains_of,
    compare_spaces,
    event_family,
    fit_backoff,
    keyed_chains_of,
    max_estimable_order,
    own_chains,
    paired_delta,
    run_space,
    score_order,
    signed_delta,
    signed_wins,
    space_kernel,
    split_rows,
    terminal_depth,
    v3_control_bucket,
    verify_gate,
    wins,
)
from analysis.technique_match import clean_label


def _ev(label: str, typ: str, actor: str = "A", ts: float | None = None,
        ok: bool = False) -> dict[str, Any]:
    e: dict[str, Any] = {"label": label, "type": typ, "actor_id": actor, "successful": ok}
    if ts is not None:
        e["ts"] = ts
    return e


def _row(events: list[dict[str, Any]], key: tuple[Any, ...], win_type: str = "SUBMISSION",
         event: str | None = "Polaris 1") -> BoutRow:
    from analysis.discipline import match_discipline
    from analysis.poc.e9_markov import _elapsed

    return BoutRow(key=key, sequence=events, athlete_a="A", athlete_b="B", event=event,
                   win_type=win_type, family=event_family(event),
                   discipline=match_discipline(event), elapsed=_elapsed(events))


# ── state spaces ────────────────────────────────────────────────────────────────
def test_state_names_survive_clean_label() -> None:
    """Prefixed names must be a fixed point of ``clean_label``, or the AUC limb goes cold."""
    for t in sorted(EVENT_TYPES):
        for name in (S_CAT.state_of(_ev("x", t)), S_V3.state_of(_ev("x", t))):
            assert name, t
            assert clean_label(name, t) == name, name
            assert clean_label(name, "") == name, name
    # and the trap that motivated the prefix is real, so the test is not vacuous
    assert clean_label("pass", "") == "Guard Pass"


def test_label_state_is_a_fixed_point() -> None:
    """S-label states ARE ``clean_label`` output, so the same identity has to hold."""
    for raw, typ in (("Back Control", "control"), ("Armbar", "submission"),
                     ("Half Guard", "guard"), ("Knee Cut Pass", "pass")):
        s = S_LABEL.state_of(_ev(raw, typ))
        assert clean_label(s, typ) == s


def test_v3_explodes_only_control() -> None:
    for t in sorted(EVENT_TYPES - {"control"}):
        assert S_V3.state_of(_ev("whatever", t)) == f"v3/{t}"
    assert S_V3.state_of(_ev("Back Control", "control")) == "v3/control/back-control"
    assert S_V3.state_of(_ev("Mount", "control")) == "v3/control/mount"
    assert S_V3.state_of(_ev("Side Control", "control")) == "v3/control/pin"
    assert S_V3.state_of(_ev("North-South Position", "control")) == "v3/control/pin"
    assert S_V3.state_of(_ev("Front Headlock", "control")) == "v3/control/front-headlock"
    assert S_V3.state_of(_ev("Collar Tie", "control")) == "v3/control/peripheral"


def test_v3_bucket_is_a_pure_function_of_the_label() -> None:
    """No bout, no type, no context — the partition is fixed before any held-out number."""
    assert v3_control_bucket("Back Control") == v3_control_bucket("back  control")
    assert v3_control_bucket("Body Triangle") == "back-control"
    assert v3_control_bucket("something nobody logged") == "peripheral"


def test_unknown_type_yields_no_state() -> None:
    """`match`/`strike`/`penalty` rows are not grappling events (attribution.EVENT_TYPES)."""
    assert S_CAT.state_of(_ev("Match", "match")) == ""
    assert S_V3.state_of(_ev("Strike", "strike")) == ""


# ── chains ──────────────────────────────────────────────────────────────────────
def test_repeats_are_not_folded() -> None:
    """The whole pairing rests on this: A → A is a real step, not an edge the graph refuses."""
    seq = [_ev("Back Control", "control"), _ev("Mount", "control"),
           _ev("Armbar", "submission")]
    (chain,) = own_chains(seq, S_CAT)
    assert [s for s, _ in chain] == ["cat/control", "cat/control", "cat/submission"]


def test_own_chains_never_cross_actors_and_bout_chain_always_does() -> None:
    seq = [_ev("Back Control", "control", "A"), _ev("Half Guard", "guard", "B"),
           _ev("Armbar", "submission", "A")]
    own = own_chains(seq, S_CAT)
    assert len(own) == 1  # B has a single event, too short for a chain
    assert [s for s, _ in own[0]] == ["cat/control", "cat/submission"]
    (bout,) = bout_chain(seq, S_CAT)
    assert [s for s, _ in bout] == ["cat/control", "cat/guard", "cat/submission"]


def test_every_space_scores_the_same_steps() -> None:
    """Paired across state spaces — the primary criterion is meaningless otherwise."""
    seq = [_ev(f"L{i}", ("control", "guard", "submission", "pass")[i % 4]) for i in range(9)]
    rows = [_row(seq, (2020, "x", str(i))) for i in range(8)]
    train, held = split_rows(rows)
    counts = set()
    for sp in SPACES:
        ev = keyed_chains_of(held, sp, "C-own")
        counts.add(sum(max(0, len(c) - MIN_SCORED_INDEX) for _, c in ev))
    assert len(counts) == 1, counts


# ── the estimator ───────────────────────────────────────────────────────────────
def test_backoff_distributions_are_normalised() -> None:
    chains = [[("a", "guard"), ("b", "pass"), ("a", "guard"), ("c", "control")]] * 6
    m = fit_backoff(chains, 2, ALPHA)
    assert UNK in m.vocab
    assert math.isclose(sum(m.state_dist(["a", "b"]).values()), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(m.category_dist(["a", "b"]).values()), 1.0, abs_tol=1e-9)
    assert set(m.category_dist([]).keys()) == set(CAT_ALPHABET)


def test_a_deterministic_successor_dominates_its_distribution() -> None:
    """Interpolation must actually let evidence through, not smooth it to the marginal."""
    chains = [[("a", "guard"), ("b", "pass")] * 12]
    m = fit_backoff(chains, 1, 0.1)
    assert m.state_dist(["a"])["b"] > 0.9


def test_interpolation_backs_off_when_the_context_is_unseen() -> None:
    """An unseen context contributes λ=0, so the answer is exactly the lower order's."""
    chains = [[("a", "guard"), ("b", "pass"), ("c", "control")] * 4]
    m2 = fit_backoff(chains, 2, ALPHA)
    unseen = m2.state_dist(["zzz", "qqq"])
    order1 = fit_backoff(chains, 1, ALPHA).state_dist(["qqq"])
    for k in m2.vocab:
        assert math.isclose(unseen[k], order1[k], abs_tol=1e-12)


def test_category_projection_is_estimated_not_assumed() -> None:
    """A label filed under two types splits its category mass — the 3.3% case, in miniature."""
    chains = [[("x", "guard")] * 3 + [("x", "pass")] * 1 + [("y", "control")] * 4]
    m = fit_backoff(chains, 1, 0.0001)
    pc = m.cat_given_state["x"]
    assert pc["guard"] > pc["pass"] > 0
    assert math.isclose(pc["guard"] + pc["pass"], 1.0, abs_tol=1e-3)


# ── order selection ─────────────────────────────────────────────────────────────
def _synthetic_rows(n: int = 40) -> list[BoutRow]:
    """Bouts whose CATEGORY is a deterministic function of the previous two categories.

    guard → pass → control → submission → guard … so a first-order model already nails it
    and a second order cannot add anything; the point of the fixture is that the machinery
    runs end to end on a corpus with a known answer.
    """
    cycle = ["guard", "pass", "control", "submission"]
    rows = []
    for i in range(n):
        seq = [_ev(f"{cycle[j % 4]} thing", cycle[j % 4], ts=float(j * 30))
               for j in range(10)]
        rows.append(_row(seq, (2000 + i, "x", str(i))))
    return rows


def test_order_rows_are_paired_and_higher_order_never_loses_coverage() -> None:
    rows = _synthetic_rows()
    train, held = split_rows(rows)
    tr = chains_of(train, S_CAT, "C-own")
    ev = keyed_chains_of(held, S_CAT, "C-own")
    scored = {k: score_order(tr, ev, k, ALPHA, "s", "c", n_boot=50) for k in ORDERS}
    n = {r.n_steps for r in scored.values()}
    assert len(n) == 1 and n != {0}
    for k in (1, 2, 3):
        assert scored[k].groups == scored[0].groups
    # a deterministic cycle: order 1 must beat the marginal by a mile
    assert scored[1].ll > scored[0].ll + 0.5


def test_max_estimable_order_stops_at_the_first_no() -> None:
    rows = _synthetic_rows()
    train, held = split_rows(rows)
    res = run_space(train, held, S_CAT, "C-own", n_boot=60, alphas=(ALPHA,))
    best, steps = max_estimable_order(res.rows, n_boot=60)
    assert best == res.max_order
    # never reports an order it did not test
    assert set(steps) <= {1, 2, 3}
    if best < 3:
        assert (best + 1) in steps and not wins(steps[best + 1])


def test_paired_delta_refuses_unpaired_rows() -> None:
    rows = _synthetic_rows(12)
    train, held = split_rows(rows)
    a = score_order(chains_of(train, S_CAT, "C-own"), keyed_chains_of(held, S_CAT, "C-own"),
                    1, ALPHA, "s", "c", n_boot=20)
    b = score_order(chains_of(train, S_CAT, "C-bout"), keyed_chains_of(held, S_CAT, "C-bout"),
                    1, ALPHA, "s", "c", n_boot=20)
    if a.groups != b.groups:
        try:
            paired_delta(a, b, n_boot=20)
        except ValueError as exc:
            assert "unpaired" in str(exc)
        else:
            raise AssertionError("unpaired rows were compared silently")


def test_a_degenerate_interval_is_never_a_win() -> None:
    """Zero-width means the bootstrap saw no bout-to-bout variation — a constant offset, not
    a prediction. It is the exact shape a smoothing difference takes."""
    assert not wins((0.005, 0.005, 0.005))
    assert wins((0.005, 0.001, 0.009))
    assert not wins((0.005, -0.001, 0.009))
    assert not wins((float("nan"),) * 3)


def test_cross_space_deltas_are_signed_consistently() -> None:
    rows = _synthetic_rows(24)
    train, held = split_rows(rows)
    results = [run_space(train, held, sp, "C-own", n_boot=40, alphas=(ALPHA,))
               for sp in SPACES]
    cross = compare_spaces(results, n_boot=40)
    assert len(cross) == 3
    fwd = signed_delta(cross, S_V3.name, S_CAT.name)
    rev = signed_delta(cross, S_CAT.name, S_V3.name)
    assert math.isclose(fwd[0], -rev[0], abs_tol=1e-12)
    for w, loser in signed_wins(cross):
        assert w != loser


# ── the finish-AUC limb ─────────────────────────────────────────────────────────
def test_kernel_nodes_and_node_of_agree() -> None:
    """The prefix test, but end to end: every scored event must land on a real graph node."""
    rows = _synthetic_rows(8)
    for sp in SPACES:
        kern = space_kernel(sp)
        g = kern.build([b for r in rows for b in r.mirrored()])
        for r in rows:
            for e in r.sequence:
                s = sp.state_of(e)
                if s:
                    assert kern.node_of(e) == s
                    assert g.has_node(s), (sp.name, s)


# ── arm 4 ───────────────────────────────────────────────────────────────────────
def test_event_family_is_the_stated_prefix_rule() -> None:
    for tag in ("ADCC 2022", "adcc", "ADCC Trials 2023 East Coast", "ADCC World Championship"):
        assert event_family(tag) == "adcc"
    others: tuple[str | None, ...] = ("Polaris 30", "CJI 2 - Day 1", None, "",
                                      "IBJJF Worlds 2023")
    for other in others:
        assert event_family(other) == "other"


def test_js_is_zero_on_identical_distributions_and_one_on_disjoint() -> None:
    p = {"a": 0.5, "b": 0.5}
    assert math.isclose(_js(p, p), 0.0, abs_tol=1e-12)
    assert math.isclose(_js({"a": 1.0}, {"b": 1.0}), 1.0, abs_tol=1e-9)


def test_divergence_permutation_p_is_never_zero_and_carries_coverage() -> None:
    """+1 in both parts: a Monte-Carlo p of exactly zero claims more than the draws support."""
    rows = ([_row([_ev("Back Control", "control"), _ev("Armbar", "submission"),
                   _ev("Mount", "control")], (2020, "x", f"a{i}"), event="ADCC 2022")
             for i in range(6)]
            + [_row([_ev("Back Control", "control"), _ev("Half Guard", "guard"),
                     _ev("Mount", "control")], (2020, "x", f"o{i}"), event="Polaris 1")
               for i in range(6)])
    out = adcc_divergence(rows, S_CAT, "C-bout", n_perm=50)
    assert out
    for r in out:
        assert 0.0 < r["p"] <= 1.0
        assert 0.0 <= r["q"] <= 1.0
        assert r["cov"].clusters >= 0


# ── arm 5 ───────────────────────────────────────────────────────────────────────
def test_terminal_alphabet_merges_points_and_never_defaults_a_missing_win_type() -> None:
    ev = [_ev("Mount", "control")]
    assert _row(ev, (1,), "SUBMISSION").terminal == "END/submission"
    assert _row(ev, (1,), "DECISION").terminal == "END/points"
    assert _row(ev, (1,), "POINTS").terminal == "END/points"
    assert _row(ev, (1,), "DRAW").terminal == "END/draw"
    assert BoutRow((1,), ev, "A", "B", None, None, "other", "grappling", None).terminal is None


def test_terminal_depth_finds_history_that_is_really_there() -> None:
    """A corpus where the LAST state fully determines the ending must show M1 beating M0."""
    rows = []
    for i in range(60):
        sub = i % 2 == 0
        tail = "Armbar" if sub else "Mount"
        typ = "submission" if sub else "control"
        seq = [_ev("Half Guard", "guard"), _ev("Guard Pass", "pass"),
               _ev("Back Control", "control"), _ev("Mount", "control"), _ev(tail, typ)]
        rows.append(_row(seq, (2000 + i, "x", str(i)),
                         "SUBMISSION" if sub else "DECISION"))
    train, held = split_rows(rows)
    d = terminal_depth(train, held, S_CAT, n_boot=200)
    assert d.n_eval > 0
    assert d.ll[1] > d.ll[0]
    assert d.history_dependent


def test_terminal_depth_is_flat_when_the_ending_is_independent_of_the_history() -> None:
    """Many distinct last states, the ending alternating regardless — nothing to learn."""
    tails = [("Mount", "control"), ("Half Guard", "guard"), ("Guard Pass", "pass"),
             ("Sweep", "sweep"), ("Escape to Standing", "escape")]
    rows = []
    for i in range(120):
        tail, typ = tails[i % 5]
        seq = [_ev("Half Guard", "guard"), _ev("Guard Pass", "pass"),
               _ev("Back Control", "control"), _ev(tail, typ)]
        rows.append(_row(seq, (2000 + i, "x", str(i)),
                         "SUBMISSION" if i % 2 else "DECISION"))
    train, held = split_rows(rows)
    d = terminal_depth(train, held, S_CAT, n_boot=300)
    assert d.contexts[1] > 1
    assert not d.history_dependent
    assert not d.deep


def test_a_single_context_can_never_be_called_history_dependent() -> None:
    """Nested interpolation shrinks once per level, so an order-1 model whose context is the
    SAME for every bout beats M0 by a constant with a zero-width interval. That is
    arithmetic, not grappling, and the guard has to catch it."""
    rows = [_row([_ev("Half Guard", "guard"), _ev("Guard Pass", "pass"),
                  _ev("Mount", "control"), _ev("Back Control", "control")],
                 (2000 + i, "x", str(i)), "SUBMISSION" if i % 2 else "DECISION")
            for i in range(60)]
    train, held = split_rows(rows)
    d = terminal_depth(train, held, S_CAT, n_boot=200)
    assert d.contexts[1] == 1
    assert d.ll[1] > d.ll[0]           # the artefact is real and measurable ...
    assert not d.history_dependent      # ... and it is refused


def test_elapsed_is_within_bout_and_a_missing_ts_is_never_defaulted() -> None:
    with_ts = _row([_ev("Mount", "control", ts=1000.0), _ev("Armbar", "submission", ts=1090.0)],
                   (1,))
    assert with_ts.elapsed == [0.0, 90.0]
    partial = _row([_ev("Mount", "control", ts=10.0), _ev("Armbar", "submission")], (1,))
    assert partial.elapsed is None
    assert bout_positions(partial, S_CAT, "time") is None
    assert bout_positions(partial, S_CAT, "step") == [1.0, 2.0]


def test_elapsed_positions_align_with_the_filtered_chain() -> None:
    """A dropped event must drop its timestamp with it, or every later bin is off by one."""
    seq = [_ev("Mount", "control", ts=0.0), _ev("Match", "match", ts=5.0),
           _ev("Armbar", "submission", ts=100.0)]
    r = _row(seq, (1,))
    assert bout_positions(r, S_CAT, "time") == [0.0, 100.0]
    assert len(bout_chain(r.sequence, S_CAT)[0]) == 2


def test_hazard_rows_are_a_real_survival_table() -> None:
    rows = []
    for i in range(40):
        n = 4 + (i % 12)
        seq = [_ev("Mount", "control", ts=float(j * 60)) for j in range(n)]
        rows.append(_row(seq, (2000 + i, "x", str(i)),
                         "DECISION" if n > 8 else "SUBMISSION"))
    h = _hazard(rows, S_CAT, "step")
    assert h.n_bouts == 40
    for b in h.bins:
        for t, (absorbed, at_risk) in h.cells[b].items():
            assert 0 <= absorbed <= at_risk, (b, t)
    # every bout absorbs exactly once, in exactly one bin
    total = sum(a for b in h.bins for a, _ in h.cells[b].values())
    assert total == 40


def test_hazard_excludes_ts_less_bouts_from_the_time_axis_only() -> None:
    good = [_row([_ev("Mount", "control", ts=float(j * 60)) for j in range(5)],
                 (2000 + i, "x", str(i))) for i in range(10)]
    bad = [_row([_ev("Mount", "control") for _ in range(5)], (2100 + i, "x", str(i)))
           for i in range(4)]
    assert _hazard(good + bad, S_CAT, "step").n_bouts == 14
    t = _hazard(good + bad, S_CAT, "time")
    assert t.n_bouts == 10 and t.n_excluded == 4


def test_bin_edges_are_half_open_on_time_and_closed_on_steps() -> None:
    assert _bin_of(1, "step") == "1-4" and _bin_of(4, "step") == "1-4"
    assert _bin_of(5, "step") == "5-8" and _bin_of(9999, "step") == "21+"
    assert _bin_of(0.0, "time") == "0-2 min" and _bin_of(120.0, "time") == "2-4"
    assert _bin_of(599.9, "time") == "8-10" and _bin_of(600.0, "time") == ">10"


# ── gate ────────────────────────────────────────────────────────────────────────
def test_gate_self_check_speaks_up_when_the_corpus_moves() -> None:
    from analysis.poc.e9_markov import E8_GATED_BOUTS, GateReport

    assert "OK" in verify_gate(GateReport(passed=E8_GATED_BOUTS))
    assert "MISMATCH" in verify_gate(GateReport(passed=E8_GATED_BOUTS - 1))
    assert "NOT RUN" in verify_gate(GateReport(error="no DATABASE_URL"))


def test_split_is_chronological_and_the_boundary_goes_to_train() -> None:
    rows = [_row([_ev("Mount", "control")], (2000 + i, "x", str(i))) for i in range(20)]
    train, held = split_rows(rows)
    assert train and held
    assert max(r.key for r in train) < min(r.key for r in held)
    assert len(train) + len(held) == 20


def test_a_space_with_one_state_carries_no_memory() -> None:
    """Degenerate by construction: the order ladder must not invent a win out of it."""
    flat = StateSpace("flat", lambda e: "flat/only")
    rows = [_row([_ev("Mount", "control") for _ in range(8)], (2000 + i, "x", str(i)))
            for i in range(24)]
    train, held = split_rows(rows)
    res = run_space(train, held, flat, "C-own", n_boot=80, alphas=(ALPHA,))
    assert res.max_order == 0
