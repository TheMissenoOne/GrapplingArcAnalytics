"""D1/D2 taxonomy classifier: `kind_of`, the reconciliation asserts, the "Back Control" carve-
out, the D2 inference table resolution, and the cross-repo fixture round-trip."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.decision_flow import ACTION_TYPES
from analysis.names import _normalize_name, canonicalize
from analysis.perspective_sequence import STABLE_STATE_TYPES
from analysis.taxonomy_kind import (
    StanceReading,
    infer_action_for_state_pair,
    infer_state_for_action_pair,
    kind_of,
    kind_of_entry,
    load_inference_table,
    load_orientation_table,
    orientation_of,
    resolve_library_entry,
    resolve_pair,
    role_of,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "rating" / "taxonomy_kind_golden.json"
_APP = ROOT.parent / "GrapplingArcApp" / "src"
APP_GOLDEN = _APP / "services" / "__fixtures__" / "taxonomyKindGolden.json"
APP_INFERENCE_TABLE = _APP / "data" / "taxonomy_inference_table.json"


# ── D1: type-first families and the owner's escape/transition call ─────────────
@pytest.mark.parametrize("event_type", ["submission", "pass", "takedown", "sweep"])
def test_type_first_families_are_always_action(event_type: str) -> None:
    assert kind_of("anything at all", event_type) == "action"


@pytest.mark.parametrize("event_type", ["escape", "transition"])
def test_escape_and_transition_are_action_by_owner_call(event_type: str) -> None:
    # These carry no Lamas action on their own (lamas_chain deliberately excludes `escape`,
    # and generic `transition` labels rarely hit BACK_TAKE_TOKENS) — the owner's decision, not
    # lamas_state, is what forces them to 'action' here.
    assert kind_of("Some Unmapped Label", event_type) == "action"


def test_concept_is_transparent_regardless_of_label() -> None:
    assert kind_of("Pressure", "concept") == "transparent"
    assert kind_of("Back Control", "concept") == "transparent"  # type wins over the carve-out


# ── D1: label-resolved actions (control/guard/transition via lamas_state) ──────
def test_guard_pull_is_action_via_lamas_pgd() -> None:
    assert kind_of("Pull Guard", "guard") == "action"
    assert kind_of("Guard Pull", "guard") == "action"


def test_back_take_is_action_via_lamas_btk() -> None:
    assert kind_of("Back Take", "control") == "action"


def test_curated_grips_are_states_even_though_lamas_calls_them_cdp() -> None:
    """N0's authority swap (docs/taxonomy/04_ONTOLOGIA_CANONICA.md): `lamas_state` reads
    "Hooks In"/"Collar Tie"/"Front Headlock" as its CDP *action* code, but `attribution`'s
    curated `_CONTROL_GRIP` list calls them positions, and that is the class the map needs —
    a grip you hold is somewhere you are. `lamas_state` itself is untouched, so the Markov
    CDP weight does not move."""
    from analysis.lamas_chain import lamas_state

    for label in ("Hooks In", "Collar Tie", "Front Headlock", "Russian Tie", "Clinch Knees"):
        assert kind_of(label, "control") == "state", label
        # ...and every one of them is still a Lamas ACTION code, which is what used to decide
        # this ("Hooks In" is a back-take token, the rest are clinch tokens).
        assert lamas_state({"type": "control", "label": label}) in ("CDP", "BTKA"), label


def test_explicit_state_labels_stay_state() -> None:
    assert kind_of("Closed Guard", "guard") == "state"
    assert kind_of("Half Guard", "guard") == "state"
    assert kind_of("Side Control", "control") == "state"


# ── the "Back Control" carve-out ────────────────────────────────────────────────
def test_back_control_is_state_despite_lamas_back_take_tokens() -> None:
    """`lamas_chain.BACK_TAKE_TOKENS` contains the literal token "back control" (Lamas reads
    it as the back-take ATTEMPT/SUCCESS) — D1 wants the durable position classified 'state',
    only the "back take" action itself classified 'action'. Regression for the carve-out."""
    from analysis.lamas_chain import lamas_state

    # Prove the token collision is real: lamas_state alone WOULD call this an action.
    assert lamas_state({"type": "control", "label": "Back Control"}) is not None
    assert kind_of("Back Control", "control") == "state"
    assert kind_of("Standing Back Control", "control") == "state"
    # "Back Take" is not swept up by the carve-out — it stays action.
    assert kind_of("Back Take", "control") == "action"


def test_back_control_carve_out_does_not_override_a_forced_action_type() -> None:
    # A hypothetical `transition`/`escape` event labelled "Back Control" is still forced to
    # 'action' by the type rule — the carve-out only intercepts control/guard-type labels.
    assert kind_of("Back Control", "transition") == "action"


# ── the carve-out's 2026-08-27 extension: Body Triangle / Body Lock from Back ──────
def test_body_triangle_and_body_lock_from_back_are_state_despite_back_take_tokens() -> None:
    """Same token collision as "Back Control" — `lamas_chain.BACK_TAKE_TOKENS` reads both
    labels as the back-take action, but they name durable POSITIONS the App library already
    lists under `control` (`attribution._CONTROL_BACK`)."""
    from analysis.lamas_chain import lamas_state

    assert lamas_state({"type": "control", "label": "Body Triangle"}) is not None
    assert lamas_state({"type": "control", "label": "Body Lock from Back"}) is not None
    assert kind_of("Body Triangle", "control") == "state"
    assert kind_of("Body Lock from Back", "control") == "state"


def test_bare_body_lock_is_not_carved_out() -> None:
    """"Body Lock" (no "from Back") is still outside `_BACK_CONTROL_STATE_LABELS`: it is not a
    `BACK_TAKE_TOKENS` collision, and touching `lamas_state` would move a Markov `CDP` weight
    (full ELO replay). It nonetheless reads as a STATE since N0, through the curated
    `attribution._CONTROL_BACK` row rather than through the carve-out — a standing body lock is
    a clinch position. `lamas_state` is unchanged, so no weight moved."""
    from analysis.lamas_chain import lamas_state

    assert lamas_state({"type": "control", "label": "Body Lock"}) == "CDP"
    assert kind_of("Body Lock", "control") == "state"


# ── kind_of_entry: library-resolved, distrusts a stale logged `type` ────────────
def test_kind_of_entry_action_logged_with_a_stale_state_type() -> None:
    """Real bug: 'Raspagem de Gancho' (a sweep) logged with `type: 'control'`. `kind_of` alone
    would read it as a state (pt label misses the English Lamas tokens); `kind_of_entry`
    resolves the library's own type (`sweep`, forced-action) and gets it right."""
    assert kind_of("Raspagem de Gancho", "control") == "state"  # the bug, unfixed at this layer
    assert kind_of_entry("Raspagem de Gancho", "control") == "action"


