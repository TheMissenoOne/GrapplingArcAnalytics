"""Tests for analysis/flowchart_compiler.py — deterministic, no network/DB."""

from pathlib import Path
from typing import Any

import pytest

from analysis.decision_flow import DecisionPattern, PatternEvidence
from analysis.flowchart_compiler import (
    COMPILER_VERSION,
    ExpertBranch,
    FlowchartDefinition,
    compile_flowchart,
    load_definitions,
    spec_to_dict,
)


def _def(**overrides: Any) -> FlowchartDefinition:
    base: dict[str, Any] = dict(
        key="gordon-ryan-closed-guard",
        title="Closed Guard Decision Flow",
        athlete_key="gordon ryan",
        root_position_key="closed guard",
    )
    base.update(overrides)
    return FlowchartDefinition(**base)


def _pattern(**overrides: Any) -> DecisionPattern:
    base: dict[str, Any] = dict(
        source_position_key="closed guard",
        action_key="hip bump sweep",
        condition_key="cond:posts-hand",
        response_key="kimura grip",
        resulting_position_key="closed guard",
        count=4,
        success_count=3,
        failure_count=1,
        unknown_result_count=0,
        match_count=2,
        confidence=0.75,
        source="observed",
        outcome_type="submission",
    )
    base.update(overrides)
    return DecisionPattern(**base)


def _expert(**overrides: Any) -> ExpertBranch:
    base: dict[str, Any] = dict(
        action_key="hip bump sweep",
        condition_key="cond:posts-hand",
        response_key="kimura grip",
    )
    base.update(overrides)
    return ExpertBranch(**base)


