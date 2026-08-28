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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from analysis.decision_flow import ACTION_TYPES
from analysis.lamas_chain import lamas_state
from analysis.names import _deaccent, _normalize_name
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
# FIRST (same file `scripts/export_taxonomy_kind_fixtures.py` already reads) and, when the
# label is a known library entry, classifies on the library's own canonical label + type,
# ignoring the caller's ``event_type`` entirely (it is unreliable exactly when it matters).
# Mirrors the App's own `kindOf`, which resolves through the library before classifying.
_APP_NODES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "GrapplingArcApp" / "src" / "data"
    / "grappling-arch.nodes.json"
)
_LIBRARY_LOOKUP: dict[str, tuple[str, str]] | None = None


def _build_library_lookup() -> dict[str, tuple[str, str]]:
    """``{_normalize_name(variant): (canonical_english_label, library_type)}`` over every
    App library entry's ``name``/``variations``/``translations.*`` (whatever locales the JSON
    carries — inspected, not assumed: currently ``pt``/``en``). Same key convention
    ``export.app_node_scores`` already uses to index the same file (``_normalize_name``, not
    ``_deaccent``-first — that's the separate `lamas_chain._key` contract).

    Collisions (two entries' variant texts normalize to the same key) are real — 11 in the
    141-entry library, measured 2026-08-27: ``side mount`` (Side Control/Mount), ``ude garami``
    (Kimura/Americana), ``harai goshi`` (Hip Throw/Sweeping Hip Throw), ``deep half guard`` +
    ``zguard`` + ``knee shield`` (Half Guard/Z-Guard/Deep Half Guard overlap), ``ashi garami``
    (X-Guard/Single Leg X), ``saddle`` (Back Control/Saddle), ``body lock das costas`` (Body
    Lock from Back/Body Triangle), ``biceps slicer`` (Calf Slicer/Bicep Slicer), ``presso``
    (Pressure Pass/Pressure — the concept/action collision the ticket names). Resolved
    deterministically: FIRST entry wins in the library's own file order (the JSON is a
    committed, static file — file order is already a fixed, reproducible order; same
    first-wins-by-file-order convention `export.app_node_scores.build_scores` already
    documents for the identical file), never overwritten by a later entry.
    """
    from export.app_node_scores import _name_variants, canonical_label

    nodes = json.loads(_APP_NODES_PATH.read_text(encoding="utf-8"))
    lookup: dict[str, tuple[str, str]] = {}
    for node in nodes:
        canon = canonical_label(node)
        if not canon:
            continue
        typ = str(node.get("type") or "")
        for text in _name_variants(node):
            key = _normalize_name(text)
            if key:
                lookup.setdefault(key, (canon, typ))
    return lookup


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
        return json.load(f)


def resolve_pair(table: Mapping[str, str], type_a: str, type_b: str) -> str:
    """D2's pair resolution: exact ``"a|b"`` > first entry (table order) whose key has a
    wildcard on one side and matches the fixed side > the ``"*|*"`` fallback.

    Keys are by event TYPE, never by label — a table with 4-5 rows can cover every type
    combination this way, where a label-keyed table could not.
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
    return table["*|*"]


def infer_state_for_action_pair(
    table: Mapping[str, Any], type_a: str, type_b: str
) -> dict[str, Any]:
    """The generic state entry bridging two consecutive action types with no state between
    them, e.g. two submission attempts chain through ``generic_states['chained submission']``."""
    key = resolve_pair(table["action_pair_to_state"], type_a, type_b)
    return table["generic_states"][key]


def infer_action_for_state_pair(
    table: Mapping[str, Any], type_a: str, type_b: str
) -> dict[str, Any]:
    """The generic action entry bridging two consecutive state types with no action between
    them, e.g. two guard states chain through ``generic_actions['guard transition']``."""
    key = resolve_pair(table["state_pair_to_action"], type_a, type_b)
    return table["generic_actions"][key]


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
        return json.load(f)


def orientation_of(canonical_label: str) -> Orientation:
    """``'top'|'bottom'|'neutral'`` for a state's own canonical label, via the curated table.
    Unresolved labels (including actions/transparent entries, which this table was never meant
    to cover) default to ``'neutral'`` — never guessed."""
    global _ORIENTATION_TABLE
    if _ORIENTATION_TABLE is None:
        _ORIENTATION_TABLE = load_orientation_table()
    key = _normalize_name(str(canonical_label or ""))
    return cast(Orientation, _ORIENTATION_TABLE.get(key, "neutral"))


# ── role: 'start' | 'finish' | None, per GENERIC state node_key (owner call, 2026-08-27) ────
# Curated only where D2's own `generic_states` block says so — `chain_compiler.ChainState`
# already carries `role` directly (set from the same table entry at the point of insertion), so
# this is the standalone lookup for a caller that only has a node_key (e.g. a renderer walking
# an already-serialized chain) and needs the same answer without re-deriving it.
def role_of(node_key: str) -> str | None:
    """``'start'|'finish'|None`` for a node_key, via the D2 inference table's ``generic_states``
    block — the only place role is curated. A real technique node (never a `generic_states` key)
    reads ``None``, same "curated, not guessed" convention as ``orientation_of``."""
    table = load_inference_table()
    entry = table["generic_states"].get(_normalize_name(str(node_key or "")))
    return entry.get("role") if entry else None
