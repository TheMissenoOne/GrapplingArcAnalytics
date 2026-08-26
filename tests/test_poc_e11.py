"""PoC-E11 — the high-confidence action chain. Pure logic only; no DB, no network.

What is worth pinning here is exactly what the pre-registration says is load-bearing:

* the action chain agrees with `lamas_chain.chain_of` position-for-position (E11 re-walks the
  sequence to keep the underlying event's type/label, so the two walks CAN drift);
* the family alphabet is total over `STATES` and annotation-invariant (the target must not be
  a function of `successful`, or the quality arms measure annotation policy);
* every context arm scores the SAME steps, which is what makes `paired_delta` legal;
* the subset gates and the size-matched subsampling are deterministic;
* the stabilisation rule and the community honesty gate behave as written;
* the PDF builder produces a real PDF from an empty run (smoke).
"""

from __future__ import annotations

from typing import Any

import pytest

from analysis.lamas_chain import STATES, chain_of
from analysis.poc.e9_markov import paired_delta, wins
from analysis.poc.e11_action_chain import (
    A7,
    A12,
    ALPHABET_ACTION,
    ALPHABET_FAMILY,
    CONTEXTS,
    FAMILY_OF,
    MIN_INDEX,
    S_LABEL,
    S_TYPE,
    BoutRow,
    CommunityReport,
    GateReport,
    Run,
    SizeCurve,
    SizePoint,
    _demo,
    _matrix,
    action_chain,
    action_graph,
    annotation_shares,
    averaged,
    community_report,
    family_of,
    outcome,
    ranked_pagerank,
    render_markdown,
    run_size_curve,
    score,
    split_rows,
    steps_of,
    subsample,
    verdicts,
    verify_gate,
    write_pdf,
)


def ev(typ: str, label: str, actor: str | None = "X", ok: bool | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"type": typ, "label": label}
    if actor is not None:
        e["actor_id"] = actor
    if ok is not None:
        e["successful"] = ok
    return e


def row(bid: str, seq: list[dict[str, Any]], year: int = 2024, win: str | None = None,
        event: str = "E") -> BoutRow:
    return BoutRow(
        key=(year, f"{year}-01-01", bid), bout_id=bid, sequence=seq,
        athlete_a="X", athlete_b="Y", event=event, year=year, win_type=win,
        chain=action_chain(seq, win),
        annotated=(sum(1 for e in seq if e.get("successful") is not None) / len(seq)
                   if seq else 0.0),
    )


def two_actor_bout(bid: str, year: int = 2024, n: int = 4,
                   annotate: bool = False) -> BoutRow:
    """A bout long enough and two-sided enough to clear HC-A."""
    pattern = [("control", "Collar Tie", "X"), ("takedown", "Single Leg", "Y"),
               ("pass", "Knee Cut Pass", "X"), ("control", "Back Control", "Y"),
               ("submission", "Rear Naked Choke", "X"), ("sweep", "Butterfly Sweep", "Y")]
    seq = [ev(t, lab, a, False if annotate else None)
           for t, lab, a in (pattern * 3)[:n]]
    return row(bid, seq, year=year)


# ── the chain ───────────────────────────────────────────────────────────────────
def test_self_check_runs() -> None:
    _demo()


def test_action_chain_matches_lamas_chain_of() -> None:
    """The parity that stops the two walks drifting — E11 re-implements the walk to keep the
    underlying event, and `lamas_chain` is the owner of the mapping rules."""
    seq = [ev("control", "Collar Tie"), ev("takedown", "Trip", ok=True),
           ev("guard", "Half Guard"), ev("pass", "Knee Cut Pass"),
           ev("control", "Back Control"), ev("submission", "Armbar", ok=True),
           ev("submission", "Tap", ok=True)]
    for win in ("SUBMISSION", "DECISION", None):
        mine = [p.action for p in action_chain(seq, win)]
        theirs = [s.state for s in chain_of({"id": "b", "win_type": win, "seq": seq}).steps]
        assert mine == theirs, win


def test_the_absorbing_rule_is_the_bouts_result_not_the_flag() -> None:
    """`lamas_chain` rule 4: a landed submission in a bout won on DECISION keeps its
    successors. Truncating on the flag would cut a real bout at event 0."""
    seq = [ev("submission", "Knee Bar", ok=True), ev("control", "Back Control"),
           ev("pass", "Knee Cut Pass")]
    assert len(action_chain(seq, "DECISION")) == 3
    assert len(action_chain(seq, "SUBMISSION")) == 1