def test_kind_of_entry_state_logged_with_a_stale_type_stays_state() -> None:
    """'Guarda Fechada' (Closed Guard, a state) also logged with `type: 'control'` in the real
    bundle — must NOT flip to action just because the logged type happens to differ."""
    assert kind_of_entry("Guarda Fechada", "control") == "state"


def test_kind_of_entry_submission_logged_with_a_stale_type() -> None:
    assert kind_of_entry("Mata-Leão", "control") == "action"


def test_kind_of_entry_turtle_is_state_not_action() -> None:
    """Real bug this carve-out (1.3) exists for: 'Turtle'/'Quatro Apoios' variants used to
    resolve to the library's `Turtle Position` node with `type: 'escape'` — a FORCED_ACTION
    type — so `kind_of_entry` misread the App's own turtle position as an ACTION. Now `type`
    is `'defensive'` (not forced), so the position reads as a state."""
    assert kind_of_entry("Turtle", "control") == "state"
    assert kind_of_entry("Quatro Apoios", "control") == "state"


def test_kind_of_entry_falls_back_to_kind_of_outside_the_library() -> None:
    assert kind_of_entry("Some Unmapped Label", "guard") == kind_of("Some Unmapped Label", "guard")
    assert kind_of_entry("Some Unmapped Label", "submission") == "action"


