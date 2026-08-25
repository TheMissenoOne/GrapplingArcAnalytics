"""The Lamas mapping table and chain rules, pinned before any matrix is read.

The load-bearing tests are the mapping ones. A transition matrix is only as good as the
question "which event is which state", and every rule in `analysis/lamas_chain`'s docstring is
a place where a plausible alternative reading produces a different number -- so each of them
gets a case that fails if the rule quietly changes.

Synthetic throughout: no DB, no corpus file. The labels are real corpus strings (enumerated
read-only, 2026-08-25), the bouts around them are invented.
"""
from __future__ import annotations

from typing import Any

import pytest

from analysis.lamas_chain import (
    ANCHOR_CELLS,
    STATE_DEFS,
    STATES,
    anchor,
    chain_of,
    lamas_state,
    markov_block,
    pathways_to_sub,
    reward_risk,
    reward_risk_comparison,
    transitions,
)


def ev(type_: str, label: str, successful: bool | None = None,
       actor: str = "a") -> dict[str, Any]:
    e: dict[str, Any] = {"type": type_, "label": label, "actor_id": actor}
    if successful is not None:
        e["successful"] = successful
    return e


def bout(*events: dict[str, Any], win_type: str = "DECISION", id_: str = "b",
         sides: tuple[str, str] | None = None) -> dict[str, Any]:
    """`sides` supplies `a_id`/`b_id`. Omitted by default: only the reward-risk layer reads
    them, and a bout with no recorded sides is refused there by construction."""
    d: dict[str, Any] = {"id": id_, "win_type": win_type, "seq": list(events)}
    if sides:
        d["a_id"], d["b_id"] = sides
    return d


def two_sided(*events: dict[str, Any], id_: str = "b",
              win_type: str = "DECISION") -> dict[str, Any]:
    return bout(*events, id_=id_, win_type=win_type, sides=("X", "Y"))


# ── rule 1: type first, then label ──────────────────────────────────────────────
@pytest.mark.parametrize(("type_", "label", "expected"), [
    # the four type-decided families, attempt side
    ("takedown", "Single Leg Takedown", "TKDA"),
    ("sweep", "Sweep", "SWPA"),
    ("pass", "Knee Cut Pass", "GPSA"),
    ("submission", "Heel Hook", "SUBA"),
    # label-decided states, on the three types the type vocabulary has no word for
    ("guard", "Guard Pull", "PGD"),
    ("guard", "Pull Guard / Inversion", "PGD"),
    ("guard", "Double Guard Pull", "PGD"),
    ("transition", "Guard Pull", "PGD"),
    ("transition", "Pull Guard / Sit Guard", "PGD"),
    ("guard", "Pull Half Guard", "PGD"),
    ("control", "Back Control", "BTKA"),
    ("control", "Standing Back Control", "BTKA"),
    ("transition", "Back Take", "BTKA"),
    ("transition", "Hooks In", "BTKA"),
    ("control", "Body Triangle", "BTKA"),
    ("control", "Rear Body Lock", "BTKA"),
    ("control", "Arm Drag to Back Take", "BTKA"),   # back-take beats the clinch token
    ("control", "Collar Tie", "CDP"),
    ("control", "Body Lock", "CDP"),
    ("control", "Front Headlock", "CDP"),
    ("control", "Double Underhooks", "CDP"),
    ("control", "Two-on-One Wrist Control", "CDP"),
    ("control", "Russian Tie", "CDP"),
    ("transition", "Duck Under", "CDP"),
    ("transition", "Snapdown", "CDP"),
    ("transition", "Clinch", "CDP"),
])
def test_mapping_table(type_: str, label: str, expected: str) -> None:
    assert lamas_state(ev(type_, label)) == expected


