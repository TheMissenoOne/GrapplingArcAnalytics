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
    load_inference_table,
    resolve_pair,
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
    assert infer_state_for_action_pair(table, "escape", "escape")["node_key"] == "scramble"
    assert infer_action_for_state_pair(table, "guard", "guard")["action_key"] == "guard transition"
    assert infer_action_for_state_pair(table, "control", "guard")["action_key"] == "transition"


# ── the cross-repo fixture ───────────────────────────────────────────────────────
def test_golden_fixture_matches_this_implementation() -> None:
    from export.app_node_scores import canonical_label

    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    nodes = json.loads(
        (ROOT.parent / "GrapplingArcApp" / "src" / "data" / "grappling-arch.nodes.json")
        .read_text(encoding="utf-8")
    )
    from analysis.names import _normalize_name

    expected_keys = {_normalize_name(canonical_label(n)) for n in nodes if canonical_label(n)}
    assert len(doc["kinds"]) == len(expected_keys)
    for node in nodes:
        label = canonical_label(node)
        if not label:
            continue
        key = _normalize_name(label)
        typ = str(node.get("type") or "")
        assert doc["kinds"][key] == {"kind": kind_of(label, typ), "type": typ}
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
