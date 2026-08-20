"""The attribution model: what a role IS, and when the corpus is not allowed to claim one.

Every case here is a string that exists in prod, not an invented one. The point of the module
under test is that a wrong role inverts the reading of a bout -- "back control" read from the
losing corner says the opposite of what happened -- so these tests are the contract, not
coverage decoration.
"""
from __future__ import annotations

import pytest

from analysis import attribution as at
from analysis.names import _normalize_name


# ── the table is reachable at all ───────────────────────────────────────────────
def test_every_table_key_is_a_normalized_label() -> None:
    """A key that is not `_normalize_name`'s own output can never be looked up.

    This is not hypothetical: the first version of the table spelled "K-Guard" as ``k guard``
    while the normalizer produces ``kguard``, and every hyphenated position in the corpus --
    K-Guard, X-Guard, Z-Guard, North-South, Two-on-One -- silently fell through to the type
    default instead of its curated row.
    """
    keys = [*at._GUARD_BOTTOM, *at._GUARD_NEUTRAL, *at._CONTROL_TOP, *at._CONTROL_BACK,
            *at._CONTROL_GRIP, *(lbl for _, lbl in at._LABEL)]
    assert [k for k in keys if _normalize_name(k) != k] == []


# ── positional states keep their topology ───────────────────────────────────────
@pytest.mark.parametrize("label", ["Half Guard", "Closed Guard", "Deep Half Guard",
                                   "Butterfly Guard", "K-Guard", "X-Guard", "Z-Guard"])
def test_guard_puts_the_actor_on_the_bottom(label: str) -> None:
    a = at.classify("guard", label)
    assert (a.category, a.actor_role, a.target_role) == (at.STATE, at.BOTTOM, at.TOP)
    assert a.status == "resolved"


def test_half_guard_distinguishes_top_from_bottom() -> None:
    """The same node, read from the two corners, must not read the same."""
    a = at.classify("guard", "Half Guard")
    assert a.actor_role == at.BOTTOM and a.target_role == at.TOP
    top = at.classify("control", "Top Half Guard")
    assert top.actor_role == at.TOP and top.target_role == at.BOTTOM


def test_back_control_is_never_inverted() -> None:
    a = at.classify("control", "Back Control")
    assert (a.actor_role, a.target_role) == (at.CONTROLLING, at.CONTROLLED)
    assert a.relation == at.HOLDS


def test_mount_is_never_inverted() -> None:
    a = at.classify("control", "Mount")
    assert (a.actor_role, a.target_role) == (at.TOP, at.BOTTOM)


@pytest.mark.parametrize("label", ["50/50 Guard", "Leg Entanglement", "Single Leg X",
                                   "Shin to Shin Guard", "Double Guard Pull"])
def test_symmetric_positions_refuse_a_topology(label: str) -> None:
    """Neither fighter is on top in 50/50. `neutral` is the answer, not a missing one."""
    a = at.classify("guard", label)
    assert a.actor_role == at.NEUTRAL and a.target_role == at.NEUTRAL
    assert a.status == "symmetric"


def test_pass_places_the_actor_on_top() -> None:
    a = at.classify("pass", "Knee Cut Pass")
    assert a.category == at.ACTION and a.actor_role == at.TOP and a.target_role == at.BOTTOM


# ── the exceptions, which are the whole reason for a table ──────────────────────
def test_turtle_filed_as_control_is_not_read_as_dominance() -> None:
    """14 events file Turtle Position under `control`. The type default would call the actor
    the controller; she is the one turtled, which is the opposite reading."""
    a = at.classify("control", "Turtle Position")
    assert a.actor_role == at.BOTTOM
    assert a.status == "unknown"           # nothing is claimed about the opponent's side


def test_escape_to_turtle_filed_as_control_is_still_an_escape() -> None:
    a = at.classify("control", "Escape to Turtle")
    assert a.category == at.ACTION and a.relation == at.ESCAPES


