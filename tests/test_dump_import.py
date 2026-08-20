"""Point scores on a sequence event — the field visual processing can fill and a transcript
could not.

A transcript says what the commentator narrated; a frame shows the scoreboard. So reading
footage makes points observable for the first time, and ``_points`` is the boundary that
decides what counts as having observed one.

Nothing scores a match from these yet: win_type/submission still decide outcomes and
rating_v2 does not read them. Feeding points into a rating is ELO-affecting (change-control
class (d), full replay) and is a separate decision — capturing the observation is what makes
that decision possible at all.
"""

from __future__ import annotations

from scripts.insert_ufc_matches import _clean_events, _points


def test_points_absent_is_none_never_zero() -> None:
    # "nobody looked" and "this scored nothing" are different facts. Only None holds the
    # first; a 0 default would let the first be summed as the second.
    assert _points({"label": "Guard Pass"}) is None
    assert _points({"label": "Guard Pass", "points": None}) is None
    assert _points({"label": "Guard Pass", "points": 0}) == 0


def test_points_coerced_from_a_string_score() -> None:
    assert _points({"points": "3"}) == 3
    assert _points({"points": 4}) == 4


def test_points_rejects_nonsense_rather_than_guessing() -> None:
    assert _points({"points": "two"}) is None
    assert _points({"points": -2}) is None      # penalties hit the score, not the action
    assert _points({"points": True}) is None    # bool is an int in Python; not a score


def test_clean_events_carries_points_through() -> None:
    events = [
        {"label": "Guard Pass", "type": "pass", "actor": "A Fighter", "ts": 90, "points": 3},
        {"label": "Sweep", "type": "sweep", "actor": "B Fighter", "ts": 120},
    ]
    out = _clean_events("A Fighter", "B Fighter", events)
    assert out[0]["points"] == 3
    assert "points" not in out[1]          # unscored stays absent, not 0
