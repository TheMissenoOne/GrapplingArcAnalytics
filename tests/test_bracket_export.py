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
import json
import re
from pathlib import Path

import pytest

from analysis.stats_rigor import MIN_N_FOR_ANY_GRADE

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


# ── the sequence layer, seen from both corners ──────────────────────────────────
# `sequence_layer` used to keep only the events where the roster athlete was the actor and
# throw the rest away, so the page could say what she DID and never what was done to her.
# These fixtures are the shape of a real bout row from data/scouting/*_sequences.json.
def _bout(bid: str, seq: list[dict[str, object]], winner: str = "A",
          div_a: str = "65 kg", div_b: str | None = None) -> dict[str, object]:
    return {"id": bid, "a": "Roster Athlete", "b": "Opponent", "a_id": "A", "b_id": "B",
            "div_a": div_a, "div_b": div_b, "event": "Test Invitational", "year": 2025,
            "winner": winner, "win_type": "SUBMISSION", "seq": seq}


def _e(ts: int, ty: str, label: str, actor: str) -> dict[str, object]:
    return {"ts": ts, "type": ty, "label": label, "actor_id": actor}


TWO_SIDED_SEQ: list[dict[str, object]] = [
    _e(30, "guard", "Half Guard", "B"),          # the opponent is underneath
    _e(45, "pass", "Knee Cut Pass", "A"),        # the roster athlete passes
    _e(70, "control", "Back Control", "A"),      # ... and takes the back
    _e(95, "submission", "Rear Naked Choke", "A"),
]
TWO_SIDED = _bout("b1", TWO_SIDED_SEQ)


def test_a_named_actor_produces_both_a_favor_and_a_contra() -> None:
    out = bx.sequence_layer([TWO_SIDED], "65 kg")
    at = out["attribution"]
    assert at["favor"] == 3 and at["contra"] == 1 and at["unattributed"] == 0
    assert at["favor"] + at["contra"] + at["unattributed"] == at["events_in_bouts"]


def test_back_control_is_controlling_a_favor_and_controlled_contra() -> None:
    """The single highest-cost inversion available in this data. Read from the wrong corner,
    `Back Control` says she lost the back when she took it."""
    out = bx.sequence_layer([TWO_SIDED], "65 kg")
    favor = {(n["label"], n["role"]) for n in out["nodes"]["favor"]["state"]}
    contra = {(n["label"], n["role"]) for n in out["nodes"]["contra"]["state"]}
    assert ("Back Control", "controlling") in favor
    assert ("Back Control", "controlling") not in contra


def test_half_guard_keeps_top_and_bottom_apart_across_the_two_corners() -> None:
    out = bx.sequence_layer([TWO_SIDED], "65 kg")
    contra = {(n["label"], n["role"]) for n in out["nodes"]["contra"]["state"]}
    # The opponent played the half guard, so the roster athlete was the one on top of it.
    assert ("Half Guard", "top") in contra


def test_a_one_sided_bout_contributes_no_directional_events() -> None:
    """The measured defect: 307 of 700 corpus bouts file every event under one athlete. Those
    events survive in the total and are refused everywhere a direction is claimed."""
    one = _bout("b2", [_e(10 + 10 * i, "control", "Back Control", "A") for i in range(7)])
    out = bx.sequence_layer([one], "65 kg")
    at = out["attribution"]
    assert at["favor"] == 0 and at["contra"] == 0
    assert at["unattributed"] == 7 == at["events_in_bouts"]
    assert at["by_reason"] == {"one_sided": 1}
    assert out["nodes"]["favor"]["state"] == []


def test_a_bout_that_contradicts_itself_keeps_perspective_and_loses_topology() -> None:
    """Nobody is mounted and in half guard at once. The actor field may still name the right
    fighter, so a favor / contra survives; the side of the position does not."""
    clash = _bout("b3", [
        _e(100, "guard", "Half Guard", "A"), _e(105, "control", "Mount", "A"),
        _e(200, "submission", "Armbar", "A"), _e(210, "takedown", "Single Leg Takedown", "B"),
    ])
    out = bx.sequence_layer([clash], "65 kg")
    at = out["attribution"]
    assert at["by_reason"] == {"self_contradictory": 1}
    assert at["favor"] == 3 and at["contra"] == 1      # perspective survives
    assert at["role_unknown"] == 4                     # topology does not
    assert all(n["role"] == "unknown" for n in out["nodes"]["favor"]["state"])


def test_a_bookkeeping_row_never_reaches_a_node_list() -> None:
    junk = _bout("b4", [*TWO_SIDED_SEQ, _e(120, "match", "Match", "A")])
    out = bx.sequence_layer([junk], "65 kg")
    labels = [n["label"] for cat in ("state", "action", "transition")
              for p in ("favor", "contra") for n in out["nodes"][p][cat]]
    assert "Match" not in labels