def test_resolve_library_entry_returns_canonical_label_and_library_type() -> None:
    assert resolve_library_entry("Raspagem de Gancho") == ("Butterfly Sweep", "sweep")
    assert resolve_library_entry("mata leao") == ("Rear Naked Choke", "submission")  # variant
    assert resolve_library_entry("Unknown Technique Nobody Logged") is None


def test_library_lookup_is_deterministic_across_calls() -> None:
    a = resolve_library_entry("Pressão")
    b = resolve_library_entry("Pressão")
    assert a == b


def test_library_lookup_never_reads_the_sibling_app_repo_at_runtime() -> None:
    """CI checks out this repo alone (no `../GrapplingArcApp`) — `taxonomy_kind` must resolve
    labels from a vendored, committed artifact, not by opening the sibling repo's file. The
    generator (`scripts/export_taxonomy_kind_fixtures.py`) is the one place allowed to read the
    App live; this module must not."""
    import inspect

    import analysis.taxonomy_kind as tk

    # `parent.parent.parent` is the sibling-repo traversal idiom used elsewhere in this repo
    # (e.g. `cv/vocab_map.py`'s `_DEFAULT_NODES_PATH`) to reach `../GrapplingArcApp` — this
    # module must not construct such a path at all.
    assert "parent.parent.parent" not in inspect.getsource(tk)
    # still resolves real library data, from the committed artifact
    assert resolve_library_entry("Raspagem de Gancho") == ("Butterfly Sweep", "sweep")


# ── reconciliation with existing constants ──────────────────────────────────────
def test_reconciled_with_decision_flow_action_types() -> None:
    assert ACTION_TYPES == frozenset(
        {"pass", "takedown", "sweep", "submission", "escape", "transition"}
    )


def test_stable_state_types_disjoint_from_forced_action_types() -> None:
    assert STABLE_STATE_TYPES.isdisjoint(ACTION_TYPES)


def test_stable_state_types_are_not_always_state() -> None:
    """`STABLE_STATE_TYPES` names a common case, not a guarantee — some of its labels resolve
    to 'action' via `lamas_state` (guard pull, back take)."""
    assert "guard" in STABLE_STATE_TYPES
    assert kind_of("Guard Pull", "guard") == "action"
    assert "control" in STABLE_STATE_TYPES
    assert kind_of("Back Take", "control") == "action"


# ── D2: inference table resolution ───────────────────────────────────────────────
def test_pair_resolution_exact_beats_wildcard() -> None:
    table = {"submission|submission": "chained submission", "*|*": "scramble"}
    assert resolve_pair(table, "submission", "submission") == "chained submission"


def test_pair_resolution_first_wildcard_match_wins() -> None:
    table = {"takedown|*": "top transition", "sweep|*": "top transition", "*|*": "scramble"}
    assert resolve_pair(table, "takedown", "pass") == "top transition"
    assert resolve_pair(table, "sweep", "submission") == "top transition"


def test_pair_resolution_falls_back_to_wildcard_wildcard() -> None:
    table = {"takedown|*": "top transition", "*|*": "scramble"}
    assert resolve_pair(table, "submission", "pass") == "scramble"


def test_shipped_inference_table_resolves_the_documented_examples() -> None:
    table = load_inference_table()
    # Phase 1: the four dead generic states took their action_pair_to_state rows with them —
    # an ordinary action-action pair no longer resolves to an anchor at all.
    assert infer_state_for_action_pair(table, "submission", "submission") is None
    assert infer_action_for_state_pair(table, "guard", "guard")["action_key"] == "guard transition"
    assert infer_action_for_state_pair(table, "control", "guard")["action_key"] == "guard recovery"


# ── D2: the genuinely-terminal marker → 'finish' ─────────────────────────────────
def test_terminal_submission_resolves_to_finish_not_scramble() -> None:
    table = load_inference_table()
    assert resolve_pair(table["action_pair_to_state"], "submission", "$terminal") == "finish"
    entry = infer_state_for_action_pair(table, "submission", "$terminal")
    assert entry is not None and entry["node_key"] == "finish"


