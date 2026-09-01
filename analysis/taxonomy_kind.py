"""D1 — the single classifier for the actions/states taxonomy migration.

**Decision D1** (owner, 2026-08-27): AÇÕES (Lamas: takedown, guard pull, sweep, pass, back
take, submission — plus ``escape``/``transition`` by explicit owner call) become edge labels;
ESTADOS (guard, control, position) become nodes. ``concept`` is ``'transparent'`` pending a
future triage pass — it is neither an action nor a state today, so it must not silently fall
into either bucket.

    kind = 'action'      iff  lamas_chain.lamas_state({type, label}) is not None
                          OR  type in {sweep, takedown, pass, submission, escape, transition}
    kind = 'transparent' iff  type == 'concept'
    kind = 'state'        otherwise

``escape`` and ``transition`` are forced into ``'action'`` by type alone, same as the four
Lamas type-first families (``lamas_state`` already resolves those by type) — the owner's call
extends that same type-first treatment to two more types rather than requiring a label match.

**Reconciliation with existing constants** (this module does not replace them, both still
answer their own narrower questions):

- ``analysis.decision_flow.ACTION_TYPES`` = ``{pass, takedown, sweep, submission, escape,
  transition}`` — the SAME six types D1 forces to ``'action'`` by type, verified byte-for-byte
  at import time below. No divergence found.
- ``analysis.perspective_sequence.STABLE_STATE_TYPES`` = ``{guard, control}`` — NOT a strict
  subset of D1's states. Both types are disjoint from D1's forced-action type list (verified
  below), so an event of one of these types is *never* forced to ``'action'`` by type alone —
  but D1 still reads its LABEL through ``lamas_state``, and some ``guard``/``control`` labels
  ARE actions (a guard pull, a landed back take). Measured on the 141-entry App library: 3 of
  the 35 ``guard``/``control`` entries resolve to ``'action'`` this way ("Body Lock", "Body
  Lock from Back" via the clinch tokens; "Body Triangle" via the back-take tokens). So "stable
  state type" describes the common case, not a guarantee — this module is the one place that
  reads the label to catch the exception `STABLE_STATE_TYPES` was never meant to.

**The "Back Control" carve-out.** ``lamas_chain.BACK_TAKE_TOKENS`` includes the literal token
``"back control"`` — correct for Lamas' own question (which reads `control/Back Control` as
the ATTEMPT/SUCCESS of taking the back, since 89 of 91 corpus occurrences carry no `successful`
flag and the position's own name says it was already taken), but wrong for D1's: "Back
Control"/"Standing Back Control" name the durable POSITION, not the "back take" action that
puts you there — and the owner's action list names "back take", not "back control", as the
action. Left alone, D1 would fold the position into the action it names for a different
reason than every other guard/control label folds in (token collision, not domain intent), so
``kind_of`` carves those two literal labels back out to ``'state'`` before consulting
``lamas_state`` at all. "Back Take" itself, and every OTHER `BACK_TAKE_TOKENS` label
("Hooks In", "Rear Body Lock"), are left exactly as `lamas_state` reads them — the carve-out is
scoped to the labels the owner's action name (`back take`, not `back control`) does not cover,
not a broader re-reading of the back-take vocabulary.

**Extension, 2026-08-27 (carve-out D1).** "Body Triangle" and "Body Lock from Back" are the
same token collision as "Back Control": both name durable POSITIONS the App library already
lists under `control` (`attribution._CONTROL_BACK`), but their labels hit `BACK_TAKE_TOKENS`,
so `lamas_state` would read them as the back-take ACTION. Carved out to `'state'` the same way,
for the same reason — the position is not the action that reaches it. "Body Lock" (bare, not
"from Back") is deliberately NOT carved out — it is not a `BACK_TAKE_TOKENS` collision and
changing its kind would move a Markov `CDP` weight, which needs a full ELO replay.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from analysis.attribution import classify
from analysis.decision_flow import ACTION_TYPES
from analysis.lamas_chain import lamas_state
from analysis.names import _deaccent, _normalize_name, canonicalize
from analysis.perspective_sequence import STABLE_STATE_TYPES

Kind = Literal["action", "state", "transparent"]

# D1's own forced-action type list. Asserted equal to `decision_flow.ACTION_TYPES` below —
# reusing that constant directly (rather than duplicating it) is what makes drift impossible.
_FORCED_ACTION_TYPES = ACTION_TYPES

# The "Back Control" carve-out (module docstring). Keyed on `lamas_chain._key` normalization
# (deaccent then `_normalize_name`) so it matches exactly what `lamas_state` itself compares
# against. "Body Triangle"/"Body Lock from Back" are the same carve-out for the same reason:
# `attribution._CONTROL_BACK` already lists them as durable POSITIONS, but their labels are
# Lamas back-take tokens (`BACK_TAKE_TOKENS`), so `lamas_state` would read them as the back-take
# ACTION without this carve-out — same pattern as "back control" already in production.
_BACK_CONTROL_STATE_LABELS = frozenset({
    "back control", "standing back control", "body triangle", "body lock from back",
})

assert _FORCED_ACTION_TYPES == frozenset(
    {"pass", "takedown", "sweep", "submission", "escape", "transition"}
), "decision_flow.ACTION_TYPES drifted from D1's forced-action type list"
assert STABLE_STATE_TYPES.isdisjoint(_FORCED_ACTION_TYPES), (
    "perspective_sequence.STABLE_STATE_TYPES overlaps a D1 forced-action type — "
    "a 'guard'/'control' event would then always be an action, contradicting D1"
)


def kind_of(label: str, event_type: str) -> Kind:
    """D1's classifier: one technique-library entry (or event) → 'action' | 'state' |
    'transparent'. ``label`` should be the entry's ONE canonical label (English preferred,
    same convention as ``export.app_node_scores.canonical_label``) — ``lamas_state`` reads
    English tokens, so a Portuguese-only label can under-classify (see module docstring)."""
    typ = (event_type or "").strip().lower()
    if typ == "concept":
        return "transparent"
    if typ not in _FORCED_ACTION_TYPES:
        key = _normalize_name(_deaccent(str(label or "")))
        if key in _BACK_CONTROL_STATE_LABELS:
            return "state"
    if typ in _FORCED_ACTION_TYPES or lamas_state({"type": typ, "label": label}) is not None:
        return "action"
    return "state"


# ── the library-resolved entry point ────────────────────────────────────────────
# Bug (owner-confirmed, real bundle): 80/114 logged entries carry a stale ``type`` snapshot
# from the App at log time (e.g. a "Raspagem de Gancho" sweep logged with ``type: "control"``,
# a "Guarda Fechada" guard logged the same way) — `kind_of` trusts that `type` when the label
# doesn't hit an English Lamas token, so a Portuguese-labelled ACTION silently reads as a STATE
# and becomes a node instead of an edge. Root cause: the caller passed the untrustworthy LOGGED
# type instead of the technique LIBRARY's own type. `kind_of_entry` fixes this at the one place
# every caller can route through — it resolves ``label`` against the App's technique library
# FIRST and, when the label is a known library entry, classifies on the library's own canonical
# label + type, ignoring the caller's ``event_type`` entirely (it is unreliable exactly when it
# matters). Mirrors the App's own `kindOf`, which resolves through the library before
# classifying.
#
# The lookup is read from ``data/taxonomy/library_lookup.json`` — a vendored, committed
# artifact, NEVER the App repo directly. CI checks out this repo alone (no sibling
# `GrapplingArcApp`), so no module under `analysis/`/`export/`/`db/` may depend on that repo
# being on disk at runtime. `scripts/export_taxonomy_kind_fixtures.py` is the one place
# allowed to open the App's JSON — it is the generator, run manually (or via `--check` in CI)
# whenever `grappling-arch.nodes.json` changes; this module only ever reads its output.
_LIBRARY_LOOKUP_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "taxonomy" / "library_lookup.json"
)
_LIBRARY_LOOKUP: dict[str, tuple[str, str]] | None = None


def _build_library_lookup() -> dict[str, tuple[str, str]]:
    """``{_normalize_name(variant): (canonical_english_label, library_type)}`` — read from the
    committed artifact (see module note above), never derived from the App's file directly."""
    raw = json.loads(_LIBRARY_LOOKUP_PATH.read_text(encoding="utf-8"))
    return {key: (canon, typ) for key, (canon, typ) in raw.items()}


