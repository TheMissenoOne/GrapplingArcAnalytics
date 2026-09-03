"""Chain compiler (Phase 1, actions/states migration) — structural, deterministic, zero prob."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from analysis.chain_compiler import compile_chain, compile_two_sided
from analysis.taxonomy_kind import load_inference_table

TABLE = load_inference_table()


def _ev(label: str, type_: str, actor: str = "a", **kw: Any) -> dict[str, Any]:
    return {"label": label, "type": type_, "actor": actor, **kw}


def test_guard_pull_then_armlock_opens_start_neutral_and_stacks_into_one_finish_edge() -> None:
    """'Guard Pull' is type 'transition' in this corpus (D1 forces transition -> 'action'), NOT
    type 'guard'. The opening gap resolves via `lamas_state` reading 'Guard Pull' as Lamas' PGD
    (guard-pull) — a standing-exchange opening, so the chain opens on 'start neutral'
    (role='anchor'). Phase 1: the MID-chain gap between the pull and the armbar no longer invents
    an intermediate state ('scramble' is gone) — both actions ride the SAME transition, stacked
    in observed order. The chain's LAST action (the armbar) is genuinely terminal, so the WHOLE
    edge resolves through the '$terminal' marker to 'finish'."""
    chain = compile_chain([
        _ev("Guard Pull", "transition"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["start neutral", "finish"]
    assert [s.role for s in chain.states] == ["anchor", "finish"]
    assert all(s.inferred for s in chain.states)
    assert len(chain.edges) == 1
    (edge,) = chain.edges
    assert edge.source_key == "start neutral" and edge.target_key == "finish"
    assert edge.terminal is True
    assert [(a.key, a.inferred) for a in edge.actions] == [
        ("guard pull", False), ("armbar", False),
    ]
    assert not chain.dropped


def test_chain_opening_on_clinch_label_gets_start_neutral() -> None:
    """Lamas CDP (clinch/grip-fighting) is label-, not type-, keyed, so a `control`-typed label
    could open the chain as an ACTION. Since N0 (docs/taxonomy/04_ONTOLOGIA_CANONICA.md) the
    curated `attribution` row wins over the Lamas token for the grips — a collar tie is a place,
    not a move — so 'Collar Tie' is now the chain's FIRST STATE and no anchor is invented. The
    label-keyed opening still exists for the labels `attribution` really does call actions
    ('Arm Drag' under `control`), which is what the second half pins."""
    chain = compile_chain([
        _ev("Collar Tie", "control"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["collar tie", "mount"]
    assert chain.states[0].role is None
    assert not chain.states[0].inferred

    arm_drag = compile_chain([
        _ev("Arm Drag", "control"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)
    assert arm_drag.states[0].node_key == "start neutral"
    assert arm_drag.states[0].role == "anchor"
    assert arm_drag.edges[0].source_key == "start neutral"
    assert arm_drag.edges[0].action_key == "arm drag"


def test_armbar_then_triangle_stack_into_one_path_to_finish() -> None:
    """`$start|submission -> start neutral` opens the chain (owner call 2026-08-27, replacing
    a dedicated 'start engaged' node he judged meaningless). Phase 1: two adjacent submissions
    no longer bridge through an invented 'chained submission' state — they stack into ONE
    transition (`start neutral -> finish`) carrying both actions, in order."""
    chain = compile_chain([
        _ev("Armbar", "submission"),
        _ev("Triangle Choke", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["start neutral", "finish"]
    assert [s.inferred for s in chain.states] == [True, True]
    (edge,) = chain.edges
    assert edge.source_key == "start neutral" and edge.target_key == "finish"
    assert not edge.inferred and edge.terminal
    assert [(a.key, a.source_event_index) for a in edge.actions] == [
        ("armbar", 0), ("triangle choke", 1),
    ]


def test_submission_then_pass_closes_on_start_top_via_role_orientation() -> None:
    """D2's declarative row (`submission|$terminal` -> `finish`) only fires when the chain's
    LAST action is itself a submission. Phase 1: two actions with no real state between them
    always stack into one edge now — there is no more mid-chain state to invent — and rule 6's
    closing anchor is keyed on the LAST action's type: here that's 'pass', which has no
    declarative row of its own ('pass|*' died with 'top transition'). Fase 1b (owner call,
    2026-08-31): the chain no longer closes UNANCHORED — `resolve_anchor_by_role` falls back to
    `attribution.classify('pass', ...).actor_role`, which is 'top' by type default (a pass
    places the passer on top), so the chain closes on 'start top'. `$start|submission -> start
    neutral` still opens the chain."""
    chain = compile_chain([
        _ev("Armbar", "submission"),
        _ev("Guard Pass", "pass"),
    ], inference_table=TABLE)
    assert [s.node_key for s in chain.states] == ["start neutral", "start top"]
    (edge,) = chain.edges
    assert edge.source_key == "start neutral" and edge.target_key == "start top"
    assert edge.terminal is True
    assert [a.key for a in edge.actions] == ["armbar", "guard pass"]


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


def test_mount_then_closed_guard_is_a_reversal_not_a_guard_recovery() -> None:
    """CHANGED BY FASE 2, deliberately. `control|guard -> guard recovery` was the type-keyed
    answer; with orientation it is the owner's own fourth example — "Controle A -> Guarda A
    => Inversão B". The same athlete held mount (top) and is now playing guard (bottom), so
    the opponent reversed her. `guard recovery` survives only where nothing inverted (see
    `test_owner_example_control_a_to_guard_b_is_a_guard_recovery_by_b`).

    The reversal has no NAME here — one side's chain names one athlete — so `actor` is None
    and `actor_is_opponent` carries the ownership instead."""
    chain = compile_chain([
        _ev("Mount", "control"),
        _ev("Closed Guard", "guard"),
    ], inference_table=TABLE)

    edge = [e for e in chain.edges if e.source_key == "mount" and e.target_key == "closed guard"][0]
    assert edge.action_key == "reversal"
    (action,) = edge.actions
    assert action.inferred is True
    assert action.actor is None and action.actor_is_opponent is True


def test_closed_guard_then_mount_is_a_sweep_not_a_guard_exit() -> None:
    """CHANGED BY FASE 2, deliberately. `guard|control -> guard exit` was the type-keyed
    answer; with orientation, playing guard (bottom) and then holding mount (top) is the same
    athlete going bottom-to-top out of her own guard, which is what a sweep IS
    (`attribution._LABEL`'s note on `sweep top position`). `guard exit` survives for a guard
    that ends in a control with NO topology — a standing collar tie is `controlling`, not
    `top`, and the rule refuses to compare the two axes."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)

    edge = [e for e in chain.edges if e.source_key == "closed guard" and e.target_key == "mount"][0]
    assert edge.action_key == "sweep"
    (action,) = edge.actions
    assert action.actor == "a" and action.actor_is_opponent is False


def test_guard_into_a_gripping_control_still_bridges_with_guard_exit() -> None:
    """The `guard exit` row is not dead: `Kimura Grip` is `controlling` (a grip, not a
    topology — `attribution._CONTROL_GRIP`), which lives on the OTHER of attribution's two
    deliberately separate axes, so it is not comparable with the guard's `bottom` and no
    inversion is claimed. The declarative `guard|control` row answers, exactly as before
    Fase 2. (Most of `_CONTROL_GRIP` is a Lamas clinch token and so classifies as an ACTION;
    `Kimura Grip` is one of the few that stays a state.)"""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Kimura Grip", "control"),
    ], inference_table=TABLE)

    edge = [e for e in chain.edges
            if e.source_key == "closed guard" and e.target_key == "kimura grip"][0]
    assert edge.action_key == "guard exit"


