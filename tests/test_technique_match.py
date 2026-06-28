"""Tests for the technique-name → library cleanup (pure, uses the committed library)."""

from __future__ import annotations

from typing import Any

from analysis.technique_match import clean_label, clean_sequence


class TestCleanLabel:
    def test_pt_translation_maps_to_canonical(self) -> None:
        assert clean_label("Guarda Fechada") == "Closed Guard"

    def test_variant_maps_to_canonical(self) -> None:
        assert clean_label("full guard") == "Closed Guard"
        assert clean_label("Back Take") == "Back Control"

    def test_alias_abbreviation(self) -> None:
        assert clean_label("RNC") == "Rear Naked Choke"

    def test_case_insensitive(self) -> None:
        assert clean_label("CLOSED GUARD") == "Closed Guard"

    def test_unknown_label_is_untouched(self) -> None:
        assert clean_label("Some Made Up Move") == "Some Made Up Move"

    def test_type_hint_blocks_cross_type_match(self) -> None:
        # "Back Take" is a control; with a mismatched type hint the rename is rejected.
        assert clean_label("Back Take", "control") == "Back Control"
        assert clean_label("Back Take", "submission") == "Back Take"

    def test_empty(self) -> None:
        assert clean_label("") == ""
        assert clean_label("  ") == ""


class TestCleanSequence:
    def test_counts_and_canonicalises(self) -> None:
        seq: list[dict[str, Any]] = [
            {"label": "Guarda Fechada", "type": "guard", "actor_id": "A"},
            {"label": "Some Made Up Move", "type": "control", "actor_id": "B"},
            {"label": "RNC", "type": "submission", "actor_id": "A", "successful": True},
        ]
        out, changed = clean_sequence(seq)
        assert changed == 2
        assert out[0]["label"] == "Closed Guard"
        assert out[1]["label"] == "Some Made Up Move"
        assert out[2]["label"] == "Rear Naked Choke"
        # other fields preserved
        assert out[2]["successful"] is True and out[2]["actor_id"] == "A"

    def test_non_mutating(self) -> None:
        seq = [{"label": "Guarda Fechada", "type": "guard", "actor_id": "A"}]
        clean_sequence(seq)
        assert seq[0]["label"] == "Guarda Fechada"  # original untouched

    def test_empty_sequence(self) -> None:
        assert clean_sequence(None) == ([], 0)