def test_the_two_perspectives_are_not_two_views_of_one_distribution() -> None:
    """Three of her events and one of her opponent's. Pooling them would report a category
    that passes guards and gets passed at the same rate, which is not what happened."""
    out = bx.sequence_layer([TWO_SIDED], "65 kg")
    favor = out["type_by_outcome"]["favor"]["win"]
    contra = out["type_by_outcome"]["contra"]["win"]
    assert set(favor) == {"pass", "control", "submission"}
    assert set(contra) == {"guard"}


# ── the cut space ───────────────────────────────────────────────────────────────
def test_the_cut_space_is_a_coordinate_system() -> None:
    """Three independent axes, composable in any combination. The old list of hand-picked
    strings had no key for "gi under IBJJF rules since 2022" and, worse, no way for the page to
    hold one selection while another moved."""
    assert bx.cut_key() == "u:all|r:all|y:all" == bx.ALL_CUT
    assert bx.cut_key("gi", "ibjjf", "2022") == "u:gi|r:ibjjf|y:2022"
    assert bx.OPENING_CUT == bx.cut_key(uniform_="no_gi", since="2024")


@pytest.mark.parametrize("u,r,y,keep", [
    ("all", "all", "all", True),
    ("no_gi", "all", "all", True),
    ("gi", "all", "all", False),
    ("all", "adcc", "all", True),
    ("all", "ibjjf", "all", False),
    ("all", "all", "2024", True),
    ("all", "all", "2026", False),
    ("no_gi", "adcc", "2024", True),
])
def test_each_axis_filters_independently(u: str, r: str, y: str, keep: bool) -> None:
    assert bx.in_cut("no_gi", "adcc", 2025, u, r, y) is keep


def test_the_cross_product_covers_every_combination_present() -> None:
    rows = [{"uniform": "no_gi", "ruleset": "adcc", "year": 2025},
            {"uniform": "gi", "ruleset": "ibjjf", "year": 2019}]
    keys = dict(bx._cuts(rows))
    # Both rows in the origin, one each in their own corner, and the corner that mixes them
    # does not exist rather than existing empty.
    assert len(keys[bx.ALL_CUT]) == 2
    assert len(keys["u:gi|r:ibjjf|y:2019"]) == 1
    assert "u:gi|r:adcc|y:all" not in keys


# ── an athlete's own page ───────────────────────────────────────────────────────
def _rec(name: str, rows: list[dict[str, object]], **kw: object) -> dict[str, object]:
    return {name: {"key": bx.athlete_key(name), "display": name, "division": "+65 kg",
                   "rows": rows, **kw}}


def _rrow(year: int, wl: str, method: str, comp: str = "ADCC",
          source: str = "own_record") -> dict[str, object]:
    return {"opp": f"Opponent {year}", "wl": wl, "method": method, "comp": comp,
            "year": year, "source": source}


NO_RATING: dict[str, object] = {"athletes": {}}


def test_a_reconstructed_record_is_not_a_second_class_page() -> None:
    """Same axes, same interval, same grade. The only thing a two-row record gets that a
    forty-row one does not is the `insufficient` grade its own n earns."""
    recs = _rec("Joslyn Molina", [_rrow(2024, "W", "Armbar", source="corpus"),
                                  _rrow(2023, "L", "Heel Hook", source="opponent_record")],
                provenance={"total": 2, "by_source": {"corpus": 1, "opponent_record": 1},
                            "has_own_table": False, "reconstructed_share": 1.0})
    out = bx.athlete_layer(recs, NO_RATING, [])
    a = out["joslyn molina"]
    cut = a["cuts"][bx.ALL_CUT]
    assert cut["n"] == 2 and cut["w"] == 1 and cut["l"] == 1
    # n < 5 -> insufficient, whatever the interval arithmetic says.
    assert cut["win_rate"]["grade"] == "insufficient"
    assert cut["finish_rate"]["grade"] == "insufficient"
    assert a["provenance"]["has_own_table"] is False
    # And the axes are the same ones the category uses, not a reduced set.
    assert bx.cut_key(uniform_="no_gi") in a["cuts"]


def test_an_athlete_page_states_that_the_cluster_gate_does_not_apply() -> None:
    """It cannot apply — there is one athlete on her own page by construction — and leaving
    that implicit would read as the gate having been quietly dropped."""
    out = bx.athlete_layer(_rec("Gabi Garcia", [_rrow(2024, "W", "Armbar")]), NO_RATING, [])
    gate = out["gabi garcia"]["gate"]
    assert gate["cluster_coverage_applies"] is False
    assert gate["why_code"] == "single_athlete_by_construction"
    assert gate["min_n_for_grade"] == MIN_N_FOR_ANY_GRADE