# ── Fase 2 — the owner's own examples (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md) ───────────
# Three of these collide under the pre-Fase-2 type-only key (`guard|guard` is both the first
# and the sixth; `control|guard` is both the third and the fourth), which is the whole reason
# the table stopped being the rule. They are written two-sided because that is how the owner
# wrote them; `compile_chain` does not split by actor, so it reads them as given.


def test_owner_example_guard_a_to_guard_b_is_a_sweep_by_a() -> None:
    """A joga guarda, depois B joga guarda => A raspou. Collides with the sixth example under
    the old `guard|guard` key; separated here by the ACTOR of each end."""
    chain = compile_chain([
        _ev("Closed Guard", "guard", actor="a"),
        _ev("Butterfly Guard", "guard", actor="b"),
    ], inference_table=TABLE)
    (edge,) = [e for e in chain.edges if e.target_key == "butterfly guard"]
    (action,) = edge.actions
    assert (action.key, action.actor, action.actor_is_opponent) == ("sweep", "a", False)


def test_owner_example_guard_a_to_control_b_is_a_guard_pass_by_b() -> None:
    """A joga guarda, depois B controla => B passou. Nobody inverted: B was already the one on
    top of A's guard, and moved from "inside the guard" to "controlling"."""
    chain = compile_chain([
        _ev("Closed Guard", "guard", actor="a"),
        _ev("Side Control", "control", actor="b"),
    ], inference_table=TABLE)
    (edge,) = [e for e in chain.edges if e.target_key == "side control"]
    (action,) = edge.actions
    assert (action.key, action.actor, action.actor_is_opponent) == ("guard pass", "b", True)