class TestCompile:
    def test_empty_patterns_keeps_root_only(self) -> None:
        spec = compile_flowchart(_def(), [])
        assert len(spec.nodes) == 1
        assert spec.nodes[0].kind == "root-position"
        assert len(spec.edges) == 0
        assert len(spec.branches) == 0

    def test_basic_branch_shape(self) -> None:
        spec = compile_flowchart(_def(), [_pattern()])
        kinds = [n.kind for n in spec.nodes]
        assert kinds == ["root-position", "athlete-action",
                         "opponent-condition", "outcome"]
        assert len(spec.edges) == 3  # action, reaction, response
        edge_kinds = {e.kind for e in spec.edges}
        assert edge_kinds == {"action", "reaction", "response"}
        assert len(spec.branches) == 1
        branch = spec.branches[0]
        assert branch.action_key == "hip bump sweep"
        assert spec.exchanges == 4
        assert spec.matches == 2

    def test_submission_becomes_outcome_node(self) -> None:
        spec = compile_flowchart(_def(), [_pattern()])
        outcome = [n for n in spec.nodes if n.kind == "outcome"][0]
        assert outcome.key == "kimura grip"
        assert outcome.success_rate == 0.75
        assert outcome.category == "submission"

    def test_plain_response_and_results_in(self) -> None:
        spec = compile_flowchart(
            _def(),
            [_pattern(outcome_type="sweep",
                     resulting_position_key="mount",
                     count=6, success_count=4, failure_count=2)],
        )
        kinds = [n.kind for n in spec.nodes]
        assert kinds == ["root-position", "athlete-action",
                         "opponent-condition", "response", "position"]
        position = [n for n in spec.nodes if n.kind == "position"][0]
        assert position.key == "mount"
        edge_kinds = {e.kind for e in spec.edges}
        assert "results-in" in edge_kinds
        response = [n for n in spec.nodes if n.kind == "response"][0]
        assert response.support == 6
        assert response.success_rate == 0.6667  # 4 / (4+2)

    def test_failure_becomes_denied_warning(self) -> None:
        spec = compile_flowchart(
            _def(),
            [_pattern(response_key=None, count=2, failure_count=2,
                      success_count=0, outcome_type="failure")],
        )
        outcome = [n for n in spec.nodes if n.kind == "outcome"][0]
        assert outcome.key == "denied"
        assert outcome.warning is True
        assert spec.exchanges == 2

    def test_wrong_root_position_filtered(self) -> None:
        spec = compile_flowchart(
            _def(),
            [_pattern(source_position_key="mount")],
        )
        assert len(spec.nodes) == 1  # only root

    def test_root_control_alias_accepted(self) -> None:
        spec = compile_flowchart(
            _def(),
            [_pattern(source_position_key="closed guard control")],
        )
        assert len(spec.nodes) == 4

    def test_minimum_support_drops_patterns(self) -> None:
        spec = compile_flowchart(
            _def(minimum_support=3),
            [_pattern(count=2)],
        )
        assert len(spec.nodes) == 1

    def test_deterministic(self) -> None:
        a = compile_flowchart(_def(), [_pattern(), _pattern(response_key="other"),
                                       _pattern(condition_key="cond:sprawl")])
        b = compile_flowchart(_def(), [_pattern(), _pattern(response_key="other"),
                                       _pattern(condition_key="cond:sprawl")])
        assert spec_to_dict(a) == spec_to_dict(b)

    def test_branch_limits_applied(self) -> None:
        patterns = [
            _pattern(action_key=f"action {i}") for i in range(8)
        ]
        spec = compile_flowchart(_def(max_primary_branches=3), patterns)
        assert len(spec.branches) == 3
        actions = [n.key for n in spec.nodes if n.kind == "athlete-action"]
        assert actions == ["action 0", "action 1", "action 2"]

    def test_condition_limit_and_rank(self) -> None:
        patterns = [
            _pattern(condition_key="cond:posts-hand", count=5),
            _pattern(condition_key="cond:sprawl", count=9),
            _pattern(condition_key="cond:stands", count=3),
        ]
        spec = compile_flowchart(_def(max_conditions_per_action=2), patterns)
        conds = [n.key for n in spec.nodes if n.kind == "opponent-condition"]
        assert conds == ["cond:sprawl", "cond:posts-hand"]

    def test_response_limit_applied(self) -> None:
        patterns = [
            _pattern(condition_key="cond:posts-hand", response_key="kimura grip", count=5),
            _pattern(condition_key="cond:posts-hand", response_key="armbar", count=4),
            _pattern(condition_key="cond:posts-hand", response_key="triangle", count=3),
            _pattern(condition_key="cond:posts-hand", response_key="omoplata", count=2),
        ]
        spec = compile_flowchart(_def(max_responses_per_condition=3), patterns)
        outcomes = [n.key for n in spec.nodes if n.kind == "outcome"]
        assert outcomes == ["kimura grip", "armbar", "triangle"]

    def test_expert_only_branch_materializes(self) -> None:
        expert = [_expert(action_key="flower sweep",
                          condition_key="cond:leans-forward",
                          response_key="sweep to mount")]
        spec = compile_flowchart(_def(), [], expert_branches=expert)
        actions = [n.key for n in spec.nodes if n.kind == "athlete-action"]
        assert "flower sweep" in actions
        nodes = {n.kind: n for n in spec.nodes}
        assert nodes["athlete-action"].source == "expert"
        assert "Expected" in (nodes["athlete-action"].subtitle or "")
        assert spec.sources["expert"] == 1

    def test_expert_overlap_marks_hybrid(self) -> None:
        expert = [_expert()]
        spec = compile_flowchart(_def(), [_pattern()], expert_branches=expert)
        outcome = [n for n in spec.nodes if n.kind == "outcome"][0]
        assert outcome.source == "hybrid"
        assert spec.sources["hybrid"] == 1
        assert spec.sources["expert"] == 0

    def test_expert_attaches_to_known_action(self) -> None:
        # known action + NEW condition → expert cond/response attach to the
        # existing branch, dashed "Expected", counted as expert source.
        expert = [_expert(condition_key="cond:stands")]
        spec = compile_flowchart(_def(), [_pattern()], expert_branches=expert)
        actions = [n for n in spec.nodes if n.kind == "athlete-action"]
        assert len(actions) == 1
        conds = [n for n in spec.nodes if n.kind == "opponent-condition"]
        assert len(conds) == 2
        attached = next(n for n in conds if n.key == "cond:stands")
        assert attached.source == "expert"
        assert attached.subtitle == "Expected"
        branch = spec.branches[0]
        assert any(c.endswith(":cond:stands") for c in branch.conditions)
        assert spec.sources["expert"] == 1

    def test_expert_duplicate_skipped(self) -> None:
        expert = [_expert(action_key="flower sweep",
                          condition_key="cond:leans-forward",
                          response_key="sweep to mount"),
                  _expert(action_key="flower sweep",
                          condition_key="cond:leans-forward",
                          response_key="sweep to mount")]
        spec = compile_flowchart(_def(), [], expert_branches=expert)
        actions = [n for n in spec.nodes if n.kind == "athlete-action"]
        assert len(actions) == 1
        assert spec.sources["expert"] == 1

    def test_portal_when_response_reused_across_conditions(self) -> None:
        patterns = [
            _pattern(condition_key="cond:posts-hand", response_key="kimura grip",
                     outcome_type="sweep", resulting_position_key="mount"),
            _pattern(condition_key="cond:sprawl", response_key="kimura grip",
                     outcome_type="sweep", resulting_position_key="mount"),
            _pattern(action_key="kimura grip", condition_key="cond:frames",
                     response_key="armbar", outcome_type="submission",
                     resulting_position_key="closed guard"),
        ]
        spec = compile_flowchart(_def(), patterns)
        portals = [n for n in spec.nodes if n.kind == "portal"]
        # scoped ids: one portal per (action, condition) context, same key
        assert len(portals) == 2
        assert {p.key for p in portals} == {"kimura grip"}
        assert all("↪" in p.title for p in portals)
        # both portal nodes keep a response edge from their own condition
        portal_targets = {e.target for e in spec.edges if e.kind == "response"}
        assert {p.id for p in portals} <= portal_targets

    def test_sources_and_meta(self) -> None:
        spec = compile_flowchart(_def(), [_pattern()])
        assert spec.schema_version == 1
        assert spec.key == "gordon-ryan-closed-guard"
        assert spec.athlete_label == "Gordon Ryan"
        assert spec.root_position_label == "Closed Guard"
        assert spec.sources == {"observed": 1, "expert": 0, "hybrid": 0}

    def test_spec_to_dict_shape(self) -> None:
        spec = compile_flowchart(_def(), [_pattern()])
        d = spec_to_dict(spec)
        assert d["schemaVersion"] == 1
        assert d["key"] == "gordon-ryan-closed-guard"
        assert {n["kind"] for n in d["nodes"]} == {
            "root-position", "athlete-action", "opponent-condition", "outcome"}
        assert {e["kind"] for e in d["edges"]} == {"action", "reaction", "response"}
        assert d["branches"][0]["actionKey"] == "hip bump sweep"
        assert d["sources"]["observed"] == 1
        assert d["compilerVersion"] == COMPILER_VERSION
        assert d["layoutVersion"] is None
        assert d["evidence"] == {}

    def test_evidence_registry_referenced_only(self) -> None:
        ev = PatternEvidence(
            match_id="m1", match_slug="gordon-ryan-vs-x-2021", athlete_id="gordon-ryan",
            action_index=14, condition_indexes=(15,), response_index=16,
            timestamp_seconds=782,
        )
        spec = compile_flowchart(
            _def(), [_pattern(evidence=[ev])],
            evidence_meta={"gordon-ryan-vs-x-2021": {
                "matchLabel": "Gordon Ryan vs X · ADCC 2021", "year": 2021,
                "videoId": "abc123"}},
            layout_version=2,
        )
        d = spec_to_dict(spec)
        assert d["compilerVersion"] == COMPILER_VERSION
        assert d["layoutVersion"] == 2
        eid = "m:gordon-ryan-vs-x-2021:i:14"
        entry = d["evidence"][eid]
        assert entry["matchSlug"] == "gordon-ryan-vs-x-2021"
        assert entry["matchLabel"] == "Gordon Ryan vs X · ADCC 2021"
        assert entry["year"] == 2021
        assert entry["videoId"] == "abc123"
        assert entry["timestampSeconds"] == 782
        refs = {eid for n in d["nodes"] for eid in n["evidenceIds"]}
        assert set(d["evidence"]) == refs

    def test_evidence_missing_meta_keeps_slug(self) -> None:
        ev = PatternEvidence(
            match_id="m1", match_slug="gordon-ryan-vs-x-2021", athlete_id="gordon-ryan",
            action_index=3, condition_indexes=(), response_index=None,
            timestamp_seconds=None,
        )
        d = spec_to_dict(compile_flowchart(_def(), [_pattern(evidence=[ev])]))
        entry = d["evidence"]["m:gordon-ryan-vs-x-2021:i:3"]
        assert entry == {"matchSlug": "gordon-ryan-vs-x-2021"}
        assert "timestampSeconds" not in entry


