"""Opponent-condition classification — precedence, aliases, bundles."""

from __future__ import annotations

from typing import Any

from analysis.opponent_conditions import (
    classify_event_condition,
    classify_opponent_condition,
)


class _Reaction:
    def __init__(self, key: str, name: str) -> None:
        self.key = key
        self.name = name
        self.description = ""


def _ev(label: str, event_type: str = "transition") -> Any:
    return type("_E", (), {"label": label, "event_type": event_type})()


def test_curated_alias_canonicalization() -> None:
    for label in ("Posts Near Hand", "posts his hand", "bases with his arm",
                  "puts hand on mat", "posts right hand"):
        c = classify_event_condition(label, "transition")
        assert c.key == "cond:posts-hand"
        assert c.label == "Opponent posts a hand"
        assert c.kind == "reaction"


def test_reaction_catalog_exact_match_wins() -> None:
    catalog = [_Reaction("escape-turn-in", "Escape Turn In")]
    c = classify_event_condition("escape turn in", "transition", catalog)
    assert c.key == "cond:escape-turn-in"
    assert c.reaction_key == "escape-turn-in"
    assert c.kind == "reaction"


def test_pattern_rules() -> None:
    assert classify_event_condition("Sprawls the leg", "transition").key == "cond:sprawls"
    assert classify_event_condition("Wide base", "transition").key == "cond:bases-out"
    assert classify_event_condition("Stands quickly", "transition").key == "cond:stands"


def test_event_type_mapping() -> None:
    assert classify_event_condition("Some Escape", "escape").key == "cond:opponent-escapes"
    assert classify_event_condition("Triangle Choke", "submission").key == "cond:opponent-attacks"


def test_deterministic_fallback() -> None:
    c = classify_event_condition("Tries weird thing", "")
    assert c.key == "cond:tries weird thing"
    assert c.kind == "unknown"
    assert "weird thing" in c.label.lower()


def test_bundle_dedupe_and_composite() -> None:
    events = [
        _ev("Posts Near Hand"),
        _ev("posts his hand"),  # duplicate → dropped
        _ev("Squares The Hips"),
    ]
    c = classify_opponent_condition(events)
    assert c is not None
    assert c.key == "cond:posts-hand-and-cond:squares-hips"
    assert c.source_event_keys == ("cond:posts-hand", "cond:squares-hips")


def test_bundle_empty() -> None:
    assert classify_opponent_condition([]) is None
    assert classify_opponent_condition([_ev("")]) is None


def test_bundle_single() -> None:
    c = classify_opponent_condition([_ev("Posts Near Hand")])
    assert c is not None
    assert c.key == "cond:posts-hand"
    assert c.source_event_keys == ("cond:posts-hand",)


# ---------------------------------------------------------------- families / normalization


def test_composite_key_order_does_not_matter():
    """The classifier emits composites in event order, so one pair of opponent events
    produces two keys and splits its own evidence. Normalizing merges them."""
    from analysis.opponent_conditions import normalize_condition_key as nk

    a = nk("cond:opponent-passes-and-cond:opponent-attacks")
    b = nk("cond:opponent-attacks-and-cond:opponent-passes")
    assert a == b


def test_normalization_leaves_simple_keys_alone():
    from analysis.opponent_conditions import normalize_condition_key as nk

    assert nk("cond:sprawls") == "cond:sprawls"


def test_every_curated_condition_has_a_family():
    from analysis.opponent_conditions import CONDITION_ALIASES, CONDITION_FAMILIES

    keys = {v.key for v in CONDITION_ALIASES.values()}
    assert keys - set(CONDITION_FAMILIES) == set()


def test_families_reference_only_real_conditions():
    from analysis.opponent_conditions import CONDITION_FAMILIES, FAMILY_LABELS

    assert set(CONDITION_FAMILIES.values()) <= set(FAMILY_LABELS)


def test_composite_inherits_a_family_only_when_components_agree():
    from analysis.opponent_conditions import condition_family

    agree = "cond:opponent-attacks-and-cond:opponent-passes"
    assert condition_family(agree) == "fam:opponent-advances"
    # spans advance + recover: it genuinely belongs to both, so neither is the answer
    split = "cond:opponent-attacks-and-cond:opponent-escapes"
    assert condition_family(split) is None


def test_unknown_condition_has_no_family():
    from analysis.opponent_conditions import condition_family

    assert condition_family("cond:not-a-real-thing") is None
