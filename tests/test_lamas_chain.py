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
    transitions,
)


def ev(type_: str, label: str, successful: bool | None = None,
       actor: str = "a") -> dict[str, Any]:
    e: dict[str, Any] = {"type": type_, "label": label, "actor_id": actor}
    if successful is not None:
        e["successful"] = successful
    return e


def bout(*events: dict[str, Any], win_type: str = "DECISION",
         id_: str = "b") -> dict[str, Any]:
    return {"id": id_, "win_type": win_type, "seq": list(events)}


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