def _library_lookup() -> dict[str, tuple[str, str]]:
    global _LIBRARY_LOOKUP
    if _LIBRARY_LOOKUP is None:
        _LIBRARY_LOOKUP = _build_library_lookup()
    return _LIBRARY_LOOKUP


def resolve_library_entry(label: str) -> tuple[str, str] | None:
    """``(canonical_english_label, library_type)`` for a raw label recognised by the App's
    technique library, else ``None``. The library's OWN type — never the caller's — travels
    with the resolved label, because that type is the trustworthy one (see module note above)."""
    key = _normalize_name(str(label or ""))
    if not key:
        return None
    return _library_lookup().get(key)


def kind_of_entry(label: str, event_type: str | None) -> Kind:
    """D1's classifier, entry point for real logged data: resolves ``label`` through the App's
    technique library first (``resolve_library_entry``) and classifies on the library's own
    canonical label + type — ``event_type`` is used only as a fallback, for labels the library
    doesn't recognise. See the module note above `_build_library_lookup` for why the caller's
    ``event_type`` cannot be trusted on its own."""
    resolved = resolve_library_entry(label)
    if resolved is not None:
        canon_label, lib_type = resolved
        return kind_of(canon_label, lib_type)
    return kind_of(label, event_type or "")


# ── D2: structural inference table ──────────────────────────────────────────────
# Never probabilistic — a fixed lookup, checked into `data/taxonomy/inference_table.json`.
# Bridges the gap the migration creates: two adjacent ACTIONS need a generic STATE node to
# connect through (edges don't chain to edges), and two adjacent STATES need a generic ACTION
# edge (nodes don't chain to nodes).
INFERENCE_TABLE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "taxonomy" / "inference_table.json"
)


