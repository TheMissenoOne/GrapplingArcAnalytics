"""Chain compiler (Phase 1, actions/states migration) — structural, deterministic, zero prob."""
from __future__ import annotations

from analysis.chain_compiler import compile_chain, compile_two_sided
from analysis.taxonomy_kind import load_inference_table

TABLE = load_inference_table()


def _ev(label, type_, actor="a", **kw):
    return {"label": label, "type": type_, "actor": actor, **kw}


def test_guard_pull_then_armlock_all_scramble_fallback():
    """'Guard Pull' is type 'transition' in this corpus (D1 forces transition -> 'action'), NOT
    type 'guard' — so the (guard, submission) pair the ticket names never actually arises. The
    real pair resolved is (transition, submission), which D2's action_pair_to_state table has no
    specific row for (only submission|submission, takedown|*, sweep|*, pass|*), so every gap —
    before the pull, between pull and armbar, after the armbar — falls through to the '*|*'
    fallback: 'scramble'. Documented finding, not a guess."""
    chain = compile_chain([
        _ev("Guard Pull", "transition"),
        _ev("Armbar", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["scramble", "scramble", "scramble"]
    assert all(s.inferred for s in chain.states)
    assert len(chain.edges) == 2
    e0, e1 = chain.edges
    assert (e0.action_key, e0.inferred, e0.terminal) == ("guard pull", False, False)
    assert e0.source_key == "scramble" and e0.target_key == "scramble"
    assert (e1.action_key, e1.inferred, e1.terminal) == ("armbar", False, True)
    assert not chain.dropped


def test_armbar_then_triangle_chains_through_chained_submission():
    chain = compile_chain([
        _ev("Armbar", "submission"),
        _ev("Triangle Choke", "submission"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["scramble", "chained submission", "scramble"]
    assert [s.inferred for s in chain.states] == [True, True, True]
    e0, e1 = chain.edges
    assert e0.action_key == "armbar" and e0.target_key == "chained submission"
    assert not e0.inferred and not e0.terminal
    assert e1.action_key == "triangle choke" and e1.source_key == "chained submission"
    assert not e1.inferred and e1.terminal


def test_kguard_then_5050_guard_bridges_with_guard_transition():
    chain = compile_chain([
        _ev("K-Guard", "guard"),
        _ev("50/50 Guard", "guard"),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["kguard", "5050 guard"]
    assert not any(s.inferred for s in chain.states)
    assert len(chain.edges) == 1
    edge = chain.edges[0]
    assert edge.action_key == "guard transition"
    assert edge.inferred is True
    assert edge.terminal is False
    assert edge.source_event_index is None
    assert edge.source_key == "kguard" and edge.target_key == "5050 guard"


def test_chain_opening_on_takedown_gets_scramble_initial_state():
    """No canonical standing/em-pe node exists in the 141-entry app library (checked against
    data/rating/taxonomy_kind_golden.json and analysis.names.CANONICAL_LABELS) so rule 5's
    fallback fires: initial state is the generic 'scramble', same mechanism as every other gap."""
    chain = compile_chain([
        _ev("Double Leg Takedown", "takedown"),
        _ev("Mount", "control"),
    ], inference_table=TABLE)

    assert chain.states[0].node_key == "scramble"
    assert chain.states[0].inferred is True
    assert [s.node_key for s in chain.states] == ["scramble", "mount"]
    assert chain.states[1].inferred is False
    assert len(chain.edges) == 1
    edge = chain.edges[0]
    assert edge.action_key == "double leg takedown"
    assert edge.source_key == "scramble" and edge.target_key == "mount"
    assert not edge.inferred and not edge.terminal


def test_chain_ending_in_submission_is_terminal():
    chain = compile_chain([
        _ev("Mount", "control"),
        _ev("Armbar", "submission", successful=True),
    ], inference_table=TABLE)

    assert [s.node_key for s in chain.states] == ["mount", "scramble"]
    assert chain.states[1].inferred is True
    edge = chain.edges[0]
    assert edge.terminal is True
    assert edge.action_key == "armbar"
    assert edge.source_key == "mount"
    assert edge.source_event_index == 1


def test_concept_event_dropped_chain_stays_intact():
    chain = compile_chain([
        _ev("Closed Guard", "guard"),
        _ev("Grip Fighting", "concept"),
        _ev("Guard Pass", "pass"),
    ], inference_table=TABLE)

    assert len(chain.dropped) == 1
    dropped = chain.dropped[0]
    assert dropped.index == 1 and dropped.reason == "transparent"
    assert [s.node_key for s in chain.states] == ["closed guard", "top transition"]
    assert chain.states[1].inferred is True
    edge = chain.edges[0]
    assert edge.source_key == "closed guard" and edge.action_key == "guard pass"
    assert edge.terminal is True


def test_compile_two_sided_splits_and_drops_unassigned_with_original_index():
    events = [
        _ev("Closed Guard", "guard", actor="x"),
        _ev("Guard Pass", "pass", actor="y"),
        _ev("Round End", "reset", actor="referee"),
    ]

    def side_of(ev):
        if ev["actor"] == "x":
            return "a"
        if ev["actor"] == "y":
            return "b"
        return None

    result = compile_two_sided(events, side_of, inference_table=TABLE)

    assert set(result) == {"a", "b", "dropped"}
    a = result["a"]
    assert [s.node_key for s in a.states] == ["closed guard"]
    assert not a.edges and not a.dropped

    b = result["b"]
    assert b.states[0].node_key == "scramble"  # rule 5 fallback for the lone Guard Pass
    assert b.edges[0].source_event_index == 1  # rewritten to the ORIGINAL events index

    d = result["dropped"]
    assert not d.states and not d.edges
    assert len(d.dropped) == 1
    assert d.dropped[0].index == 2 and d.dropped[0].reason == "no_side"


def test_determinism():
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