def test_mid_chain_submission_no_longer_resolves_to_a_generic_state() -> None:
    """Only a REAL terminal call (the literal `$terminal` sentinel) resolves to 'finish' — a
    submission followed by a real next action type used to fall to the '*|*' fallback
    ('scramble'); Phase 1 removed that row along with the state (the two actions now just
    stack in the caller's own transition instead), so resolution legitimately returns None."""
    table = load_inference_table()
    assert resolve_pair(table["action_pair_to_state"], "submission", "pass") is None
    assert infer_state_for_action_pair(table, "submission", "pass") is None


def test_terminal_takedown_sweep_pass_now_resolve_to_no_anchor() -> None:
    """Phase 1 removed the 'type|*' rows (`data/taxonomy/inference_table.json`) — only
    'submission|$terminal' survives as a closing anchor. A chain ending on takedown/sweep/pass
    now closes with NO anchor, same spirit as `ChainState.nascent`."""
    table = load_inference_table()
    for typ in ("takedown", "sweep", "pass"):
        assert infer_state_for_action_pair(table, typ, "$terminal") is None


# ── D2: 2026-08-27 pairs — generic vocabulary differentiation ───────────────────
def test_start_pass_resolves_to_start_top() -> None:
    """A chain opening on a `pass` means you were already on top of their guard —
    established position, not scramble."""
    table = load_inference_table()
    entry = infer_state_for_action_pair(table, "$start", "pass")
    assert entry is not None and entry["node_key"] == "start top"


def test_start_escape_resolves_to_start_bottom() -> None:
    """A chain opening on an `escape` means you were pinned — established bottom."""
    table = load_inference_table()
    entry = infer_state_for_action_pair(table, "$start", "escape")
    assert entry is not None and entry["node_key"] == "start bottom"


def test_start_submission_folds_into_start_neutral() -> None:
    """A chain opening on a `submission` attempt folds into 'start neutral'. It briefly had a
    dedicated 'start engaged' node; the owner removed it (2026-08-27) as a meaningless concept,
    and the neutral anchor is the honest home: where the athlete was before an unlogged
    submission is genuinely unknown. There is deliberately NO generic state left that means
    'engaged but unplaced'."""
    table = load_inference_table()
    entry = infer_state_for_action_pair(table, "$start", "submission")
    assert entry is not None
    assert entry["node_key"] == "start neutral"
    assert entry["orientation"] == "neutral"
    assert entry["role"] == "anchor"
    assert "start engaged" not in table["generic_states"]


def test_control_control_resolves_to_control_transition() -> None:
    table = load_inference_table()
    assert infer_action_for_state_pair(table, "control", "control")["action_key"] == (
        "control transition"
    )


def test_control_guard_resolves_to_guard_recovery() -> None:
    table = load_inference_table()
    assert infer_action_for_state_pair(table, "control", "guard")["action_key"] == "guard recovery"


def test_guard_control_resolves_to_guard_exit() -> None:
    table = load_inference_table()
    assert infer_action_for_state_pair(table, "guard", "control")["action_key"] == "guard exit"


# ── D2: opening ($start) rows and the role-marked generic states ────────────────
def test_start_sentinel_resolves_takedown_to_start_neutral() -> None:
    table = load_inference_table()
    assert resolve_pair(table["action_pair_to_state"], "$start", "takedown") == "start neutral"
    entry = infer_state_for_action_pair(table, "$start", "takedown")
    assert entry is not None and entry["node_key"] == "start neutral"


def test_start_sweep_resolves_to_start_bottom() -> None:
    """Fase 1b (owner call, 2026-08-31): a sweep is EXECUTED from the bottom — the sweeper
    starts underneath and reverses — so a chain opening on one anchors at 'start bottom'."""
    table = load_inference_table()
    entry = infer_state_for_action_pair(table, "$start", "sweep")
    assert entry is not None and entry["node_key"] == "start bottom"