def test_takedown_defense_is_a_defence_not_an_attack() -> None:
    a = at.classify("takedown", "Takedown Defense")
    assert a.relation == at.DEFENDS
    assert a.actor_role == at.DEFENDER and a.target_role == at.EXECUTOR


def test_generic_finish_labels_belong_to_the_finisher() -> None:
    """Measured against `matches.winner_id`: Tap 55/64 and Finish 95/112 belong to the winner,
    against a 62.5% baseline. The convention holds; the label just carries no technique."""
    for lbl in ("Tap", "Finish", "Submission"):
        a = at.classify("submission", lbl)
        assert a.relation == at.EXECUTES and a.status == "resolved"


def test_submit_refuses_rather_than_inverting_on_six_events() -> None:
    a = at.classify("submission", "Submit")
    assert a.status == "unknown"


def test_match_bookkeeping_rows_are_not_grappling_events() -> None:
    assert at.classify("match", "Match").status == "unknown"


def test_an_unseen_label_falls_back_to_its_type_and_says_so() -> None:
    a = at.classify("guard", "Some Guard Nobody Has Logged Yet")
    assert a.actor_role == at.BOTTOM
    assert a.source == "type_default"


def test_an_unknown_type_resolves_to_nothing() -> None:
    a = at.classify("strike", "Jab")
    assert a.status == "unknown"


# ── bout-level: when the corpus cannot carry a role at all ──────────────────────
def _ev(ts: int, ty: str, label: str, actor: str) -> dict[str, object]:
    return {"ts": ts, "type": ty, "label": label, "actor_id": actor}


def test_a_one_sided_bout_cannot_carry_a_role() -> None:
    """The measured defect. 307 of 700 prod bouts file every event under one athlete; this is
    the shape of the Emily Leva x Mary Butterfield row, where one fighter is given Half Guard,
    Smash Pass, Back Control and Scarf Hold inside a minute."""
    seq = [_ev(10 + 10 * i, "control", "Back Control", "A") for i in range(7)]
    f = at.bout_flags(seq, "A", "B")
    assert f["one_sided"] and not f["role_reliable"] and not f["perspective_reliable"]
    assert f["reason_code"] == "one_sided"
    assert f["unused_side"] == "B"


def test_a_short_one_sided_bout_makes_no_claim() -> None:
    """A twenty-second armbar genuinely produces two events from one corner. Below the
    threshold the flag refuses to fire rather than guessing."""
    seq = [_ev(10, "takedown", "Double Leg Takedown", "A"),
           _ev(30, "submission", "Armbar", "A")]
    assert at.bout_flags(seq, "A", "B")["one_sided"] is False


def test_a_bout_that_contradicts_itself_loses_its_roles() -> None:
    """Nobody is mounted and in half guard at the same time. At least one row is mis-filed and
    there is no way to tell which, so neither is used."""
    seq = [_ev(100, "guard", "Half Guard", "A"), _ev(110, "control", "Mount", "A"),
           _ev(200, "submission", "Armbar", "B")]
    f = at.bout_flags(seq, "A", "B")
    assert f["contradiction_count"] == 1
    assert not f["role_reliable"]
    # Perspective survives: the actor field may still name the right fighter on most rows.
    assert f["perspective_reliable"]
    assert f["reason_code"] == "self_contradictory"


def test_control_and_topology_are_separate_axes() -> None:
    """Back control taken from underneath is `controlling` AND `bottom` at once. Pooling the
    two axes into one sign made `Half Guard` beside `Back Control` look like a defect and
    gated 53% of the roster's events over a distinction the model had invented; separated, the
    same corpus loses 9.2%."""
    seq = [_ev(100, "guard", "Half Guard", "A"), _ev(105, "control", "Back Control", "A")]
    assert at.bout_flags(seq, "A", "B")["contradiction_count"] == 0