def load_inference_table(path: Path | None = None) -> dict[str, Any]:
    """Read the D2 table. No caching — it's a few hundred bytes, read once per export run."""
    with open(path or INFERENCE_TABLE_PATH, encoding="utf-8") as f:
        table: dict[str, Any] = json.load(f)
        return table


def resolve_pair(table: Mapping[str, str], type_a: str, type_b: str) -> str | None:
    """D2's pair resolution: exact ``"a|b"`` > first entry (table order) whose key has a
    wildcard on one side and matches the fixed side > ``None`` when nothing matches.

    Keys are by event TYPE, never by label — a table with a handful of rows can cover every
    type combination this way, where a label-keyed table could not.

    Phase 1 (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md): the old universal ``"*|*"`` catch-all is
    gone from ``action_pair_to_state`` (the four dead generic states it fed no longer exist) —
    a caller with no match must handle ``None`` itself; this function never invents an anchor.
    ``state_pair_to_action`` keeps its own ``"*|*"`` row (rule 4, untouched), so
    ``infer_action_for_state_pair`` below still always resolves.
    """
    a, b = (type_a or "").strip().lower(), (type_b or "").strip().lower()
    exact = f"{a}|{b}"
    if exact in table:
        return table[exact]
    for key, value in table.items():
        if key == "*|*":
            continue
        ka, _, kb = key.partition("|")
        if (ka == a or ka == "*") and (kb == b or kb == "*"):
            return value
    return table.get("*|*")


def infer_state_for_action_pair(
    table: Mapping[str, Any], type_a: str, type_b: str
) -> dict[str, Any] | None:
    """The presentation-anchor state for a chain that opens/closes on an action (``"$start"``/
    ``"$terminal"`` sentinels) — ``None`` when the table has no declarative row for this pair
    (Phase 1: every OTHER caller of this function was removed along with the four dead generic
    states it used to bridge action-action gaps for; see the module docstring)."""
    key = resolve_pair(table["action_pair_to_state"], type_a, type_b)
    if key is None:
        return None
    entry: dict[str, Any] = table["generic_states"][key]
    return entry