def test_start_transition_resolves_to_start_neutral() -> None:
    """Fase 1b: a generic transition carries no orientation claim of its own, so a chain
    opening on one anchors at 'start neutral'."""
    table = load_inference_table()
    entry = infer_state_for_action_pair(table, "$start", "transition")
    assert entry is not None and entry["node_key"] == "start neutral"


def test_start_sentinel_unmapped_type_now_resolves_to_no_declarative_row() -> None:
    """'takedown'/'pass'/'escape'/'submission'/'sweep'/'transition' have declarative opening
    rows — every OTHER type used to fall through to the '*|*' fallback ('scramble'); Phase 1
    removed that catch-all, so an unmapped opening type resolves to no DECLARATIVE row here.
    (Fase 1b: the caller, `chain_compiler._opening_state`, still resolves it — via
    `resolve_anchor_by_role` — this function's contract stays 'declarative rows only'.)"""
    table = load_inference_table()
    for typ in ("guard", "control"):
        assert infer_state_for_action_pair(table, "$start", typ) is None


def test_generic_states_role_markers() -> None:
    table = load_inference_table()
    states = table["generic_states"]
    assert states["finish"]["role"] == "finish"
    assert states["start neutral"]["role"] == "anchor"
    assert states["start top"]["role"] == "anchor"
    assert states["start bottom"]["role"] == "anchor"
    # Phase 1 killed the four generics that never carried a role ('scramble', 'top transition',
    # 'bottom transition', 'chained submission') — only the presentation anchors remain, and
    # every one of them DOES carry a role.
    assert set(states) == {"finish", "start neutral", "start top", "start bottom"}


def test_start_nodes_carry_the_documented_orientation() -> None:
    table = load_inference_table()
    states = table["generic_states"]
    assert states["start neutral"]["orientation"] == "neutral"
    assert states["start top"]["orientation"] == "top"
    assert states["start bottom"]["orientation"] == "bottom"


# ── role_of: the standalone node_key -> role lookup ──────────────────────────────
def test_role_of_generic_states() -> None:
    assert role_of("finish") == "finish"
    assert role_of("start neutral") == "anchor"
    assert role_of("start top") == "anchor"
    assert role_of("start bottom") == "anchor"


def test_role_of_unknown_or_real_technique_node_is_none() -> None:
    assert role_of("mount") is None
    assert role_of("Some Made Up Node Nobody Curated") is None


# ── orientation_of: top | bottom | neutral per state (curated) ──────────────────
def test_orientation_of_bottom_guards() -> None:
    for label in ("Closed Guard", "Half Guard", "Spider Guard", "X-Guard"):
        assert orientation_of(label) == "bottom"


def test_symmetric_positions_are_neutral() -> None:
    """Owner call 2026-09-03 (docs/taxonomy/04_ONTOLOGIA_CANONICA.md §5): symmetric positions
    default to `neutral`; top/bottom only where the data supports it. These read `bottom` until
    then, contradicting `attribution._GUARD_NEUTRAL`, which always called them symmetric."""
    from analysis.attribution import classify

    for label in ("50/50 Guard", "Single Leg X", "Shin to Shin Guard"):
        assert orientation_of(label) == "neutral", label
        assert classify("guard", label).actor_role == "neutral", label


def test_orientation_of_top_controls() -> None:
    for label in ("Mount", "Side Control", "Knee on Belly", "Back Control", "Crucifix"):
        assert orientation_of(label) == "top"


def test_orientation_of_carve_out_positions() -> None:
    assert orientation_of("Turtle Position") == "bottom"
    assert orientation_of("Body Triangle") == "top"
    assert orientation_of("Body Lock from Back") == "top"


def test_orientation_of_neutral_defaults_for_unknown_and_ambiguous_labels() -> None:
    assert orientation_of("Some Made Up State Nobody Curated") == "neutral"
    assert orientation_of("Electric Chair") == "neutral"  # leg-lock control, ambiguous by design


