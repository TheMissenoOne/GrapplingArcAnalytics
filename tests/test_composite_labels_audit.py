"""Sanity check for the 2026-09-15 composite-labels audit proposal.

``data/taxonomy/composite_labels.proposed.json`` is a PROPOSAL, not the live table
(``data/taxonomy/composite_labels.json``) -- report: ``docs/repairs/
2026-09-15_composite_labels_audit.md``. This only asserts internal consistency: every
curated key the proposal references actually resolves in the technique library (or is
explicitly null), and none of the audit's known-legitimate composites got mis-flagged
for decomposition.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name
from analysis.technique_match import _index

PROPOSED_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "taxonomy" / "composite_labels.proposed.json"
)

# The audit's own explicit false positives (task brief + docs/taxonomy/
# 04_ONTOLOGIA_CANONICA.md's COMPOSITE_FALSE_POSITIVES): single positions that merely
# contain a separator character. None of these may ever be proposed for decomposition.
KNOWN_LEGITIMATE = {"shin to shin", "chest-to-chest", "50/50 guard", "x-guard"}


def _load() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(PROPOSED_PATH.read_text(encoding="utf-8"))
    return result


def test_proposed_file_exists_and_parses() -> None:
    rows = _load()
    assert "_comment" in rows
    assert len(rows) > 1


def test_every_referenced_key_resolves_or_is_null() -> None:
    rows = _load()
    idx = _index()

    def resolves(key: str) -> bool:
        return key.lower() in ("top", "bottom", "neutral") or _normalize_name(key) in idx

    for label, row in rows.items():
        if label == "_comment":
            continue
        # `state` on a {state, perspective} row is documentation only --
        # analysis.composite_labels.expand_composite never reads it for that shape (the
        # label is kept unchanged); only action/to/alias_of are actually consumed
        # downstream, so only THOSE must resolve.
        fields = ("action", "to", "alias_of") if row.get("perspective") is not None else (
            "state", "action", "to", "alias_of"
        )
        for field in fields:
            value = row.get(field)
            if value is None:
                continue
            assert resolves(value), (
                f"{label!r}.{field} = {value!r} does not resolve in the curated "
                f"technique library (analysis.technique_match._index)"
            )


def test_no_legitimate_composite_is_proposed_for_decomposition() -> None:
    rows = _load()
    for label, row in rows.items():
        if label == "_comment":
            continue
        if _normalize_name(label) in KNOWN_LEGITIMATE:
            assert row["verdict"] != "decompose", (
                f"{label!r} is a known false positive, got verdict='decompose'"
            )


def test_decompose_rows_carry_a_usable_shape() -> None:
    """Every 'decompose' row must match one of the 3 shapes
    ``analysis.composite_labels.expand_composite`` understands: {action,to},
    {state,action} or {state,perspective}."""
    rows = _load()
    for label, row in rows.items():
        if label == "_comment" or row["verdict"] != "decompose":
            continue
        has_perspective = row.get("perspective") is not None
        has_action_to = bool(row.get("action")) and bool(row.get("to"))
        has_state_action = (
            bool(row.get("state")) and bool(row.get("action")) and not has_perspective
        )
        assert has_perspective or has_action_to or has_state_action, (
            f"{label!r} verdict=decompose but shape matches none of expand_composite's 3 forms"
        )