def resolve_anchor_by_role(
    table: Mapping[str, Any], action_type: str, action_label: str
) -> dict[str, Any]:
    """The generic anchor for one end of a chain that lands on an ACTION with no declarative
    row (owner call, 2026-08-31: ``start neutral``/``start top``/``start bottom`` now serve
    BOTH ends of a chain, not just openings -- ``finish`` stays exclusive to submission). This
    is the fallback of LAST RESORT: a caller tries a declarative table row first
    (``infer_state_for_action_pair`` -- ``$start|sweep``, ``submission|$terminal``, ...) and
    only calls here when that returns ``None``.

    Orientation comes from ``analysis.attribution.classify``'s curated ACTOR role for this
    action's own ``(type, label)`` -- the same curated source Fase 2's inference rule will use,
    not a second one invented here: ``TOP`` -> ``start top``, ``BOTTOM`` -> ``start bottom``,
    anything else (``controlling``/``controlled``/``executor``/``defender``/``neutral``/
    ``unknown``) -> ``start neutral``. ``classify`` always returns a role, so this always
    resolves -- no chain end is ever left with an empty ``node_key`` (the phantom
    ``""``-keyed node this replaces, docs/taxonomy/03_ARESTA_COMO_CAMINHO.md)."""
    role = classify(action_type, action_label).actor_role
    key = {"top": "start top", "bottom": "start bottom"}.get(role, "start neutral")
    return cast(dict[str, Any], table["generic_states"][key])


def infer_action_for_state_pair(
    table: Mapping[str, Any], type_a: str, type_b: str
) -> dict[str, Any]:
    """The generic action entry bridging two consecutive state types with no action between
    them, e.g. two guard states chain through ``generic_actions['guard transition']``. Always
    resolves — ``state_pair_to_action`` keeps its own ``"*|*"`` fallback (untouched by Phase 1,
    which only removed the four dead action-pair-to-STATE generics)."""
    key = resolve_pair(table["state_pair_to_action"], type_a, type_b)
    assert key is not None, "state_pair_to_action must always resolve (keeps its *|* fallback)"
    entry: dict[str, Any] = table["generic_actions"][key]
    return entry


# ── orientation: top | bottom | neutral, per STATE (owner call, 2026-08-27) ─────────────
# Curated, high-confidence only — a fixed lookup in ``data/taxonomy/state_orientation.json``,
# never derived/guessed. Every leg-entanglement control the owner did not name explicitly
# (electric chair, leg hug, outside ashi garami, saddle inside sankaku — neither top nor bottom
# in the classic guard/pass sense) defaults to ``'neutral'`` on purpose, same as any future
# state this table has not been curated for yet. The four D2 ``generic_states`` carry their own
# orientation too, keyed the same way (``chained submission``/``top transition``/``scramble``/
# ``finish``) — this table is their single source, not a special case.
#
# This is the state's OWN orientation only. An opponent's mount is still 'top' here — reading
# it as 'bottom' relative to the OTHER fighter is a perspective flip the caller (App renderer /
# a dossier) applies, not something this table encodes; it has no opponent-relative concept.
Orientation = Literal["top", "bottom", "neutral"]

ORIENTATION_TABLE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "taxonomy" / "state_orientation.json"
)
_ORIENTATION_TABLE: dict[str, str] | None = None


def load_orientation_table(path: Path | None = None) -> dict[str, str]:
    """Read the curated orientation table. No caching — a few hundred bytes, same convention
    as ``load_inference_table``."""
    with open(path or ORIENTATION_TABLE_PATH, encoding="utf-8") as f:
        table: dict[str, str] = json.load(f)
        return table


def orientation_of(canonical_label: str) -> Orientation:
    """``'top'|'bottom'|'neutral'`` for a state's own canonical label, via the curated table.
    Unresolved labels (including actions/transparent entries, which this table was never meant
    to cover) default to ``'neutral'`` — never guessed."""
    global _ORIENTATION_TABLE
    if _ORIENTATION_TABLE is None:
        _ORIENTATION_TABLE = load_orientation_table()
    key = _normalize_name(str(canonical_label or ""))
    return cast(Orientation, _ORIENTATION_TABLE.get(key, "neutral"))


# ── role: 'anchor' | 'finish' | None, per GENERIC state node_key (owner call, 2026-08-27,
# renamed 'start' -> 'anchor' 2026-08-31 — the three oriented generics now serve BOTH ends of a
# chain, not just openings) ──────────────────────────────────────────────────────────────────
# Curated only where D2's own `generic_states` block says so — `chain_compiler.ChainState`
# already carries `role` directly (set from the same table entry at the point of insertion), so
# this is the standalone lookup for a caller that only has a node_key (e.g. a renderer walking
# an already-serialized chain) and needs the same answer without re-deriving it.
def role_of(node_key: str) -> str | None:
    """``'anchor'|'finish'|None`` for a node_key, via the D2 inference table's ``generic_states``
    block — the only place role is curated. A real technique node (never a `generic_states` key)
    reads ``None``, same "curated, not guessed" convention as ``orientation_of``."""
    table = load_inference_table()
    entry = table["generic_states"].get(_normalize_name(str(node_key or "")))
    return entry.get("role") if entry else None


