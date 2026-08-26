"""The Lamas mapping table and chain rules, pinned before any matrix is read.

The load-bearing tests are the mapping ones. A transition matrix is only as good as the
question "which event is which state", and every rule in `analysis/lamas_chain`'s docstring is
a place where a plausible alternative reading produces a different number -- so each of them
gets a case that fails if the rule quietly changes.

Synthetic throughout: no DB, no corpus file. The labels are real corpus strings (enumerated
read-only, 2026-08-25), the bouts around them are invented.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from analysis.lamas_chain import (
    ANCHOR_CELLS,
    STATE_DEFS,
    STATES,
    anchor,
    chain_factor,
    chain_of,
    lamas_state,
    markov_block,
    pathways_to_sub,
    reward_risk,
    reward_risk_comparison,
    rrb,
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


# ── RRB: two-sided submission absorption ────────────────────────────────────────
def finished(*events: dict[str, Any], winner: str = "X", id_: str = "f") -> dict[str, Any]:
    """A bout the corpus records as WON BY SUBMISSION, with the winner named.

    `winner` is what decides which absorbing state the chain falls into — never the finishing
    event's own `actor_id`, which the corpus files under the loser in seven of the ADCC cycle's
    twenty-four flagged finishes.
    """
    d = two_sided(*events, id_=id_, win_type="SUBMISSION")
    d["winner"] = winner
    return d


def _rrb(*bouts: dict[str, Any], n_boot: int = 0) -> dict[str, Any]:
    return rrb([chain_of(b) for b in bouts], n_boot=n_boot)


def _by(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["state"]: r for r in block["rows"]}


# The deterministic fixture every absorption assertion below is read off: Y clinches, then X
# takes down, passes and finishes. One path, no branching, so every absorption probability is
# exactly 0 or 1 and can be checked by eye rather than by re-running the solver.
def _one_path(winner: str = "X", id_: str = "f") -> dict[str, Any]:
    return finished(ev("control", "Collar Tie", actor="Y"),
                    ev("takedown", "Trip", actor="X"),
                    ev("pass", "Pass", actor="X"),
                    ev("submission", "RNC", successful=True, actor="X"),
                    winner=winner, id_=id_)


def test_absorption_is_hand_computable_on_a_single_deterministic_chain() -> None:
    by = _by(_rrb(_one_path()))
    # CDP is Y's action and X wins: from CDP the finish is the OPPONENT's, with certainty.
    assert (by["CDP"]["p_sub_own"]["p"], by["CDP"]["p_sub_opp"]["p"]) == (0.0, 1.0)
    assert by["CDP"]["balance"] == -1.0 and by["CDP"]["sub_share"] == 0.0
    # TKDA and GPSA are X's, and X finishes.
    for s in ("TKDA", "GPSA"):
        assert (by[s]["p_sub_own"]["p"], by[s]["p_sub_opp"]["p"]) == (1.0, 0.0), s
        assert by[s]["balance"] == 1.0 and by[s]["sub_share"] == 1.0


def test_the_side_of_the_finish_comes_from_the_winner_not_from_the_event_actor() -> None:
    """The same four events, with only `winner` changed, must flip every row.

    This is the rule that matters most in practice: the finishing event is filed under the
    LOSER in a third of the ADCC cycle's flagged finishes, so an implementation that read the
    side off `actor_id` would pass every other test in this file and still be wrong there.
    """
    x_won, y_won = _by(_rrb(_one_path("X"))), _by(_rrb(_one_path("Y")))
    for s in ("CDP", "TKDA", "GPSA"):
        assert x_won[s]["p_sub_own"]["p"] == y_won[s]["p_sub_opp"]["p"], s
        assert x_won[s]["balance"] == -y_won[s]["balance"], s


def test_a_bout_not_won_by_submission_absorbs_into_the_no_sub_end() -> None:
    """The third absorbing state is what makes the rows honest — and it is the DEFAULT."""
    d = two_sided(ev("control", "Collar Tie", actor="Y"), ev("takedown", "Trip", actor="X"),
                  ev("submission", "RNC", successful=True, actor="X"), win_type="DECISION")
    d["winner"] = "X"
    block = _rrb(d)
    assert block["absorption"]["absorbing_bouts"] == 0
    assert block["absorption"]["reason_code"] == "no_absorbing_bouts"
    for r in block["rows"]:
        assert r["p_sub_own"]["p"] is None and r["p_sub_opp"]["p"] is None
        assert r["balance"] is None and r["sub_share"] is None


def test_a_submission_win_with_no_flagged_sub_still_absorbs() -> None:
    """Rule 4 carried one step further: the tap marker is the BOUT. A bout won by submission
    whose chain never reached a flagged `SUB` absorbs at its last step anyway."""
    b = finished(ev("control", "Collar Tie", actor="Y"), ev("takedown", "Trip", actor="X"),
                 winner="X")
    block = _rrb(b)
    assert block["absorption"]["absorbing_bouts"] == 1
    assert block["absorption"]["absorbed_without_flagged_sub"] == 1
    assert _by(block)["CDP"]["p_sub_opp"]["p"] == 1.0


def test_a_submission_win_with_no_recorded_winner_does_not_absorb() -> None:
    """A missing fact is never read as a finish."""
    b = two_sided(ev("control", "Collar Tie", actor="Y"), ev("takedown", "Trip", actor="X"),
                  win_type="SUBMISSION")
    assert _rrb(b)["absorption"]["absorbing_bouts"] == 0


def test_rows_are_a_proper_distribution_over_the_three_absorbing_states() -> None:
    """own + opp + no-sub-end = 1 for every state that appears, on a branching corpus."""
    bs = [_one_path("X", f"w{i}") for i in range(4)]
    bs += [two_sided(ev("control", "Collar Tie", actor="Y"), ev("takedown", "Trip", actor="X"),
                     ev("control", "Back Control", actor="Y"), id_=f"d{i}") for i in range(4)]
    for r in _by(_rrb(*bs)).values():
        if not r["n"]:
            continue
        own, opp = r["p_sub_own"]["p"], r["p_sub_opp"]["p"]
        assert 0.0 <= own + opp <= 1.0 + 1e-9, r
        assert r["balance"] == round(own - opp, 4)
        assert r["sub_share"] == round(own / (own + opp), 4) if own + opp else True


def test_by_next_mover_splits_the_row_on_who_acts_next() -> None:
    """From `CDP`, handing off to the finisher and keeping the exchange must differ.

    Both arms come from one state so the split cannot be an artefact of two populations: in
    `keep` the clincher goes on to finish, in `hand` she is finished by the athlete she hands
    to.
    """
    keep = [finished(ev("guard", "Guard Pull", actor="Y"), ev("control", "Collar Tie", actor="X"),
                     ev("takedown", "Trip", actor="X"),
                     ev("submission", "RNC", successful=True, actor="X"),
                     winner="X", id_=f"k{i}") for i in range(3)]
    hand = [finished(ev("guard", "Guard Pull", actor="Y"), ev("control", "Collar Tie", actor="X"),
                     ev("takedown", "Trip", actor="Y"),
                     ev("submission", "RNC", successful=True, actor="Y"),
                     winner="Y", id_=f"h{i}") for i in range(3)]
    arms = _by(_rrb(*keep, *hand))["CDP"]["by_next_mover"]
    assert arms["own"]["n"] == 3 and arms["opp"]["n"] == 3
    assert arms["own"]["balance"] == 1.0            # kept it, and finished
    assert arms["opp"]["balance"] == -1.0           # handed it over, and was finished


def test_by_next_mover_excludes_the_appearance_that_is_itself_the_last_step() -> None:
    """Both arms condition on HAVING a successor, so a terminal appearance is in neither."""
    row = _by(_rrb(_one_path()))["SUB"]
    assert row["n"] == 1 and row["n_terminal"] == 1
    for arm in row["by_next_mover"].values():
        assert arm["n"] == 0 and arm["balance"] is None
        assert arm["reason_code"] == "no_transitions"


def test_the_absorbing_bout_gate_withholds_every_interval_below_five_finishes() -> None:
    """The gate this layer ADDS. A row can rest on ten bouts of appearances while all of its
    absorbing mass traces to four finishes, and nothing else in the block would say so."""
    bs = [_one_path("X", f"w{i}") for i in range(4)]
    bs += [two_sided(ev("control", "Collar Tie", actor="Y"), ev("takedown", "Trip", actor="X"),
                     ev("pass", "Pass", actor="X"), id_=f"d{i}") for i in range(8)]
    block = _rrb(*bs, n_boot=200)
    assert block["absorption"]["absorbing_bouts"] == 4
    assert block["absorption"]["estimable"] is False
    assert block["absorption"]["reason_code"] == "few_absorbing_bouts"
    cdp = _by(block)["CDP"]
    assert cdp["coverage"]["estimable"] is True and cdp["gated"] is False
    assert cdp["balance"] is not None                      # the point estimate survives
    assert cdp["balance_lo"] is None and cdp["balance_hi"] is None
    assert cdp["p_sub_own"]["lo"] is None and cdp["sub_share"] is not None
    assert cdp["share_lo"] is None
    assert all(a["balance_lo"] is None for a in cdp["by_next_mover"].values())


def test_the_gate_passes_at_five_finishes_and_yields_a_clustered_interval() -> None:
    bs = [_one_path("X", f"w{i}") for i in range(5)]
    bs += [two_sided(ev("control", "Collar Tie", actor="Y"), ev("takedown", "Trip", actor="X"),
                     ev("pass", "Pass", actor="X"), id_=f"d{i}") for i in range(8)]
    block = _rrb(*bs, n_boot=400)
    assert block["absorption"]["estimable"] is True
    cdp = _by(block)["CDP"]
    assert cdp["gated"] is True
    assert cdp["balance_lo"] is not None and cdp["balance_hi"] is not None
    assert cdp["balance_lo"] <= cdp["balance"] <= cdp["balance_hi"]
    assert cdp["p_sub_own"]["grade"] != "none"


def test_rrb_refuses_the_same_bouts_the_reward_risk_gate_does() -> None:
    """More actor-dependent, never less: the side of every transition is that field."""
    single = finished(ev("control", "Collar Tie", actor="X"),
                      ev("submission", "RNC", successful=True, actor="X"))
    block = _rrb(single)
    assert block["absorption"]["usable_bouts"] == 0
    assert block["absorption"]["bouts_refused"] == {"single_actor": 1}
    assert all(r["n"] == 0 for r in block["rows"])


def test_rrb_rows_are_the_twelve_states_in_the_fixed_order() -> None:
    """Fixed order, not ranked — a reader compares a state across five corpora, and a table
    that re-sorts itself per corpus turns that into a search."""
    for block in (_rrb(_one_path()), _rrb()):
        assert [r["state"] for r in block["rows"]] == list(STATES)


def test_rrb_is_deterministic() -> None:
    bs = [_one_path("X", f"w{i}") for i in range(5)]
    bs += [two_sided(ev("control", "Collar Tie", actor="Y"), ev("takedown", "Trip", actor="X"),
                     ev("pass", "Pass", actor="X"), id_=f"d{i}") for i in range(8)]
    a = _rrb(*bs, n_boot=300)
    b = _rrb(*bs, n_boot=300)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_expected_actions_to_absorption_is_published() -> None:
    """The diagnostic that explains why `balance` is flat: how far the chain runs before it
    ends. Without it a reader has no way to tell a mixed-out zero from a measured one."""
    row = _by(_rrb(_one_path()))["CDP"]
    # Actions from this one until the bout ends, INCLUSIVE: CDP, TKDA, GPSA, SUB.
    assert row["expected_actions"] == 4.0


# ── chain factor ────────────────────────────────────────────────────────────────
def _cf(*bouts: dict[str, Any], n_boot: int = 0) -> dict[str, dict[str, Any]]:
    return {r["state"]: r
            for r in chain_factor([chain_of(b) for b in bouts], n_boot=n_boot)["rows"]}


def test_chain_factor_counts_a_run_of_the_same_athlete_at_depth_two() -> None:
    by = _cf(two_sided(ev("control", "Collar Tie", actor="X"),
                       ev("takedown", "Trip", actor="X"),
                       ev("pass", "Pass", actor="X"),
                       ev("control", "Back Control", actor="Y")))
    assert (by["CDP"]["n"], by["CDP"]["k"], by["CDP"]["factor"]["p"]) == (1, 1, 1.0)
    # TKDA's window is X, X, Y — the run breaks on the second step.
    assert (by["TKDA"]["n"], by["TKDA"]["k"]) == (1, 0)


def test_chain_factor_breaks_on_the_first_step_too() -> None:
    by = _cf(two_sided(ev("control", "Collar Tie", actor="X"),
                       ev("takedown", "Trip", actor="Y"),
                       ev("pass", "Pass", actor="Y")))
    assert (by["CDP"]["n"], by["CDP"]["k"]) == (1, 0)


def test_an_appearance_without_two_following_actions_is_out_of_the_denominator() -> None:
    """build_graph's rule at depth two, and the drop is COUNTED rather than implied."""
    by = _cf(two_sided(ev("control", "Collar Tie", actor="X"),
                       ev("takedown", "Trip", actor="X"),
                       ev("pass", "Pass", actor="Y")))
    assert (by["TKDA"]["n"], by["TKDA"]["n_short"]) == (0, 1)
    assert (by["GPSA"]["n"], by["GPSA"]["n_short"]) == (0, 1)
    assert by["CDP"]["n"] == 1


