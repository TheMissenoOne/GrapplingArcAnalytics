"""analysis/gendered_text.pick — gender-selected prose, NULL never defaults masculine."""

from __future__ import annotations

from analysis.gendered_text import pick


def test_female_gets_f_variant() -> None:
    assert pick("f", m="he", f="she", neutral="they") == "she"


def test_male_gets_m_variant() -> None:
    assert pick("m", m="he", f="she", neutral="they") == "he"


def test_unknown_gender_gets_neutral_never_masculine() -> None:
    assert pick(None, m="he", f="she", neutral="they") == "they"


def test_unrecognised_value_falls_back_to_neutral() -> None:
    """Anything that isn't exactly 'f'/'m' is treated as unknown, not coerced to masculine."""
    assert pick("x", m="he", f="she", neutral="they") == "they"  # type: ignore[arg-type]