@pytest.mark.parametrize(("type_", "label"), [
    # escapes: never a Lamas action, and `escape/Back Escape` is the false positive the type
    # gate exists to stop -- it contains the back-take vocabulary.
    ("escape", "Back Escape"),
    ("escape", "Escape to Standing"),
    ("escape", "Turtle Position"),
    # dwell states and guard postures
    ("guard", "Closed Guard"),
    ("guard", "Half Guard"),
    ("guard", "50/50 Guard"),
    ("control", "Mount"),
    ("control", "Side Control"),
    ("control", "Turtle Position"),
    ("control", "Escape to Turtle"),
    # deliberate non-members, documented as decisions rather than oversights
    ("control", "Crucifix"),
    ("control", "Mounted Crucifix"),
    ("control", "Kimura Grip"),
    # the two measured token collisions, overridden by name
    ("control", "Top Control (Body Lock)"),
    ("control", "Body Triangle (Bottom)"),      # the person UNDER it, not the back-taker
    # transitions that are not back-takes
    ("transition", "Berimbolo"),
    ("transition", "Inversion"),
    # an unknown type carries no action at all
    ("weird", "Back Control"),
    ("control", ""),
])
def test_unmapped_events_are_passed_over(type_: str, label: str) -> None:
    assert lamas_state(ev(type_, label)) is None


def test_type_beats_label_on_the_measured_collisions() -> None:
    """A snapdown typed `takedown` is a TKDA, not a CDP; a back-take typed `sweep` is a SWP.

    Rule 1, and the only place the mapping deliberately contradicts the label's own semantics.
    """
    assert lamas_state(ev("takedown", "Snapdown")) == "TKDA"
    assert lamas_state(ev("takedown", "Snap Down")) == "TKDA"
    assert lamas_state(ev("takedown", "Arm Drag")) == "TKDA"
    assert lamas_state(ev("sweep", "Sweep / Back Take", True)) == "SWP"
    assert lamas_state(ev("takedown", "Takedown to Back Take", True)) == "TKD"
    assert lamas_state(ev("pass", "Body Lock Pass")) == "GPSA"


# ── rule 3: attempt vs success ──────────────────────────────────────────────────
@pytest.mark.parametrize(("type_", "label", "attempt", "success"), [
    ("takedown", "Double Leg Takedown", "TKDA", "TKD"),
    ("sweep", "Hip Bump Sweep", "SWPA", "SWP"),
    ("pass", "Smash Pass", "GPSA", "GPS"),
    ("submission", "Triangle Choke", "SUBA", "SUB"),
    ("control", "Back Control", "BTKA", "BTK"),
])
def test_absent_and_false_both_read_as_attempt(type_: str, label: str,
                                               attempt: str, success: str) -> None:
    assert lamas_state(ev(type_, label)) == attempt            # absent
    assert lamas_state(ev(type_, label, False)) == attempt     # explicit false
    assert lamas_state(ev(type_, label, True)) == success


def test_pull_guard_and_clinch_have_no_success_form() -> None:
    """PGD and CDP are single states in the paper's space -- a `successful` flag on one of them
    must not conjure a thirteenth code."""
    assert lamas_state(ev("guard", "Guard Pull", True)) == "PGD"
    assert lamas_state(ev("control", "Collar Tie", True)) == "CDP"


# ── rule 2: the chain links only the survivors ──────────────────────────────────
def test_chain_skips_unmapped_events_and_keeps_order() -> None:
    ch = chain_of(bout(
        ev("control", "Collar Tie"),
        ev("guard", "Closed Guard"),          # skipped
        ev("takedown", "Uchi Mata", True),
        ev("control", "Mount"),               # skipped
        ev("control", "Back Control"),
    ))
    assert [s.state for s in ch.steps] == ["CDP", "TKD", "BTKA"]
    assert (ch.mapped, ch.skipped) == (3, 2)
    assert ch.skipped_labels["guard/Closed Guard"] == 1


def test_self_loops_survive() -> None:
    """`network_from_sequences` drops A -> A and `normalize_chain` folds repeats; both would
    delete the exact cell Lamas publishes (guard pass -> guard pass)."""
    ch = chain_of(bout(ev("pass", "Guard Pass"), ev("pass", "Guard Pass")))
    assert [t.src for t in transitions([ch])] == ["GPSA"]
    assert [t.dst for t in transitions([ch])] == ["GPSA"]


# ── rule 4: SUB is absorbing, by the bout's result ──────────────────────────────
def test_sub_absorbs_when_the_bout_ended_by_submission() -> None:
    ch = chain_of(bout(
        ev("control", "Back Control"),
        ev("submission", "Rear Naked Choke", True),
        ev("submission", "Tap", True),          # the same finish, logged twice
        ev("pass", "Pass"),
        win_type="SUBMISSION",
    ))
    assert [s.state for s in ch.steps] == ["BTKA", "SUB"]
    assert ch.truncated and ch.after_finish == 2
    assert not [t for t in transitions([ch]) if t.src == "SUB"]