# ── Fase 2: orientation ON THE INFERENCE PATH (docs/taxonomy/03_ARESTA_COMO_CAMINHO.md) ──
# `orientation_of` above is deliberately NOT touched: its docstring's promise ("unresolved
# labels default to 'neutral' — never guessed") is the reason it exists, and `render_map_
# prototypes`/`export_taxonomy_kind_fixtures` (an App-mirrored fixture) read it. The inference
# rule needs a SECOND, wider reading, so it gets its own function and its own return type that
# says which level answered.
#
# Measured 2026-08-31, on the curated label lists in `analysis.attribution`: 52 of the 74 labels
# that ought to carry an orientation read 'neutral' through `state_orientation.json` alone (70%);
# the 13 `_CONTROL_GRIP` labels have zero coverage, and the corpus's third-largest state node
# ("Back Take", degree 211) is one of the misses — it is keyed on the RAW label while the
# curated row is under its library-canonical name ("Back Control"). Hence the three levels below,
# which take that 52 to 0 — `_GUARD_NEUTRAL`'s 14 stay `neutral` because symmetric IS the answer
# there (`tests/test_taxonomy_kind.py::test_orientation_for_inference_covers_every_curated_label`).
#
# Five values, not three: `attribution` keeps top/bottom and controlling/controlled as SEPARATE
# axes on purpose (`attribution._AXES`) — back control taken from underneath is `controlling`
# AND `bottom` at once, and an earlier collapse of the two threw away 53% of the events. This
# function preserves that split; `_dominance` below only ever compares WITHIN one axis.
Stance = Literal["top", "bottom", "controlling", "controlled", "neutral"]

# Which `attribution` actor roles carry a positional claim at all. `executor`/`defender` (the
# relation axis) and `unknown` deliberately do not — a sweep's executor role says who did it,
# never where they ended up, which is what `action_exit_orientation` is for.
_POSITIONAL_ROLES = frozenset({"top", "bottom", "controlling", "controlled"})


@dataclass(frozen=True)
class StanceReading:
    """A state's positional stance plus WHICH level answered — `declared` when the curated
    `state_orientation.json` had the row (directly or under the label's library-canonical
    name), `derived` when it fell through to `attribution.classify(...).actor_role`. Same
    "a reader can tell a curated row from an inherited one" convention as
    `attribution.Attribution.source`."""
    value: Stance
    source: Literal["declared", "derived"]


def orientation_for_inference(event_type: str, label: str) -> StanceReading:
    """The stance of ONE state, for the inference rule only. Declared table first (under the
    canonical node key, then under the label's library-canonical name), curated
    `attribution` role second, `neutral` when neither answers."""
    declared = orientation_of(canonicalize(_normalize_name(str(label or ""))))
    if declared != "neutral":
        return StanceReading(declared, "declared")
    resolved = resolve_library_entry(label)
    if resolved is not None:
        by_library = orientation_of(resolved[0])
        if by_library != "neutral":
            return StanceReading(by_library, "declared")
    role = classify(event_type, label).actor_role
    if role in _POSITIONAL_ROLES:
        return StanceReading(cast(Stance, role), "derived")
    return StanceReading("neutral", "derived")


def entry_orientation(table: Mapping[str, Any], action_type: str) -> Orientation | None:
    """Where an action STARTS from, reusing the `"$start|<type>"` rows the opening anchors
    already declare (`$start|sweep -> start bottom` = a sweep is executed from underneath).
    `None` when the table declares nothing for this type — no claim, not 'neutral'."""
    entry = infer_state_for_action_pair(table, "$start", action_type)
    return cast(Orientation, entry["orientation"]) if entry is not None else None


