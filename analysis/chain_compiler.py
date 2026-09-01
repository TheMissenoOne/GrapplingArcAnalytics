"""Phase 1 of the actions/states migration — the shadow chain compiler.

Turns one contiguous sequence of events (a chain / round — the CALLER decides the cut,
same convention as ``decision_flow.extract_patterns``) into a walk of state NODES linked by
action EDGES, per D1's classifier (``analysis.taxonomy_kind.kind_of_entry`` — resolves the
event's label through the App technique library before classifying, so a stale logged ``type``
never misreads an action as a state) and D2's structural
inference table (``analysis.taxonomy_kind.load_inference_table`` +
``infer_state_for_action_pair``/``infer_action_for_state_pair``). Purely structural — no
probability, no success scoring (that is a separate design, D7, in progress: this module
only preserves ``successful`` on the ORIGINAL event, reachable via
``ChainAction.source_event_index`` into the caller's own ``events`` list).

**Phase 1 (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md): an edge is a PATH, not a single hop.**
Consecutive ACTIONS with no observed state between them no longer invent an intermediate state
(the old "rule 3") — they stack, in observed order, into the SAME ``ChainEdge.actions`` tuple,
via the ``_Pending`` buffer (now a list accumulator, not a single slot). A transition closes
with every action that accumulated between two observed states (or the chain's own open/close).
Two gap-filling mechanisms remain, both routed through D2's tables (keyed ``"type_a|type_b"``
with ``"*"`` wildcards, resolved by ``resolve_pair``: exact pair > one side fixed + other side
wildcarded — the ``"*|*"`` catch-all is GONE, ``resolve_pair`` now returns ``None`` when nothing
matches):

    the action rule (Fase 2): ``taxonomy_kind.infer_transition_actions(table, source, target,
        observed, actor_readable=…)`` — see "The action rule" below. ``state_pair_to_action``
        is no longer the rule, only its vocabulary and its answer for a pair that carries no
        positional claim (it keeps its ``"*|*" -> "transition"`` fallback, so that path always
        resolves).
    anchor (chain opens/closes on an action): infer_state_for_action_pair(table, "$start"/type,
        type/"$terminal"), falling back to ``taxonomy_kind.resolve_anchor_by_role`` when the
        table has no declarative row
        The presentation FRAME, not a technique-sequence inference (module docstring further
        down, "Perspective, and the role field") — the owner's explicit exception to "never
        infer a state". Declarative rows first (``"$start|takedown"``, ``"submission|$terminal"``,
        …); Fase 1b (owner call, 2026-08-31) closed the remaining gap: an open/close end the
        table doesn't name resolves via the last/first action's own curated actor-role
        orientation instead of going unanchored — a chain end is never empty any more.

Every other action-action or action-state adjacency needs NO inference at all any more: the
actions just accumulate in the buffer until a real state (or the end of the chain) closes the
edge. This is what made the four ``"*|*"``-fed generic states (``chained submission``, ``top
transition``, ``bottom transition``, ``scramble``) dead: they existed ONLY to give the old
per-hop model somewhere to land between actions; a path model has nowhere left that needs one.

**The genuinely-terminal marker (owner call, 2026-08-27): a chain ending on ``submission``
resolves to the generic state ``finish``, never left unanchored.** A row shaped
``"submission|*"`` cannot express this alone — D2's row-side ``"*"`` matches ANY right-hand
value (see ``resolve_pair``), so it would also swallow a MID-chain submission followed by a real
next action. Phase 1 makes that concern moot for MOST types (the mid-chain case no longer
resolves a state at all, ever — the actions just stack), but ``submission|$terminal`` still
needs to be distinct from an ordinary ``submission|<next-type>`` pair for the SAME reason it
always did: only an actually-terminal call passes the literal ``"$terminal"`` sentinel (never
``"*"``) for the right side, so ``resolve_pair``'s exact-then-wildcard order can tell "no more
events" apart from "next event's type happens to be unknown here" — but since there is no more
``"*|*"`` catch-all, no OTHER closing type has a declarative row of its own; Fase 1b (below)
resolves every one of them by role instead.

**Anchor nodes (owner call, 2026-08-27; revised 2026-08-31 — anchors now serve BOTH ends).**
A chain's opening gap was, until Phase 1, the bare ``"*|*"`` fallback (``scramble``) when
nothing more specific matched — semantically poor for a chain that visibly opens standing, on
top, or on bottom. Three curated ``generic_states`` entries fill it, named by ORIENTATION (not
gesture — the renderer anchors on orientation: neutral in the middle, top up, bottom down),
each carrying ``"role": "anchor"`` (renamed from ``"start"`` 2026-08-31: the SAME three nodes
now also close a chain that ends unanchored, not just open one — a chain ending in a sweep
lands on ``start bottom``/``start top`` exactly the same node an opening sweep would land on).
``finish`` keeps its own ``"role": "finish"`` and stays exclusive to a chain ending on
``submission``.

- ``start neutral`` — the chain opens/closes on a standing exchange: rule 5's declarative
  ``"$start|takedown"``/``"$start|transition"`` rows (same mechanism as ``"$terminal"``), the
  first action's ``lamas_chain.lamas_state`` reading ``"PGD"`` (guard pull) or ``"CDP"``
  (clinch/grip-fighting) — both LABEL-keyed, not type-keyed, so the table cannot express them by
  type alone and ``_opening_state`` checks ``lamas_state`` directly — or (Fase 1b) the
  orientation-by-role fallback below reading ``neutral``/``unknown``/``executor``/``defender``/
  ``controlling``/``controlled``.
- ``start top`` / ``start bottom`` — reached the same way as ``start neutral``: as the inferred
  SOURCE of a chain-opening ACTION, never prepended to a chain-opening STATE. That prepending
  existed briefly and was removed (owner call 2026-08-27): "costas não deveria ser presumido
  como precedido por top start" — reaching a state through an action nobody logged is an
  invention, and only actions connect to an anchor. A chain that opens on a real state opens
  there, and that state carries ``nascent=True`` so a consumer can say so.

**Fase 1b — a chain end never goes unanchored any more.** When neither a declarative table row
nor the PGD/CDP check resolves an OPENING, ``taxonomy_kind.resolve_anchor_by_role`` resolves it:
the curated ``analysis.attribution.classify(type, label).actor_role`` for that action — ``TOP``
-> ``start top``, ``BOTTOM`` -> ``start bottom``, anything else -> ``start neutral``. ``classify``
always returns a role, so this always resolves — the empty ``""``-keyed phantom node
(``source_key``/``target_key`` = ``""``) this replaced is GONE; the compiler emits zero edges
with an empty endpoint (``tests/test_actions_parity.py``).

**Fase 2 revised the CLOSING end (owner call, 2026-08-31).** Fase 1b used the same
actor-role reading on both ends and §7 of the contract doc recorded the resulting defect:
``actor_role`` answers ``executor`` for ``sweep``/``escape``/``takedown``, which is a RELATION
and not a position, so 146 of the corpus's 160 chain closes landed on ``start neutral`` — the
node became the graph's artificial number two (degree 326 against ``mount``'s 349). Closings now
go through ``taxonomy_kind.resolve_closing_anchor``, reading the new curated
``action_exit_orientation`` block: a sweep and a takedown both END on top (31 + 18 closings moved
to ``start top``), a guard pull ends on the bottom. ``escape`` stays ``neutral`` and that is
MEASURED, not a shrug — 75 of the corpus's 83 escape events are literally "Escape to Standing" or
"Stand-up Escape", i.e. escapes to the FEET, which contradicts the illustration in the owner's
own plan. ``start neutral`` fell 326 -> 277.

**The action rule (Fase 2).** ``state_pair_to_action`` could not express the owner's own six
examples: keyed by TYPE alone, ``guard|guard`` is both "Guarda A -> Guarda B => Raspagem A" and
"Meia-Guarda A -> Guarda Fechada A => Transição de Guarda". The decision moved into
``taxonomy_kind.infer_transition_actions``, which reads each end's ACTOR and ORIENTATION as well
as its type, plus the buffer of already-observed actions. Its guarantees, and the reasons:

- Observed actions are immutable in order and position; inference only INSERTS between them.
- With an EMPTY buffer it always yields exactly one action (nodes cannot chain to nodes) — the
  degenerate case of the same walk, and where the old rule 4 lived.
- With a non-empty buffer it inserts AT MOST ONE action, and only when the two OBSERVED endpoint
  states invert on the same axis. An observed action's EXIT orientation may only SUPPRESS that
  inference (redundancy — a wrestle-up out of guard IS the bottom-to-top move); it may never
  create one, because 196 of the corpus's 228 ``sweep`` events carry ``successful = NULL`` and
  reading an attempt's exit as a fact is the D7 line this module does not cross. A first cut that
  advanced a rolling position on every exit manufactured 160 extra actions, oscillating
  ``closed guard --[sweep, reversal, sweep, reversal, …]--> closed guard`` on repeated logs.
- Actor inference is gated on ``actor_readable`` (below): where the ``actor`` field carries no
  information, an actor DIFFERENCE is not evidence and the rule falls back to the table.

**Perspective, and the ``role`` field.** ``compile_chain`` is actor-agnostic — a ``role='anchor'``
node's own ``ChainState.actor`` is whichever event supplied it (same convention as every other
inferred node here), NOT a perspective claim. The CONSUMER (renderer/dossier) must always
qualify a ``role='anchor'`` node on the chain OWNER's own side, never the opponent's — it is
where THIS chain's flow begins or ends, by construction, regardless of whose name is in
``actor``. ``role='finish'`` (the pre-existing ``finish`` node, now carrying the same field)
remains per-actor as before — whoever's submission ended the chain.
``analysis.taxonomy_kind.role_of`` is the standalone lookup for a caller holding only a
node_key.

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
from typing import Any, cast

from analysis.attribution import MIN_EVENTS_FOR_ONE_SIDED
from analysis.lamas_chain import lamas_state
from analysis.names import _normalize_name, canonicalize
from analysis.taxonomy_kind import (
    InferredInsert,
    infer_state_for_action_pair,
    infer_transition_actions,
    kind_of_entry,
    load_inference_table,
    resolve_anchor_by_role,
    resolve_closing_anchor,
)

# Rule 6's genuinely-terminal marker (module docstring) — distinct from the plain ``"*"``
# used everywhere else, so D2 can express "the chain truly ends on this action type" without
# also matching every mid-chain pair whose next type happens to be unknown.
_CHAIN_END = "$terminal"

# Rule 5's opening marker (owner call, 2026-08-27), mirroring ``_CHAIN_END``: a fixed left side
# ``resolve_pair`` can match exactly, so D2 can say a given first-action TYPE always opens on a
# specific generic state (``"$start|takedown" -> "start neutral"``) without also swallowing an
# unrelated ``"*|<type>"`` mid-chain row. No row for a type falls straight through to the
# existing ``"*|*"`` fallback — same as before this marker existed.
_CHAIN_START = "$start"


@dataclass(frozen=True)
class ChainState:
    node_key: str
    label: str
    type: str
    actor: str | None
    inferred: bool
    # 'anchor' | 'finish' | None — set only for D2 generic-state table entries that carry the
    # field (module docstring, "Perspective, and the role field"); a REAL (non-inferred) state
    # never carries it. Perspective is the CONSUMER's job, not this dataclass's: qualify
    # role='anchor' always on the chain owner's own side, never the opponent's.
    role: str | None = None
    # True when the chain simply BEGINS at this state — no action preceded it and none was
    # invented (owner call 2026-08-27). An anchor exists to be an ACTION's missing source/target;
    # a state that opens a chain needs no source, so it is marked rather than wired.
    nascent: bool = False


@dataclass(frozen=True)
class ChainAction:
    """Phase 0 of the actions/states migration (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md):
    one action occurrence riding a ``ChainEdge``. Today's edges carry exactly one — the
    compiler still emits single-action chains, byte-identical to before this dataclass
    existed (``tests/test_actions_parity.py``). ``key``/``label``/``type`` are the same
    triple ``ChainEdge.action_key``/``action_label``/``action_type`` always meant."""
    key: str
    label: str
    type: str
    actor: str | None
    inferred: bool
    source_event_index: int | None
    # Fase 2: set only on an INFERRED action the rule attributes to the athlete this chain is
    # NOT about ("Controle A -> Guarda A => Inversão B"). A chain compiled per side names one
    # athlete, so that owner is real but unnameable here — ``actor`` carries the name only when
    # the input names both, this flag carries whose it is either way. Additive with a default:
    # every existing construction site and reader is unaffected.
    actor_is_opponent: bool = False


@dataclass(frozen=True)
class ChainEdge:
    """An edge = an ordered path of ``actions`` between two states (the target shape,
    ``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md``). Phase 0: ``actions`` is always a
    single-element tuple — the scalar ``action_key``/``action_label``/``action_type``/
    ``actor``/``inferred``/``source_event_index`` properties below are the compatibility
    adapter every existing consumer still reads, derived from ``actions[0]``. Do not add a
    second element to ``actions`` before Phase 1+ migrates those consumers — see the doc's
    inventory of who still depends on the scalar view (and on the resulting index-0)."""
    source_key: str
    target_key: str
    actions: tuple[ChainAction, ...]
    terminal: bool

    @property
    def action_key(self) -> str:
        return self.actions[0].key

    @property
    def action_label(self) -> str:
        return self.actions[0].label

    @property
    def action_type(self) -> str:
        return self.actions[0].type

    @property
    def actor(self) -> str | None:
        return self.actions[0].actor

    @property
    def inferred(self) -> bool:
        return self.actions[0].inferred

    @property
    def source_event_index(self) -> int | None:
        return self.actions[0].source_event_index


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
    """Phase 1: real action events, seen but not yet resolved to a target state — a LIST
    accumulator, not a single slot. Every action observed since the last real state (or the
    chain's own open) rides the same buffer; the transition that eventually closes it carries
    ALL of them, in observed order (module docstring, "an edge is a PATH, not a single hop").

    Fase 2 also keeps the SOURCE state's own ``(type, label, actor)``, not just its key: the
    inference rule reads both ends' orientation and actor, and a bare node_key carries
    neither."""
    source_key: str
    source_type: str
    source_label: str
    source_actor: str | None
    actions: list[ChainAction] = field(default_factory=list)


def _label_of(event: Mapping[str, Any]) -> str:
    return str(event.get("label", event.get("event_label", "")) or "")


def _type_of(event: Mapping[str, Any]) -> str:
    return str(event.get("type", event.get("event_type", "")) or "").strip().lower()


def _opening_state(table: Mapping[str, Any], label: str, etype: str) -> dict[str, Any]:
    """The presentation anchor before a chain that opens on an ACTION. Declarative first via
    the ``"$start"`` sentinel (a type the table names explicitly resolves with no code change
    here); guard-pull/clinch opens are label-, not type-, keyed (Lamas ``PGD``/``CDP``) so the
    table cannot express them by type alone — caught here via ``lamas_state`` before falling
    through to the table. Fase 1b (owner call, 2026-08-31): when NEITHER resolves, falls back
    to ``resolve_anchor_by_role`` — the same orientation-by-role mechanism the closing anchor
    uses — so an opening is never left unanchored (empty ``source_key``). See the module
    docstring."""
    if lamas_state({"type": etype, "label": label}) in ("PGD", "CDP"):
        return cast(dict[str, Any], table["generic_states"]["start neutral"])
    declared = infer_state_for_action_pair(table, _CHAIN_START, etype)
    if declared is not None:
        return declared
    return resolve_anchor_by_role(table, etype, label)


def _edge_from_pending(p: _Pending, *, target_key: str, terminal: bool) -> ChainEdge:
    return ChainEdge(source_key=p.source_key, target_key=target_key,
                      actions=tuple(p.actions), terminal=terminal)


def _splice(observed: list[ChainAction], inserts: list[InferredInsert]) -> tuple[ChainAction, ...]:
    """Fase 2: fold the rule's inferred actions into the observed buffer WITHOUT reordering or
    replacing any of them — ``InferredInsert.index`` is a position in the ORIGINAL buffer to
    insert before, so a generic can land in the middle of an observed pair (contract invariant
    3, ``docs/taxonomy/03_ARESTA_COMO_CAMINHO.md``: nobody may depend on an action's index)."""
    out: list[ChainAction] = []
    by_index: dict[int, list[InferredInsert]] = {}
    for ins in inserts:
        by_index.setdefault(ins.index, []).append(ins)
    for i in range(len(observed) + 1):
        for ins in by_index.get(i, []):
            out.append(ChainAction(key=ins.entry["action_key"], label=ins.entry["label"],
                                    type=ins.entry["type"], actor=ins.actor,
                                    inferred=True, source_event_index=None,
                                    actor_is_opponent=ins.is_opponent))
        if i < len(observed):
            out.append(observed[i])
    return tuple(out)


def compile_chain(
    events: Sequence[Mapping[str, Any]],
    *,
    actor_of: Callable[[Mapping[str, Any]], str | None] | None = None,
    inference_table: Mapping[str, Any] | None = None,
    actor_readable: bool = True,
) -> CompiledChain:
    """One actor's own ordered event stream -> a walk of state nodes + action-path edges.

    ``events`` items are tolerant dicts: ``label``/``event_label``, ``type``/``event_type``,
    optional ``actor`` (read via ``actor_of`` when given, else ``event.get("actor")``).
    ``successful``/``ts``/``timestamp`` are NOT read here — see the module docstring on D7.

    ``actor_readable`` is ``attribution.bout_flags``' verdict, passed IN rather than re-derived:
    ``False`` means this bout's ``actor`` field carries no information (43.9% of the prod corpus,
    20.3% of the owner's 281-bout dump, file every event under one athlete) and the Fase 2 rule
    must not read an actor DIFFERENCE as evidence of an inversion. ``compile_two_sided`` derives
    it from its own buckets; a caller holding real ``bout_flags`` can pass ``role_reliable``.
    """
    table = inference_table if inference_table is not None else load_inference_table()
    states: list[ChainState] = []
    edges: list[ChainEdge] = []
    dropped: list[DroppedEvent] = []
    state_after_event: dict[int, str | None] = {}

    prev_kind: str | None = None          # 'state' | 'action' | None (nothing seen yet)
    prev_state_key: str | None = None
    prev_state_type: str = ""
    # Fase 2: the rule reads the SOURCE state's own label + actor, not just its type/key.
    prev_state_label: str = ""
    prev_state_actor: str | None = None
    last_actor: str | None = None
    pending: _Pending | None = None       # non-None exactly while prev_kind == "action"

    for idx, ev in enumerate(events):
        label = _label_of(ev)
        etype = _type_of(ev)
        actor = actor_of(ev) if actor_of is not None else ev.get("actor")
        kind = kind_of_entry(label, etype)

        if kind == "transparent":
            dropped.append(DroppedEvent(index=idx, label=label, event_type=etype,
                                         reason="transparent"))
            state_after_event[idx] = prev_state_key
            continue

        key = canonicalize(_normalize_name(label))

        if kind == "state":
            if pending is not None or prev_kind == "state":
                # Fase 2: ONE rule for both cases. The buffer (possibly empty) closes here, and
                # the inference rule gets both ends' type/label/actor plus the observed actions
                # — it inserts a generic only where nothing observed explains the change, and an
                # EMPTY buffer degenerates to the old rule 4 (nodes cannot chain to nodes, so it
                # always yields exactly one action there).
                buf = pending if pending is not None else _Pending(
                    source_key=prev_state_key or "", source_type=prev_state_type,
                    source_label=prev_state_label, source_actor=prev_state_actor)
                inserts = infer_transition_actions(
                    table,
                    (buf.source_type, buf.source_label, buf.source_actor),
                    (etype, label, actor),
                    [(a.type, a.label, a.actor) for a in buf.actions],
                    actor_readable=actor_readable,
                    fallback_actor=last_actor,
                )
                edges.append(ChainEdge(source_key=buf.source_key, target_key=key,
                                        actions=_splice(buf.actions, inserts), terminal=False))
                pending = None
            # A chain that OPENS on a real state opens there, full stop (owner call
            # 2026-08-27): prepending `start top`/`start bottom` and an inferred action to reach
            # it invented a move nobody logged — "costas não deveria ser presumido como precedido
            # por top start". A start anchor exists to be an ACTION's missing source, which is why
            # the action branch below still infers one; a state needs no source. Such a state is
            # flagged ``nascent`` instead, so a consumer can show that the chain simply begins
            # there rather than pretending an edge into it exists.
            states.append(ChainState(node_key=key, label=label, type=etype, actor=actor,
                                      inferred=False, nascent=prev_kind is None))
            prev_state_key, prev_state_type = key, etype
            prev_state_label, prev_state_actor = label, actor
            prev_kind = "state"
            last_actor = actor
            state_after_event[idx] = prev_state_key
            continue

        # kind == "action"
        action = ChainAction(key=key, label=label, type=etype, actor=actor,
                              inferred=False, source_event_index=idx)
        if pending is not None:
            # Stacks onto the SAME transition — no intermediate state invented (Phase 1: the
            # old "rule 3" is gone).
            pending.actions.append(action)
        else:
            if prev_kind is None:
                # Fase 1b: `_opening_state` always resolves now (declarative row, PGD/CDP, or
                # the orientation-by-role fallback) — an opening is never left unanchored.
                st = _opening_state(table, label, etype)
                states.append(ChainState(node_key=st["node_key"], label=st["label"],
                                          type=st["type"], actor=actor, inferred=True,
                                          role=st.get("role")))
                prev_state_key, prev_state_type = st["node_key"], st["type"]
                prev_state_label, prev_state_actor = st["label"], actor
            # prev_kind == "state": prev_state_* already current.
            pending = _Pending(source_key=prev_state_key or "", source_type=prev_state_type,
                                source_label=prev_state_label, source_actor=prev_state_actor,
                                actions=[action])
        prev_kind = "action"
        last_actor = actor
        state_after_event[idx] = prev_state_key

    if pending is not None:
        # Rule 6's anchor, keyed on the LAST accumulated action. Declarative row first
        # (``submission|$terminal -> finish`` keeps precedence — it is still the only EXACT
        # match ``infer_state_for_action_pair`` can find here); Fase 1b (owner call,
        # 2026-08-31): every OTHER closing type now falls back to ``resolve_anchor_by_role``
        # — the last action's own curated actor-role orientation — instead of closing
        # unanchored. A chain never closes on an empty ``target_key`` any more.
        last_action = pending.actions[-1]
        closing = infer_state_for_action_pair(table, last_action.type, _CHAIN_END)
        if closing is None:
            closing = resolve_closing_anchor(table, last_action.type, last_action.label)
        states.append(ChainState(node_key=closing["node_key"], label=closing["label"],
                                  type=closing["type"], actor=last_action.actor, inferred=True,
                                  role=closing.get("role")))
        target_key = closing["node_key"]
        edges.append(_edge_from_pending(pending, target_key=target_key, terminal=True))
        last_idx = last_action.source_event_index
        if last_idx is not None:
            state_after_event[last_idx] = target_key or None

    return CompiledChain(states=states, edges=edges, dropped=dropped,
                          state_after_event=state_after_event)


def compile_two_sided(
    events: Sequence[Mapping[str, Any]],
    side_of: Callable[[Mapping[str, Any]], str | None],
    *,
    actor_of: Callable[[Mapping[str, Any]], str | None] | None = None,
    inference_table: Mapping[str, Any] | None = None,
    actor_readable: bool | None = None,
) -> dict[str, CompiledChain]:
    """Match-shaped convenience over ``compile_chain``: split by ``side_of`` (returning
    ``'a'``/``'b'``/anything else), preserving each side's own relative order, then compile
    each side independently. ``source_event_index``/dropped-event ``index`` are rewritten back
    to the ORIGINAL position in ``events`` (not the per-side sub-list) so a caller can always
    trace an edge back to the raw sequence. Events with no side land in ``result['dropped']``
    (a ``CompiledChain`` carrying nothing but the audit trail) — never silently discarded.

    ``actor_readable`` defaults to this function's OWN reading of the buckets, which is
    ``attribution.bout_flags``' ``one_sided`` test expressed in side terms (its
    ``MIN_EVENTS_FOR_ONE_SIDED`` constant is imported, not copied): a bout with enough sided
    events that landed entirely on ONE side did not have the other athlete standing still — her
    side was never recorded, so the ``actor`` field carries nothing the Fase 2 rule may read.
    Pass it explicitly to override (e.g. with a real ``bout_flags(...)["role_reliable"]``, which
    also refuses self-contradictory bouts this cheap test cannot see).
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

    if actor_readable is None:
        sided = len(buckets["a"]) + len(buckets["b"])
        one_sided = (sided >= MIN_EVENTS_FOR_ONE_SIDED
                     and not (buckets["a"] and buckets["b"]))
        actor_readable = not one_sided

    result: dict[str, CompiledChain] = {}
    for side in ("a", "b"):
        pairs = buckets[side]
        orig_idx = [i for i, _ in pairs]
        sub_events = [e for _, e in pairs]
        compiled = compile_chain(sub_events, actor_of=actor_of, inference_table=table,
                                  actor_readable=actor_readable)
        result[side] = CompiledChain(
            states=compiled.states,
            edges=[
                replace(e, actions=tuple(
                    replace(a, source_event_index=orig_idx[a.source_event_index]
                            if a.source_event_index is not None else None)
                    for a in e.actions
                ))
                for e in compiled.edges
            ],
            dropped=[replace(d, index=orig_idx[d.index]) for d in compiled.dropped],
            state_after_event={orig_idx[i]: v for i, v in compiled.state_after_event.items()},
        )
    result["dropped"] = CompiledChain(states=[], edges=[], dropped=unassigned)
    return result