def test_a_real_reversal_is_not_a_contradiction() -> None:
    """Bottom, sweep, top is a normal minute of grappling. Only the same half-minute counts."""
    seq = [_ev(60, "guard", "Half Guard", "A"), _ev(70, "sweep", "Sweep", "A"),
           _ev(140, "control", "Mount", "A"), _ev(150, "submission", "Armbar", "A")]
    f = at.bout_flags(seq, "A", "B")
    assert f["contradiction_count"] == 0


def test_a_two_sided_bout_keeps_its_roles() -> None:
    seq = [_ev(30, "guard", "Half Guard", "B"), _ev(40, "pass", "Knee Cut Pass", "A"),
           _ev(60, "control", "Mount", "A"), _ev(90, "submission", "Armbar", "A")]
    f = at.bout_flags(seq, "A", "B")
    assert f["role_reliable"] and f["perspective_reliable"] and f["reason_code"] is None


# ── the event, seen from one corner ─────────────────────────────────────────────
OK = {"role_reliable": True, "perspective_reliable": True}
BAD = {"role_reliable": False, "perspective_reliable": False}


def test_a_known_actor_gives_favor_to_one_side_and_contra_to_the_other() -> None:
    """`Helena executes Single Leg on Ane` -> Helena A FAVOR, Ane CONTRA. The second half is
    not an inversion by elimination: the bout has two people and the actor is named."""
    ev = _ev(20, "takedown", "Single Leg Takedown", "helena")
    mine = at.attribute(ev, "helena", "ane", OK)
    hers = at.attribute(ev, "ane", "helena", OK)
    assert mine["perspective"] == "favor" and mine["target_id"] == "ane"
    assert hers["perspective"] == "contra" and hers["target_id"] == "ane"
    assert mine["subject_role"] == at.EXECUTOR and hers["subject_role"] == at.DEFENDER


def test_back_control_read_from_the_other_corner_is_being_controlled() -> None:
    ev = _ev(90, "control", "Back Control", "helena")
    assert at.attribute(ev, "helena", "ane", OK)["subject_role"] == at.CONTROLLING
    assert at.attribute(ev, "ane", "helena", OK)["subject_role"] == at.CONTROLLED


def test_half_guard_read_from_the_other_corner_is_on_top() -> None:
    ev = _ev(90, "guard", "Half Guard", "helena")
    assert at.attribute(ev, "helena", "ane", OK)["subject_role"] == at.BOTTOM
    assert at.attribute(ev, "ane", "helena", OK)["subject_role"] == at.TOP


def test_an_unreliable_bout_keeps_the_event_and_drops_the_role() -> None:
    """Preserved raw, excluded from anything with a direction. That is the whole rule."""
    ev = _ev(90, "guard", "Half Guard", "helena")
    out = at.attribute(ev, "helena", "ane", BAD)
    assert out["label"] == "Half Guard" and out["actor_id"] == "helena"
    assert out["attribution_status"] == "unknown"
    assert out["subject_role"] == at.UNKNOWN
    assert at.directional(out) is False


def test_an_unknown_label_stays_out_of_directional_statistics() -> None:
    out = at.attribute(_ev(10, "match", "Match", "helena"), "helena", "ane", OK)
    assert at.directional(out) is False


def test_a_symmetric_position_is_directional_and_stays_neutral() -> None:
    out = at.attribute(_ev(10, "guard", "50/50 Guard", "helena"), "helena", "ane", OK)
    assert out["subject_role"] == at.NEUTRAL
    assert at.directional(out) is True


def test_bookkeeping_rows_are_not_events() -> None:
    """Eight rows labelled "Match" survive from one import batch. Letting them inherit a
    `transition` role put "Match" in a node list beside Back Control."""
    assert at.is_event({"type": "match"}) is False
    assert at.is_event({"type": "strike"}) is False
    for t in at.EVENT_TYPES:
        assert at.is_event({"type": t}) is True