def test_an_athlete_page_carries_no_cut_the_record_does_not_reach() -> None:
    """A cut with no bouts must be ABSENT, so the page can say "nenhuma luta neste corte"
    instead of drawing an empty table that looks like a zero."""
    out = bx.athlete_layer(_rec("Helena Crevar", [_rrow(2024, "W", "Armbar", "ADCC")]),
                           NO_RATING, [])
    cuts = out["helena crevar"]["cuts"]
    assert bx.cut_key(uniform_="gi") not in cuts
    assert bx.cut_key(uniform_="no_gi", ruleset_="adcc", since="2024") in cuts


def test_a_later_own_table_lands_on_the_same_identity() -> None:
    """The record is one object with a mix, not an own record beside a reconstructed one."""
    recs = _rec("Ane Svendsen",
                [_rrow(2024, "W", "Armbar"),
                 _rrow(2023, "L", "Heel Hook", source="opponent_record")],
                provenance={"total": 2, "by_source": {"own_record": 1, "opponent_record": 1},
                            "has_own_table": True, "reconstructed_share": 0.5})
    a = bx.athlete_layer(recs, NO_RATING, [])["ane svendsen"]
    assert a["provenance"]["has_own_table"] is True
    assert a["provenance"]["reconstructed_share"] == 0.5
    assert a["cuts"][bx.ALL_CUT]["by_source"] == {"own_record": 1, "opponent_record": 1}


# ── the fold, at the layer that publishes it ────────────────────────────────────
REPEATS = _bout("b5", [
    _e(10, "guard", "Half Guard", "A"), _e(20, "guard", "Half Guard", "A"),
    _e(30, "guard", "Half Guard", "A"),
    _e(40, "pass", "Guard Pass", "A"),
    _e(50, "control", "Mount", "A"), _e(60, "control", "Mount", "A"),
    _e(70, "submission", "Armbar", "B"),
])


def test_the_transition_graph_carries_no_self_loop() -> None:
    """A -> A is not a transition. Before the fold, the +65 kg heatmap held 31 of them on the
    diagonal and the win path five self-loop edges — a graph substantially about nothing."""
    out = bx.sequence_layer([REPEATS], "65 kg")
    for kind in ("path_to_victory", "path_to_defeat"):
        assert [e for e in out[kind]["edges"] if e["from"] == e["to"]] == []
        pairs = [b[0].split(" → ") for b in out[kind]["bigrams"]]
        assert [p for p in pairs if p[0] == p[1]] == []
    h = out["heatmap"]
    assert all(h["matrix"][i][i] == 0 for i in range(len(h["labels"])))


def test_the_fold_is_published_rather_than_described() -> None:
    out = bx.sequence_layer([REPEATS], "65 kg")
    n = out["sequence_normalization"]
    assert n["raw_events"] == 6 and n["normalized_events"] == 3
    assert n["consecutive_duplicates_removed"] == 3
    assert n["state_collapses"] == 3 and n["action_repeats_folded"] == 0
    assert dict(n["re_entries"]) == {"Half Guard": 2, "Mount": 1}
    assert n["rule_code"] == "consecutive_only_array_order"


def test_the_counts_do_not_move_when_the_graph_folds() -> None:
    """The fold is for edges. Every row still reaches the node list and the type mix."""
    out = bx.sequence_layer([REPEATS], "65 kg")
    assert out["attribution"]["favor"] == 6          # all six of hers, unfolded
    state = {(x["label"], x["k"]) for x in out["nodes"]["favor"]["state"]}
    assert ("Half Guard", 3) in state and ("Mount", 2) in state


def test_a_return_still_draws_its_edge() -> None:
    back = _bout("b6", [
        _e(10, "guard", "Half Guard", "A"), _e(20, "pass", "Guard Pass", "A"),
        _e(30, "guard", "Half Guard", "A"), _e(40, "submission", "Armbar", "A"),
    ])
    out = bx.sequence_layer([back], "65 kg")
    edges = {(e["from"], e["to"]) for e in out["path_to_victory"]["edges"]}
    assert ("Half Guard", "Guard Pass") in edges
    assert ("Guard Pass", "Half Guard") in edges
    assert out["sequence_normalization"]["consecutive_duplicates_removed"] == 0


# ── the importer's type gate ────────────────────────────────────────────────────
def test_a_match_marker_cannot_reach_the_sequence() -> None:
    """Eight "Match starts" markers reached prod as events and were counted by every
    aggregate that asked "how many events" -- until they were deleted on 2026-08-20 (backup in
    runs/db_consolidation/). This pins the door shut: an event whose type is not in the
    model's vocabulary is dropped at ingestion, loudly, never passed through."""
    from scripts.insert_ufc_matches import VALID_EVENT_TYPES, _clean_events

    events = [
        {"type": "match", "label": "Match", "actor": "Alice A"},
        {"type": "penalty", "label": "Stalling", "actor": "Alice A"},
        {"type": "takedown", "label": "Single Leg Takedown", "actor": "Alice A"},
    ]
    kept = _clean_events("Alice A", "Bob B", events)
    assert [e["type"] for e in kept] == ["takedown"]
    assert "match" not in VALID_EVENT_TYPES