def test_unmapped_events_are_passed_over_not_broken_into() -> None:
    seq = [ev("control", "Collar Tie"), ev("guard", "Closed Guard"),
           ev("escape", "Back Escape"), ev("pass", "Knee Cut Pass")]
    assert [p.action for p in action_chain(seq, None)] == ["CDP", "GPSA"]


# ── the target alphabet ─────────────────────────────────────────────────────────
def test_family_alphabet_covers_every_action() -> None:
    assert set(FAMILY_OF) == set(STATES)
    assert set(FAMILY_OF.values()) == set(ALPHABET_FAMILY)
    assert ALPHABET_ACTION == STATES


def test_the_family_target_is_invariant_to_the_successful_flag() -> None:
    """The whole reason the primary target is seven symbols and not twelve: `successful` is
    annotated per ingest batch, so a target that reads it would make the quality arms a
    measurement of annotation policy."""
    for typ, label in (("takedown", "Trip"), ("pass", "Knee Cut"), ("sweep", "Butterfly"),
                       ("submission", "Armbar"), ("control", "Back Control")):
        attempt = action_chain([ev(typ, label, ok=False)], None)
        landed = action_chain([ev(typ, label, ok=True)], None)
        assert attempt[0].action != landed[0].action, (typ, label)
        assert attempt[0].family == landed[0].family, (typ, label)


def test_family_of_is_total_and_pure() -> None:
    assert all(family_of(s) in ALPHABET_FAMILY for s in STATES)
    with pytest.raises(KeyError):
        family_of("NOT-A-STATE")


# ── pairing ─────────────────────────────────────────────────────────────────────
def test_every_context_scores_the_same_steps() -> None:
    """The structural guarantee `paired_delta` depends on: four vocabularies, one position
    list, therefore one step count and one cluster vector."""
    r = two_actor_bout("b", n=6)
    lengths = {len(steps_of(r.chain, c, ALPHABET_FAMILY)) for c in CONTEXTS}
    assert lengths == {len(r.chain)}


def test_context_names_cannot_collide_across_vocabularies() -> None:
    """`clean_label` canonicalises a bare `pass` into `Guard Pass`; unprefixed context names
    would let two arms secretly become one arm."""
    p = action_chain([ev("pass", "Knee Cut Pass")], None)[0]
    names = {A12.of(p), A7.of(p), S_TYPE.of(p), S_LABEL.of(p)}
    assert len(names) == 4
    assert all("/" in n for n in names)


def test_paired_delta_against_itself_is_exactly_zero() -> None:
    train = [two_actor_bout(f"t{i}", year=2020 + i % 3, n=6) for i in range(12)]
    held = [two_actor_bout(f"e{i}", year=2025, n=6) for i in range(4)]
    a = score(train, held, A7, "a", n_boot=50)
    b = score(train, held, A7, "b", n_boot=50)
    d = paired_delta(a, b, 50)
    assert d == (0.0, 0.0, 0.0)
    assert not wins(d)


def test_scored_steps_start_at_min_index() -> None:
    held = [two_actor_bout("e", n=6)]
    train = [two_actor_bout(f"t{i}", n=6) for i in range(4)]
    r = score(train, held, A7, "x", n_boot=0)
    assert r.n_steps == len(held[0].chain) - MIN_INDEX


# ── subsets ─────────────────────────────────────────────────────────────────────
def test_hc_a_refuses_a_single_actor_bout() -> None:
    """`lamas_chain._actor_reliability`'s `single_actor` hole: a short bout filed entirely
    under one name would score reward 1.00 by construction."""
    one = row("one", [ev("control", "Collar Tie", "X"), ev("takedown", "Trip", "X"),
                      ev("pass", "Knee Cut Pass", "X"), ev("control", "Back Control", "X")])
    assert one.n_actors == 1 and not one.hc_a
    assert two_actor_bout("two", n=4).hc_a


def test_hc_a_refuses_a_chain_that_is_too_short() -> None:
    short = row("s", [ev("control", "Collar Tie", "X"), ev("takedown", "Trip", "Y")])
    assert not short.hc_a


