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


TWO_SIDED = _bout("b1", [
    _e(30, "guard", "Half Guard", "B"),          # the opponent is underneath
    _e(45, "pass", "Knee Cut Pass", "A"),        # the roster athlete passes
    _e(70, "control", "Back Control", "A"),      # ... and takes the back
    _e(95, "submission", "Rear Naked Choke", "A"),
])


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
    junk = _bout("b4", [*TWO_SIDED["seq"], _e(120, "match", "Match", "A")])
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
