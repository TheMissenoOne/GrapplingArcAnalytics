"""Decision-pattern extraction — windowing matrix, aggregation, boundaries."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from analysis.decision_flow import (
    DecisionPattern,
    PatternEvidence,
    aggregate_patterns,
    build_athlete_decision_patterns,
    extract_patterns,
)
from analysis.perspective_sequence import PerspectiveEvent, perspective_events


def _persp(match: Any, athlete_id: str = "a1") -> list[PerspectiveEvent]:
    return perspective_events(match, athlete_id)


def _ev(actor: Any, label: str, event_type: str = "transition", successful: bool | None = None,
        idx: int | None = None, ts: int | None = None) -> PerspectiveEvent:
    raw: dict[str, Any] = {"label": label, "type": event_type,
                           "actor_id": "a1" if actor == "you" else "b1"}
    if successful is not None:
        raw["successful"] = successful
    return PerspectiveEvent(
        index=idx if idx is not None else 0, actor=actor, label=label,
        node_key=label.lower(), event_type=event_type,
        successful=successful, timestamp_seconds=ts, raw=raw)


def _match(seq: list[Any], athlete_a: str = "a1", athlete_b: str = "b1",
           match_id: str = "m1") -> Any:
    return type("_M", (), {
        "id": match_id,
        "athlete_a_id": athlete_a,
        "athlete_b_id": athlete_b,
        "sequence": seq,
        "timeline": None,
    })()


def test_you_opponent_you_pattern() -> None:
    m = _match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1"},
        {"label": "Posts Right Hand", "type": "escape", "actor_id": "b1"},
        {"label": "Front Triangle", "type": "submission", "actor_id": "a1", "successful": True},
    ])
    patterns = extract_patterns(_persp(m), match_id="m1", match_slug="s1", athlete_id="a1")
    assert len(patterns) == 1
    p = patterns[0]
    assert p.source_position_key == "closed guard"
    assert p.action_key == "hip bump sweep"
    assert p.condition_key == "cond:posts-hand"
    assert p.response_key == "front triangle"
    assert p.success_count == 1
    assert p.evidence[0].action_index == 1
    assert p.evidence[0].condition_indexes == (2,)
    assert p.evidence[0].response_index == 3


def test_opponent_events_never_skipped() -> None:
    m = _match([
        {"label": "Arm Drag", "type": "transition", "actor_id": "a1"},
        {"label": "Pulls Elbow Free", "type": "escape", "actor_id": "b1"},
        {"label": "Squares Hips", "type": "transition", "actor_id": "b1"},
        {"label": "Side Scissor", "type": "sweep", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m), match_id="m1")
    assert len(patterns) == 1
    assert patterns[0].condition_key == "cond:elbow-free-and-cond:squares-hips"


def test_direct_continuation_no_fake_condition() -> None:
    m = _match([
        {"label": "Arm Drag", "type": "transition", "actor_id": "a1"},
        {"label": "Side Scissor", "type": "sweep", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m))
    assert len(patterns) == 1
    assert patterns[0].condition_key is None
    assert patterns[0].response_key == "side scissor"


def test_chaining_action_response_roundtrip() -> None:
    m = _match([
        {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1"},
        {"label": "Posts Right Hand", "type": "escape", "actor_id": "b1"},
        {"label": "Front Triangle", "type": "submission", "actor_id": "a1"},
        {"label": "Defends", "type": "escape", "actor_id": "b1"},
        {"label": "Kimura", "type": "submission", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m))
    assert len(patterns) == 2
    assert patterns[0].response_key == "front triangle"
    assert patterns[0].condition_key == "cond:posts-hand"
    assert patterns[1].action_key == "front triangle"
    assert patterns[1].condition_key == "cond:opponent-escapes"


def test_failed_action_is_terminal_failure() -> None:
    m = _match([
        {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1", "successful": False},
        {"label": "Posts Right Hand", "type": "escape", "actor_id": "b1"},
        {"label": "Front Triangle", "type": "submission", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m))
    # failed attempt closes before the opponent events; nothing chains into it
    assert len(patterns) == 1
    assert patterns[0].response_key is None
    assert patterns[0].failure_count == 1


def test_failed_standalone_attempt() -> None:
    m = _match([
        {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1", "successful": False},
    ])
    patterns = extract_patterns(_persp(m))
    assert len(patterns) == 1
    assert patterns[0].action_key == "hip bump sweep"
    assert patterns[0].response_key is None
    assert patterns[0].failure_count == 1


def test_unknown_success_not_failure() -> None:
    m = _match([
        {"label": "Arm Drag", "type": "transition", "actor_id": "a1"},
        {"label": "Side Scissor", "type": "sweep", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m))
    assert patterns[0].unknown_result_count == 1
    assert patterns[0].failure_count == 0
    assert patterns[0].success_count == 0


def test_terminal_submission_from_match() -> None:
    m = _match([
        {"label": "Arm Drag", "type": "transition", "actor_id": "a1"},
        {"label": "Side Scissor", "type": "sweep", "actor_id": "a1"},
        {"label": "Rear Naked Choke", "type": "submission", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m), winning_submission_key="rear naked choke")
    assert patterns[-1].success_count == 1  # winning submission = explicit outcome


def test_position_updates_flow() -> None:
    m = _match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1"},
        {"label": "Posts Right Hand", "type": "escape", "actor_id": "b1"},
        {"label": "Mount", "type": "control", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m))
    assert len(patterns) == 1
    p = patterns[0]
    assert p.source_position_key == "closed guard"
    assert p.resulting_position_key == "mount"


def test_opponent_position_updates_state_not_condition() -> None:
    m = _match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Arm Drag", "type": "transition", "actor_id": "a1"},
        {"label": "Half Guard", "type": "guard", "actor_id": "b1"},  # opponent re-guards
        {"label": "Side Scissor", "type": "sweep", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m))
    assert patterns[0].condition_key is None  # no condition from position events
    assert patterns[0].resulting_position_key == "half guard"


def test_return_to_position_is_response() -> None:
    m = _match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1"},
        {"label": "Posts Right Hand", "type": "escape", "actor_id": "b1"},
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1", "successful": True},
    ])
    patterns = extract_patterns(_persp(m))
    assert len(patterns) == 1
    assert patterns[0].response_key == "closed guard"
    assert patterns[0].resulting_position_key == "closed guard"


def test_resets_break_windows() -> None:
    m = _match([
        {"label": "A", "type": "transition", "actor_id": "a1"},
        {"label": "X", "type": "transition", "actor_id": None},
        {"label": "B", "type": "transition", "actor_id": "b1"},
        {"label": "C", "type": "transition", "actor_id": "a1"},
        {"label": "D", "type": "transition", "actor_id": "a1"},
    ])
    patterns = extract_patterns(_persp(m))
    assert len(patterns) == 1
    assert patterns[0].action_key == "c"
    assert patterns[0].condition_key is None  # B dropped: opponent event after the break
    assert patterns[0].response_key == "d"


def test_no_pattern_crosses_matches() -> None:
    m1 = _match([{"label": "A", "type": "transition", "actor_id": "a1"}], match_id="m1")
    m2 = _match([{"label": "B", "type": "transition", "actor_id": "a1"}], match_id="m2")
    assert extract_patterns(_persp(m1), match_id="m1") == []
    assert extract_patterns(_persp(m2), match_id="m2") == []


def test_aggregation_counts_and_matches() -> None:
    def mk(match_id: str) -> list[DecisionPattern]:
        m = _match([
            {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
            {"label": "Hip Bump Sweep", "type": "sweep", "actor_id": "a1", "successful": True},
            {"label": "Posts Right Hand", "type": "escape", "actor_id": "b1"},
            {"label": "Front Triangle", "type": "submission", "actor_id": "a1"},
        ], match_id=match_id)
        return extract_patterns(_persp(m), match_id=match_id, match_slug=match_id,
                                winning_submission_key="front triangle")

    patterns = aggregate_patterns(mk("m1") + mk("m2") + mk("m3"))
    assert len(patterns) == 1
    p = patterns[0]
    assert p.count == 3
    assert p.match_count == 3
    assert p.success_count == 3
    assert 0.0 < p.confidence < 1.0


def test_aggregation_distinct_match_count_beyond_evidence_cap() -> None:
    m = _match([
        {"label": "A", "type": "transition", "actor_id": "a1"},
        {"label": "B", "type": "transition", "actor_id": "a1"},
    ])
    all_p = []
    for i in range(12):
        all_p += extract_patterns(_persp(m), match_id=f"m{i}", match_slug=f"m{i}")
    patterns = aggregate_patterns(all_p)
    assert patterns[0].count == 12
    assert patterns[0].match_count == 12  # distinct matches, not capped evidence
    assert len(patterns[0].evidence) == 8


def test_build_athlete_decision_patterns_smoke() -> None:
    m = _match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Arm Drag", "type": "transition", "actor_id": "a1"},
        {"label": "Pulls Elbow Free", "type": "escape", "actor_id": "b1"},
        {"label": "Side Scissor", "type": "sweep", "actor_id": "a1", "successful": True},
    ])
    patterns = build_athlete_decision_patterns(
        "a1", [m], match_slug_of=lambda x: "s-" + x.id)

    # Chain conditioning keeps BOTH links of the chain, where the old windowing kept one:
    # the own-to-own combination (nothing from the opponent in between) is a real finding,
    # not a gap, so it is emitted with condition_key=None.
    by_action = {p.action_key: p for p in patterns}
    assert set(by_action) == {"closed guard", "arm drag"}

    assert by_action["arm drag"].condition_key == "cond:elbow-free"
    assert by_action["arm drag"].response_key == "side scissor"
    assert by_action["arm drag"].evidence[0].opponent_id == "b1"

    assert by_action["closed guard"].condition_key is None
    assert by_action["closed guard"].response_key == "arm drag"


def test_patterns_survive_the_export_cache_json_roundtrip() -> None:
    """style_profile must hand the export DICTS, never DecisionPattern dataclasses.

    export.incremental.ItemCache normalises every payload through
    ``json.dumps(..., default=str)``. A dataclass isn't JSON-serialisable, so
    ``default=str`` silently replaces it with its repr string — on cache HIT *and*
    MISS. That shipped once and made every dossier's Decision Flow vanish with no
    error at all, so pin the contract here.
    """
    m = _match([
        {"label": "Closed Guard", "type": "guard", "actor_id": "a1"},
        {"label": "Arm Drag", "type": "transition", "actor_id": "a1"},
        {"label": "Pulls Elbow Free", "type": "escape", "actor_id": "b1"},
        {"label": "Side Scissor", "type": "sweep", "actor_id": "a1", "successful": True},
    ])
    original = aggregate_patterns(extract_patterns(_persp(m), match_id="m1"))
    assert original, "fixture should produce at least one pattern"

    # what style_profile stores → what ItemCache writes and reads back
    revived = json.loads(json.dumps([asdict(p) for p in original], default=str))
    assert all(isinstance(p, dict) for p in revived), "dataclass would degrade to a repr string"
    assert all(isinstance(e, dict) for p in revived for e in p["evidence"])

    # what render_profile_page rebuilds — must equal what we started with
    rebuilt = [
        DecisionPattern(
            **{k: v for k, v in p.items() if k != "evidence"},
            evidence=[
                PatternEvidence(**{**e, "condition_indexes": tuple(e["condition_indexes"])})
                for e in p["evidence"]
            ],
        )
        for p in revived
    ]
    assert rebuilt == original


def test_escape_clears_the_position_for_what_follows() -> None:
    """The escape starts from the position; nothing after it does.

    Real case (GSP vs Condit '12): Condit mounts, GSP escapes to standing, GSP
    shoots a double leg. Without clearing, the double leg is recorded as starting
    from mount — and mount then wins the root-position vote for GSP's whole flow.
    """
    m = _match([
        {"label": "Mount", "type": "control", "actor_id": "b1"},
        {"label": "Escape to Standing", "type": "escape", "actor_id": "a1"},
        {"label": "Double Leg Takedown", "type": "takedown", "actor_id": "a1"},
        {"label": "Back Control", "type": "control", "actor_id": "a1"},
    ])
    by_action = {p.action_key: p for p in extract_patterns(_persp(m))}
    assert by_action["escape to standing"].source_position_key == "mount"
    assert by_action["double leg takedown"].source_position_key is None


def test_opponent_escape_also_clears_the_position() -> None:
    m = _match([
        {"label": "Side Control", "type": "control", "actor_id": "a1"},
        {"label": "Escape to Standing", "type": "escape", "actor_id": "b1"},
        {"label": "Double Leg Takedown", "type": "takedown", "actor_id": "a1"},
        {"label": "Guard Pass", "type": "pass", "actor_id": "a1"},
    ])
    by_action = {p.action_key: p for p in extract_patterns(_persp(m))}
    assert by_action["double leg takedown"].source_position_key is None


# ---------------------------------------------------------------- chain conditioning


def _chain(events, **kw):
    from analysis.decision_flow import extract_chain_patterns

    return extract_chain_patterns(events, **kw)


def _idx(events: list[PerspectiveEvent]) -> list[PerspectiveEvent]:
    """Re-stamp sequential indexes — `_ev` defaults them all to 0."""
    return [
        PerspectiveEvent(
            index=i, actor=e.actor, label=e.label, node_key=e.node_key,
            event_type=e.event_type, successful=e.successful,
            timestamp_seconds=e.timestamp_seconds, raw=e.raw,
        )
        for i, e in enumerate(events)
    ]


def test_chain_recovers_the_condition_the_window_extractor_drops():
    """A_guard -> B_pass -> A_sweep: B_pass IS A_sweep's condition.

    The window extractor loses it twice over — a stable-state own event opens no window, and
    an opponent event with no open window is discarded — so it reports no condition at all.
    """
    events = _idx([
        _ev("you", "Closed Guard", "guard"),
        _ev("opponent", "Guard Pass", "pass"),
        _ev("you", "Hip Bump Sweep", "sweep"),
    ])

    old = extract_patterns(events)
    assert all(p.condition_key is None for p in old), "precondition: the old path finds none"

    new = _chain(events)
    assert len(new) == 1
    assert new[0].action_key == "closed guard"
    assert new[0].response_key == "hip bump sweep"
    assert new[0].condition_key is not None, "the pass must become the condition"


def test_chain_full_alternation_conditions_every_link():
    """A_guard -> B_pass -> A_sweep -> B_escape, from A's side: each own move is conditioned
    on what the opponent did just before it."""
    events = _idx([
        _ev("you", "Closed Guard", "guard"),
        _ev("opponent", "Guard Pass", "pass"),
        _ev("you", "Hip Bump Sweep", "sweep"),
        _ev("opponent", "Escape", "escape"),
    ])
    got = _chain(events)
    assert [(p.action_key, p.response_key) for p in got] == [
        ("closed guard", "hip bump sweep"),
    ]
    assert got[0].condition_key is not None


def test_chain_includes_opponent_stable_state_as_a_condition():
    """Opponent guard/control is a condition here; the window extractor treats it as position."""
    events = _idx([
        _ev("you", "Single Leg", "takedown"),
        _ev("opponent", "Half Guard", "guard"),
        _ev("you", "Knee Slice", "pass"),
    ])
    got = _chain(events)
    assert len(got) == 1
    assert got[0].condition_key is not None


def test_chain_bundles_an_opponent_run():
    events = _idx([
        _ev("you", "Single Leg", "takedown"),
        _ev("opponent", "Sprawl", "escape"),
        _ev("opponent", "Front Headlock", "control"),
        _ev("you", "Stand Up", "escape"),
    ])
    got = _chain(events)
    assert len(got) == 1
    assert len(got[0].evidence[0].condition_indexes) == 2


def test_chain_emits_none_condition_for_an_own_combination():
    """Two own moves with nothing in between is a real finding, not a gap."""
    events = _idx([
        _ev("you", "Arm Drag", "transition"),
        _ev("you", "Back Take", "transition"),
    ])
    got = _chain(events)
    assert len(got) == 1
    assert got[0].condition_key is None


def test_chain_does_not_span_a_boundary():
    events = _idx([
        _ev("you", "Closed Guard", "guard"),
        _ev("opponent", "Guard Pass", "pass"),
        _ev("you", "Hip Bump Sweep", "sweep"),
    ])
    assert _chain(events, boundaries={1}) == []


def test_chain_resets_on_a_neutral_event():
    events = _idx([
        _ev("you", "Closed Guard", "guard"),
        PerspectiveEvent(index=1, actor="neutral", label="Reset", node_key="reset",
                         event_type="reset", successful=None, timestamp_seconds=None, raw={}),
        _ev("you", "Hip Bump Sweep", "sweep"),
    ])
    assert _chain(events) == []


def test_chain_carries_source_position_and_opponent_id():
    events = _idx([
        _ev("you", "Closed Guard", "guard"),
        _ev("opponent", "Guard Pass", "pass"),
        _ev("you", "Hip Bump Sweep", "sweep"),
    ])
    got = _chain(events, match_id="m1", athlete_id="a1", opponent_id="b1")
    assert got[0].source_position_key == "closed guard"
    assert got[0].evidence[0].opponent_id == "b1"
    assert got[0].evidence[0].match_id == "m1"


def test_chain_records_outcome_on_the_response():
    events = _idx([
        _ev("you", "Closed Guard", "guard"),
        _ev("opponent", "Guard Pass", "pass"),
        _ev("you", "Hip Bump Sweep", "sweep", successful=False),
    ])
    got = _chain(events)
    assert got[0].failure_count == 1
    assert got[0].success_count == 0


def test_chain_produces_more_conditions_than_the_window_extractor():
    """The whole point: same events, strictly more conditions recovered."""
    events = _idx([
        _ev("you", "Closed Guard", "guard"),
        _ev("opponent", "Guard Pass", "pass"),
        _ev("you", "Hip Bump Sweep", "sweep"),
        _ev("opponent", "Half Guard", "guard"),
        _ev("you", "Knee Slice", "pass"),
    ])
    old = sum(1 for p in extract_patterns(events) if p.condition_key)
    new = sum(1 for p in _chain(events) if p.condition_key)
    assert new > old