def test_a_locked_submission_in_a_decision_bout_does_not_end_the_chain() -> None:
    """Measured on the real corpus: a bout Amy Campo LOST on decision carries
    `Knee Bar successful=true` and runs for seventeen more events. Truncating on the flag
    would have cut that bout at event 0."""
    ch = chain_of(bout(
        ev("submission", "Knee Bar", True),
        ev("sweep", "Sweep", True),
        ev("submission", "Guillotine Choke", False),
        win_type="DECISION",
    ))
    assert [s.state for s in ch.steps] == ["SUB", "SWP", "SUBA"]
    assert not ch.truncated and ch.after_finish == 0
    assert [t.src for t in transitions([ch])] == ["SUB", "SWP"]


def test_absorption_needs_a_submission_to_absorb() -> None:
    ch = chain_of(bout(ev("takedown", "Trip", True), win_type="SUBMISSION"))
    assert not ch.truncated and ch.after_finish == 0


# ── the matrix ──────────────────────────────────────────────────────────────────
def _block() -> dict[str, Any]:
    return markov_block([
        bout(ev("control", "Collar Tie"), ev("takedown", "Single Leg Takedown", True),
             ev("pass", "Knee Cut Pass"), ev("pass", "Knee Cut Pass", True),
             ev("control", "Back Control"), ev("submission", "Rear Naked Choke", True),
             win_type="SUBMISSION", id_="b1"),
        bout(ev("guard", "Guard Pull", actor="b"), ev("sweep", "Sweep", True, actor="b"),
             ev("pass", "Smash Pass", actor="a"), ev("control", "Back Control", actor="a"),
             ev("submission", "Armbar", actor="a"),
             win_type="DECISION", id_="b2"),
    ])


def test_matrix_is_square_on_the_fixed_state_order() -> None:
    b = _block()
    assert [s["code"] for s in b["states"]] == list(STATES)
    assert [s["definition"] for s in b["states"]] == [STATE_DEFS[s] for s in STATES]
    for key in ("counts", "probs", "ci"):
        assert len(b[key]) == len(STATES)
        assert all(len(row) == len(STATES) for row in b[key])
    assert [r["state"] for r in b["rows"]] == list(STATES)
    assert [o["state"] for o in b["occupancy"]] == list(STATES)


def test_rows_are_normalised_or_empty() -> None:
    b = _block()
    for src, row, counts in zip(STATES, b["probs"], b["counts"]):
        total = sum(counts)
        if not total:
            assert all(p is None for p in row), src
            continue
        assert abs(sum(p for p in row if p is not None) - 1.0) < 1e-9, src
        assert all(abs(p - c / total) < 1e-4 for p, c in zip(row, counts)), src


def test_counts_account_for_every_transition() -> None:
    b = _block()
    assert sum(sum(r) for r in b["counts"]) == b["n_transitions"]
    assert sum(o["k"] for o in b["occupancy"]) == b["n_events_mapped"]


def test_output_is_deterministic() -> None:
    import json
    assert json.dumps(_block(), sort_keys=True) == json.dumps(_block(), sort_keys=True)


def test_cross_actor_chain_links_across_the_two_athletes() -> None:
    """The second bout hands the action from `b` to `a` mid-chain. The cross-actor matrix must
    hold that transition; the within-actor flag must refuse it."""
    b = _block()
    trans = [t for t in transitions([chain_of(x) for x in [
        bout(ev("sweep", "Sweep", True, actor="b"), ev("pass", "Smash Pass", actor="a"),
             id_="x")]])]
    assert [(t.src, t.dst, t.same_actor) for t in trans] == [("SWP", "GPSA", False)]
    assert b["within_actor_transitions"] < b["n_transitions"]
    assert b["chain"] == "cross_actor"


