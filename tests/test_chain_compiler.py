"""Chain compiler (Phase 1, actions/states migration) — structural, deterministic, zero prob."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from analysis.chain_compiler import compile_chain, compile_two_sided
from analysis.taxonomy_kind import load_inference_table

TABLE = load_inference_table()


def _ev(label: str, type_: str, actor: str = "a", **kw: Any) -> dict[str, Any]:
    return {"label": label, "type": type_, "actor": actor, **kw}


def test_guard_pull_then_armlock_opens_start_neutral_then_scramble_fallback() -> None:
    """'Guard Pull' is type 'transition' in this corpus (D1 forces transition -> 'action'), NOT
    type 'guard'. Rule 5's opening gap now resolves via `lamas_state` reading 'Guard Pull' as
    Lamas' PGD (guard-pull) — a standing-exchange opening, so the chain opens on 'start neutral'
    (role='start'), not the bare fallback. The MID-chain gap (transition, submission) between the
    pull and the armbar is untouched — D2's action_pair_to_state table has no specific row for it
    (only submission|submission, submission|$terminal, $start|takedown, takedown|*, sweep|*,
    pass|*), so it still falls through to the '*|*' fallback: 'scramble'. The chain's LAST action
    (the armbar) is genuinely terminal, so it resolves through the '$terminal' marker to 'finish'
    instead."""
    chain = compile_chain([
        _ev("Guard Pull", "transition"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["start neutral", "scramble", "finish"]
    assert [s.role for s in chain.states] == ["start", None, "finish"]
    assert all(s.inferred for s in chain.states)
    assert len(chain.edges) == 2
    e0, e1 = chain.edges
    assert (e0.action_key, e0.inferred, e0.terminal) == ("guard pull", False, False)
    assert e0.source_key == "start neutral" and e0.target_key == "scramble"
    assert (e1.action_key, e1.inferred, e1.terminal) == ("armbar", False, True)
    assert e1.target_key == "finish"
    assert not chain.dropped


def test_chain_opening_on_clinch_label_gets_start_neutral() -> None:
    """Lamas CDP (clinch/grip-fighting) is label-, not type-, keyed — 'Collar Tie' is type
    'control', same type as an ordinary position, so only `lamas_state` (not the table) can tell
    this opening is a standing exchange."""
    chain = compile_chain([
        _ev("Collar Tie", "control"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)

    assert chain.states[0].node_key == "start neutral"
    assert chain.states[0].role == "start"
    assert chain.edges[0].source_key == "start neutral"
    assert chain.edges[0].action_key == "collar tie"


def test_armbar_then_triangle_chains_through_chained_submission() -> None:
    """`$start|submission -> start neutral` opens the chain (owner call 2026-08-27, replacing
    a dedicated 'start engaged' node he judged meaningless); this test's own focus is the
    MID-chain submission|submission bridge, unaffected either way."""
    chain = compile_chain([
        _ev("Armbar", "submission"),
        _ev("Triangle Choke", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["start neutral", "chained submission", "finish"]
    assert [s.inferred for s in chain.states] == [True, True, True]
    e0, e1 = chain.edges
    assert e0.action_key == "armbar" and e0.target_key == "chained submission"
    assert not e0.inferred and not e0.terminal
    assert e1.action_key == "triangle choke" and e1.source_key == "chained submission"
    assert not e1.inferred and e1.terminal
    assert e1.target_key == "finish"


def test_submission_terminal_resolves_to_finish_not_scramble() -> None:
    """D2's declarative row (`submission|$terminal` -> `finish`), owner call 2026-08-27: the
    LAST action of a chain being a landed/attempted submission is a semantically different gap
    than 'no more info' — it should land on the generic 'finish' node, not 'scramble'. A
    submission in the MIDDLE of the chain (bridging to another action, not a real state) is
    untouched — still the '*|*' fallback, same as before this change. `$start|submission ->
    start neutral` opens the chain — this test's own focus is the mid-chain and terminal gaps,
    unaffected by that row."""
    chain = compile_chain([
        _ev("Armbar", "submission"),
        _ev("Guard Pass", "pass"),  # mid-chain: (submission, pass) has no dedicated row
    ], inference_table=TABLE)
    assert [s.node_key for s in chain.states] == ["start neutral", "scramble", "top transition"]
    e0, e1 = chain.edges
    assert e0.action_key == "armbar" and e0.target_key == "scramble"  # mid-chain: unaffected
    assert e1.action_key == "guard pass" and e1.terminal is True
    assert e1.target_key == "top transition"  # non-submission terminal: also unaffected


def test_kguard_then_5050_guard_bridges_with_guard_transition() -> None:
    """`orientation_of('K-Guard') == 'bottom'`, so the chain also opens on a PREPENDED 'start
    bottom' (module docstring) — this test's own focus is the MID-chain bridge, unaffected: two
    real guard states still bridge through the real 'guard transition' edge."""
    chain = compile_chain([
        _ev("K-Guard", "guard"),
        _ev("50/50 Guard", "guard"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["kguard", "5050 guard"]
    assert chain.states[0].nascent is True  # opens on a real state: no anchor invented
    assert not any(s.inferred for s in chain.states[1:])
    assert len(chain.edges) == 1
    (edge,) = chain.edges
    assert edge.action_key == "guard transition"
    assert edge.inferred is True
    assert edge.terminal is False
    assert edge.source_event_index is None
    assert edge.source_key == "kguard" and edge.target_key == "5050 guard"


def test_mount_then_side_control_bridges_with_control_transition() -> None:
    """Two real `control` states with no action between them bridge through the new
    `control|control -> control transition` row (2026-08-27), not the bare `transition`
    fallback. `orientation_of('Mount') == 'top'` also prepends 'start top' (module docstring)
    ahead of the pair this test targets — unpacked explicitly, same convention as the
    kguard/5050 guard-transition test above."""
    chain = compile_chain([
        _ev("Mount", "control"),
        _ev("Side Control", "control"),
    ], inference_table=TABLE)

    (edge,) = chain.edges
    assert edge.source_key == "mount" and edge.target_key == "side control"
    assert edge.action_key == "control transition"
    assert edge.inferred is True


def test_mount_then_closed_guard_bridges_with_guard_recovery() -> None:
    """`control|guard -> guard recovery` (2026-08-27): control lost, back in someone's guard."""
    chain = compile_chain([
        _ev("Mount", "control"),
        _ev("Closed Guard", "guard"),
    ], inference_table=TABLE)

    edge = [e for e in chain.edges if e.source_key == "mount" and e.target_key == "closed guard"][0]
    assert edge.action_key == "guard recovery"