def test_hc_b_reads_annotation_coverage_of_the_whole_sequence() -> None:
    seq = [ev("control", "Collar Tie", "X", False), ev("takedown", "Trip", "Y", True),
           ev("pass", "Knee Cut Pass", "X", True), ev("control", "Back Control", "Y", True)]
    full = row("f", seq)
    assert full.hc_a and full.hc_b
    partial = row("p", [*seq[:3], ev("control", "Back Control", "Y")])
    assert partial.hc_a and not partial.hc_b


def test_annotation_shares_expose_the_batch_confound() -> None:
    """The table the report leads with: the same family lands at a different rate inside the
    annotation-complete subset, which is the confound, not a finding."""
    full_batch = row("hc", [ev("takedown", "Trip", "X", True),
                            ev("pass", "Knee Cut", "Y", True),
                            ev("control", "Back Control", "X", True),
                            ev("submission", "Armbar", "Y", True)])
    sparse = [row(f"s{i}", [ev("takedown", "Trip", "X"), ev("pass", "Knee Cut", "Y"),
                            ev("control", "Back Control", "X"),
                            ev("submission", "Armbar", "Y")]) for i in range(5)]
    table = dict((f, (a, b)) for f, a, b in annotation_shares([full_batch, *sparse]))
    assert table["TKD*"][0] < table["TKD*"][1]
    assert table["TKD*"][1] == 1.0


# ── determinism ─────────────────────────────────────────────────────────────────
def test_subsample_is_deterministic_and_without_replacement() -> None:
    rows = [two_actor_bout(f"b{i}") for i in range(20)]
    a = subsample(rows, 7, 1)
    b = subsample(rows, 7, 1)
    c = subsample(rows, 7, 2)
    assert [r.bout_id for r in a] == [r.bout_id for r in b]
    assert len({r.bout_id for r in a}) == 7
    assert [r.bout_id for r in a] != [r.bout_id for r in c]
    assert subsample(rows, 99, 1) == rows


def test_subsample_preserves_chronological_order() -> None:
    rows = [two_actor_bout(f"b{i}", year=2000 + i) for i in range(20)]
    got = subsample(rows, 8, 3)
    assert [r.key for r in got] == sorted(r.key for r in got)


def test_averaged_refuses_unpaired_rows_and_averages_log_space() -> None:
    train = [two_actor_bout(f"t{i}", year=2020, n=6) for i in range(8)]
    held = [two_actor_bout(f"e{i}", year=2025, n=6) for i in range(3)]
    a = score(train[:4], held, A7, "a", n_boot=0)
    b = score(train[4:], held, A7, "b", n_boot=0)
    avg = averaged([a, b], A7.name, "avg")
    assert avg.logps == pytest.approx([(x + y) / 2 for x, y in zip(a.logps, b.logps, strict=True)])
    other = score(train, [held[0]], A7, "c", n_boot=0)
    with pytest.raises(ValueError, match="unpaired"):
        averaged([a, other], A7.name, "avg")


def test_split_is_chronological_and_the_boundary_goes_to_train() -> None:
    rows = [two_actor_bout(f"b{i}", year=2000 + i) for i in range(8)]
    train, held = split_rows(rows, 0.25)
    assert len(train) + len(held) == len(rows)
    assert max(r.key for r in train) < min(r.key for r in held)


# ── the three pre-declared outcomes ─────────────────────────────────────────────
def test_outcome_names_all_three_branches_and_prints_the_half_width() -> None:
    assert "A WINS" in outcome((0.2, 0.1, 0.3), "A", "B")
    assert "B WINS" in outcome((-0.2, -0.3, -0.1), "A", "B")
    null = outcome((0.0, -0.1, 0.1), "A", "B")
    assert "INDISTINGUISHABLE" in null and "0.1000" in null


def test_a_degenerate_interval_is_still_never_a_win() -> None:
    """PoC-E9's guard, inherited: a zero-width interval "excluding 0" is a constant smoothing
    offset, not evidence."""
    assert not wins((0.005, 0.005, 0.005))


