"""Exchange cycle metrics — run length and position revisits."""

from __future__ import annotations

from typing import Any

from analysis.exchange_cycles import (
    count_revisits,
    cycle_metrics,
    runs_of,
    split_exchanges,
)
from analysis.perspective_sequence import PerspectiveEvent


def _ev(actor: str, label: str, idx: int, event_type: str = "transition") -> PerspectiveEvent:
    raw: dict[str, Any] = {"label": label, "type": event_type}
    return PerspectiveEvent(
        index=idx, actor=actor, label=label, node_key=label.lower(),
        event_type=event_type, successful=None, timestamp_seconds=None, raw=raw,
    )


def _seq(spec: str) -> list[PerspectiveEvent]:
    """'a:guard b:pass a:sweep' -> events. 'a' = you, 'b' = opponent, 'n' = neutral."""
    actors = {"a": "you", "b": "opponent", "n": "neutral"}
    out = []
    for i, token in enumerate(spec.split()):
        who, label = token.split(":")
        out.append(_ev(actors[who], label, i))
    return out


# ---------------------------------------------------------------- runs


def test_alternating_sequence_is_all_runs_of_one():
    runs = runs_of(_seq("a:guard b:pass a:sweep b:escape"))
    assert [r.length for r in runs] == [1, 1, 1, 1]
    assert [r.actor for r in runs] == ["you", "opponent", "you", "opponent"]


def test_opponent_run_is_measured():
    """A_guard B_pass B_kneeslice B_pass A_sweep -> opponent held it for 3."""
    runs = runs_of(_seq("a:guard b:pass b:kneeslice b:pass a:sweep"))
    assert [r.length for r in runs] == [1, 3, 1]
    assert runs[1].actor == "opponent"


def test_own_run_is_measured():
    runs = runs_of(_seq("a:grip a:drag a:back b:escape"))
    assert runs[0].actor == "you"
    assert runs[0].length == 3


def test_runs_of_empty_is_empty():
    assert runs_of([]) == []


def test_single_event_is_one_run():
    runs = runs_of(_seq("a:guard"))
    assert len(runs) == 1
    assert runs[0].length == 1


# ---------------------------------------------------------------- exchanges


def test_neutral_event_splits_the_exchange():
    parts = split_exchanges(_seq("a:guard b:pass n:reset a:sweep"))
    assert len(parts) == 2
    assert [e.node_key for e in parts[0]] == ["guard", "pass"]
    assert [e.node_key for e in parts[1]] == ["sweep"]


def test_boundary_index_splits_the_exchange():
    parts = split_exchanges(_seq("a:guard b:pass a:sweep"), boundaries={1})
    assert len(parts) == 2


def test_a_run_never_spans_a_boundary():
    """Without this, a round break would fuse two exchanges into one giant fake run."""
    events = _seq("a:one a:two n:reset a:three a:four")
    m = cycle_metrics(events)
    assert m.own_runs == [2, 2]
    assert max(m.own_runs) == 2


# ---------------------------------------------------------------- revisits


def test_returning_to_a_position_is_a_revisit():
    revisits, loops = count_revisits(_seq("a:guard b:pass a:guard"))
    assert revisits == 1
    assert loops["guard"] == 1


def test_immediate_repeat_is_not_a_revisit():
    """The same key twice in a row is one action logged twice, not a loop back to it."""
    revisits, _ = count_revisits(_seq("a:guard a:guard"))
    assert revisits == 0


def test_repeated_loop_counts_each_return():
    revisits, loops = count_revisits(_seq("a:guard b:pass a:guard b:pass a:guard"))
    assert revisits == 3          # guard, pass, guard
    assert loops["guard"] == 2
    assert loops["pass"] == 1


def test_no_revisits_in_a_linear_exchange():
    revisits, loops = count_revisits(_seq("a:guard b:pass a:sweep b:escape"))
    assert revisits == 0
    assert loops == {}


# ---------------------------------------------------------------- aggregate


def test_metrics_summarize_initiative_and_burden():
    m = cycle_metrics(_seq("a:guard b:pass b:kneeslice b:pass a:sweep"))
    j = m.to_json()
    assert j["oppRunMax"] == 3
    assert j["ownRunMax"] == 1
    assert j["oppSustainedRuns"] == 1     # the run of 3
    assert j["ownSustainedRuns"] == 0
    assert j["initiativeShare"] == 0.4    # 2 own of 5 events


def test_initiative_share_is_symmetric():
    j = cycle_metrics(_seq("a:one b:two")).to_json()
    assert j["initiativeShare"] == 0.5


def test_empty_sequence_is_all_zeroes_not_a_crash():
    j = cycle_metrics([]).to_json()
    assert j["initiativeShare"] == 0.0
    assert j["ownRunMean"] == 0.0
    assert j["revisits"] == 0
    assert j["exchanges"] == 0


def test_most_repeated_is_reported():
    m = cycle_metrics(_seq("a:guard b:pass a:guard b:pass a:guard"))
    j = m.to_json()
    assert j["mostRepeated"][0]["key"] == "guard"
    assert j["mostRepeated"][0]["revisits"] == 2


def test_merge_accumulates_across_matches():
    a = cycle_metrics(_seq("a:one b:two"))
    b = cycle_metrics(_seq("a:three a:four b:five"))
    a.merge(b)
    j = a.to_json()
    assert j["exchanges"] == 2
    assert j["ownRunMax"] == 2
    assert a.own_events == 3