def test_owner_example_control_a_to_guard_b_is_a_guard_recovery_by_b() -> None:
    """A controla, depois B joga guarda => B recompôs. A is still on top, so nothing inverted —
    and the recovery belongs to the GUARD player, not to the dominant one."""
    chain = compile_chain([
        _ev("Mount", "control", actor="a"),
        _ev("Closed Guard", "guard", actor="b"),
    ], inference_table=TABLE)
    (edge,) = [e for e in chain.edges if e.target_key == "closed guard"]
    (action,) = edge.actions
    assert (action.key, action.actor) == ("guard recovery", "b")


def test_owner_example_control_a_to_guard_a_is_a_reversal_by_the_opponent() -> None:
    """A controla, depois A joga guarda => B inverteu. Same `control|guard` key as the example
    above and the opposite answer — the difference is entirely the actor of the second end."""
    chain = compile_chain([
        _ev("Mount", "control", actor="a"),
        _ev("Closed Guard", "guard", actor="a"),
    ], inference_table=TABLE)
    (edge,) = [e for e in chain.edges if e.target_key == "closed guard"]
    (action,) = edge.actions
    assert action.key == "reversal"
    assert action.actor_is_opponent is True


def test_owner_example_control_a_to_control_b_is_a_reversal_by_b() -> None:
    """A controla, depois B controla => B inverteu. Named here, because both athletes appear."""
    chain = compile_chain([
        _ev("Mount", "control", actor="a"),
        _ev("Side Control", "control", actor="b"),
    ], inference_table=TABLE)
    (edge,) = [e for e in chain.edges if e.target_key == "side control"]
    (action,) = edge.actions
    assert (action.key, action.actor, action.actor_is_opponent) == ("reversal", "b", True)


def test_owner_example_half_guard_a_to_closed_guard_a_is_a_guard_transition() -> None:
    """Meia-guarda A -> guarda fechada A => transição de guarda A. Same `guard|guard` key as
    the first example, opposite answer, separated by the actor."""
    chain = compile_chain([
        _ev("Half Guard", "guard", actor="a"),
        _ev("Closed Guard", "guard", actor="a"),
    ], inference_table=TABLE)
    (edge,) = [e for e in chain.edges if e.target_key == "closed guard"]
    (action,) = edge.actions
    assert (action.key, action.actor) == ("guard transition", "a")


def test_owner_example_side_control_a_to_mount_a_is_a_control_transition() -> None:
    """Side control A -> montada A => troca de controle A. Both ends top, same athlete."""
    chain = compile_chain([
        _ev("Side Control", "control", actor="a"),
        _ev("Mount", "control", actor="a"),
    ], inference_table=TABLE)
    (edge,) = [e for e in chain.edges if e.target_key == "mount"]
    (action,) = edge.actions
    assert (action.key, action.actor) == ("control transition", "a")