def test_orientation_of_generic_states() -> None:
    """Phase 1 removed the four dead generics ('scramble', 'top transition', 'bottom
    transition', 'chained submission') from `state_orientation.json` along with the states
    themselves — only the presentation anchors remain curated."""
    assert orientation_of("Finish") == "neutral"
    assert orientation_of("Start Neutral") == "neutral"
    assert orientation_of("Start Top") == "top"
    assert orientation_of("Start Bottom") == "bottom"
    assert orientation_of("Start Engaged") == "neutral"  # never existed — default, not curated


def test_state_orientation_json_agrees_with_inference_table_generic_states() -> None:
    """`state_orientation.json` is the curated table's own doc-claimed "single source" for a
    generic state's orientation — the copy inside `inference_table.json`'s `generic_states`
    block must never drift from it."""
    table = load_inference_table()
    orientation = load_orientation_table()
    for key, entry in table["generic_states"].items():
        assert entry["orientation"] == orientation[key], key


def test_orientation_of_deterministic_across_calls() -> None:
    assert orientation_of("Mount") == orientation_of("Mount")


def test_orientation_table_only_carries_the_three_valid_values() -> None:
    table = load_orientation_table()
    assert set(table.values()) <= {"top", "bottom", "neutral"}


# ── Fase 2: orientation on the inference path, and the exit axis ─────────────────
def test_orientation_for_inference_prefers_the_declared_table() -> None:
    from analysis.taxonomy_kind import orientation_for_inference
    assert orientation_for_inference("control", "Mount") == StanceReading("top", "declared")
    assert orientation_for_inference("guard", "Closed Guard") == StanceReading(
        "bottom", "declared")


def test_orientation_for_inference_resolves_through_the_library_before_deriving() -> None:
    """"Back Take" is the corpus's third-largest state node (degree 211) and has NO row in
    `state_orientation.json` — the curated row is under its library-canonical name, "Back
    Control". Reading the label as written misses it; resolving through the library does not,
    and the answer is still `declared`, not a guess."""
    from analysis.taxonomy_kind import orientation_for_inference
    assert orientation_of("Back Take") == "neutral"          # `orientation_of` is untouched
    assert orientation_for_inference("control", "Back Take") == StanceReading("top", "declared")


def test_orientation_for_inference_falls_back_to_attribution_and_says_so() -> None:
    """The second level is explicitly labelled `derived`: `Kimura Grip` has no curated
    orientation row, and `attribution` reads it as `controlling` — a real positional claim on
    the OTHER of attribution's two axes, which is why the return type carries five values and
    not three."""
    from analysis.taxonomy_kind import orientation_for_inference
    assert orientation_of("Kimura Grip") == "neutral"
    assert orientation_for_inference("control", "Kimura Grip") == StanceReading(
        "controlling", "derived")


def test_orientation_for_inference_is_neutral_when_nothing_answers() -> None:
    from analysis.taxonomy_kind import orientation_for_inference
    assert orientation_for_inference("submission", "Armbar").value == "neutral"
    assert orientation_for_inference("control", "Some Made Up State").value == "controlling"


def test_orientation_of_is_untouched_by_fase_2() -> None:
    """The owner's explicit constraint: `orientation_of`'s refusal to guess is the reason it
    exists, and `export_taxonomy_kind_fixtures` mirrors its output into the App. Fase 2 added a
    SECOND function instead of widening this one."""
    assert orientation_of("Sweep") == "neutral"
    assert orientation_of("Guard Pass") == "neutral"
    assert set(load_orientation_table().values()) <= {"top", "bottom", "neutral"}