# ── who is in a piece of footage ────────────────────────────────────────────────
# The reading page linked an athlete to her own bouts by folding her display name and testing
# it as a SUBSTRING of the footage title, which could only ever be as good as the spellings the
# renderer happened to be handed. The identity lives on this side; the export carries it.
ALIASES = {"helena cravar": "helena crevar", "helena crevar": "helena crevar",
           "sarah galvao": "sarah galvao", "mo black": "morgan black"}


@pytest.mark.parametrize("title,expected", [
    ("Helena Crevar vs Amanda Nicole, Polaris 37 2026", ["Helena Crevar", "Amanda Nicole"]),
    ("Anabel Lopez vs Aurelie Le Vern, European No-Gi 2024",
     ["Anabel Lopez", "Aurelie Le Vern"]),
    ("Yara Soares vs Ana Carolina Vieira, ? 2021", ["Yara Soares", "Ana Carolina Vieira"]),
    # A comma inside the event name is the common case; only the LAST one separates the bout
    # from where it happened.
    # A comma inside the event name must not take the second athlete with it.
    ("Morgan Black vs Sophia Delgado, ADCC Trials 2023, East Coast 2023",
     ["Morgan Black", "Sophia Delgado"]),
])
def test_a_footage_title_names_the_pair_it_is_of(title: str, expected: list[str]) -> None:
    assert bx.title_pair(title) == expected


@pytest.mark.parametrize("title", ["Open mat, 2024", "", "Helena Crevar highlight"])
def test_a_title_that_names_no_pair_resolves_to_nothing_rather_than_a_guess(title: str) -> None:
    """An empty list makes the gap visible on the page. A guess makes it invisible."""
    assert bx.footage_keys(bx.title_pair(title), ALIASES) == []


def test_footage_is_linked_by_key_not_by_the_spelling_in_the_title() -> None:
    """`Helena Cravar` is a real misspelling in the corpus and one of her manifest aliases.
    Substring matching on the display name misses it; the key resolves it."""
    keys = bx.footage_keys(bx.title_pair("Helena Cravar vs Yara Soares, ADCC 2024"), ALIASES)
    assert keys == ["helena crevar", "yara soares"]


def test_an_accent_is_not_an_identity() -> None:
    assert bx.footage_keys(["Sarah Galvão"], ALIASES) == ["sarah galvao"]


def test_a_bout_between_two_rostered_athletes_carries_both_of_them() -> None:
    """Her footage list is not the roster's minus her: a bout she fought against another woman
    in this bracket belongs on BOTH pages."""
    keys = bx.footage_keys(("Helena Crevar", "Mo Black"), ALIASES)
    assert keys == ["helena crevar", "morgan black"]


def test_the_alias_index_maps_every_spelling_to_one_key() -> None:
    idx = bx.alias_index({"Sarah Galvão": {"key": "sarah galvao", "display": "Sarah Galvão",
                                           "aliases": ["Sarah Galvao"], "rows": []}})
    assert idx["sarah galvao"] == "sarah galvao"
    assert set(idx.values()) == {"sarah galvao"}


# ── the 2026-08-25 archive policy: frames dropped, PDF + sidecar in out/processed/ ──────────
def _write_legacy_bout(root: Path, slug: str, *, events: int | None, clip: bool) -> None:
    d = root / slug
    d.mkdir(parents=True)
    (d / "frames.jsonl").write_text("{}\n", encoding="utf-8")
    if clip:
        (d / "clip.mp4").write_bytes(b"")
    if events is not None:
        (d / "events.json").write_text(
            json.dumps({"events": [{}] * events}), encoding="utf-8")


def _write_processed_bout(root: Path, slug: str, *, events: int | None) -> None:
    d = root / "processed"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.pdf").write_bytes(b"")
    if events is not None:
        (d / f"{slug}.events.json").write_text(
            json.dumps({"events": [{}] * events}), encoding="utf-8")


def test_an_archived_pdf_is_footage_without_a_clip(tmp_path: Path) -> None:
    """The archive policy drops the clip -- a bout whose frames were never re-rendered has a
    PDF and no clip.mp4, and the loader must still count it as footage, honestly (no clip)."""
    slug = "a-vs-b--event-2024-VID1"
    _write_processed_bout(tmp_path, slug, events=4)
    out = bx._local_footage(tmp_path, {}, {"VID1": "A vs B, Event 2024"})
    assert len(out) == 1
    entry = out[0]
    assert entry["slug"] == slug
    assert entry["clip"] is False
    assert entry["registered_events"] == 4
    assert entry["registered"] is True
    assert entry["title"] == "A vs B, Event 2024"