def test_closed_guard_then_mount_bridges_with_guard_exit() -> None:
    """`guard|control -> guard exit` (2026-08-27): left the guard, into a control position."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)

    edge = [e for e in chain.edges if e.source_key == "closed guard" and e.target_key == "mount"][0]
    assert edge.action_key == "guard exit"


def test_escape_action_then_submission_action_bridges_with_bottom_transition() -> None:
    """Two adjacent ACTIONS with no state between them: an escape (forced-action type) followed
    by a submission bridges through the new `escape|* -> bottom transition` row, mirroring
    `takedown|* -> top transition`. The invented STATE is inferred; the EDGE reaching it is the
    real, logged 'Some Escape' action."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Some Escape", "escape"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)

    bridge_states = [s for s in chain.states if s.node_key == "bottom transition"]
    assert len(bridge_states) == 1 and bridge_states[0].inferred is True
    bridge_edge = [e for e in chain.edges if e.target_key == "bottom transition"][0]
    assert bridge_edge.inferred is False
    assert bridge_edge.action_key == "some escape"
    assert bridge_edge.source_key == "closed guard"


def test_chain_opening_on_takedown_gets_start_neutral_initial_state() -> None:
    """D2's declarative opening row (owner call, 2026-08-27): a chain whose first action is a
    'takedown' resolves rule 5's gap via the `"$start"` sentinel — `"$start|takedown" ->
    "start neutral"` — mirroring `_CHAIN_END`'s own declarative pattern, no code needed for this
    specific type. 'start neutral' carries role='start' and is PREPENDED before the real first
    state, linked by the takedown's own (real, non-inferred) edge."""
    chain = compile_chain([
        _ev("Double Leg Takedown", "takedown"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)

    assert chain.states[0].node_key == "start neutral"
    assert chain.states[0].role == "start"
    assert chain.states[0].inferred is True
    assert [s.node_key for s in chain.states] == ["start neutral", "mount"]
    assert chain.states[1].inferred is False
    assert len(chain.edges) == 1
    edge = chain.edges[0]
    assert edge.action_key == "double leg takedown"
    assert edge.source_key == "start neutral" and edge.target_key == "mount"
    assert not edge.inferred and not edge.terminal


def test_chain_opening_on_submission_resolves_to_start_neutral() -> None:
    """D2's declarative opening row: a chain whose first action is a 'submission' opens on
    'start neutral'. A dedicated 'start engaged' node was tried and REMOVED — the owner judged
    the concept meaningless, and routing here is the honest alternative: where the athlete was
    before an unlogged submission is genuinely unknown, which is what the neutral anchor is
    for. Measured cost, accepted: start neutral becomes the highest-degree node in his own
    graph (9), above butterfly guard."""
    chain = compile_chain([
        _ev("Armbar", "submission"),
        _ev("Guard Pass", "pass"),
    ], inference_table=TABLE)

    assert chain.states[0].node_key == "start neutral"
    assert chain.states[0].role == "start"


def test_chain_opening_on_closed_guard_state_state_is_nascent() -> None:
    """The prepend this test used to pin is GONE (owner call 2026-08-27): reaching a
    chain-opening state through an invented action was a claim the log never made. The state
    opens the chain on its own and carries ``nascent`` — see
    ``test_chain_opening_on_a_state_starts_loose_and_is_marked_nascent`` for the full contract."""
    chain = compile_chain([_ev("Closed Guard", "guard")], inference_table=TABLE)
    assert [s.node_key for s in chain.states] == ["closed guard"]
    assert chain.states[0].nascent is True
    assert not any(s.role == "start" for s in chain.states)


def test_chain_opening_on_mount_state_state_is_nascent() -> None:
    """The prepend this test used to pin is GONE (owner call 2026-08-27): reaching a
    chain-opening state through an invented action was a claim the log never made. The state
    opens the chain on its own and carries ``nascent`` — see
    ``test_chain_opening_on_a_state_starts_loose_and_is_marked_nascent`` for the full contract."""
    chain = compile_chain([_ev("Mount", "control")], inference_table=TABLE)
    assert [s.node_key for s in chain.states] == ["mount"]
    assert chain.states[0].nascent is True
    assert not any(s.role == "start" for s in chain.states)


def test_chain_opening_on_neutral_state_stays_unprepended() -> None:
    """`orientation_of('Electric Chair') == 'neutral'` (ambiguous by design) — no start node is
    invented for a neutral-orientation opening state; the state itself remains the first node,
    exactly as before this change."""
    chain = compile_chain([
        _ev("Electric Chair", "control"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)

    assert chain.states[0].node_key == "electric chair"
    assert chain.states[0].role is None
    assert chain.states[0].inferred is False


def test_chain_ending_in_submission_is_terminal() -> None:
    """`orientation_of('Mount') == 'top'`, so this chain also opens on a PREPENDED 'start top'
    (module docstring) — this test's own focus is the TERMINAL end, unaffected."""
    chain = compile_chain([
        _ev("Mount", "control"),
        _ev("Armbar", "submission", successful=True),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["mount", "finish"]
    assert chain.states[0].nascent is True  # opens on a real state: no anchor invented
    assert chain.states[1].inferred is True
    assert chain.states[1].role == "finish"
    edge = chain.edges[-1]
    assert edge.terminal is True
    assert edge.action_key == "armbar"
    assert edge.source_key == "mount"
    assert edge.target_key == "finish"
    assert edge.source_event_index == 1


def test_concept_event_dropped_chain_stays_intact() -> None:
    """`orientation_of('Closed Guard') == 'bottom'`, so this chain also opens on a PREPENDED
    'start bottom' (module docstring) — this test's own focus is the dropped concept event,
    unaffected: it stays skipped, and the surviving real states/edges are untouched."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Grip Fighting", "concept"),
        _ev("Guard Pass", "pass"),
    ], inference_table=TABLE)

    assert len(chain.dropped) == 1
    dropped = chain.dropped[0]
    assert dropped.index == 1 and dropped.reason == "transparent"
    assert [s.node_key for s in chain.states] == ["closed guard", "top transition"]
    assert chain.states[0].nascent is True  # opens on a real state: no anchor invented
    assert chain.states[1].inferred is True
    edge = chain.edges[-1]
    assert edge.source_key == "closed guard" and edge.action_key == "guard pass"
    assert edge.terminal is True


def test_compile_two_sided_splits_and_drops_unassigned_with_original_index() -> None:
    events = [
        _ev("Closed Guard", "guard", actor="x"),
        _ev("Guard Pass", "pass", actor="y"),
        _ev("Round End", "reset", actor="referee"),
    ]

    def side_of(ev: Mapping[str, Any]) -> str | None:
        if ev["actor"] == "x":
            return "a"
        if ev["actor"] == "y":
            return "b"
        return None

    result = compile_two_sided(events, side_of, inference_table=TABLE)

    assert set(result) == {"a", "b", "dropped"}
    a = result["a"]
    # side 'a' opens on a real state, so it opens THERE — no anchor, and with a single event
    # no edge either; this test's own focus is the split/original-index remapping.
    assert [s.node_key for s in a.states] == ["closed guard"]
    assert a.states[0].nascent is True
    assert a.edges == []
    assert not a.dropped

    b = result["b"]
    assert b.states[0].node_key == "start top"  # $start|pass (2026-08-27) for the lone Guard Pass
    assert b.edges[0].source_event_index == 1  # rewritten to the ORIGINAL events index

    d = result["dropped"]
    assert not d.states and not d.edges
    assert len(d.dropped) == 1
    assert d.dropped[0].index == 2 and d.dropped[0].reason == "no_side"


def test_state_after_event_tracks_current_state_incl_terminal_and_dropped() -> None:
    """``state_after_event[idx]`` = the node_key live right after processing raw index idx —
    unchanged across a dropped/transparent event, and overwritten to the terminal-inferred
    state once the trailing pending action resolves post-loop (rule 6)."""
    chain = compile_chain([
        _ev("Mount", "control"),          # 0: state -> "mount"
        _ev("Grip Fighting", "concept"),  # 1: transparent, carries "mount" forward
        _ev("Armbar", "submission"),      # 2: pending action, resolves post-loop -> terminal
    ], inference_table=TABLE)

    assert chain.state_after_event[0] == "mount"
    assert chain.state_after_event[1] == "mount"
    assert chain.state_after_event[2] == chain.states[-1].node_key == "finish"


def test_compile_two_sided_remaps_state_after_event_to_original_index() -> None:
    events = [
        _ev("Closed Guard", "guard", actor="x"),
        _ev("Guard Pass", "pass", actor="y"),
        _ev("Round End", "reset", actor="referee"),
    ]

    def side_of(ev: Mapping[str, Any]) -> str | None:
        if ev["actor"] == "x":
            return "a"
        if ev["actor"] == "y":
            return "b"
        return None

    result = compile_two_sided(events, side_of, inference_table=TABLE)
    assert result["a"].state_after_event == {0: "closed guard"}
    # rule 6: pass|* -> top transition
    assert result["b"].state_after_event == {1: "top transition"}
    assert result["dropped"].state_after_event == {}


def test_determinism() -> None:
    events = [
        _ev("K-Guard", "guard"),
        _ev("Guard Pull", "transition"),
        _ev("Armbar", "submission"),
        _ev("Grip Fighting", "concept"),
        _ev("Triangle Choke", "submission"),
    ]
    first = compile_chain(events, inference_table=TABLE)
    second = compile_chain(events, inference_table=TABLE)
    assert first == second


def test_chain_opening_on_a_state_starts_loose_and_is_marked_nascent() -> None:
    """Owner call 2026-08-27: "costas não deveria ser presumido como precedido por top start —
    deveria começar solto sem inferência de ação prévia". A start anchor exists to be the missing
    SOURCE of a chain-opening action; a state that opens a chain needs no source, so prepending
    one (and the edge to reach it) invented a move nobody logged. Only actions connect to a start
    anchor. Such a state is marked `nascent` instead — the chain simply begins there."""
    chain = compile_chain([
        _ev("Back Control", "control"),   # orientation 'top' — used to be prepended by start top
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["back control", "finish"]
    assert chain.states[0].nascent is True
    assert chain.states[0].inferred is False
    assert not any(s.role == "start" for s in chain.states)
    assert [(e.source_key, e.target_key) for e in chain.edges] == [("back control", "finish")]


def test_a_state_reached_by_an_action_is_not_nascent() -> None:
    """The flag is about having no predecessor, not about being first in some chain: the opening
    state is nascent, the one an action leads into never is."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)
    by_key = {s.node_key: s for s in chain.states}
    assert by_key["closed guard"].nascent is True
    assert by_key["finish"].nascent is False


def test_only_actions_connect_to_a_start_anchor() -> None:
    """A start anchor is only ever reached as an ACTION's inferred source — never as a prepended
    state-to-state hop. Chain opening on an action gets one; chain opening on a state does not."""
    opens_on_action = compile_chain([_ev("Double Leg Takedown", "takedown")],
                                    inference_table=TABLE)
    assert opens_on_action.states[0].role == "start"
    assert opens_on_action.edges[0].source_key == opens_on_action.states[0].node_key

    opens_on_state = compile_chain([_ev("Mount", "control")], inference_table=TABLE)
    assert not any(s.role == "start" for s in opens_on_state.states)
    assert opens_on_state.edges == []
