"""Stored style-profile patterns -> compiled dossier Decision Flow."""

from __future__ import annotations

from copy import deepcopy

import pytest

from analysis.dossier_decision_flow import (
    build_decision_flow_from_raw_patterns,
    decision_pattern_from_mapping,
)
from analysis.flowchart_compiler import COMPILER_VERSION
from analysis.flowchart_layout import LAYOUT_VERSION
from analysis.flowchart_router import ROUTING_VERSION


def _evidence(match_id: str, slug: str, index: int) -> dict[str, object]:
    return {
        "match_id": match_id,
        "match_slug": slug,
        "athlete_id": "athlete-1",
        "action_index": index,
        "condition_indexes": [index + 1],
        "response_index": index + 2,
        "timestamp_seconds": index * 10,
    }


def _pattern(
    action: str,
    condition: str | None,
    response: str,
    match_id: str,
    index: int,
) -> dict[str, object]:
    slug = "match:one/raw" if index == 1 else f"ada-vs-rival-{match_id}"
    return {
        "source_position_key": "closed-guard",
        "action_key": action,
        "condition_key": condition,
        "response_key": response,
        "resulting_position_key": "top-control",
        "count": 1,
        "success_count": 1,
        "failure_count": 0,
        "unknown_result_count": 0,
        "match_count": 1,
        "confidence": 0.66,
        "source": "observed",
        "action_type": "sweep",
        "outcome_type": "transition",
        "evidence": [_evidence(match_id, slug, index)],
    }


@pytest.fixture()
def eligible_raw_patterns() -> list[dict[str, object]]:
    return [
        _pattern("hip-bump", "cond:posts-hand", "mount", "m1", 1),
        _pattern("hip-bump", "cond:squares-hips", "armbar", "m2", 4),
        _pattern("hip-bump", "cond:posts-hand", "kimura", "m3", 7),
        _pattern("arm-drag", "cond:squares-hips", "back-control", "m1", 10),
        _pattern("arm-drag", "cond:posts-hand", "single-leg", "m2", 13),
    ]


def _build(raw_patterns: list[dict[str, object]]) -> dict[str, object] | None:
    return build_decision_flow_from_raw_patterns(
        athlete_name="Ada Grappler",
        athlete_key="ada grappler",
        raw_patterns=raw_patterns,
    )


def test_mapping_reconstructs_all_fields_and_tuple_evidence(
    eligible_raw_patterns: list[dict[str, object]],
) -> None:
    value = eligible_raw_patterns[0]
    pattern = decision_pattern_from_mapping(value)

    assert pattern.source_position_key == value["source_position_key"]
    assert pattern.action_key == value["action_key"]
    assert pattern.condition_key == value["condition_key"]
    assert pattern.response_key == value["response_key"]
    assert pattern.resulting_position_key == value["resulting_position_key"]
    assert pattern.count == value["count"]
    assert pattern.success_count == value["success_count"]
    assert pattern.failure_count == value["failure_count"]
    assert pattern.unknown_result_count == value["unknown_result_count"]
    assert pattern.match_count == value["match_count"]
    assert pattern.confidence == value["confidence"]
    assert pattern.source == value["source"]
    assert pattern.action_type == value["action_type"]
    assert pattern.outcome_type == value["outcome_type"]
    assert pattern.evidence[0].condition_indexes == (2,)


def test_raw_patterns_compile_deterministically_with_runtime_versions_and_opaque_ids(
    eligible_raw_patterns: list[dict[str, object]],
) -> None:
    first = _build(eligible_raw_patterns)
    second = _build(deepcopy(eligible_raw_patterns))

    assert first == second
    assert first is not None
    assert first["compilerVersion"] == COMPILER_VERSION
    assert first["layoutVersion"] == LAYOUT_VERSION
    assert first["layouts"]["compact"]["routingVersion"] == ROUTING_VERSION
    assert first["layouts"]["desktop"]["routingVersion"] == ROUTING_VERSION
    evidence = first["evidence"]
    evidence_ids = [item for node in first["nodes"] for item in node["evidenceIds"]]
    assert all(item in evidence for item in evidence_ids)
    assert "m:match:one/raw:i:1" in evidence


def test_raw_patterns_omit_empty_or_rootless_payload(
    eligible_raw_patterns: list[dict[str, object]],
) -> None:
    rootless = [{**pattern, "source_position_key": None} for pattern in eligible_raw_patterns]
    assert _build([]) is None
    assert _build(rootless) is None


@pytest.mark.parametrize("failure", ["matches", "patterns", "conditions", "branches"])
def test_raw_patterns_preserve_existing_eligibility_failures(
    eligible_raw_patterns: list[dict[str, object]],
    failure: str,
) -> None:
    patterns = deepcopy(eligible_raw_patterns)
    if failure == "matches":
        for pattern in patterns:
            pattern["evidence"][0]["match_id"] = "only-match"
    elif failure == "patterns":
        patterns = patterns[:4]
    elif failure == "conditions":
        for pattern in patterns:
            pattern["condition_key"] = "cond:posts-hand"
    else:
        for pattern in patterns:
            pattern["action_key"] = "hip-bump"

    assert _build(patterns) is None