# ── pathways and anchor ─────────────────────────────────────────────────────────
def test_pathways_are_three_long_end_in_a_submission_and_rank_by_count() -> None:
    chains = [chain_of(bout(
        ev("control", "Back Control"), ev("submission", "Armbar"),
        ev("submission", "Armbar"), id_=f"b{i}")) for i in range(3)]
    chains.append(chain_of(bout(
        ev("takedown", "Trip"), ev("control", "Back Control"),
        ev("submission", "Armbar"), id_="c")))
    probs = markov_block([])["probs"]           # empty matrix -> p_chain is None, not a crash
    out = pathways_to_sub(chains, probs)
    assert [p["label"] for p in out] == ["BTKA → SUBA → SUBA", "TKDA → BTKA → SUBA"]
    assert [p["k"] for p in out] == [3, 1]
    assert all(len(p["path"]) == 3 and p["path"][-1] in ("SUB", "SUBA") for p in out)
    assert out[0]["p_chain"] is None
    assert out[0]["bouts"] == 3 and out[0]["bout_rate"]["n"] == 4


def test_anchor_reports_both_conventions_against_the_published_values() -> None:
    rows = anchor(transitions([chain_of(bout(
        ev("control", "Back Control"), ev("submission", "Armbar"),
        ev("pass", "Pass"), ev("pass", "Pass"), id_="b1"))]))
    assert [r["name"] for r in rows] == [c[0] for c in ANCHOR_CELLS]
    assert [r["lamas"] for r in rows] == [c[3] for c in ANCHOR_CELLS]
    back = rows[0]
    assert back["src_states"] == ["BTKA", "BTK"] and back["dst_states"] == ["SUBA", "SUB"]
    assert back["cross"]["k"] == 1 and back["cross"]["n"] == 1
    assert back["within"]["k"] == 1                       # one actor throughout
    gp = rows[2]
    assert gp["cross"]["k"] == 1 and gp["cross"]["n"] == 1  # the self-transition survives
    # The paper's own guard-pass cell IS a re-entry, so the diagnostic arm has to refuse it
    # rather than report a zero it manufactured.
    assert gp["no_reentry"] == {"available": False,
                                "reason_code": "published_cell_is_the_reentry"}


def test_no_reentry_arm_drops_same_family_re_entries_from_the_denominator() -> None:
    """Three back-control events then a submission: the cross arm sees one hit in three
    transitions out of a back-control, the diagnostic arm sees one in one."""
    rows = anchor(transitions([chain_of(bout(
        ev("control", "Back Control"), ev("control", "Back Control"),
        ev("control", "Back Control"), ev("submission", "Rear Naked Choke"), id_="b"))]))
    back = rows[0]
    assert (back["cross"]["k"], back["cross"]["n"]) == (1, 3)
    assert (back["no_reentry"]["k"], back["no_reentry"]["n"]) == (1, 1)


def test_empty_division_produces_a_full_shaped_block() -> None:
    b = markov_block([])
    assert b["n_bouts"] == 0 and b["n_transitions"] == 0
    assert len(b["counts"]) == len(STATES)
    assert b["pathways_to_sub"] == []
    assert all(r["cross"]["n"] == 0 and not r["cross"]["agrees"] for r in b["anchor"])
    assert b["caveats"] and b["source"].startswith("Lamas")