def test_a_legacy_dir_still_counts_and_keeps_its_clip_flag(tmp_path: Path) -> None:
    _write_legacy_bout(tmp_path, "old-slug", events=2, clip=True)
    out = bx._local_footage(tmp_path, {}, {})
    assert len(out) == 1
    assert out[0]["clip"] is True
    assert out[0]["registered_events"] == 2


def test_both_layouts_present_are_not_double_counted(tmp_path: Path) -> None:
    """A bout rendered before AND after the policy is one bout, not two -- the legacy dir
    wins because it still has the clip."""
    slug = "dup-slug"
    _write_legacy_bout(tmp_path, slug, events=1, clip=True)
    _write_processed_bout(tmp_path, slug, events=99)
    out = bx._local_footage(tmp_path, {}, {})
    assert len(out) == 1
    assert out[0]["clip"] is True
    assert out[0]["registered_events"] == 1


def test_an_unregistered_archived_bout_reports_zero_not_missing(tmp_path: Path) -> None:
    slug = "no-sidecar--event-2024-VID2"
    _write_processed_bout(tmp_path, slug, events=None)
    out = bx._local_footage(tmp_path, {}, {})
    assert out[0]["registered_events"] == 0
    assert out[0]["registered"] is False


def test_title_falls_back_to_the_slug_when_the_manifest_does_not_cover_it() -> None:
    assert bx._title_for_slug("unknown-slug-XYZ", {"VID1": "A vs B, Event 2024"}) \
        == "unknown-slug-XYZ"


def test_title_lookup_matches_a_video_id_that_itself_contains_a_hyphen() -> None:
    """`LQUors-3gZM` is a real YouTube id with a hyphen in it (measured 2026-08-25) -- suffix
    matching must prefer the longest/most specific id, not the shortest tail split on `-`."""
    titles = {"3gZM": "Wrong Bout, 2020", "LQUors-3gZM": "Right Bout, 2025"}
    assert bx._title_for_slug("a-vs-b--event-2025-LQUors-3gZM", titles) == "Right Bout, 2025"


# ── how she wins against how she loses ──────────────────────────────────────────
def test_how_she_wins_and_how_she_loses_have_their_own_denominators() -> None:
    """Two wins and one loss: a family that took one of the wins is 1/2, and one that took the
    loss is 1/1. Pooling them -- which is what `by_family` alone can do -- makes both 1/3."""
    recs = _rec("Helena Crevar", [_rrow(2024, "W", "Armbar"), _rrow(2024, "W", "Points"),
                                  _rrow(2023, "L", "Heel Hook")])
    cut = bx.athlete_layer(recs, NO_RATING, [])["helena crevar"]["cuts"][bx.ALL_CUT]
    assert cut["by_family_win"]["joint"]["k"] == 1
    assert cut["by_family_win"]["joint"]["n"] == 2
    assert cut["by_family_loss"]["leg"]["k"] == 1
    assert cut["by_family_loss"]["leg"]["n"] == 1
    # And the pooled histogram it sits beside still says what it always said.
    assert cut["by_family"]["joint"] == 1


def test_a_family_only_one_side_reached_is_published_on_both() -> None:
    """A submission she hits and has never been caught in is a finding, and it only exists as a
    pair: 1/2 against 0/1, on the same row."""
    recs = _rec("Gabi Garcia", [_rrow(2024, "W", "Armbar"), _rrow(2024, "W", "Points"),
                                _rrow(2023, "L", "Points")])
    cut = bx.athlete_layer(recs, NO_RATING, [])["gabi garcia"]["cuts"][bx.ALL_CUT]
    assert cut["by_family_win"]["joint"]["k"] == 1
    assert cut["by_family_loss"]["joint"]["k"] == 0
    assert cut["by_family_loss"]["joint"]["n"] == 1


def test_a_family_neither_side_reached_is_not_published_at_all() -> None:
    """688 athlete-cuts times eight families times two sides is a megabyte of zeroes."""
    recs = _rec("Ane Svendsen", [_rrow(2024, "W", "Armbar")])
    cut = bx.athlete_layer(recs, NO_RATING, [])["ane svendsen"]["cuts"][bx.ALL_CUT]
    assert set(cut["by_family_win"]) == {"joint"}
    assert "leg" not in cut["by_family_loss"]


