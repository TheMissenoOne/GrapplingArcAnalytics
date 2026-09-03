"""N1 alias pass — pairs measured live against prod (`scripts/audit_ontology.py`
`alias_candidates` family, 2026-09-04) + two domain merges (north-south spellings,
`leg lock entanglement`) the automatic near-duplicate detector's edit-distance cutoff
can't see. See ``data/taxonomy/audit_baseline.json`` and
``docs/repairs/2026-09-04_n1_alias_replay.md``.
"""

from __future__ import annotations

from analysis.names import SYNONYMS, _normalize_name, canonicalize


def _key(label: str) -> str:
    return canonicalize(_normalize_name(label))


def test_alias_candidates_from_audit_collapse_to_the_higher_count_spelling() -> None:
    assert _key("Close Guard") == "closed guard"
    assert _key("Take Down") == "takedown"
    assert _key("Snap Down") == "snapdown"
    assert _key("Shin on Shin Guard") == "shin to shin guard"
    assert _key("Nearfall") == "near fall"


def test_north_south_variants_collapse_to_north_south_position() -> None:
    # "North-South Position" itself normalizes to "northsouth position" (hyphen dropped,
    # no space inserted) -- that pre-existing key is the target, untouched.
    assert _key("North-South Position") == "northsouth position"
    assert _key("North South") == "northsouth position"
    assert _key("North South Control") == "northsouth position"


def test_north_south_pass_spelling_forward_compat() -> None:
    assert _key("North-South Pass") == "northsouth pass"
    assert _key("North South Pass") == "northsouth pass"  # spaced spelling not seen yet


def test_leg_lock_entanglement_collapses_to_leg_entanglement() -> None:
    assert _key("Leg Lock Entanglement") == "leg entanglement"


def test_kimura_grip_and_kimura_trap_stay_distinct() -> None:
    # Reviewed, not merged: "Kimura Grip" logs as `control` (a grip), "Kimura Trap" mostly
    # as `submission` -- different techniques the audit's edit-distance heuristic conflates.
    assert "kimura grip" not in SYNONYMS
    assert "kimura trap" not in SYNONYMS
    assert _key("Kimura Grip") != _key("Kimura Trap")