# ── Fase 2 — insertion, redundancy, abstention ────────────────────────────────────────────


def test_inferred_action_is_inserted_between_two_observed_ones() -> None:
    """The owner's insertion case. `Pass B` cannot happen with A holding side control, so the
    inversion happened BEFORE it — the generic lands at index 1, and neither observed action
    is reordered, replaced or dropped (contract invariant 3)."""
    chain = compile_chain([
        _ev("Side Control", "control", actor="a"),
        _ev("Kimura", "submission", actor="a"),
        _ev("Guard Pass", "pass", actor="b"),
        _ev("Side Control", "control", actor="b"),
    ], inference_table=TABLE)

    (edge,) = [e for e in chain.edges if e.source_key == "side control"]
    assert [(a.key, a.inferred) for a in edge.actions] == [
        ("kimura", False), ("reversal", True), ("guard pass", False),
    ]
    assert [a.source_event_index for a in edge.actions] == [1, None, 2]
    assert edge.actions[1].actor == "b" and edge.actions[1].actor_is_opponent is True


def test_no_generic_when_an_observed_action_already_explains_the_inversion() -> None:
    """Redundancy (rule 2). Bottom-to-top with an observed sweep in between: the sweep's own
    declared exit orientation IS the inversion, so naming it again would invent a second one.
    The transition carries the observed action alone."""
    chain = compile_chain([
        _ev("Closed Guard", "guard", actor="a"),
        _ev("Hip Bump Sweep", "sweep", actor="a"),
        _ev("Mount", "control", actor="a"),
    ], inference_table=TABLE)

    (edge,) = [e for e in chain.edges if e.source_key == "closed guard"]
    assert [(a.key, a.inferred) for a in edge.actions] == [("hip bump sweep", False)]


def test_actor_inference_abstains_when_the_bout_actor_field_is_unreadable() -> None:
    """`attribution.bout_flags` refuses a bout that files every event under one athlete (20.3%
    of the owner's 281-bout dump, 43.9% of prod). There the `actor` field carries no
    information, so an actor DIFFERENCE is not evidence — the rule falls back to the
    declarative table instead of claiming an inversion it cannot see."""
    events = [
        _ev("Mount", "control", actor="a"),
        _ev("Side Control", "control", actor="b"),
    ]
    claims = compile_chain(events, inference_table=TABLE, actor_readable=True)
    abstains = compile_chain(events, inference_table=TABLE, actor_readable=False)

    assert [e.action_key for e in claims.edges] == ["reversal"]
    assert [e.action_key for e in abstains.edges] == ["control transition"]


def test_compile_two_sided_derives_actor_readable_from_its_own_buckets() -> None:
    """A bout long enough to judge whose every event landed on ONE side is `bout_flags`'
    `one_sided`: the other athlete did not stand still, her side was never recorded."""
    one_sided = [_ev("Mount", "control", actor="x") for _ in range(6)]
    two_sided = [_ev("Mount", "control", actor="x"), _ev("Mount", "control", actor="y")]

    def side_of(ev: Mapping[str, Any]) -> str | None:
        return "a" if ev["actor"] == "x" else "b"

    # Nothing asserted about the edges here — the point is that the derivation runs and does
    # not crash on either shape; the abstention behaviour itself is pinned above.
    assert compile_two_sided(one_sided, side_of, inference_table=TABLE)["b"].edges == []
    assert compile_two_sided(two_sided, side_of, inference_table=TABLE)["a"].states