def exit_orientation(table: Mapping[str, Any], action_type: str) -> Orientation:
    """Where an action LEAVES you — the axis Fase 1b was missing (`docs/taxonomy/
    03_ARESTA_COMO_CAMINHO.md` §7 recorded the gap: `classify(...).actor_role` answers
    `executor` for sweep/escape/takedown, which is a relation, not a position). Curated by
    TYPE in `action_exit_orientation`; the `"*"` row is the declared default, never a guess.

    ponytail: keyed by type only. Label-level overrides would buy 2 events on the 281-bout
    corpus (`escape to top`, a `guard recovery` mis-filed under `escape`) — add a
    `<type>|<label>` row here if a real consumer ever needs one.
    """
    row: Mapping[str, str] = table["action_exit_orientation"]
    return cast(Orientation, row.get((action_type or "").strip().lower(), row["*"]))


def resolve_closing_anchor(
    table: Mapping[str, Any], action_type: str, action_label: str
) -> dict[str, Any]:
    """The generic anchor a chain CLOSING on an action lands in — by that action's exit
    orientation (above), not by its actor role. Replaces `resolve_anchor_by_role` on the
    closing end only; the OPENING end still uses it, because an opening asks the opposite
    question (where you came FROM). `action_label` is accepted for symmetry with
    `resolve_anchor_by_role` and for the label-level upgrade path noted on `exit_orientation`.
    """
    del action_label  # ponytail: type-keyed today — see `exit_orientation`'s note
    key = {"top": "start top", "bottom": "start bottom"}.get(
        exit_orientation(table, action_type), "start neutral")
    return cast(dict[str, Any], table["generic_states"][key])


# ── Fase 2: the action-inference RULE (a function, no longer a two-key table) ────────────
# `state_pair_to_action` stays, demoted to what it always really was: the VOCABULARY of
# generic actions plus the answer for pairs that carry no positional claim. The decision moved
# here, because it needs four things a `"type_a|type_b"` key cannot hold — each side's ACTOR,
# each side's ORIENTATION, and the buffer of actions already observed inside the transition.
# Three of the owner's own examples collide under the type-only key
# (`guard|guard` is both "Guarda A -> Guarda B => Raspagem A" and "Meia-Guarda A -> Guarda
# Fechada A => Transição de Guarda"); with actor + orientation they separate cleanly.
_SELF, _OPPONENT = "self", "opponent"

_STANCE_AXIS: dict[str, str] = {
    "top": "topology", "bottom": "topology",
    "controlling": "control", "controlled": "control",
}
# True when the stance puts the state's OWN actor on the dominant side of that axis.
_ACTOR_IS_DOMINANT: dict[str, bool] = {
    "top": True, "controlling": True, "bottom": False, "controlled": False,
}


@dataclass(frozen=True)
class _Dominance:
    """Who is on the dominant side, relative to a REFERENCE actor, and on which axis. Kept
    relative rather than named because a chain compiled per-side knows exactly one athlete —
    "the other one" is a real, unnameable answer there, and pretending otherwise is how an
    inference starts guessing."""
    who: str    # _SELF | _OPPONENT
    axis: str   # 'topology' | 'control'


@dataclass(frozen=True)
class InferredInsert:
    """One generic action the rule wants to add, and WHERE. `index` is a position in the
    caller's observed-action buffer to insert BEFORE (`len(observed)` = append) — observed
    actions are immutable in order and position, inference only inserts between them.

    `is_opponent` is the half of the answer a name cannot carry: a chain compiled per side knows
    exactly ONE athlete, so "Controle A -> Guarda A => Inversão B" has a real owner that has no
    name here. `actor` is that name when the input actually names both athletes and `None` when
    it does not; `is_opponent` says whose it is either way."""
    index: int
    entry: dict[str, Any]
    actor: str | None
    is_opponent: bool = False


def _dominance(
    stance: Stance | None, actor: str | None, reference: str | None, *, actor_readable: bool
) -> _Dominance | None:
    """`None` = this state makes no positional claim. When the actor field cannot be read
    (`attribution.bout_flags` says the bout files everything under one athlete), every event is
    treated as the reference's own — an actor DIFFERENCE is then not evidence of anything."""
    if stance is None or stance not in _ACTOR_IS_DOMINANT:
        return None
    own = (not actor_readable) or actor is None or reference is None or actor == reference
    return _Dominance(_SELF if own == _ACTOR_IS_DOMINANT[stance] else _OPPONENT,
                      _STANCE_AXIS[stance])