# ── reward-risk ─────────────────────────────────────────────────────────────────
def _rows(*bouts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rr = reward_risk([chain_of(b) for b in bouts], n_boot=200)
    return {r["state"]: r for r in rr["rows"]}


def test_reward_and_risk_are_hand_computable() -> None:
    """X, X, Y, Y — CDP hands off to X (reward), TKDA to Y (risk), GPSA to Y (reward)."""
    by = _rows(two_sided(
        ev("control", "Collar Tie", actor="X"),      # CDP  → X acts next  → reward
        ev("takedown", "Trip", actor="X"),           # TKDA → Y acts next  → risk
        ev("pass", "Pass", actor="Y"),               # GPSA → Y acts next  → reward
        ev("submission", "Armbar", actor="Y"),       # SUBA → no successor → out of denom
    ))
    assert (by["CDP"]["n"], by["CDP"]["reward"]["k"], by["CDP"]["risk"]["k"]) == (1, 1, 0)
    assert by["CDP"]["score"] == 1.0
    assert (by["TKDA"]["n"], by["TKDA"]["reward"]["k"], by["TKDA"]["risk"]["k"]) == (1, 0, 1)
    assert by["TKDA"]["score"] == -1.0
    assert by["GPSA"]["score"] == 1.0


def test_terminal_appearance_is_out_of_the_denominator() -> None:
    """build_graph's rule, verbatim: a state that simply ends the sequence is not scored."""
    by = _rows(two_sided(ev("control", "Collar Tie", actor="X"),
                         ev("submission", "Armbar", actor="Y")))
    assert by["SUBA"]["n"] == 0 and by["SUBA"]["score"] is None
    assert by["CDP"]["n"] == 1


def test_score_is_reward_minus_risk_over_one_denominator() -> None:
    """Three appearances of TKDA: two hand off to the same athlete, one to the opponent."""
    by = _rows(two_sided(
        ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="X"),
        ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="X"),
        ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="Y"),
    ))
    r = by["TKDA"]
    assert (r["n"], r["reward"]["k"], r["risk"]["k"]) == (3, 2, 1)
    assert r["score"] == round((2 - 1) / 3, 4)
    assert abs(r["reward"]["p"] - 2 / 3) < 1e-9


def test_unknown_actor_is_neutral_and_stays_in_the_denominator() -> None:
    """build_graph leaves unknown attribution 'neutral, never charged' — so a state whose
    successor has no actor is diluted toward 0 rather than scored either way."""
    a = ev("takedown", "Trip", actor="X")
    b = ev("pass", "Pass", actor="X")
    b["actor_id"] = None
    # TKDA appears twice; only the FIRST has a successor, so the denominator is 1.
    by = _rows(two_sided(a, b, ev("takedown", "Trip", actor="Y")))
    r = by["TKDA"]
    assert r["n"] == 1 and r["reward"]["k"] == 0 and r["risk"]["k"] == 0
    assert r["neutral"] == 1 and r["score"] == 0.0
    assert by["GPSA"]["neutral"] == 1 and by["GPSA"]["score"] == 0.0


def test_single_actor_bout_is_refused_not_scored() -> None:
    """The whole point of the gate: a bout filed under one name scores reward 1.00 for every
    state in it, and `bout_flags` alone would let a SHORT one through (its one-sided rule
    needs six events)."""
    rr = reward_risk([chain_of(two_sided(
        ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="X")))], n_boot=200)
    assert rr["bouts_used"] == 0
    assert rr["bouts_refused"] == {"single_actor": 1}
    assert all(r["n"] == 0 for r in rr["rows"])


def test_one_sided_long_bout_is_refused_by_the_corpus_own_verdict() -> None:
    rr = reward_risk([chain_of(two_sided(
        *[ev("takedown", "Trip", actor="X") for _ in range(8)]))], n_boot=200)
    assert rr["bouts_refused"] == {"one_sided": 1}


def test_bout_without_recorded_sides_is_refused() -> None:
    rr = reward_risk([chain_of(bout(ev("takedown", "Trip", actor="X"),
                                    ev("pass", "Pass", actor="Y")))], n_boot=200)
    assert rr["bouts_used"] == 0 and rr["bouts_refused"] == {"no_sides_recorded": 1}


def test_the_matrix_does_not_consult_the_actor_gate() -> None:
    """Cross-actor by construction: a bout refused by reward-risk still feeds the matrix."""
    b = markov_block([two_sided(ev("takedown", "Trip", actor="X"),
                                ev("pass", "Pass", actor="X"))])
    assert b["n_transitions"] == 1
    assert b["reward_risk"]["bouts_used"] == 0


def test_gate_and_intervals_follow_the_matrix_convention() -> None:
    """Below the bout-cluster gate: counts survive, EVERY interval is withheld."""
    by = _rows(two_sided(ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="Y")))
    r = by["TKDA"]
    assert r["n"] == 1 and r["gated"] is False
    assert r["reward"]["lo"] is None and r["risk"]["lo"] is None
    assert r["score_lo"] is None and r["score_hi"] is None
    assert r["score"] == -1.0                       # the count-derived score survives