def test_a_window_with_an_unknown_actor_leaves_the_denominator_and_is_counted() -> None:
    """A two-valued statistic has no neutral outcome, so an unknown cannot be parked at 0 the
    way `reward_risk` parks it — scoring it a failure would measure annotation coverage."""
    mid = ev("takedown", "Trip", actor="X")
    mid["actor_id"] = None
    by = _cf(two_sided(ev("control", "Collar Tie", actor="X"), mid,
                       ev("pass", "Pass", actor="X"),
                       ev("control", "Back Control", actor="Y")))
    assert (by["CDP"]["n"], by["CDP"]["n_unknown_actor"]) == (0, 1)


def test_chain_factor_gate_withholds_both_intervals_together() -> None:
    by = _cf(two_sided(ev("control", "Collar Tie", actor="X"),
                       ev("takedown", "Trip", actor="X"),
                       ev("pass", "Pass", actor="X"),
                       ev("control", "Back Control", actor="Y")), n_boot=200)
    r = by["CDP"]
    assert r["gated"] is False
    assert r["factor"]["lo"] is None and r["factor_lo"] is None and r["factor_hi"] is None


def test_chain_factor_gate_passes_over_five_bouts_and_publishes_both() -> None:
    bs = [two_sided(ev("control", "Collar Tie", actor="X"), ev("takedown", "Trip", actor="X"),
                    ev("pass", "Pass", actor="X"), ev("control", "Back Control", actor="Y"),
                    id_=f"b{i}") for i in range(6)]
    by = _cf(*bs, n_boot=400)
    r = by["CDP"]
    assert (r["n"], r["k"], r["bouts"]) == (6, 6, 6) and r["gated"] is True
    assert r["factor"]["lo"] is not None
    assert r["factor_lo"] is not None and r["factor_hi"] is not None