def test_orientation_for_inference_covers_every_curated_label() -> None:
    """The payoff, measured over `attribution`'s own curated lists: originally 52 of the 74
    labels that ought to carry an orientation read `neutral` through `state_orientation.json`
    alone (70%, and all 13 grips), which left the inversion rule blind. The three-level reading
    takes that to zero. `_GUARD_NEUTRAL` is excluded from the count on purpose — 50/50 is
    symmetric by construction, so `neutral` is the correct answer there and stays.

    50, not 52, since N1 (2026-09-04): "close guard" and "north south control" now
    `canonicalize` to a declared node ("closed guard" / "northsouth position" — the same
    corpus-count merge as `names.SYNONYMS`), so they're no longer blind before the
    three-level reading even runs."""
    from analysis import attribution as attr
    from analysis.taxonomy_kind import orientation_for_inference

    curated = [(label, "guard") for label in attr._GUARD_BOTTOM]
    curated += [(label, "control")
                for group in (attr._CONTROL_TOP, attr._CONTROL_BACK, attr._CONTROL_GRIP)
                for label in group]
    assert len(curated) == 74
    blind_before = [
        label for label, _t in curated
        if orientation_of(canonicalize(_normalize_name(label))) == "neutral"
    ]
    assert len(blind_before) == 50
    blind_after = [label for label, t in curated
                   if orientation_for_inference(t, label).value == "neutral"]
    assert blind_after == []
    # `_GUARD_NEUTRAL` keeps its `neutral`, EXCEPT for the four labels where the curated
    # orientation table and `attribution` already contradict each other — a pre-existing item on
    # the owner's explicit "não tocar agora" backlog, inherited here rather than silently
    # resolved: the declared table stays the truth, and this pins exactly which labels it wins.
    # Owner call 2026-09-03: the four labels that used to read `bottom` here through the
    # declared table WERE that contradiction. Resolved in `state_orientation.json`, so the set
    # is empty now and any regrowth is a regression.
    contradicted: set[str] = set()
    assert {label for label in attr._GUARD_NEUTRAL
            if orientation_for_inference("guard", label).value != "neutral"} == contradicted


def test_exit_orientation_is_curated_by_type_with_a_declared_default() -> None:
    """The axis Fase 1b recorded as missing (§7 of the contract doc): where an action LEAVES
    you. `classify(...).actor_role` answers `executor` for sweep/escape/takedown, which is a
    relation and not a position, so 146 of 160 chain closes landed on `start neutral`."""
    from analysis.taxonomy_kind import exit_orientation
    table = load_inference_table()
    assert exit_orientation(table, "sweep") == "top"
    assert exit_orientation(table, "takedown") == "top"
    assert exit_orientation(table, "pass") == "top"
    assert exit_orientation(table, "guard") == "bottom"
    assert exit_orientation(table, "reset") == "neutral"  # the declared "*" row


def test_exit_orientation_of_escape_is_neutral_because_the_corpus_says_standing() -> None:
    """MEASURED, and it contradicts the illustration in the owner's own plan ("uma cadeia que
    termina em escapada chega em por baixo"). Of the 83 `escape` events in the 281-bout corpus,
    75 are literally `Escape to Standing` (60) or `Stand-up Escape` (15) — 90% escape to the
    FEET, which is neutral, not bottom. Only 2 escapes are followed by a state at all, so
    there is no outcome evidence either way; the labels are the evidence."""
    from analysis.taxonomy_kind import exit_orientation
    assert exit_orientation(load_inference_table(), "escape") == "neutral"


def test_closing_anchor_uses_exit_orientation_not_the_actor_role() -> None:
    from analysis.taxonomy_kind import resolve_anchor_by_role, resolve_closing_anchor
    table = load_inference_table()
    assert resolve_closing_anchor(table, "sweep", "Hip Bump Sweep")["node_key"] == "start top"
    assert resolve_closing_anchor(table, "takedown", "Double Leg")["node_key"] == "start top"
    assert resolve_closing_anchor(table, "escape", "Escape to Standing")["node_key"] == (
        "start neutral")
    # the OPENING end still asks the opposite question and keeps its own resolver
    assert resolve_anchor_by_role(table, "sweep", "Hip Bump Sweep")["node_key"] == "start neutral"