def _flips(before: _Dominance, after: _Dominance) -> bool:
    """Only ever WITHIN one axis (see `Stance`'s note): a standing collar tie is `controlling`
    and a closed guard is `bottom`, and calling that pair an inversion would be inventing the
    comparison `attribution._AXES` refuses to make."""
    return before.axis == after.axis and before.who != after.who


def _named(who: str, reference: str | None, other: str | None) -> str | None:
    return reference if who == _SELF else other


def _flip_entry(
    table: Mapping[str, Any], new_dominant: str | None, source_type: str, source_actor: str | None
) -> dict[str, Any]:
    """A sweep is a reversal FROM GUARD — that is what makes it a sweep and not a scramble
    (`attribution._LABEL`'s own note on `sweep top position`). Every other inversion is the
    generic `reversal`; the rule never invents a sweep SUBTYPE.

    ponytail: reads the SOURCE state's family even when the flip lands several observed actions
    into the buffer. Tracking a rolling family would need a state model the compiler does not
    have; upgrade path is to carry the last state-like context in the walk below."""
    swept_from_guard = (source_type == "guard" and new_dominant is not None
                        and new_dominant == source_actor)
    key = "sweep" if swept_from_guard else "reversal"
    return cast(dict[str, Any], table["generic_actions"][key])


def _state_pair_entry(
    table: Mapping[str, Any],
    source: tuple[str, str, str | None],
    target: tuple[str, str, str | None],
    source_dom: _Dominance | None,
    target_dom: _Dominance | None,
    reference: str | None,
    other: str | None,
    fallback_actor: str | None,
) -> tuple[dict[str, Any], str | None, bool]:
    """Two states, nothing observed between them (nodes cannot chain to nodes, so exactly ONE
    action is always emitted here). The owner's six examples, in order of the checks below:

        A Guarda   -> B Guarda    inversion, new dominant played the guard  => Raspagem A
        A Controle -> A Guarda    inversion, source was not a guard         => Inversão B
        A Controle -> B Controle  inversion, source was not a guard         => Inversão B
        A Guarda   -> B Controle  no inversion, guard -> control            => Passagem B
        A Controle -> B Guarda    no inversion, control -> guard            => Recomposição B
        Meia-Guarda A -> Guarda Fechada A   no inversion, guard -> guard
                                                                  => Transição de Guarda A
        Side Control A -> Montada A         no inversion, control -> control
                                                                  => Transição de Controle A
    """
    src_type, _src_label, src_actor = source
    tgt_type, _tgt_label, tgt_actor = target
    tgt_owner = tgt_actor if tgt_actor is not None else fallback_actor

    # Both ends must speak on the SAME axis before either branch below may read them as
    # agreeing: a closed guard is `bottom` (topology) and a kimura grip is `controlling`
    # (control), and "they agree that nobody inverted" is not a thing those two can say to each
    # other — `attribution._AXES` keeps them apart for exactly this reason.
    if (source_dom is not None and target_dom is not None
            and source_dom.axis == target_dom.axis):
        if _flips(source_dom, target_dom):
            new_dominant = _named(target_dom.who, reference, other)
            return (_flip_entry(table, new_dominant, src_type, src_actor), new_dominant,
                    target_dom.who == _OPPONENT)
        if src_type == "guard" and tgt_type == "control":
            # The guard player stayed underneath and the same athlete stayed on top of them:
            # the top athlete moved from "in their guard" to "controlling" — a pass.
            return (table["generic_actions"]["guard pass"],
                    _named(target_dom.who, reference, other),
                    target_dom.who == _OPPONENT)
        if src_type == "control" and tgt_type == "guard":
            # Control held, and the controlled athlete now has a guard: recovery, and it is
            # the GUARD player's action, not the dominant one's.
            return table["generic_actions"]["guard recovery"], tgt_owner, False
        if src_type == tgt_type and src_type in ("guard", "control"):
            key = "guard transition" if src_type == "guard" else "control transition"
            return table["generic_actions"][key], tgt_owner, False
    # No positional claim on one of the ends (or an unnamed pair): the declarative table is the
    # honest answer, exactly as before Fase 2 — this is where `transition` legitimately lives.
    return infer_action_for_state_pair(table, src_type, tgt_type), fallback_actor, False