# ── C5 ──────────────────────────────────────────────────────────────────────────
def test_stabilisation_rule_needs_every_larger_n_to_hold() -> None:
    """A single quiet point in the middle of a noisy curve is not stabilisation."""
    def pt(n: int, d: tuple[float, float, float]) -> SizePoint:
        return SizePoint(n, "recency", 0.0, 0.0, 0.0, d)

    good = (0.001, -0.01, 0.01)
    bad = (0.05, 0.02, 0.08)
    curve = SizeCurve([pt(10, good), pt(20, bad), pt(30, good)], 30, None, "r")
    from analysis.poc.e11_action_chain import STABILISE_TOL

    assert STABILISE_TOL == 0.010
    rec = [p for p in curve.points if p.scheme == "recency"]
    # replicate the rule: only n=30 has an all-good tail
    ok = [p.n for i, p in enumerate(rec)
          if all(not wins(q.delta_vs_full)
                 and not wins((-q.delta_vs_full[0], -q.delta_vs_full[2], -q.delta_vs_full[1]))
                 and abs(q.delta_vs_full[0]) < STABILISE_TOL for q in rec[i:])]
    assert ok == [30]


def test_an_averaged_row_scores_exactly_the_mean_of_its_draws() -> None:
    """The identity the first draft of this cell got backwards: the per-step and per-draw means
    commute, so averaging log-probabilities buys NO ensembling advantage in the point estimate.
    (It does narrow the interval — that caveat lives in the report, not in an assertion.)"""
    train = [two_actor_bout(f"t{i}", year=2000 + i, n=6) for i in range(16)]
    held = [two_actor_bout(f"e{i}", year=2025, n=6) for i in range(4)]
    draws = [score(subsample(train, 6, s), held, A7, f"d{s}", n_boot=0) for s in range(5)]
    avg = averaged(draws, A7.name, "avg")
    assert avg.ll == pytest.approx(sum(d.ll for d in draws) / len(draws))


def test_random_points_record_their_individual_draws() -> None:
    train = [two_actor_bout(f"t{i}", year=2000 + i, n=6) for i in range(30)]
    held = [two_actor_bout(f"e{i}", year=2025, n=6) for i in range(5)]
    full = score(train, held, A7, "FULL", n_boot=50)
    curve = run_size_curve(train, held, full, n_boot=50)
    partial = [p for p in curve.points if p.scheme == "random" and p.n < len(train)]
    assert partial and all(len(p.draw_lls) > 1 for p in partial)
    for p in partial:
        lo, hi = p.draw_spread
        assert lo <= p.median_draw <= hi
        # the mean sits inside its own draws (float slack: identical draws round in the sum)
        assert lo - 1e-12 <= p.ll <= hi + 1e-12
    for p in curve.points:
        if p.scheme == "recency":
            assert p.median_draw == p.ll and p.draw_spread == (p.ll, p.ll)


def test_recency_note_is_built_from_the_median_not_the_mean() -> None:
    from analysis.poc.e11_action_chain import _recency_note

    curve = SizeCurve(
        [SizePoint(10, "recency", -2.0, -2.1, -1.9, (0.0, -0.1, 0.1)),
         SizePoint(10, "random", -1.8, -1.9, -1.7, (0.0, -0.1, 0.1), [-1.9, -1.75, -1.7])],
        20, None, "r")
    note = _recency_note(curve)
    assert "MEDIAN" in note and "1 of 1 grid points" in note
    assert "+0.2500" in note                       # median -1.75 vs recency -2.00
    assert "+0.1000" in note                       # worst draw -1.90 vs recency -2.00
    assert "still clears" in note


def test_recency_note_says_so_when_the_worst_draw_loses() -> None:
    """The sign glitch this caught in the first run: "beats it by -0.07" is not a sentence."""
    from analysis.poc.e11_action_chain import _recency_note

    curve = SizeCurve(
        [SizePoint(10, "recency", -2.0, -2.1, -1.9, (0.0, -0.1, 0.1)),
         SizePoint(10, "random", -1.9, -2.0, -1.8, (0.0, -0.1, 0.1), [-2.3, -1.8, -1.7])],
        20, None, "r")
    note = _recency_note(curve)
    assert "BEHIND" in note and "0.3000" in note
    assert "still clears" not in note


def test_size_curve_runs_and_the_full_point_ties_itself() -> None:
    train = [two_actor_bout(f"t{i}", year=2000 + i, n=6) for i in range(30)]
    held = [two_actor_bout(f"e{i}", year=2025, n=6) for i in range(5)]
    full = score(train, held, A7, "FULL", n_boot=50)
    curve = run_size_curve(train, held, full, n_boot=50)
    last = [p for p in curve.points if p.n == len(train)]
    assert last and all(p.delta_vs_full == (0.0, 0.0, 0.0) for p in last)
    assert {p.scheme for p in curve.points} == {"recency", "random"}


