"""Dossier section headings (export/site_data.py) pronoun-agree with athletes.gender —
None (unknown) must never read masculine (root task rule, see analysis/gendered_text)."""

from __future__ import annotations

from export.site_data import (
    _counters_heading,
    _defense_heading,
    _progression_heading,
    _signature_heading,
)

_HEADINGS = [_defense_heading, _counters_heading, _progression_heading, _signature_heading]


def test_female_headings_use_she_her_not_he_his() -> None:
    for fn in _HEADINGS:
        text = f" {fn('f').lower()} "
        assert " he " not in text and " his " not in text and " him " not in text


def test_unknown_gender_headings_never_masculine() -> None:
    for fn in _HEADINGS:
        text = f" {fn(None).lower()} "
        assert " he " not in text and " his " not in text and " him " not in text


def test_male_headings_unchanged_from_original_copy() -> None:
    assert _defense_heading("m") == "What he stops, weighted by who threw it"
    assert _counters_heading("m") == "His highest-value answer from each position"
    assert _progression_heading("m") == "Where his sequences move him"
    assert _signature_heading("m") == "What he reaches for first"