def test_her_own_page_grades_the_split_but_does_not_gate_it() -> None:
    """The cluster gate asks how many independent athletes a number comes from. Here there is
    one by construction, so it cannot apply -- and would refuse every cell if it did. What
    still applies is the n<5 floor."""
    recs = _rec("Yara Soares", [_rrow(2024, "W", "Armbar"), _rrow(2023, "L", "Heel Hook")])
    cut = bx.athlete_layer(recs, NO_RATING, [])["yara soares"]["cuts"][bx.ALL_CUT]
    assert cut["by_family_win"]["joint"]["grade"] == "insufficient"
    assert "estimable" not in cut["by_family_win"]["joint"]
    assert cut["by_family_win"]["joint"]["lo"] is not None


def test_a_side_with_no_bouts_publishes_no_interval() -> None:
    """She has never lost in this cut, so `0/0` is not `0%` -- there is nothing to be a share
    of, and the interval is absent rather than [0,1]."""
    recs = _rec("Nadia Frankland", [_rrow(2024, "W", "Armbar")])
    cut = bx.athlete_layer(recs, NO_RATING, [])["nadia frankland"]["cuts"][bx.ALL_CUT]
    loss = cut["by_family_loss"]["joint"]
    assert loss["n"] == 0 and loss["p"] is None and loss["lo"] is None
    assert loss["grade"] == "none"


def test_her_tape_counts_bouts_from_either_corner() -> None:
    """`sequence_bouts` walks every corpus bout checking BOTH `a` and `b` against her key --
    an athlete recovered only from an opponent's page (no `own_record` row of her own) still
    gets her tape wherever the corpus bout happens to list her second. A regression here
    reproduces the reported defect: an athlete with real sequenced bouts reading as if she had
    none, because only bouts filed under her own perspective were counted."""
    recs = _rec("Ane Svendsen", [_rrow(2024, "L", "Armbar", source="opponent_record")])
    as_a = _bout("bA", TWO_SIDED_SEQ)                              # roster athlete is corner "a"
    as_b = {**_bout("bB", TWO_SIDED_SEQ), "a": "Someone Else", "b": "Ane Svendsen",
            "a_id": "X", "b_id": "Y"}                              # she is corner "b"
    out = bx.athlete_layer(recs, NO_RATING, [as_a, as_b])
    tape = out["ane svendsen"]["sequence_bouts"]
    assert len(tape) == 1                       # bA is "Roster Athlete" -- a different identity
    assert tape[0]["opponent"] == "Someone Else"


# ── the tape declares its own universe ──────────────────────────────────────────
def test_the_tape_has_a_scope_of_its_own_instead_of_the_page_inventing_one() -> None:
    """`sequence_bouts` is her whole career whatever the cut, and the reading page was
    hard-coding the word CARREIRA because the export gave it nothing to read."""
    sc = bx.SCOPES["athlete_tape"]
    assert sc["kind"] == bx.CAREER
    assert sorted(sc["ignores"]) == sorted(bx.ALL_AXES)
    assert sc["respects"] == []
    assert sc["ignored_reason_code"] == "tape_is_too_thin_to_slice"


def test_every_scope_that_ignores_an_axis_says_why() -> None:
    for key, sc in bx.SCOPES.items():
        assert bool(sc.get("ignores")) == bool(sc.get("ignored_reason_code")), key


# ── the perspective's denominator ───────────────────────────────────────────────
def _five_sided_bouts() -> list[dict[str, object]]:
    """Five bouts from five different roster athletes, so the coverage gate passes."""
    out = []
    for i in range(5):
        b = _bout(f"c{i}", [
            _e(30, "guard", "Half Guard", f"B{i}"),
            _e(45, "pass", "Knee Cut Pass", f"A{i}"),
            _e(70, "control", "Back Control", f"A{i}"),
        ], winner=f"A{i}")
        b.update({"a_id": f"A{i}", "b_id": f"B{i}", "a": f"Roster {i}", "b": f"Opponent {i}"})
        out.append(b)
    return out


def test_a_node_carries_the_denominator_its_rate_needs() -> None:
    """Counts alone compare two sides that do not have the same number of events. `n` is the
    perspective's own total, so a share of one side is comparable to a share of the other."""
    out = bx.sequence_layer(_five_sided_bouts(), "65 kg")
    assert out["coverage"]["favor"]["all"]["estimable"] is True
    back = next(n for n in out["nodes"]["favor"]["state"] if n["label"] == "Back Control")
    assert back["k"] == 5
    assert back["n"] == out["events_own"] == 10
    # The share arrives decided too. A proportion the page computes is a proportion that can
    # disagree with the analysis that produced the counts under it.
    assert back["p"] == 0.5
    guard = next(n for n in out["nodes"]["contra"]["state"] if n["label"] == "Half Guard")
    assert guard["n"] == out["events_against"] == 5