# ── C6 ──────────────────────────────────────────────────────────────────────────
def test_action_graph_keeps_self_loops_and_is_cross_actor() -> None:
    """Both differ from `transitions/build_graph` on purpose: Lamas' chain is the MATCH's flow
    and publishes guard pass -> guard pass at 0.30, a cell folding would delete."""
    r = row("b", [ev("pass", "Knee Cut Pass", "X"), ev("pass", "Body Lock Pass", "X"),
                  ev("control", "Back Control", "Y")])
    g = action_graph([r], lambda p: p.action)
    assert g.has_edge("GPSA", "GPSA")
    assert g.has_edge("GPSA", "BTKA")           # crosses actors X -> Y


def test_pagerank_ties_break_by_name() -> None:
    """The deterministic tie-break scar: a hash-seeded order reshuffles a published table
    between two identical runs."""
    r = row("b", [ev("takedown", "Trip", "X"), ev("pass", "Knee Cut Pass", "X"),
                  ev("takedown", "Trip", "X"), ev("pass", "Knee Cut Pass", "X")])
    ranked = ranked_pagerank(action_graph([r], lambda p: p.action))
    assert ranked == sorted(ranked, key=lambda kv: (-kv[1], kv[0]))


def test_community_gate_refuses_a_near_complete_graph() -> None:
    """Density above 0.9 makes any partition an arbitrary cut of one blob, whatever Q says."""
    acts = [("takedown", "Trip"), ("pass", "Knee Cut Pass"),
            ("sweep", "Butterfly Sweep"), ("submission", "Armbar")]
    # every ordered pair adjacent at least once -> a complete graph with self-loops
    seq = [ev(t, lab, "X") for a in acts for b in acts for t, lab in (a, b)]
    rep = community_report([row("b", seq)], lambda p: p.action, n_resamples=5)
    assert rep.density > 0.9
    assert rep.interpretable is False


def test_community_report_is_deterministic() -> None:
    rows = [two_actor_bout(f"b{i}", n=6) for i in range(6)]
    a = community_report(rows, lambda p: p.action, n_resamples=10)
    b = community_report(rows, lambda p: p.action, n_resamples=10)
    assert (a.communities, a.mean_jaccard, a.p10_jaccard) == (
        b.communities, b.mean_jaccard, b.p10_jaccard)


def test_matrix_rows_are_normalised_or_empty() -> None:
    rows = [two_actor_bout(f"b{i}", n=6) for i in range(4)]
    for r in _matrix(rows):
        assert sum(r) == pytest.approx(0.0) or sum(r) == pytest.approx(1.0)


# ── report + PDF ────────────────────────────────────────────────────────────────
def test_gate_self_check_speaks_up_when_the_corpus_moves() -> None:
    from analysis.poc.e11_action_chain import E9_GATED_BOUTS

    assert "OK" in verify_gate(GateReport(passed=E9_GATED_BOUTS))
    moved = verify_gate(GateReport(passed=E9_GATED_BOUTS + 37))
    assert "MOVED" in moved and "+37" in moved
    assert "NOT RUN" in verify_gate(GateReport(error="boom"))


def test_verdicts_refuse_to_speak_without_a_run() -> None:
    assert verdicts(Run(GateReport(error="skipped"))) == {
        "all": "UNDECIDED — the corpus pass did not run."}


def test_markdown_carries_the_prereg_verbatim() -> None:
    md = render_markdown(Run(GateReport(error="skipped")), "PREREG-SENTINEL")
    assert "PREREG-SENTINEL" in md
    assert "do not hand-edit" in md


def test_pdf_builder_smoke(tmp_path: Any) -> None:
    """No network, no DB: a PDF must come out even from a run that never touched the corpus."""
    out = write_pdf(Run(GateReport(error="skipped (--skip-corpus)")), [],
                    tmp_path / "e11" / "e11.pdf")
    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")
    assert out.stat().st_size > 800


def test_community_report_dataclass_is_the_shape_the_pdf_reads() -> None:
    rep = CommunityReport(0.5, 0.3, [["A"]], [1.0], [1.0], True)
    assert rep.interpretable and rep.communities == [["A"]]