def infer_transition_actions(
    table: Mapping[str, Any],
    source: tuple[str, str, str | None],
    target: tuple[str, str, str | None],
    observed: Sequence[tuple[str, str, str | None]] = (),
    *,
    actor_readable: bool = True,
    fallback_actor: str | None = None,
) -> list[InferredInsert]:
    """Every generic action ONE transition warrants, and where each one goes.

    `source`/`target`/`observed` items are `(event_type, label, actor)`. Order of the rule
    (the owner's, 2026-08-31):

    1. Observed actions are immutable in order and position — this only ever returns positions
       to INSERT at.
    2. Redundancy first: an observed action whose own entry/exit orientation already explains
       the change gets no generic beside it. The pre-Fase-2 code had no such concept because it
       only ever fired on an EMPTY buffer.
    3. The most informative generic the context supports; bare `transition` only where nothing
       positional is known.
    4. Fill maximally — a transition whose endpoints invert while every observed action is
       positionally silent DOES get the inversion spelled out.

    An empty buffer is the degenerate case of the same walk and always yields exactly one
    action (nodes cannot chain to nodes).
    """
    src_type, src_label, src_actor = source
    tgt_type, tgt_label, tgt_actor = target
    reference = src_actor
    known = [a for a in [src_actor, tgt_actor, *(a for _, _, a in observed)] if a]
    other = next((a for a in known if a != reference), None) if actor_readable else None

    src_dom = _dominance(orientation_for_inference(src_type, src_label).value, src_actor,
                         reference, actor_readable=actor_readable)
    tgt_dom = _dominance(orientation_for_inference(tgt_type, tgt_label).value, tgt_actor,
                         reference, actor_readable=actor_readable)

    if not observed:
        entry, actor, is_opponent = _state_pair_entry(
            table, source, target, src_dom, tgt_dom, reference, other, fallback_actor)
        return [InferredInsert(index=0, entry=entry, actor=actor, is_opponent=is_opponent)]

    if src_dom is None or tgt_dom is None or not _flips(src_dom, tgt_dom):
        # The two ends make no comparable claim, or make the same one. Nothing observed needs
        # explaining, so nothing is added — an observed buffer is already a complete account.
        return []

    # There IS an inversion between two OBSERVED states: that is the one high-confidence fact
    # this transition carries beyond its own actions, and it is worth exactly one generic.
    #
    # WHERE it goes is decided by the observed actions' ENTRY requirements, never by their
    # outcomes: 196 of the 228 `sweep` events in the corpus carry `successful = NULL`, so
    # treating an action's exit orientation as a FACT ("she is on top now") reads an outcome
    # the log never recorded — the same D7 line `compile_chain` refuses to cross. Measured cost
    # of getting this wrong: a first cut that advanced a rolling position on every action's exit
    # manufactured 160 extra actions on the 281-bout corpus, including
    # `closed guard --[sweep, reversal, sweep, reversal, ...]--> closed guard`, where consecutive
    # repeats of one logged attempt oscillated the position back and forth.
    #
    # So exit orientation may only ever SUPPRESS an inference (redundancy, rule 2), never create
    # one; entry orientation, which is a precondition rather than an outcome, may position one.
    new_dominant = _named(tgt_dom.who, reference, other)
    entry = _flip_entry(table, new_dominant, src_type, src_actor)
    is_opponent = tgt_dom.who == _OPPONENT
    for i, (a_type, _a_label, a_actor) in enumerate(observed):
        wants = _dominance(entry_orientation(table, a_type), a_actor, reference,
                           actor_readable=actor_readable)
        if wants is not None and not _flips(wants, tgt_dom):
            # This observed action already presupposes the NEW dominance (a pass needs the
            # passer on top), so the inversion happened before it — insert here, between the
            # observed actions, reordering nothing.
            return [InferredInsert(index=i, entry=entry, actor=new_dominant,
                                   is_opponent=is_opponent)]
    for a_type, _a_label, a_actor in observed:
        leaves = _dominance(exit_orientation(table, a_type), a_actor, reference,
                            actor_readable=actor_readable)
        if leaves is not None and not _flips(leaves, tgt_dom):
            # Redundancy (rule 2): an observed action already explains the inversion — a
            # wrestle-up out of guard IS the bottom-to-top move, and naming it twice would be
            # inventing a second one.
            return []
    return [InferredInsert(index=len(observed), entry=entry, actor=new_dominant,
                           is_opponent=is_opponent)]