def test_a_refused_perspective_publishes_no_denominator() -> None:
    """The count survives -- it is a fact about the corpus. The denominator does not, because
    a rate over a refused side reads as the category claim the gate exists to withhold."""
    out = bx.sequence_layer([TWO_SIDED], "65 kg")
    assert out["coverage"]["favor"]["all"]["estimable"] is False
    assert all(n["n"] is None and n["p"] is None
               for cat in ("state", "action", "transition")
               for n in out["nodes"]["favor"][cat])
    assert any(n["k"] for n in out["nodes"]["favor"]["state"])


# ── mirror folding is uniform across every bout-level statistic ─────────────────
def _stat_row(athlete: str, opp: str, wl: str, uniform: str, family: str,
              year: int = 2024, stage: str = "4F") -> dict[str, str | int]:
    """A row as `category_layer` sees it: classified, stamped with its cut axes."""
    return {"athlete": athlete, "opp": opp, "wl": wl, "comp": "NoGi Pan", "year": year,
            "stage": stage, "uniform": uniform, "ruleset": "other", "family": family}


def test_uniform_test_folds_the_mirror_pair() -> None:
    """The defect was inconsistency: `_ruleset_test` folded mirrors while `_uniform_test`
    counted athlete-perspective rows, so a roster-vs-roster bout entered the gi/no-gi
    contrast twice -- anti-correlated, inflating n while deflating the variance of exactly
    this comparison. Ten distinct bouts here, twelve rows."""
    rows = []
    for i in range(5):
        rows.append(_stat_row(f"G{i}", f"GO{i}", "W", "gi", "points", stage=f"g{i}"))
        rows.append(_stat_row(f"N{i}", f"NO{i}", "W", "no_gi", "strangle", stage=f"n{i}"))
    # one roster-vs-roster bout per uniform, each seen from both corners
    rows.append(_stat_row("G0", "G1", "W", "gi", "points", stage="RRg"))
    rows.append(_stat_row("G1", "G0", "L", "gi", "points", stage="RRg"))
    rows.append(_stat_row("N0", "N1", "W", "no_gi", "strangle", stage="RRn"))
    rows.append(_stat_row("N1", "N0", "L", "no_gi", "strangle", stage="RRn"))
    out = bx._uniform_test(rows)
    assert out["available"] is True
    assert out["gi_n"] == 6, "6 distinct gi bouts, not 7 rows"
    assert out["no_gi_n"] == 6, "6 distinct no-gi bouts, not 7 rows"


def test_year_series_folds_the_mirror_pair() -> None:
    """Same rule, same reason: how bouts ended in a year is a bout-level question."""
    rows = [_stat_row("A", "B", "W", "no_gi", "strangle", year=2024),
            _stat_row("B", "A", "L", "no_gi", "strangle", year=2024),
            _stat_row("A", "C", "W", "no_gi", "points", year=2024, stage="F")]
    out = bx._year_series(rows)
    assert out["2024"]["all"]["n"] == 2, "two bouts, not three rows"
    # the mirror keeps the winner's row, so the finish count stays intact
    assert out["2024"]["all"]["finish"]["k"] == 1


def test_radar_usage_goes_through_the_coverage_gate() -> None:
    """The regression: the radar shipped a naive Wilson interval with an 'adequate'
    precision grade BESIDE a coverage block that refused the estimate -- the -65 kg `pass`
    axis read 'top precision' and '3 sources; a category estimate needs 5' in one row.
    Below the gate the usage cell must refuse like every other category estimate."""
    def bout(i: int) -> dict[str, object]:
        # one division athlete per bout: a{i} lands one pass and one control
        seq = [{"actor_id": f"a{i}", "type": "pass"},
               {"actor_id": f"a{i}", "type": "control"}]
        return {"div_a": "65 kg", "div_b": "other", "a_id": f"a{i}", "b_id": f"opp{i}",
                "readable": True, "seq": seq}

    # three contributors on the pass axis -> below MIN_CLUSTERS_FOR_CATEGORY_ESTIMATE
    out = bx._radar_block([bout(i) for i in range(3)], "65 kg", {}, {}, corpus_mean=1500.0)
    axis = next(a for a in out["axes"] if a["axis"] == "pass")
    assert axis["coverage"]["estimable"] is False
    assert axis["usage"]["estimable"] is False, "usage interval must respect the gate"
    assert axis["usage"]["lo"] is None and axis["usage"]["grade"] == "none"
    assert axis["usage"]["k"] == 3, "the observed count survives the refusal"

    # five contributors, balanced -> the gate passes and the interval returns
    out2 = bx._radar_block([bout(i) for i in range(5)], "65 kg", {}, {}, corpus_mean=1500.0)
    axis2 = next(a for a in out2["axes"] if a["axis"] == "pass")
    assert axis2["coverage"]["estimable"] is True
    assert axis2["usage"]["estimable"] is True
    assert axis2["usage"]["lo"] is not None


