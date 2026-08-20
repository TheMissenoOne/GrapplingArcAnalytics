"""Reading a BJJ Heroes result onto a contact sheet — the two places it can go wrong.

The result line is context, not evidence, but a wrong one is worse than none: it is printed
above frames a reader is being asked to describe, so a wrong W/L invites a wrong story about
what those frames show.

Both failure modes below were live before these tests existed. The first was found by
comparing the generated line against the athlete's own page and finding the letter flipped.
"""
from __future__ import annotations

import pytest

from scripts.frame_pdf import _bout_names, _result_line, _same_person


class _Row:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


@pytest.mark.parametrize(("a", "b"), [
    # BJJ Heroes' match table abbreviates the opponent; the profile spells it out.
    ("Aurelie Vern", "Aurelie Le Vern"),      # dropped particle
    ("L. Bernales", "Leilani Bernales"),      # initial for the first name
    ("I. Goodman", "Injana Goodman"),
    ("Sula Lowenthal", "Sula Mae Lowenthal"),  # dropped middle name
    ("Anabel Lopez", "Anabel Lopez"),
])
def test_abbreviated_opponent_is_the_same_person(a: str, b: str) -> None:
    assert _same_person(a, b)


@pytest.mark.parametrize(("a", "b"), [
    # These are open duplicate questions in the corpus. A contact sheet must not answer them:
    # merging two athletes is a reviewed, twice-corroborated decision, not a string ratio.
    ("Jon Hansen", "John Hansen"),
    ("Ana Vieira", "Anna Karolina Vieira"),
    ("G. Vasconcelos", "F. Vasconcelos"),     # different initials = different people
    ("Morgan Black", "Brianna Ste-Marie"),
])
def test_different_spellings_are_not_merged(a: str, b: str) -> None:
    assert not _same_person(a, b)


def test_result_line_is_their_row_verbatim() -> None:
    """W/L, method, competition, weight, stage, year -- in BJJ Heroes' own order and words.
    A reworded method would be a claim we did not verify."""
    row = _Row(wl="L", method="Armbar", competition="Euro NoGi", weight="ABS",
               stage="F", year=2024)
    assert _result_line(row) == "L  Armbar  Euro NoGi  ABS  F  2024"


def test_result_line_drops_empty_fields_without_shifting_the_rest() -> None:
    row = _Row(wl="W", method="RNC", competition="WNO 27", weight="", stage="SPF", year=2025)
    assert _result_line(row) == "W  RNC  WNO 27  SPF  2025"


@pytest.mark.parametrize(("label", "want"), [
    ("Anabel Lopez vs Aurelie Le Vern, European No-Gi 2024", ("Anabel Lopez", "Aurelie Le Vern")),
    ("Helena Crevar vs Elisabeth Clay, WNO 27 2025", ("Helena Crevar", "Elisabeth Clay")),
    ("Sarah Galvao vs. Zara Tofano", ("Sarah Galvao", "Zara Tofano")),
])
def test_bout_names_splits_on_the_versus(label: str, want: tuple[str, str]) -> None:
    assert _bout_names(label) == want


def test_bout_names_returns_none_for_a_non_bout_label() -> None:
    """An event reel is not a bout, and inventing two competitors from its title would look
    up a result for a fight that does not exist."""
    assert _bout_names("ADCC European Trials 2025 highlights") is None


def test_typographic_lookalikes_are_folded_to_ascii() -> None:
    """U+2011 is not hypothetical: "World No-Gi" is already in the corpus spelled with a
    non-breaking hyphen, and no candidate font carries a glyph for it."""
    from scripts.frame_pdf import _register_fonts, _txt

    _register_fonts()
    assert _txt("World No‑Gi 2024") == "World No-Gi 2024"
    assert _txt("Aurélie’s bout — final") == "Aurélie's bout - final"


def test_undrawable_characters_become_a_marker_not_a_black_box() -> None:
    """reportlab does not raise on a missing glyph -- it draws a filled box, which reaches
    both the page and the extracted text as mojibake. '?' is not better typography, it is an
    honest marker that a character was dropped."""
    from scripts.frame_pdf import _register_fonts, _txt

    _register_fonts()
    assert _txt("IBJJF \U0001f94b official") == "IBJJF ? official"
    assert "■" not in _txt("柔術")


def test_accented_and_non_latin1_names_survive() -> None:
    from scripts.frame_pdf import _register_fonts, _txt

    _register_fonts()
    assert _txt("Gabrieli Pessanha, São Paulo") == "Gabrieli Pessanha, São Paulo"
