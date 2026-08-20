"""Vocabulary and taxonomy tests for the BracketAnalysis exporter.

`uniform`, `ruleset` and `family` are pure functions over a string. They need no database, no
network and no fixture, and they decide how every bout in the report is counted -- which is
exactly why the absence of these tests let an entire sanctioning body be filed under the wrong
rules for months. Ninety-five bouts, all of them in one division, were counted as ADCC while
being AJP; half of the cut the page called "no-gi under ADCC rules" was gi.

Each case below is a real string from the corpus, not an invented one.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "bracket_export", Path(__file__).resolve().parent.parent / "scripts" / "bracket_export.py")
assert _SPEC and _SPEC.loader
bx = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bx)


# ── uniform + ruleset ───────────────────────────────────────────────────────────
# Every AJP-family competition name the roster records actually carry.
AJP_EVENTS = (
    "World Pro", "World Pro.", "AJP World Pro", "AJP WGC", "AJP SA Cont.",
    "Grand Slam RJ", "Grand Slam AD", "Grand Slam LDN", "Grand Slam MIA", "Grand Slam MSK",
    "ADGS Rome", "ADGS ABD", "ADGS ABDB", "ADGS RJ", "ADGS DLS", "ADGS LDN", "ADGS Miami",
    "ADGS IST", "ADGS XIAN", "ADGS CNU", "ADGS - RJN", "ADGS - MSK", "ADGS - IST",
)


@pytest.mark.parametrize("event", AJP_EVENTS)
def test_ajp_events_are_gi_under_ajp_rules_not_adcc(event: str) -> None:
    assert bx.ruleset(event) == "ajp"
    assert bx.uniform(event) == "gi"


@pytest.mark.parametrize("event", ["ADCC AUS Trials", "ADCC EU", "ADCC WC Trials", "ADCC ECTrials"])
def test_real_adcc_events_still_resolve_to_adcc(event: str) -> None:
    assert bx.ruleset(event) == "adcc"
    assert bx.uniform(event) == "no_gi"


def test_ajp_is_tried_before_adcc() -> None:
    """`ruleset` returns on the first family that matches, and dicts iterate in insertion
    order, so this ordering is load-bearing rather than cosmetic."""
    assert list(bx.RULESETS)[:2] == ["ajp", "adcc"]


@pytest.mark.parametrize("event,expected", [
    ("UFC Copenhagen", "other"),      # "open" must not fire inside "Copenhagen"
    ("European Open", "ibjjf"),       # ... but must still fire on a real Open
    ("Houston Open", "ibjjf"),
])
def test_open_matches_a_word_not_a_substring(event: str, expected: str) -> None:
    assert bx.ruleset(event) == expected


@pytest.mark.parametrize("event", ["NoGi Worlds", "NoGi Pan", "No Gi Pan Am."])
def test_gi_token_does_not_fire_inside_nogi(event: str) -> None:
    """`"gi "` sits in the GI vocabulary and matches "nogi " as a substring. Today NO_GI is
    tested first so nothing breaks; the leading word boundary is what keeps that true if the
    order ever changes."""
    assert bx.uniform(event) == "no_gi"
    assert not bx.GI.search(bx._fold(event))


@pytest.mark.parametrize("event", [
    "Sacramento NGO", "Denver NGO", "American NGN", "American NNG", "IBJJF NGGP",
    "Austin SNGO", "Houston FNGO", "JJ Con NG", "MCharacter 5",
])
def test_no_gi_abbreviations_are_read_as_no_gi(event: str) -> None:
    assert bx.uniform(event) == "no_gi"


@pytest.mark.parametrize("event", ["Sacramento WO", "Santa Cruz O", "SA Cont. Pro", "Curitiba Pro"])
def test_genuinely_ambiguous_names_stay_unknown(event: str) -> None:
    """An AJP "Pro" runs gi and no-gi brackets, and a bare "O" suffix could be either. Guessing
    here would manufacture a uniform split out of nothing; `unknown` is its own cut on the page
    precisely so it can stay honest."""
    assert bx.uniform(event) == "unknown"


# ── family ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,expected", [
    ("Calf slicer", "slicer"),        # LEG's `calf` used to claim this, making `slicer` dead
    ("Triangle armlock", "joint"),    # STRANGLE's `triangle` used to claim this
    ("Triangle armbar", "joint"),
    ("Inside heel hook", "leg"),
    ("Outside heel hook", "leg"),
    ("Toe hold", "leg"),
    ("RNC", "strangle"),
    ("Darce choke", "strangle"),
    ("Kimura", "joint"),
    ("Omoplata", "joint"),
    ("Pts: 3x1", "points"),
    ("Referee Decision", "decision"),
    ("Submission", "sub_other"),
    ("Verbal tap", "sub_other"),
    ("---", "void"),                  # VOID anchored `^-$`, which never matched "---"
    ("DQ", "void"),
    ("Injury", "void"),
])
def test_family_of_a_real_corpus_method(method: str, expected: str) -> None:
    assert bx.family(method) == expected


def test_the_slicer_family_is_reachable() -> None:
    """It scored k=0 in both divisions not because nobody hits one, but because every string
    that could reach the branch was claimed by an earlier one."""
    assert bx.family("Calf slicer") == "slicer"
    assert "slicer" in bx.SUB_FAMILIES


# Every method string in the roster records that matches at least one submission-family regex.
# The point of checking them in is the ambiguity invariant below, not the individual answers.
CORPUS_SUBMISSION_METHODS = (
    "Amassa pao choke", "Americana", "Anaconda choke", "Aoki lock", "Armbar", "Armlock",
    "Bow and arrow", "Calf slicer", "Canto choke", "Choke", "Choke from back", "Clock choke",
    "Cross Choke", "Darce choke", "Footlock", "Guillotine", "Inside heel hook", "Katagatame",
    "Kimura", "Kneebar", "Omoplata", "Outside heel hook", "RNC", "Reverse triangle",
    "Straight ankle lock", "Toe hold", "Triangle", "Triangle armbar", "Triangle armlock",
)
_FAMILY_PATTERNS = (("leg", bx.LEG), ("strangle", bx.STRANGLE),
                    ("joint", bx.JOINT), ("slicer", bx.SLICER))


@pytest.mark.parametrize("method", CORPUS_SUBMISSION_METHODS)
def test_an_ambiguous_method_is_decided_explicitly_never_by_test_order(method: str) -> None:
    """The invariant that keeps this class of bug from coming back.

    A name matching two family regexes is resolved by whichever is tested first, which is an
    accident of source order rather than a judgement about the technique. Any such name must
    carry an entry in METHOD_FAMILY, where the choice is visible and reviewable.
    """
    folded = bx._fold(method)
    matched = [name for name, pat in _FAMILY_PATTERNS if re.search(pat, folded)]
    if len(matched) > 1:
        assert folded in bx.METHOD_FAMILY, (
            f"{method!r} matches {matched} and would be decided by test order alone")


def test_every_method_family_override_earns_its_place() -> None:
    """An entry that is not ambiguous is dead weight -- it hides that the fallback already
    agrees, and it will silently outlive the regex it was written to overrule."""
    for label, family_name in bx.METHOD_FAMILY.items():
        matched = [n for n, pat in _FAMILY_PATTERNS if re.search(pat, label)]
        assert len(matched) > 1, f"{label!r} matches only {matched}; the fallback handles it"
        assert family_name in bx.SUB_FAMILIES


# ── bout identity ───────────────────────────────────────────────────────────────
def _row(athlete: str, opp: str, wl: str, comp: str, year: int, stage: str,
         method: str = "Pts: 2x0") -> dict[str, str | int]:
    return {"athlete": athlete, "opp": opp, "wl": wl, "comp": comp, "year": year,
            "stage": stage, "method": method}


def test_a_mirror_pair_is_one_bout() -> None:
    """The real one from -65 kg: both records describe the same quarter-final."""
    rows = [_row("Nadia Frankland", "Morgan Black", "W", "NoGi Pan", 2024, "4F"),
            _row("Morgan Black", "Nadia Frankland", "L", "NoGi Pan", 2024, "4F")]
    kept = bx._distinct_bouts(rows)
    assert len(kept) == 1
    assert kept[0]["wl"] == "W", "the winner's row carries the method that decided the bout"


def test_a_rematch_at_the_same_event_is_two_bouts() -> None:
    """The regression that matters. Keying on (pair, competition, year) alone collapsed 25
    pairs in +65 kg that were not duplicates: Yara Soares beat Isabely Lemos on points in the
    semi-final and by submission in the final of the same event. Two bouts, two results.
    """
    rows = [_row("Yara Soares", "Isabely Lemos", "W", "SA Cont. Pro", 2024, "SF", "Pts: 4x0"),
            _row("Yara Soares", "Isabely Lemos", "W", "SA Cont. Pro", 2024, "F", "Submission")]
    assert len(bx._distinct_bouts(rows)) == 2


def test_two_round_robin_meetings_are_two_bouts() -> None:
    """A round robin produces repeat meetings by design, with the same stage label."""
    rows = [_row("Yara Soares", "Meng Xiang", "W", "ADGS XIAN", 2024, "RR", "Submission"),
            _row("Yara Soares", "Meng Xiang", "W", "ADGS XIAN", 2024, "RR", "Submission")]
    assert len(bx._distinct_bouts(rows)) == 2


def test_two_wins_are_never_a_mirror() -> None:
    """A mirror has to be provable: each names the other, and one won while the other lost."""
    a = _row("A", "B", "W", "Worlds", 2025, "F")
    b = _row("B", "A", "W", "Worlds", 2025, "F")
    assert bx._is_mirror(a, b) is False
    assert len(bx._distinct_bouts([a, b])) == 2


def test_a_mirror_needs_each_side_to_name_the_other() -> None:
    a = _row("A", "C", "W", "Worlds", 2025, "F")
    b = _row("B", "A", "L", "Worlds", 2025, "F")
    assert bx._is_mirror(a, b) is False