# ── ADCC 2023-24 cycle selection ────────────────────────────────────────────────
# The SQL that feeds this is deliberately dumb (`event ilike '%adcc%'`); every decision about
# which bout belongs to the cycle lives in `adcc_corpus_of`, so the whole selection — including
# the exclusion of the PREVIOUS cycle — is testable without a database.
#
# Reached through `bx`, the importlib-loaded module at the top of this file, and NOT through a
# plain `from scripts.bracket_export import ...`. That is not a style choice: `scripts/` is in
# mypy's `exclude` list (pyproject.toml), and a real import here drags the module into the
# checked set and surfaces three pre-existing errors in code the gate was configured to skip.
ADCC_TRIALS_2023_24 = bx.ADCC_TRIALS_2023_24
ADCC_TRIALS_LABEL = bx.ADCC_TRIALS_LABEL
ADCC_WORLDS_LABEL = bx.ADCC_WORLDS_LABEL
ADCC_CYCLE_LABEL = bx.ADCC_CYCLE_LABEL
adcc_corpus_of = bx.adcc_corpus_of


@pytest.mark.parametrize("tag", ADCC_TRIALS_2023_24)
def test_every_listed_trials_tag_lands_in_the_trials_corpus(tag: str) -> None:
    """The tag names its own year, so the year column is not consulted at all — a row whose
    `year` is missing or wrong still classifies correctly."""
    assert adcc_corpus_of(tag, 2023) == ADCC_TRIALS_LABEL
    assert adcc_corpus_of(tag, None) == ADCC_TRIALS_LABEL


def test_the_east_coast_tag_covers_finals_and_semis_under_one_name() -> None:
    """Both EC-2023 dumps carry the same tag, so semis are in by construction rather than by a
    second rule that could drift from the first."""
    assert adcc_corpus_of("ADCC Trials 2023 East Coast", 2023) == ADCC_TRIALS_LABEL


def test_the_worlds_tag_lands_in_the_worlds_corpus() -> None:
    assert adcc_corpus_of("ADCC 2024", 2024) == ADCC_WORLDS_LABEL


@pytest.mark.parametrize(("tag", "year"), [
    ("ADCC 2022", 2022),                        # the previous cycle's Worlds, 53 bouts w/ events
    ("ADCC Trials 2022 South America", 2022),   # the previous cycle's qualifier, 6 bouts
    ("ADCC", 2017), ("ADCC", 2019), ("ADCC", 2022),
    ("ADCC World Championship", 2022),
    ("ADCC WC Trials", 2017),
    ("Polaris 25", 2024), ("", 2024), (None, 2024),
])
def test_out_of_cycle_and_unrelated_tags_are_refused(tag: str | None, year: int) -> None:
    assert adcc_corpus_of(tag, year) is None


@pytest.mark.parametrize("tag", ["ADCC", "ADCC World Championship"])
@pytest.mark.parametrize("year", [2023, 2024])
def test_an_undated_tag_is_admitted_only_by_its_row_year(tag: str, year: int) -> None:
    """These two tags name no year, so the row decides. Measured: the in-cycle rows under them
    are Worlds bouts (the Ryan x Pena superfight and two Crelinsten matches) and none duplicates
    an `ADCC 2024` row — which is why they resolve to the Worlds bucket."""
    assert adcc_corpus_of(tag, year) == ADCC_WORLDS_LABEL


@pytest.mark.parametrize("year", [2017, 2019, 2021, 2022, 2025, None, "", "abc"])
def test_an_undated_tag_outside_the_cycle_years_is_refused(year: object) -> None:
    assert adcc_corpus_of("ADCC", year) is None


def test_a_trials_named_undated_tag_never_reaches_the_worlds_bucket() -> None:
    """`ADCC WC Trials` names a trials, so it is deliberately absent from the undated list —
    admitting it by year would file a qualifier under the World Championship."""
    assert adcc_corpus_of("ADCC WC Trials", 2024) is None


def test_tags_are_matched_exactly_after_stripping_not_by_substring() -> None:
    """`ADCC` is a prefix of every other tag in the table. Substring matching here would drag
    the whole 2022 cycle into the Worlds bucket the moment the year happened to be in range."""
    assert adcc_corpus_of("  ADCC 2024  ", 2024) == ADCC_WORLDS_LABEL
    assert adcc_corpus_of("ADCC 2024 Trials Wildcard", 2024) is None
    assert adcc_corpus_of("Pre-ADCC 2024", 2024) is None


def test_the_three_corpus_labels_are_distinct() -> None:
    assert len({ADCC_TRIALS_LABEL, ADCC_WORLDS_LABEL, ADCC_CYCLE_LABEL}) == 3