def test_gate_passes_and_yields_a_clustered_interval() -> None:
    """Six bouts, so the cluster gate clears and the composite earns a bootstrap interval."""
    bs = [two_sided(ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="Y"),
                    ev("control", "Back Control", actor="X"), id_=f"b{i}") for i in range(6)]
    r = _rows(*bs)["TKDA"]
    assert r["gated"] is True and r["bouts"] == 6
    assert r["reward"]["lo"] is not None
    assert r["score_lo"] is not None and r["score_lo"] <= r["score"] <= r["score_hi"]


def test_rows_are_ranked_estimable_first_then_by_score_deterministically() -> None:
    # Both athletes must appear or the whole bout is refused, so the fixture is three long.
    bs = [two_sided(ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="X"),
                    ev("control", "Back Control", actor="Y"), id_=f"g{i}") for i in range(6)]
    bs.append(two_sided(ev("control", "Collar Tie", actor="X"),
                        ev("sweep", "Sweep", actor="Y"), id_="thin"))
    rr = reward_risk([chain_of(b) for b in bs], n_boot=200)
    gated = [r["state"] for r in rr["rows"] if r["gated"]]
    assert gated and rr["rows"][0]["gated"] is True
    # CDP scores -1.0 off ONE bout; ranking it above a gated row would be exactly the
    # confident-looking noise the gate exists to keep off the top of the table.
    assert rr["rows"].index(next(r for r in rr["rows"] if r["state"] == "CDP")) > 0
    flags = [r["gated"] for r in rr["rows"]]
    assert flags == sorted(flags, reverse=True)
    assert [r["state"] for r in rr["rows"]] == [
        r["state"] for r in reward_risk([chain_of(b) for b in bs], n_boot=200)["rows"]]


def test_every_state_appears_exactly_once_in_the_rows() -> None:
    rr = reward_risk([chain_of(two_sided(ev("takedown", "Trip", actor="X"),
                                         ev("pass", "Pass", actor="Y")))], n_boot=200)
    assert sorted(r["state"] for r in rr["rows"]) == sorted(STATES)


def test_comparison_is_in_state_order_and_flags_both_estimable() -> None:
    six = [two_sided(ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="Y"),
                     id_=f"a{i}") for i in range(6)]
    one = [two_sided(ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="X"),
                     ev("control", "Back Control", actor="Y"), id_="z")]
    a = reward_risk([chain_of(b) for b in six], n_boot=200)["rows"]
    b = reward_risk([chain_of(x) for x in one], n_boot=200)["rows"]
    cmp_ = reward_risk_comparison(a, b)
    assert [c["state"] for c in cmp_] == list(STATES)      # fixed order, never ranked
    tk = next(c for c in cmp_ if c["state"] == "TKDA")
    assert tk["d65"] == -1.0 and tk["d65p"] == 1.0 and tk["delta"] == -2.0
    assert tk["both_estimable"] is False and "contrast" not in tk


def test_comparison_carries_a_contrast_only_when_both_sides_clear_the_gate() -> None:
    # Both fixtures name both athletes (or the actor gate refuses them outright); they differ
    # only in WHO acts after the takedown attempt, which is exactly what reward-risk measures.
    def mk(tag: str, after_tkd: str, last: str) -> list[dict[str, Any]]:
        return [two_sided(ev("takedown", "Trip", actor="X"),
                          ev("pass", "Pass", actor=after_tkd),
                          ev("control", "Back Control", actor=last),
                          id_=f"{tag}{i}") for i in range(6)]

    a = reward_risk([chain_of(b) for b in mk("a", "X", "Y")], n_boot=200)["rows"]
    b = reward_risk([chain_of(x) for x in mk("b", "Y", "X")], n_boot=200)["rows"]
    tk = next(c for c in reward_risk_comparison(a, b) if c["state"] == "TKDA")
    assert tk["both_estimable"] is True
    assert tk["d65"] == 1.0 and tk["d65p"] == -1.0 and tk["delta"] == 2.0
    # reward 6/6 against 0/6 — the one contrast in this file that should NOT cover zero
    assert tk["contrast"]["diff_lo"] > 0


def test_comparison_skips_a_state_missing_from_one_side() -> None:
    a = reward_risk([chain_of(two_sided(ev("takedown", "Trip", actor="X"),
                                        ev("pass", "Pass", actor="Y")))], n_boot=200)["rows"]
    assert reward_risk_comparison(a, []) == []