def test_escape_action_then_submission_action_stack_into_one_edge_to_finish() -> None:
    """Two adjacent ACTIONS with no state between them: an escape (forced-action type) followed
    by a submission. Phase 1: no intermediate state is invented for this any more ('bottom
    transition' is gone) — both actions stack into ONE edge out of the real 'closed guard'
    state, and since the chain's LAST action IS a submission, rule 6's terminal marker still
    resolves the whole path to 'finish'."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Some Escape", "escape"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["closed guard", "finish"]
    (edge,) = [e for e in chain.edges if e.source_key == "closed guard"]
    assert edge.target_key == "finish" and edge.terminal is True
    assert [(a.key, a.inferred) for a in edge.actions] == [
        ("some escape", False), ("armbar", False),
    ]


def test_chain_opening_on_takedown_gets_start_neutral_initial_state() -> None:
    """D2's declarative opening row (owner call, 2026-08-27): a chain whose first action is a
    'takedown' resolves rule 5's gap via the `"$start"` sentinel — `"$start|takedown" ->
    "start neutral"` — mirroring `_CHAIN_END`'s own declarative pattern, no code needed for this
    specific type. 'start neutral' carries role='anchor' and is PREPENDED before the real first
    state, linked by the takedown's own (real, non-inferred) edge."""
    chain = compile_chain([
        _ev("Double Leg Takedown", "takedown"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)

    assert chain.states[0].node_key == "start neutral"
    assert chain.states[0].role == "anchor"
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
    assert chain.states[0].role == "anchor"


def test_chain_opening_on_closed_guard_state_state_is_nascent() -> None:
    """The prepend this test used to pin is GONE (owner call 2026-08-27): reaching a
    chain-opening state through an invented action was a claim the log never made. The state
    opens the chain on its own and carries ``nascent`` — see
    ``test_chain_opening_on_a_state_starts_loose_and_is_marked_nascent`` for the full contract."""
    chain = compile_chain([_ev("Closed Guard", "guard")], inference_table=TABLE)
    assert [s.node_key for s in chain.states] == ["closed guard"]
    assert chain.states[0].nascent is True
    assert not any(s.role == "anchor" for s in chain.states)


def test_chain_opening_on_mount_state_state_is_nascent() -> None:
    """The prepend this test used to pin is GONE (owner call 2026-08-27): reaching a
    chain-opening state through an invented action was a claim the log never made. The state
    opens the chain on its own and carries ``nascent`` — see
    ``test_chain_opening_on_a_state_starts_loose_and_is_marked_nascent`` for the full contract."""
    chain = compile_chain([_ev("Mount", "control")], inference_table=TABLE)
    assert [s.node_key for s in chain.states] == ["mount"]
    assert chain.states[0].nascent is True
    assert not any(s.role == "anchor" for s in chain.states)


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
    """The dropped concept event stays skipped, unaffected by Phase 1. The trailing 'Guard Pass'
    action closes the chain; 'pass' has no declarative closing row ('top transition' is gone),
    but Fase 1b's role-orientation fallback resolves it to 'start top' (a pass places the
    passer on top) — the chain ends anchored, still carrying the real action on its edge."""
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Grip Fighting", "concept"),
        _ev("Guard Pass", "pass"),
    ], inference_table=TABLE)

    assert len(chain.dropped) == 1
    dropped = chain.dropped[0]
    assert dropped.index == 1 and dropped.reason == "transparent"
    assert [s.node_key for s in chain.states] == ["closed guard", "start top"]
    assert chain.states[0].nascent is True  # opens on a real state: no anchor invented
    edge = chain.edges[-1]
    assert edge.source_key == "closed guard" and edge.target_key == "start top"
    assert [a.key for a in edge.actions] == ["guard pass"]
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
    # rule 6: 'pass' has no declarative closing row ('top transition' is gone), but Fase 1b's
    # role-orientation fallback resolves it to 'start top' (a pass places the passer on top) —
    # the live state after it is no longer unknown.
    assert result["b"].state_after_event == {1: "start top"}
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
    assert not any(s.role == "anchor" for s in chain.states)
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
    assert opens_on_action.states[0].role == "anchor"
    assert opens_on_action.edges[0].source_key == opens_on_action.states[0].node_key

    opens_on_state = compile_chain([_ev("Mount", "control")], inference_table=TABLE)
    assert not any(s.role == "anchor" for s in opens_on_state.states)
    assert opens_on_state.edges == []
