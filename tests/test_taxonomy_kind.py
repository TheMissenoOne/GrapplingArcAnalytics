"""D1/D2 taxonomy classifier: `kind_of`, the reconciliation asserts, the "Back Control" carve-
out, the D2 inference table resolution, and the cross-repo fixture round-trip."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from analysis.decision_flow import ACTION_TYPES
from analysis.perspective_sequence import STABLE_STATE_TYPES
from analysis.taxonomy_kind import (
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
    assert kind_of("Hooks In", "control") == "action"


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
    """"Body Lock" (no "from Back") is deliberately left alone — not a `BACK_TAKE_TOKENS`
    collision, and folding it in would move a Markov `CDP` weight (full ELO replay)."""
    assert kind_of("Body Lock", "control") == "action"


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
    assert infer_state_for_action_pair(table, "submission", "submission")["node_key"] == (
        "chained submission"
    )
    assert infer_state_for_action_pair(table, "takedown", "pass")["node_key"] == "top transition"
    assert infer_state_for_action_pair(table, "escape", "escape")["node_key"] == "bottom transition"
    assert infer_action_for_state_pair(table, "guard", "guard")["action_key"] == "guard transition"
    assert infer_action_for_state_pair(table, "control", "guard")["action_key"] == "guard recovery"


# ── D2: the genuinely-terminal marker → 'finish' ─────────────────────────────────
def test_terminal_submission_resolves_to_finish_not_scramble() -> None:
    table = load_inference_table()
    assert resolve_pair(table["action_pair_to_state"], "submission", "$terminal") == "finish"
    assert infer_state_for_action_pair(table, "submission", "$terminal")["node_key"] == "finish"


def test_mid_chain_submission_is_unaffected_by_the_terminal_marker() -> None:
    """Only a REAL terminal call (the literal `$terminal` sentinel) resolves to 'finish' — a
    submission followed by a real next action type still falls to the '*|*' fallback,
    unchanged (D2's own spec: mid-chain submission stays exactly as it was)."""
    table = load_inference_table()
    assert resolve_pair(table["action_pair_to_state"], "submission", "pass") == "scramble"
    assert infer_state_for_action_pair(table, "submission", "pass")["node_key"] == "scramble"


def test_terminal_takedown_sweep_pass_unaffected_by_the_new_marker() -> None:
    """The pre-existing 'type|*' rows still catch the terminal marker the same way they catch
    any other right-hand value — no behavior change for takedown/sweep/pass."""
    table = load_inference_table()
    for typ in ("takedown", "sweep", "pass"):
        assert infer_state_for_action_pair(table, typ, "$terminal")["node_key"] == "top transition"


# ── D2: 2026-08-27 pairs — generic vocabulary differentiation ───────────────────
def test_start_pass_resolves_to_start_top() -> None:
    """A chain opening on a `pass` means you were already on top of their guard —
    established position, not scramble."""
    table = load_inference_table()
    assert infer_state_for_action_pair(table, "$start", "pass")["node_key"] == "start top"


def test_start_escape_resolves_to_start_bottom() -> None:
    """A chain opening on an `escape` means you were pinned — established bottom."""
    table = load_inference_table()
    assert infer_state_for_action_pair(table, "$start", "escape")["node_key"] == "start bottom"


def test_start_submission_folds_into_start_neutral() -> None:
    """A chain opening on a `submission` attempt folds into 'start neutral'. It briefly had a
    dedicated 'start engaged' node; the owner removed it (2026-08-27) as a meaningless concept,
    and the neutral anchor is the honest home: where the athlete was before an unlogged
    submission is genuinely unknown. There is deliberately NO generic state left that means
    'engaged but unplaced'."""
    table = load_inference_table()
    entry = infer_state_for_action_pair(table, "$start", "submission")
    assert entry["node_key"] == "start neutral"
    assert entry["orientation"] == "neutral"
    assert entry["role"] == "start"
    assert "start engaged" not in table["generic_states"]


def test_escape_star_resolves_to_bottom_transition_mirroring_top_transition() -> None:
    table = load_inference_table()
    assert infer_state_for_action_pair(table, "escape", "anything")["node_key"] == (
        "bottom transition"
    )
    assert infer_state_for_action_pair(table, "escape", "anything")["orientation"] == "bottom"


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
    assert infer_state_for_action_pair(table, "$start", "takedown")["node_key"] == "start neutral"


def test_start_sentinel_unresolved_type_falls_back_to_scramble() -> None:
    """'takedown'/'pass'/'escape'/'submission' now have declarative opening rows (2026-08-27) —
    every OTHER type still falls through to the pre-existing '*|*' fallback via the `$start`
    sentinel, same as the plain '*' sentinel it replaces did."""
    table = load_inference_table()
    for typ in ("sweep", "transition"):
        assert infer_state_for_action_pair(table, "$start", typ)["node_key"] == "scramble"


def test_generic_states_role_markers() -> None:
    table = load_inference_table()
    states = table["generic_states"]
    assert states["finish"]["role"] == "finish"
    assert states["start neutral"]["role"] == "start"
    assert states["start top"]["role"] == "start"
    assert states["start bottom"]["role"] == "start"
    for bridge_key in ("scramble", "top transition", "bottom transition", "chained submission"):
        assert "role" not in states[bridge_key]


def test_start_nodes_carry_the_documented_orientation() -> None:
    table = load_inference_table()
    states = table["generic_states"]
    assert states["start neutral"]["orientation"] == "neutral"
    assert states["start top"]["orientation"] == "top"
    assert states["start bottom"]["orientation"] == "bottom"


# ── role_of: the standalone node_key -> role lookup ──────────────────────────────
def test_role_of_generic_states() -> None:
    assert role_of("finish") == "finish"
    assert role_of("start neutral") == "start"
    assert role_of("start top") == "start"
    assert role_of("start bottom") == "start"
    assert role_of("scramble") is None
    assert role_of("bottom transition") is None


def test_role_of_unknown_or_real_technique_node_is_none() -> None:
    assert role_of("mount") is None
    assert role_of("Some Made Up Node Nobody Curated") is None


# ── orientation_of: top | bottom | neutral per state (curated) ──────────────────
def test_orientation_of_bottom_guards() -> None:
    for label in ("Closed Guard", "Half Guard", "Spider Guard", "X-Guard", "50/50 Guard"):
        assert orientation_of(label) == "bottom"


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
    assert orientation_of("Scramble") == "neutral"
    assert orientation_of("Top Transition") == "top"
    assert orientation_of("Bottom Transition") == "bottom"
    assert orientation_of("Chained Submission") == "neutral"
    assert orientation_of("Finish") == "neutral"
    assert orientation_of("Start Neutral") == "neutral"
    assert orientation_of("Start Top") == "top"
    assert orientation_of("Start Bottom") == "bottom"
    assert orientation_of("Start Engaged") == "neutral"


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


# ── the cross-repo fixture ───────────────────────────────────────────────────────
def test_golden_fixture_matches_this_implementation() -> None:
    from export.app_node_scores import canonical_label

    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    nodes = json.loads(
        (ROOT.parent / "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json")
        .read_text(encoding="utf-8")
    )
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
    file is a defect this test exists to catch."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.export_taxonomy_kind_fixtures", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
