"""Reading a BJJ Heroes result onto a contact sheet — the two places it can go wrong.

The result line is context, not evidence, but a wrong one is worse than none: it is printed
above frames a reader is being asked to describe, so a wrong W/L invites a wrong story about
what those frames show.

Both failure modes below were live before these tests existed. The first was found by
comparing the generated line against the athlete's own page and finding the letter flipped.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.frame_pdf import (
    _bout_names,
    _result_line,
    _same_person,
    build_windows,
    parse_bout_index,
    parse_transcript,
    transcript_window,
)


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


def test_prose_says_where_the_timestamp_actually_is() -> None:
    """The PDF prints the time above each frame; the folder puts it in the filename and
    leaves the JPEG unmarked. An agent told to read a stamp off an unmarked image looks for
    something that is not there, so the sentence is a parameter, not a constant."""
    from scripts.frame_pdf import context_prose

    sheet = " ".join(b for _, b in context_prose(5, True, 0.0, 120.0))
    folder = " ".join(b for _, b in
                      context_prose(5, False, 0.0, 120.0, ts_source="in its filename"))
    assert "directly above it" in sheet and "in its filename" not in sheet
    assert "in its filename" in folder and "directly above it" not in folder


def test_prose_names_the_vocabulary_file_it_was_given() -> None:
    """Both formats carry the label list, under different names. Naming the wrong one sends
    the reader to a file that is not there, and the closed vocabulary becomes a rule with no
    list attached."""
    from scripts.frame_pdf import context_prose

    md = [b for k, b in context_prose(5, False, 0.0, 1.0, lib_file="labels.md")
          if k == "p" and "verbatim from" in b]
    assert md and "labels.md" in md[0]
    inline = [b for k, b in context_prose(5, True, 0.0, 1.0)
              if k == "p" and "CLOSED VOCABULARY" in b]
    assert inline and "pages immediately after" in inline[0]


# ── Transcript alignment ─────────────────────────────────────────────────────────
# A trimmed real sample: the header's partial bout index (6 complete ranges, 2 bare
# timestamps, one line with trailing junk after the range) plus a few transcript triples in
# the real "H:MM:SS / Portuguese duration / caption" shape.
_SAMPLE = """\
ref:"Dominic Mahia's path to the finals and match breakdown (0:48:09)
Elijah Dorsey vs. Nikki Ryan (1:23:32 - 1:26:13)
Dorian Oliver vs. Dominic Mahia (1:37:45 - 1:40:55)
Josh Saunders victory by rear-naked choke (1:45:53)
Anna Carolina Vieira vs. Franciele Nascimento (3:08:08 - 3:29:34)
Jasmine Rocha vs. Enriquez (5:54:44 - 6:00:05)
William Tackett vs. Jacob Rodriguez (6:58:49 - 7:03:43)
Andrew Tackett vs. Oliver Taza (7:11:14 - 7:15:29)" (Pode haver mais)


Transcrição
Pesquisar transcrição
0:00
0 segundo
[Music]
0:06
6 segundos
Fight.
1:05
1 minuto e 5 segundos
Oh yeah.
"""


def test_parse_bout_index_keeps_only_complete_ranges(tmp_path: Path) -> None:
    """A bare timestamp with no range and the trailing '(Pode haver mais)' junk must not
    produce a phantom entry or corrupt the last real one."""
    p = tmp_path / "t.md"
    p.write_text(_SAMPLE, encoding="utf-8")
    bouts = parse_bout_index(p)
    assert len(bouts) == 6
    assert bouts[0] == ("Elijah Dorsey vs. Nikki Ryan", 1 * 3600 + 23 * 60 + 32,
                        1 * 3600 + 26 * 60 + 13)
    assert bouts[-1] == ("Andrew Tackett vs. Oliver Taza", 7 * 3600 + 11 * 60 + 14,
                         7 * 3600 + 15 * 60 + 29)
    assert all("Pode haver mais" not in label for label, _, _ in bouts)


def test_parse_transcript_discards_the_portuguese_duration_line(tmp_path: Path) -> None:
    p = tmp_path / "t.md"
    p.write_text(_SAMPLE, encoding="utf-8")
    rows = parse_transcript(p)
    assert rows == [(0, "[Music]"), (6, "Fight."), (65, "Oh yeah.")]


def test_transcript_window_matches_video_absolute_seconds() -> None:
    times, texts = [0, 6, 65], ["[Music]", "Fight.", "Oh yeah."]
    assert transcript_window(times, texts, 0, 10) == "[Music] Fight."
    assert transcript_window(times, texts, 60, 10) == "Oh yeah."


def test_transcript_window_is_empty_not_shifted_when_nothing_was_said() -> None:
    """A window with no caption returns '', not the neighbouring window's text -- the caller
    renders a placeholder rather than silently borrowing a caption from elsewhere."""
    times, texts = [0, 6, 65], ["[Music]", "Fight.", "Oh yeah."]
    assert transcript_window(times, texts, 20, 10) == ""


def test_build_windows_covers_the_gap_before_and_after_a_named_bout() -> None:
    windows = build_windows([("B", 50, 80)], total_seconds=200, window=50)
    assert windows == [(0, 50), (80, 130), (130, 180), (180, 200)]


def test_build_windows_produces_no_gap_when_a_bout_starts_at_zero() -> None:
    windows = build_windows([("A", 0, 100)], total_seconds=250, window=100)
    assert windows == [(100.0, 200.0), (200.0, 250)]
