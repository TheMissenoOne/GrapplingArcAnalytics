"""Decision Flow dossier integration — eligibility grader and payload builder.

Determines whether an athlete qualifies for the Decision Flow view inside
their Grapple Like dossier, builds the payload, and emits the cache key
used by the export layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from analysis.decision_flow import DecisionPattern, aggregate_patterns, extract_patterns
from analysis.flowchart_compiler import (
    ExpertBranch,
    FlowchartDefinition,
    compile_flowchart,
    spec_to_dict,
)
from analysis.flowchart_layout import LAYOUT_VERSION, layout_flowchart, layout_to_dict


@dataclass(frozen=True)
class DecisionFlowQuality:
    matches: int
    observed_patterns: int
    distinct_conditions: int
    primary_branches: int


@dataclass(frozen=True)
class DecisionFlowEligibility:
    """Eligibility result for Decision Flow view."""
    eligible: bool
    quality: DecisionFlowQuality
    reason: str | None = None  # why not eligible (for beta badge copy)


def check_eligibility(
    *,
    distinct_matches: int,
    observed_patterns: int,
    distinct_opponent_conditions: int,
    primary_branches: int,
) -> DecisionFlowEligibility:
    """Eligibility rule per spec:
    distinct_matches >= 3
    AND observed_patterns >= 5
    AND distinct_opponent_conditions >= 2
    AND primary_branches >= 2
    """
    if distinct_matches < 3:
        return DecisionFlowEligibility(
            eligible=False,
            quality=DecisionFlowQuality(distinct_matches, observed_patterns,
                                        distinct_opponent_conditions, primary_branches),
            reason="Need ≥ 3 distinct matches"
        )
    if observed_patterns < 5:
        return DecisionFlowEligibility(
            eligible=False,
            quality=DecisionFlowQuality(distinct_matches, observed_patterns,
                                        distinct_opponent_conditions, primary_branches),
            reason="Need ≥ 5 observed patterns"
        )
    if distinct_opponent_conditions < 2:
        return DecisionFlowEligibility(
            eligible=False,
            quality=DecisionFlowQuality(distinct_matches, observed_patterns,
                                        distinct_opponent_conditions, primary_branches),
            reason="Need ≥ 2 distinct opponent conditions"
        )
    if primary_branches < 2:
        return DecisionFlowEligibility(
            eligible=False,
            quality=DecisionFlowQuality(distinct_matches, observed_patterns,
                                        distinct_opponent_conditions, primary_branches),
            reason="Need ≥ 2 primary branches"
        )
    return DecisionFlowEligibility(
        eligible=True,
        quality=DecisionFlowQuality(distinct_matches, observed_patterns,
                                    distinct_opponent_conditions, primary_branches)
    )


def _count_observed_patterns(patterns: list[DecisionPattern]) -> int:
    """Count patterns with source='observed'."""
    return sum(1 for p in patterns if p.source == "observed")


def _count_distinct_conditions(patterns: list[DecisionPattern]) -> int:
    """Count distinct opponent conditions across observed patterns."""
    conds = set()
    for p in patterns:
        if p.source == "observed" and p.condition_key:
            conds.add(p.condition_key)
    return len(conds)


def _count_primary_branches(spec: Any) -> int:
    """Count primary branches in the compiled spec."""
    return len(getattr(spec, "branches", []) or [])


def _count_distinct_matches(patterns: list[DecisionPattern]) -> int:
    """Count distinct matches from pattern evidence."""
    match_ids = set()
    for p in patterns:
        for ev in p.evidence:
            if ev.match_id:
                match_ids.add(ev.match_id)
    return len(match_ids)


def build_decision_flow_payload(
    athlete_name: str,
    athlete_key: str,
    root_position_key: str,
    root_position_label: str,
    matches: list[Any],
    perspective_events_fn: Callable[..., Any],
    match_slug_of_fn: Callable[..., str],
    sequence_boundaries_fn: Callable[..., set[int]],
    reaction_catalog: list[Any] | None,
) -> dict[str, Any] | None:
    """Build the full Decision Flow payload for an athlete.

    Returns None if not eligible (will show beta badge instead).
    """
    # Extract patterns from matches
    all_patterns: list[DecisionPattern] = []
    for m in matches:
        slug = match_slug_of_fn(m)
        winning = None
        submission = getattr(m, "submission", None)
        if submission:
            from analysis.names import _normalize_name
            winning = _normalize_name(str(submission))
        all_patterns.extend(extract_patterns(
            perspective_events_fn(m, None),  # athlete_id not needed here
            match_id=str(getattr(m, "id", "")),
            match_slug=slug,
            athlete_id="",
            boundaries=sequence_boundaries_fn(m, None),
            reaction_catalog=reaction_catalog,
            winning_submission_key=winning,
        ))
    patterns = aggregate_patterns(all_patterns)

    # Check eligibility
    distinct_matches = _count_distinct_matches(patterns)
    observed_patterns = _count_observed_patterns(patterns)
    distinct_conditions = _count_distinct_conditions(patterns)
    primary_branches = 0  # need spec to count branches, will recompute after compile

    # First compile to get branch count
    # We need a definition with the athlete's primary position
    # For now, use the most common root position from patterns
    root_pos = None
    for p in patterns:
        if p.source_position_key:
            root_pos = p.source_position_key
            break
    if not root_pos:
        return None

    definition = FlowchartDefinition(
        key=f"{athlete_key}-{root_pos}",
        title=f"{athlete_name} Decision Flow",
        athlete_key=athlete_key,
        root_position_key=root_pos,
    )
    expert_branches: list[ExpertBranch] = []
    spec = compile_flowchart(definition, patterns, expert_branches=expert_branches,
                             athlete_label=athlete_name, root_position_label=root_pos,
                             layout_version=LAYOUT_VERSION)

    primary_branches = _count_primary_branches(spec)
    eligibility = check_eligibility(
        distinct_matches=distinct_matches,
        observed_patterns=observed_patterns,
        distinct_opponent_conditions=distinct_conditions,
        primary_branches=primary_branches,
    )
    if not eligibility.eligible:
        return None

    # Build layouts
    desktop = layout_to_dict(layout_flowchart(spec, "desktop"))
    compact = layout_to_dict(layout_flowchart(spec, "compact"))

    # Full spec dict with layouts
    payload = spec_to_dict(spec)
    payload["layouts"] = {"desktop": desktop, "compact": compact}

    # Add quality metadata
    payload["decisionFlow"] = {
        "quality": {
            "matches": eligibility.quality.matches,
            "observedPatterns": eligibility.quality.observed_patterns,
            "distinctConditions": eligibility.quality.distinct_conditions,
            "primaryBranches": eligibility.quality.primary_branches,
        }
    }
    return payload


def decision_flow_cache_key(
    *,
    match_sequence_hash: str,
    ontology_revision: str,
    definition_hash: str,
    compiler_version: str,
    layout_version: int,
    routing_version: int,
) -> str:
    """Cache key for Decision Flow payload.

    Per spec: match-sequence hash + ontology revision + definition
    + compiler version + routing version + layout version
    """
    import hashlib
    parts = [
        match_sequence_hash,
        ontology_revision,
        definition_hash,
        compiler_version,
        str(layout_version),
        str(routing_version),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _infer_root_position(patterns: list[DecisionPattern]) -> str | None:
    for p in patterns:
        if p.source_position_key:
            return p.source_position_key
    return None


# Convenience: build from existing compiled patterns (used when patterns already aggregated)
def build_decision_flow_from_patterns(
    athlete_name: str,
    athlete_key: str,
    patterns: list[DecisionPattern],
    root_position_key: str,
    root_position_label: str,
    expert_branches: list[ExpertBranch] | None = None,
) -> dict[str, Any] | None:
    """Build payload from pre-aggregated patterns (export layer convenience)."""
    if not patterns:
        return None

    definition = FlowchartDefinition(
        key=f"{athlete_key}-{root_position_key}",
        title=f"{athlete_name} Decision Flow",
        athlete_key=athlete_key,
        root_position_key=root_position_key,
    )
    spec = compile_flowchart(definition, patterns, expert_branches=expert_branches or [],
                             athlete_label=athlete_name, root_position_label=root_position_label,
                             layout_version=LAYOUT_VERSION)

    primary_branches = len(spec.branches or [])
    distinct_matches = _count_distinct_matches(patterns)
    observed_patterns = _count_observed_patterns(patterns)
    distinct_conditions = _count_distinct_conditions(patterns)

    eligibility = check_eligibility(
        distinct_matches=distinct_matches,
        observed_patterns=observed_patterns,
        distinct_opponent_conditions=distinct_conditions,
        primary_branches=primary_branches,
    )
    if not eligibility.eligible:
        return None

    desktop = layout_to_dict(layout_flowchart(spec, "desktop"))
    compact = layout_to_dict(layout_flowchart(spec, "compact"))

    payload = spec_to_dict(spec)
    payload["layouts"] = {"desktop": desktop, "compact": compact}
    payload["decisionFlow"] = {
        "quality": {
            "matches": eligibility.quality.matches,
            "observedPatterns": eligibility.quality.observed_patterns,
            "distinctConditions": eligibility.quality.distinct_conditions,
            "primaryBranches": eligibility.quality.primary_branches,
        }
    }
    return payload
