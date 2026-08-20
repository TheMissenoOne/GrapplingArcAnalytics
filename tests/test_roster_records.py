"""One identity per athlete, and one row per bout.

Both halves of that have been broken in this file before, in opposite directions: a record
keyed by display name forks the moment a spelling changes, and a dedup key that compares method
strings raw counts the same bout twice because one source writes "RNC" and the other "Rear
Naked Choke". Every case here is a real row from the ADCC 2026 records.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "build_roster_records",
    Path(__file__).resolve().parent.parent / "scripts" / "build_roster_records.py")
assert _SPEC and _SPEC.loader
brr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(brr)


def _row(opp: str, year: int, method: str, source: str = "own_record") -> dict[str, Any]:
    return {"opp": opp, "year": year, "method": method, "wl": "W", "comp": "Test",
            "source": source}


# ── the dedup key ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("a,b", [
    ("RNC", "Rear Naked Choke"),          # Ane Svendsen's 2025 loss, counted twice
    ("Referee Decision", "Decision"),     # Paige Ivette's 2025 loss, counted twice
    ("Inside heel hook", "Heel Hook"),
    ("Outside heel hook", "heel hook"),
    ("Points", "Pts"),
    ("Decision.", "Judges Decision"),
])
def test_one_ending_has_one_spelling(a: str, b: str) -> None:
    assert brr._method_key(a) == brr._method_key(b)


@pytest.mark.parametrize("a,b", [
    ("Pts: 5x0", "Pts: 3x0"),      # what separates a real rematch from a duplicate
    ("Armbar", "Kneebar"),
    ("Heel Hook", "Toe Hold"),
])
def test_two_endings_stay_two_bouts(a: str, b: str) -> None:
    assert brr._method_key(a) != brr._method_key(b)


def test_a_rematch_in_the_same_year_is_not_a_duplicate() -> None:
    """Sarah Galvao met Yara Soares twice in 2021. An opponent+year key merged them."""
    one = _row("Yara Soares", 2021, "Pts: 2x0")
    two = _row("Yara Soares", 2021, "Armbar")
    assert brr._key(one) != brr._key(two)


def test_the_same_bout_from_two_sources_is_one_bout() -> None:
    corpus = _row("Helena Crevar", 2025, "Rear Naked Choke", "corpus")
    page = _row("Helena Crevar", 2025, "RNC", "opponent_record")
    assert brr._key(corpus) == brr._key(page)


def test_an_alias_does_not_fork_a_bout() -> None:
    """`athlete_key` resolves the alias table, so the opponent side of the key is one value
    however the source spelled her."""
    assert (brr._key(_row("Helena Cravar", 2025, "Armbar"))
            == brr._key(_row("Helena Crevar", 2025, "Armbar")))


# ── one identity ────────────────────────────────────────────────────────────────
def test_two_spellings_in_the_file_merge_into_one_record(tmp_path: Path) -> None:
    """The file is keyed by display name for a human to read, which is not an identity.
    Re-spelling an athlete in the manifest must add to the record that is already there."""
    path = tmp_path / "recs.json"
    path.write_text(json.dumps({"athletes": {
        "Sarah Galvão": {"division": "65 kg", "rows": [_row("A", 2024, "Armbar")]},
        "Sarah Galvao": {"division": None, "rows": [_row("B", 2023, "Heel Hook",
                                                        "opponent_record")]},
    }}), encoding="utf-8")
    out = brr._load(path, {"sarah galvao": "Sarah Galvão"})
    assert list(out) == ["sarah galvao"]
    assert len(out["sarah galvao"]["rows"]) == 2
    # The earlier entry may hold her own table and the later only a reconstruction, so the
    # merge keeps both rather than letting the last one win.
    assert {r["opp"] for r in out["sarah galvao"]["rows"]} == {"A", "B"}
    assert out["sarah galvao"]["division"] == "65 kg"


def test_the_merge_does_not_duplicate_a_shared_bout(tmp_path: Path) -> None:
    path = tmp_path / "recs.json"
    same = _row("Helena Crevar", 2025, "RNC")
    path.write_text(json.dumps({"athletes": {
        "Ane Svendsen": {"division": "65 kg", "rows": [same]},
        "ane svendsen": {"division": "65 kg",
                         "rows": [_row("Helena Crevar", 2025, "Rear Naked Choke", "corpus")]},
    }}), encoding="utf-8")
    out = brr._load(path, {"ane svendsen": "Ane Svendsen"})
    assert len(out["ane svendsen"]["rows"]) == 1


def test_the_flat_legacy_shape_still_loads(tmp_path: Path) -> None:
    """The file on disk is the previous shape until this script runs once."""
    path = tmp_path / "recs.json"
    path.write_text(json.dumps({"Gabi Garcia": {"division": "+65 kg", "rows": []}}),
                    encoding="utf-8")
    out = brr._load(path, {"gabi garcia": "Gabi Garcia"})
    assert out["gabi garcia"]["display"] == "Gabi Garcia"


# ── provenance: one record, the mix visible ─────────────────────────────────────
def test_a_record_can_be_her_own_table_and_recovered_rows_at_once() -> None:
    """The thing the old `record_source` binary could not express. Eight of the sixteen are
    exactly this, and calling such a record either "própria" or "reconstruída" hides half."""
    rows = [_row("A", 2024, "Armbar"), _row("B", 2023, "Heel Hook", "opponent_record"),
            _row("C", 2022, "Decision", "corpus")]
    pv = brr._provenance(rows)
    assert pv["total"] == 3
    assert pv["has_own_table"] is True
    assert pv["reconstructed_share"] == pytest.approx(2 / 3, abs=1e-3)
    assert pv["by_source"] == {"own_record": 1, "opponent_record": 1, "corpus": 1}


def test_a_record_with_no_table_of_her_own_says_so() -> None:
    pv = brr._provenance([_row("A", 2024, "Armbar", "opponent_record")])
    assert pv["has_own_table"] is False and pv["reconstructed_share"] == 1.0


def test_an_empty_record_claims_nothing() -> None:
    pv = brr._provenance([])
    assert pv["total"] == 0 and pv["reconstructed_share"] is None


# ── opponents get an identity, not a career ─────────────────────────────────────
def test_an_athlete_who_only_appears_as_an_opponent_gets_a_canonical_identity() -> None:
    records = {"helena crevar": {"display": "Helena Crevar", "rows": [
        _row("Amanda Levy", 2024, "Pts: 4x2"),
        _row("Amanda Levy", 2023, "Armbar", "opponent_record"),
    ]}}
    idx = brr._opponent_index(records, {"helena crevar": "Helena Crevar"})
    assert "amanda levy" in idx
    assert idx["amanda levy"]["bouts_vs_roster"] == 2
    assert idx["amanda levy"]["vs"] == ["Helena Crevar"]


def test_two_spellings_of_one_opponent_do_not_fork() -> None:
    records = {"x": {"display": "X", "rows": [
        _row("Helena Cravar", 2025, "Armbar"),
        _row("Helena Crevar", 2024, "Heel Hook"),
    ]}}
    idx = brr._opponent_index(records, {"x": "X"})
    assert list(idx) == ["helena crevar"]
    assert sorted(idx["helena crevar"]["spellings"]) == ["Helena Cravar", "Helena Crevar"]


def test_an_initial_form_does_not_open_a_second_identity() -> None:
    """BJJ Heroes abbreviates the opponent cell — "L. Bernales" for Leilani Bernales — and
    `athlete_key` cannot see through that on its own."""
    records = {"x": {"display": "X", "rows": [
        _row("L. Bernales", 2024, "Armbar"),
        _row("Leilani Bernales", 2023, "Heel Hook"),
    ]}}
    idx = brr._opponent_index(records, {"x": "X"})
    assert len(idx) == 1
    o = next(iter(idx.values()))
    # An initial is the abbreviation and never the name.
    assert o["display"] == "Leilani Bernales"
    assert o["bouts_vs_roster"] == 2


def test_a_genuinely_open_pair_is_left_as_two() -> None:
    """"Jon Hansen" against "John Hansen" is a real unresolved question in this corpus. A
    string is not allowed to answer it, and neither is a display-name heuristic."""
    records = {"x": {"display": "X", "rows": [
        _row("Jon Hansen", 2024, "Armbar"),
        _row("John Hansen", 2023, "Heel Hook"),
    ]}}
    assert len(brr._opponent_index(records, {"x": "X"})) == 2


def test_a_roster_athlete_is_never_listed_as_her_own_opponent() -> None:
    """The scope line: the roster gets records, the athletes it fought get identities, and
    nobody is on both sides of it."""
    records = {"a": {"display": "A", "rows": [_row("Yara Soares", 2024, "Armbar")]}}
    idx = brr._opponent_index(records, {"a": "A", "yara soares": "Yara Soares"})
    assert idx == {}
