"""Shared perspective conversion — both actors kept, tri-state success, ts."""

from __future__ import annotations

from typing import Any

from analysis.perspective_sequence import (
    STABLE_STATE_TYPES,
    perspective_events,
    sequence_boundaries,
)


def _mk_match(events: list[Any], timeline: Any = None, athlete_a: str = "a1",
              athlete_b: str = "b1", match_id: str = "m1") -> Any:
    return type("_M", (), {
        "id": match_id,
        "athlete_a_id": athlete_a,
        "athlete_b_id": athlete_b,
        "sequence": events,
        "timeline": timeline,
        "winner_id": None,
        "win_type": None,
        "submission": None,
        "year": 2025,
        "created_at": None,
    })()


def test_both_actors_preserved_in_order() -> None:
    m = _mk_match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1", "successful": True},
        {"label": "Posts Right Hand", "type": "escape", "actor_id": "b1"},
        {"label": "Front Triangle", "type": "submission", "actor_id": "a1"},
    ])
    events = perspective_events(m, "a1")
    assert [(e.actor, e.label) for e in events] == [
        ("you", "Closed Guard"),
        ("you", "Hip Bump Sweep"),
        ("opponent", "Posts Right Hand"),
        ("you", "Front Triangle"),
    ]
    # reversed perspective for the other athlete
    events_b = perspective_events(m, "b1")
    assert [(e.actor, e.label) for e in events_b] == [
        ("opponent", "Closed Guard"),
        ("opponent", "Hip Bump Sweep"),
        ("you", "Posts Right Hand"),
        ("opponent", "Front Triangle"),
    ]


def test_node_key_normalized() -> None:
    m = _mk_match([{"label": "Hip Bump Sweep!", "type": "sweep", "actor_id": "a1"}])
    assert perspective_events(m, "a1")[0].node_key == "hip bump sweep"


def test_successful_tri_state() -> None:
    m = _mk_match([
        {"label": "A", "type": "transition", "actor_id": "a1", "successful": False},
        {"label": "B", "type": "transition", "actor_id": "a1", "successful": True},
        {"label": "C", "type": "transition", "actor_id": "a1"},
    ])
    events = perspective_events(m, "a1")
    assert events[0].successful is False
    assert events[1].successful is True
    assert events[2].successful is None  # absent key is unknown, never False


def test_timestamps_from_sequence_and_timeline() -> None:
    m = _mk_match(
        [{"label": "A", "type": "transition", "actor_id": "a1", "ts": 42},
         {"label": "B", "type": "transition", "actor_id": "a1"}],
        timeline=[{"label": "A", "type": "transition", "actor": "a", "ts": 40},
                  {"label": "B", "type": "transition", "actor": "a", "ts": 90}],
    )
    events = perspective_events(m, "a1")
    assert events[0].timestamp_seconds == 42  # sequence ts wins
    assert events[1].timestamp_seconds == 90  # best-effort from timeline


def test_no_timeline_no_fabrication() -> None:
    m = _mk_match([{"label": "A", "type": "transition", "actor_id": "a1"}])
    assert perspective_events(m, "a1")[0].timestamp_seconds is None


def test_neutral_actor() -> None:
    m = _mk_match([
        {"label": "A", "type": "transition", "actor_id": "a1"},
        {"label": "Reset", "type": "reset", "actor_id": None},
    ])
    events = perspective_events(m, "a1")
    assert events[1].actor == "neutral"
    assert events[1].event_type == "reset"


def test_non_dict_events_skipped_and_no_mutation() -> None:
    seq = [
        {"label": "A", "type": "transition", "actor_id": "a1"},
        "junk",
        None,
        {"label": "B", "type": "transition", "actor_id": "b1"},
    ]
    m = _mk_match(seq)
    events = perspective_events(m, "a1")
    assert [e.label for e in events] == ["A", "B"]
    assert seq[1] == "junk"  # original untouched


def test_boundaries_neutral_and_gap() -> None:
    m = _mk_match([
        {"label": "A", "type": "transition", "actor_id": "a1", "ts": 0},
        {"label": "Reset", "type": "reset", "actor_id": None, "ts": 30},
        {"label": "B", "type": "transition", "actor_id": "a1", "ts": 60},
        {"label": "C", "type": "transition", "actor_id": "a1", "ts": 600},
    ])
    bounds = sequence_boundaries(m, "a1")
    assert bounds == {1, 3}  # neutral event + >120s gap


def test_boundaries_from_timeline_reset() -> None:
    m = _mk_match(
        [{"label": "A", "type": "transition", "actor_id": "a1"},
         {"label": "B", "type": "transition", "actor_id": "b1"}],
        timeline=[{"label": "A", "type": "transition", "actor": "a", "ts": 10},
                  {"label": "Reset", "type": "reset", "actor": None, "ts": 50},
                  {"label": "B", "type": "transition", "actor": "b", "ts": 90}],
    )
    bounds = sequence_boundaries(m, "a1")
    assert bounds == {1}  # first sequence event at/after the timeline reset


def test_stable_state_types_constant() -> None:
    assert STABLE_STATE_TYPES == frozenset({"guard", "control"})


def test_perspective_view_shim_byte_identical() -> None:
    """Regression guard: db.repository._perspective_view keeps its historical
    dict shape after the refactor onto analysis.perspective_sequence."""
    from db.repository import _perspective_view

    m = _mk_match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Hip Bump", "type": "sweep", "actor_id": "a1", "successful": True},
        {"label": "Post", "type": "escape", "actor_id": "b1"},
        {"label": "No Actor", "type": "reset", "actor_id": None},
        {"label": "Triangle", "type": "submission", "actor_id": "a1", "successful": False},
        "junk",
    ])
    view = _perspective_view(m, "a1")
    assert view.sequence == [
        {"label": "Closed Guard", "type": "guard", "actor": "you"},
        {"label": "Hip Bump", "type": "sweep", "actor": "you", "successful": True},
        {"label": "Post", "type": "escape", "actor": "opponent"},
        # None actor_id maps to "opponent" historically — preserved exactly
        {"label": "No Actor", "type": "reset", "actor": "opponent"},
        {"label": "Triangle", "type": "submission", "actor": "you", "successful": False},
    ]
    # reversed perspective
    view_b = _perspective_view(m, "b1")
    assert view_b.sequence[0] == {"label": "Closed Guard", "type": "guard", "actor": "opponent"}
