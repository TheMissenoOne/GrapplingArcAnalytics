"""Phase 1 of the actions/states migration — the shadow chain compiler.

Turns one contiguous sequence of events (a chain / round — the CALLER decides the cut,
same convention as ``decision_flow.extract_patterns``) into a walk of state NODES linked by
action EDGES, per D1's classifier (``analysis.taxonomy_kind.kind_of``) and D2's structural
inference table (``analysis.taxonomy_kind.load_inference_table`` +
``infer_state_for_action_pair``/``infer_action_for_state_pair``). Purely structural — no
probability, no success scoring (that is a separate design, D7, in progress: this module
only preserves ``successful`` on the ORIGINAL event, reachable via
``ChainEdge.source_event_index`` into the caller's own ``events`` list).

**The gap-filling trick (rules 3-6 below) is ONE mechanism, not four.** D2's tables are keyed
`"type_a|type_b"` with `"*"` wildcards, resolved by ``resolve_pair``: exact pair > one side
fixed + other side wildcarded > the ``"*|*"`` fallback. Every place this module needs a state
or action that no event supplies — bridging two adjacent actions (rule 3), bridging two
adjacent states (rule 4), the state before a chain that OPENS on an action (rule 5), the state
after a chain that CLOSES on an action (rule 6) — calls the *same* two D2 functions, using the
literal string ``"*"`` for whichever side has no event to read a type from:

    rule 3 (action, action):  infer_state_for_action_pair(table, prev_type, next_type)
    rule 4 (state, state):    infer_action_for_state_pair(table, prev_type, next_type)
    rule 5 (chain opens on action):  infer_state_for_action_pair(table, "*", first_type)
    rule 6 (chain closes on action): infer_state_for_action_pair(table, last_type, "*")

Rule 5's ``"*"`` on the left never matches any table row with a fixed left side (every
`action_pair_to_state` key either names both sides or wildcards the RIGHT side), so it always
falls through to the ``"*|*"`` fallback (``scramble``) today — which is also rule 5's own
spec: "if there's no confidently-canonical standing/em-pé node, use the fallback." Checked
against both ``data/rating/taxonomy_kind_golden.json`` (the 141-entry app library) and
``analysis.names.CANONICAL_LABELS``: neither carries a standing/em-pé node, so the fallback
is what fires. If the table ever grows a row shaped ``"*|takedown"`` (a specific "what comes
before a takedown" answer), rule 5 picks it up with no code change here.

Rule 6's ``"*"`` on the right DOES sometimes match a fixed-left row exactly (e.g.
``"takedown|*" → "top transition"``) — a terminal takedown resolves to "top transition", not
the bare fallback, because the table already says what a takedown structurally leads to
regardless of what's next. That is rule 6's own spec: "create a specific terminal state ONLY IF
the table has a mapping, else the generic fallback" — ``resolve_pair``'s own exact-then-wildcard-
then-fallback order already implements exactly that priority, so no separate terminal-vs-
fallback branch is written here.

**The genuinely-terminal marker (owner call, 2026-08-27): a chain ending on ``submission``
resolves to the generic state ``finish``, not ``scramble``.** A row shaped ``"submission|*"``
cannot express this alone — D2's row-side ``"*"`` matches ANY right-hand value (see
``resolve_pair``), so it would also swallow a MID-chain submission followed by a real next
action, which must keep falling through to ``"*|*"`` unchanged (D2's own spec: only the
TERMINAL submission changes). So the trailing/closing call only — where ``compile_chain`` has
confirmed there truly is no next event — passes the literal ``"$terminal"`` sentinel instead of
``"*"`` for the right side, letting the table carry an EXACT row, ``"submission|$terminal" →
finish``, that only an actually-terminal call can hit (``resolve_pair`` checks the exact pair
before the wildcard loop). Every other ``"type|*"`` row (`takedown`/`sweep`/`pass`) is
unaffected — their row-side ``"*"`` still matches ``"$terminal"`` the same way it matches any
other right-hand value, so a terminal takedown/sweep/pass still resolves exactly as before.
Rule 5's opening call keeps using the plain ``"*"`` sentinel — the marker distinction only
matters on the closing side, which is the only place D2 needs to tell "no more events" apart
from "next event's type happens to be unknown here".

Actor model (rule 8): ``compile_chain`` assumes ``events`` is already ONE actor's own ordered
flow (the within-actor grouping ``transitions/build_graph.py`` calls ``by_actor``) — it does
not itself split or validate actor consistency. ``compile_two_sided`` is the thin wrapper for
the two-fighter match case: it buckets by ``side_of(event) -> 'a' | 'b' | None`` (preserving
relative order within each side) and compiles each side independently; events with no side are
never silently lost — they land in the ``'dropped'`` pseudo-side of the returned dict, a
``CompiledChain`` with empty ``states``/``edges`` and just the audit trail.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from analysis.names import _normalize_name, canonicalize
from analysis.taxonomy_kind import (
    infer_action_for_state_pair,
    infer_state_for_action_pair,
    kind_of,
    load_inference_table,
)

# Rule 6's genuinely-terminal marker (module docstring) — distinct from the plain ``"*"``
# used everywhere else, so D2 can express "the chain truly ends on this action type" without
# also matching every mid-chain pair whose next type happens to be unknown.
_CHAIN_END = "$terminal"


@dataclass(frozen=True)
class ChainState:
    node_key: str
    label: str
    type: str
    actor: str | None
    inferred: bool


@dataclass(frozen=True)
class ChainEdge:
    source_key: str
    target_key: str
    action_key: str
    action_label: str
    action_type: str
    actor: str | None
    inferred: bool
    terminal: bool
    source_event_index: int | None


@dataclass(frozen=True)
class DroppedEvent:
    index: int
    label: str
    event_type: str
    reason: str


@dataclass(frozen=True)
class CompiledChain:
    states: list[ChainState]
    edges: list[ChainEdge]
    dropped: list[DroppedEvent]
    # event index (into the ORIGINAL ``events`` this chain was built from — already rewritten
    # by ``compile_two_sided``, same convention as ``ChainEdge.source_event_index``) -> the
    # node_key that is the CURRENT/live state right after that event was processed. Used by
    # callers that need to bridge across actors at a point in the raw stream (e.g. handover
    # edges in ``scripts/render_map_prototypes.py``) without re-deriving the walk themselves.
    state_after_event: dict[int, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class _Pending:
    """A real action event, seen but not yet resolved to a target state."""
    source_key: str
    action_key: str
    label: str
    type: str
    actor: str | None
    index: int


def _label_of(event: Mapping[str, Any]) -> str:
    return str(event.get("label", event.get("event_label", "")) or "")


def _type_of(event: Mapping[str, Any]) -> str:
    return str(event.get("type", event.get("event_type", "")) or "").strip().lower()


def _edge_from_pending(p: _Pending, *, target_key: str, terminal: bool) -> ChainEdge:
    return ChainEdge(
        source_key=p.source_key, target_key=target_key, action_key=p.action_key,
        action_label=p.label, action_type=p.type, actor=p.actor,
        inferred=False, terminal=terminal, source_event_index=p.index,
    )


def compile_chain(
    events: Sequence[Mapping[str, Any]],
    *,
    actor_of: Callable[[Mapping[str, Any]], str | None] | None = None,
    inference_table: Mapping[str, Any] | None = None,
) -> CompiledChain:
    """One actor's own ordered event stream -> a walk of state nodes + action edges.

    ``events`` items are tolerant dicts: ``label``/``event_label``, ``type``/``event_type``,
    optional ``actor`` (read via ``actor_of`` when given, else ``event.get("actor")``).
    ``successful``/``ts``/``timestamp`` are NOT read here — see the module docstring on D7.
    """
    table = inference_table if inference_table is not None else load_inference_table()
    states: list[ChainState] = []
    edges: list[ChainEdge] = []
    dropped: list[DroppedEvent] = []
    state_after_event: dict[int, str | None] = {}

    prev_kind: str | None = None          # 'state' | 'action' | None (nothing seen yet)
    prev_state_key: str | None = None
    prev_state_type: str = ""
    prev_action_type: str = ""
    last_actor: str | None = None
    pending: _Pending | None = None

    for idx, ev in enumerate(events):
        label = _label_of(ev)
        etype = _type_of(ev)
        actor = actor_of(ev) if actor_of is not None else ev.get("actor")
        kind = kind_of(label, etype)

        if kind == "transparent":
            dropped.append(DroppedEvent(index=idx, label=label, event_type=etype,
                                         reason="transparent"))
            state_after_event[idx] = prev_state_key
            continue

        key = canonicalize(_normalize_name(label))

        if kind == "state":
            if prev_kind == "action" and pending is not None:
                edges.append(_edge_from_pending(pending, target_key=key, terminal=False))
                pending = None
            elif prev_kind == "state":
                act = infer_action_for_state_pair(table, prev_state_type, etype)
                edges.append(ChainEdge(
                    source_key=prev_state_key or "", target_key=key,
                    action_key=act["action_key"], action_label=act["label"],
                    action_type=act["type"], actor=last_actor,
                    inferred=True, terminal=False, source_event_index=None,
                ))
            states.append(ChainState(node_key=key, label=label, type=etype, actor=actor,
                                      inferred=False))
            prev_state_key, prev_state_type = key, etype
            prev_kind = "state"
            last_actor = actor
            state_after_event[idx] = prev_state_key
            continue

        # kind == "action"
        if prev_kind == "action" and pending is not None:
            st = infer_state_for_action_pair(table, prev_action_type, etype)
            states.append(ChainState(node_key=st["node_key"], label=st["label"],
                                      type=st["type"], actor=last_actor, inferred=True))
            edges.append(_edge_from_pending(pending, target_key=st["node_key"], terminal=False))
            prev_state_key, prev_state_type = st["node_key"], st["type"]
        elif prev_kind is None:
            st = infer_state_for_action_pair(table, "*", etype)
            states.append(ChainState(node_key=st["node_key"], label=st["label"],
                                      type=st["type"], actor=actor, inferred=True))
            prev_state_key, prev_state_type = st["node_key"], st["type"]
        # prev_kind == "state": prev_state_key/prev_state_type already current.

        pending = _Pending(source_key=prev_state_key or "", action_key=key, label=label,
                            type=etype, actor=actor, index=idx)
        prev_kind = "action"
        prev_action_type = etype
        last_actor = actor
        state_after_event[idx] = prev_state_key

    if pending is not None:
        st = infer_state_for_action_pair(table, pending.type, _CHAIN_END)
        states.append(ChainState(node_key=st["node_key"], label=st["label"], type=st["type"],
                                  actor=pending.actor, inferred=True))
        edges.append(_edge_from_pending(pending, target_key=st["node_key"], terminal=True))
        state_after_event[pending.index] = st["node_key"]

    return CompiledChain(states=states, edges=edges, dropped=dropped,
                          state_after_event=state_after_event)


def compile_two_sided(
    events: Sequence[Mapping[str, Any]],
    side_of: Callable[[Mapping[str, Any]], str | None],
    *,
    actor_of: Callable[[Mapping[str, Any]], str | None] | None = None,
    inference_table: Mapping[str, Any] | None = None,
) -> dict[str, CompiledChain]:
    """Match-shaped convenience over ``compile_chain``: split by ``side_of`` (returning
    ``'a'``/``'b'``/anything else), preserving each side's own relative order, then compile
    each side independently. ``source_event_index``/dropped-event ``index`` are rewritten back
    to the ORIGINAL position in ``events`` (not the per-side sub-list) so a caller can always
    trace an edge back to the raw sequence. Events with no side land in ``result['dropped']``
    (a ``CompiledChain`` carrying nothing but the audit trail) — never silently discarded.
    """
    table = inference_table if inference_table is not None else load_inference_table()
    buckets: dict[str, list[tuple[int, Mapping[str, Any]]]] = {"a": [], "b": []}
    unassigned: list[DroppedEvent] = []
    for idx, ev in enumerate(events):
        side = side_of(ev)
        if side not in ("a", "b"):
            unassigned.append(DroppedEvent(index=idx, label=_label_of(ev), event_type=_type_of(ev),
                                            reason="no_side"))
            continue
        buckets[side].append((idx, ev))

    result: dict[str, CompiledChain] = {}
    for side in ("a", "b"):
        pairs = buckets[side]
        orig_idx = [i for i, _ in pairs]
        sub_events = [e for _, e in pairs]
        compiled = compile_chain(sub_events, actor_of=actor_of, inference_table=table)
        result[side] = CompiledChain(
            states=compiled.states,
            edges=[
                replace(e, source_event_index=orig_idx[e.source_event_index]
                        if e.source_event_index is not None else None)
                for e in compiled.edges
            ],
            dropped=[replace(d, index=orig_idx[d.index]) for d in compiled.dropped],
            state_after_event={orig_idx[i]: v for i, v in compiled.state_after_event.items()},
        )
    result["dropped"] = CompiledChain(states=[], edges=[], dropped=unassigned)
    return result