def test_chain_factor_refuses_the_actor_gated_bouts() -> None:
    single = two_sided(ev("control", "Collar Tie", actor="X"),
                       ev("takedown", "Trip", actor="X"), ev("pass", "Pass", actor="X"))
    by = _cf(single)
    assert all(r["n"] == 0 for r in by.values())


def test_chain_factor_rows_are_the_twelve_states_in_the_fixed_order() -> None:
    assert list(_cf(two_sided(ev("control", "Collar Tie", actor="X"),
                              ev("takedown", "Trip", actor="Y")))) == list(STATES)


def test_chain_factor_is_deterministic() -> None:
    bs = [two_sided(ev("control", "Collar Tie", actor="X"), ev("takedown", "Trip", actor="X"),
                    ev("pass", "Pass", actor="X"), ev("control", "Back Control", actor="Y"),
                    id_=f"b{i}") for i in range(6)]
    chains = [chain_of(b) for b in bs]
    assert json.dumps(chain_factor(chains, n_boot=300), sort_keys=True) == \
        json.dumps(chain_factor(chains, n_boot=300), sort_keys=True)


# ── both blocks inside the division block ───────────────────────────────────────
def test_markov_block_carries_both_new_blocks_in_the_shape_the_page_reads() -> None:
    b = markov_block([_one_path("X", f"w{i}") for i in range(2)])
    for key in ("rrb", "chain_factor"):
        assert len(b[key]["rows"]) == len(STATES), key
        assert b[key]["caveats"], key
    assert b["rrb"]["method"] and b["chain_factor"]["definition"]
    assert b["rrb"]["absorption"]["absorbing_states"] == ["SUB_OWN", "SUB_OPP", "NO_SUB_END"]


def test_n_boot_zero_skips_every_bootstrap_and_still_ships_the_full_shape() -> None:
    """`markov_layer` rebuilds this block once per cut only to COUNT refused rows."""
    bs = [_one_path("X", f"w{i}") for i in range(6)]
    b = markov_block(bs, n_boot=0)
    assert len(b["rrb"]["rows"]) == len(STATES)
    assert b["rrb"]["absorption"]["boot"]["n"] == 0
    assert all(r["balance_lo"] is None for r in b["rrb"]["rows"])
    assert all(r["factor_lo"] is None for r in b["chain_factor"]["rows"])
    assert all(r["score_lo"] is None for r in b["reward_risk"]["rows"])


def test_an_empty_division_still_produces_twelve_rows_in_both_blocks() -> None:
    b = markov_block([])
    assert [r["state"] for r in b["rrb"]["rows"]] == list(STATES)
    assert [r["state"] for r in b["chain_factor"]["rows"]] == list(STATES)
    assert b["rrb"]["absorption"]["reason_code"] == "no_absorbing_bouts"