class TestLoadDefinitions:
    def test_loads_real_file(self) -> None:
        path = Path(__file__).resolve().parents[1] / "data" / "flowchart_definitions.json"
        defs = load_definitions(path)
        assert len(defs) == 1
        d = defs[0]
        assert d.key == "gordon-ryan-closed-guard"
        assert d.root_position_key == "closed guard"
        assert d.published is True
        assert d.minimum_support == 1

    def test_missing_required_field(self, tmp_path: Path) -> None:
        p = tmp_path / "defs.json"
        p.write_text('[{"title": "x"}]', encoding="utf-8")
        with pytest.raises(ValueError, match="missing 'key'"):
            load_definitions(p)

    def test_not_a_list(self, tmp_path: Path) -> None:
        p = tmp_path / "defs.json"
        p.write_text('{"key": "x"}', encoding="utf-8")
        with pytest.raises(ValueError, match="must be a JSON list"):
            load_definitions(p)

    def test_default_published(self, tmp_path: Path) -> None:
        p = tmp_path / "defs.json"
        p.write_text('[{"key": "k", "title": "t", "athlete_key": "a",'
                     ' "root_position_key": "r"}]', encoding="utf-8")
        assert load_definitions(p)[0].published is True


class TestMeasuredDetail:
    """Node detail must be measured, never authored — and must survive missing data."""

    def test_action_detail_counts_matches_and_destinations(self) -> None:
        spec = compile_flowchart(_def(), [
            _pattern(resulting_position_key="mount", count=3,
                     evidence=[PatternEvidence("m1", "gordon-ryan-vs-kaynan-duarte-2025",
                                               "a", 1, (2,), 3, 222)]),
            _pattern(condition_key="cond:frames", resulting_position_key="back control",
                     count=1,
                     evidence=[PatternEvidence("m2", "anna-vieira-vs-gordon-ryan-2024",
                                               "a", 1, (2,), 3, None)]),
        ])
        action = next(n for n in spec.nodes if n.kind == "athlete-action")
        assert action.detail[0] == "4× across 2 matches"
        assert any(d.startswith("lands in Mount 3×") for d in action.detail)
        # provenance names the OPPONENT and the clock, whichever side the athlete was on
        assert "vs Kaynan Duarte '25 · 3:42" in action.detail

    def test_no_evidence_means_no_fabricated_lines(self) -> None:
        spec = compile_flowchart(_def(), [_pattern(evidence=[])])
        action = next(n for n in spec.nodes if n.kind == "athlete-action")
        # counts and rate still hold; provenance simply does not appear
        assert action.detail == ["4× across 0 matches", "75% completed (3/4)"]
        assert not any("vs " in d for d in action.detail)

    def test_rate_line_withheld_below_three_known_outcomes(self) -> None:
        thin = compile_flowchart(_def(), [_pattern(success_count=1, failure_count=1)])
        assert not any("completed" in d
                       for n in thin.nodes for d in n.detail)
        thick = compile_flowchart(_def(), [_pattern(success_count=3, failure_count=1)])
        action = next(n for n in thick.nodes if n.kind == "athlete-action")
        assert "75% completed (3/4)" in action.detail

    def test_category_comes_from_the_move_not_the_tree_slot(self) -> None:
        spec = compile_flowchart(_def(), [_pattern(action_type="sweep")])
        action = next(n for n in spec.nodes if n.kind == "athlete-action")
        assert action.category == "sweep"
        # opponent reactions are not techniques and must stay uncoloured
        cond = next(n for n in spec.nodes if n.kind == "opponent-condition")
        assert cond.category is None


class TestReactionFidelity:
    """The extractor refuses to invent a reaction; the chart must not invent one either."""

    def test_no_condition_means_no_condition_node(self) -> None:
        spec = compile_flowchart(_def(), [_pattern(condition_key=None)])
        assert not [n for n in spec.nodes if n.kind == "opponent-condition"]
        # the response hangs straight off the action instead
        action = next(n for n in spec.nodes if n.kind == "athlete-action")
        resp = next(n for n in spec.nodes if n.kind in ("response", "outcome"))
        assert any(e.source == action.id and e.target == resp.id for e in spec.edges)
        assert spec.branches[0].conditions == []

    def test_bundled_condition_key_is_humanised(self) -> None:
        spec = compile_flowchart(_def(), [
            _pattern(condition_key="cond:posts-hand-and-cond:squares-hips")])
        cond = next(n for n in spec.nodes if n.kind == "opponent-condition")
        assert cond.title == "Posts Hand and Squares Hips"
        assert "cond:" not in cond.title