def test_the_three_new_generics_collide_with_real_observed_action_keys() -> None:
    """Recorded, not fixed. `sweep`/`reversal`/`guard pass` are the canonical keys of real
    logged labels ("Sweep" 207 events, "Reversal" 16, "Guard Pass"+"Pass" 55), so an inferred
    generic and an observed action share one `node_key`. That is what contract invariant 1
    asks for — identity is `canonicalize(_normalize_name(label))`, full stop — and
    `ChainAction.inferred` is the discriminator every consumer already has. It matters at Fase
    6, where `graph_edges` would conflate the two without it; naming the generics something
    the vocabulary does not contain would have been the worse trade."""
    from analysis.names import _normalize_name
    table = load_inference_table()
    for key in ("sweep", "reversal", "guard pass"):
        assert table["generic_actions"][key]["action_key"] == key
        assert canonicalize(_normalize_name(table["generic_actions"][key]["label"])) != key


# ── the cross-repo fixture ───────────────────────────────────────────────────────
def test_golden_fixture_matches_this_implementation() -> None:
    app_nodes_path = ROOT.parent / "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json"
    if not app_nodes_path.is_file():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    from export.app_node_scores import canonical_label

    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    nodes = json.loads(app_nodes_path.read_text(encoding="utf-8"))
    from analysis.names import _normalize_name

    expected_keys = {
        _normalize_name(label) for n in nodes if (label := canonical_label(n))
    }
    assert len(doc["kinds"]) == len(expected_keys)
    for node in nodes:
        label = canonical_label(node)
        if not label:
            continue
        key = _normalize_name(label)
        typ = str(node.get("type") or "")
        kind = kind_of(label, typ)
        expected = {"kind": kind, "type": typ}
        if kind == "state":
            expected["orientation"] = orientation_of(label)
        assert doc["kinds"][key] == expected
    assert doc["inference_table"] == load_inference_table()
    assert doc["state_orientation"] == load_orientation_table()


def test_golden_actor_role_block_answers_exactly_like_classify() -> None:
    """The App reads `attribution.classify(...).actor_role` as a FLAT TABLE rather than as a
    second port of `attribution.py`'s 74 curated labels. That is only legitimate while the
    table answers identically for every curated pair AND for a label no table names (where the
    per-type default has to carry it) — this is the test that keeps it legitimate."""
    from analysis.attribution import classify
    from scripts.export_taxonomy_kind_fixtures import _curated_pairs

    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rows: dict[str, str] = doc["actor_role"]
    default: dict[str, str] = doc["actor_role_default"]

    def lookup(typ: str, label: str) -> str:
        key = f"{(typ or '').strip().lower()}|{_normalize_name(label)}"
        return rows.get(key) or default.get((typ or "").strip().lower(), "unknown")

    for typ, label in sorted(_curated_pairs()):
        assert lookup(typ, label) == classify(typ, label).actor_role, (typ, label)
    for typ in ("guard", "control", "pass", "sweep", "escape", "takedown", "submission",
                "transition", "", "nao existe"):
        unseen = "um rotulo que nenhuma tabela nomeia"
        assert lookup(typ, unseen) == classify(typ, unseen).actor_role, typ


def test_golden_orientation_probes_match_orientation_for_inference() -> None:
    from analysis.taxonomy_kind import orientation_for_inference

    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert doc["orientation_for_inference"], "probe set must not be empty"
    for probe, expected in doc["orientation_for_inference"].items():
        typ, _, label = probe.partition("|")
        reading = orientation_for_inference(typ, label)
        assert {"value": reading.value, "source": reading.source} == expected, probe


def test_both_repos_carry_the_same_fixture_bytes() -> None:
    if not APP_GOLDEN.is_file():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    assert APP_GOLDEN.read_text(encoding="utf-8") == GOLDEN.read_text(encoding="utf-8")


def test_app_inference_table_matches_analytics_source() -> None:
    if not APP_INFERENCE_TABLE.is_file():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    assert json.loads(APP_INFERENCE_TABLE.read_text(encoding="utf-8")) == load_inference_table()


def test_generator_check_flag_is_green() -> None:
    """`--check` must pass on the fixtures already materialized in the tree — a stale golden
    file is a defect this test exists to catch. Needs the sibling App repo (the generator's
    own source of truth); skip on a single-repo CI checkout, same pattern as the other
    cross-repo tests above."""
    app_nodes_path = ROOT.parent / "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json"
    if not app_nodes_path.is_file():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.export_taxonomy_kind_fixtures", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
