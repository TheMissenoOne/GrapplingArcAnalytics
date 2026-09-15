"""Matcher + grid + report-shape tests for scripts/research/vision_read_experiment.py.

Pure functions only -- no network, no DB. Mirrors tests/test_gemini_baseline.py's own
conventions (same matcher family, this module's strict variant additionally requires actor
agreement).
"""
from __future__ import annotations

from typing import Any

from analysis.names import athlete_key
from scripts.research.vision_read_experiment import (
    BOUTS,
    CallSpec,
    aggregate_by_config,
    build_grid,
    count_unresolved_actors,
    extract_kit_colors,
    fold_actor,
    kit_winner_agreement,
    match_events,
    metadata_agreement,
    render_report,
    score_events,
    score_events_swap_invariant,
    swap_bout_actors,
    thinking_levels_for,
)


def _ev(ts: int, label: str, actor: str, type_: str, **extra: Any) -> dict[str, Any]:
    return {"ts": ts, "label": label, "actor": actor, "type": type_, **extra}


# ------------------------------------------------------------------------- matcher

def test_match_events_exact() -> None:
    ref = [_ev(100, "Armbar", "Nicky Ryan", "submission")]
    cand = [_ev(103, "Armbar", "Nicky Ryan", "submission")]
    matches = match_events(ref, cand, tol=10)
    assert len(matches) == 1
    assert matches[0].ref_idx == 0 and matches[0].cand_idx == 0
    assert matches[0].ts_diff == 3
    assert matches[0].actor_match is True


def test_match_events_tolerance_edge() -> None:
    ref = [_ev(100, "Armbar", "A", "submission")]
    within = [_ev(110, "Armbar", "A", "submission")]  # exactly at tol=10
    outside = [_ev(111, "Armbar", "A", "submission")]  # one second past
    assert len(match_events(ref, within, tol=10)) == 1
    assert len(match_events(ref, outside, tol=10)) == 0


def test_match_events_actor_swap_strict_rejects_relaxed_accepts() -> None:
    ref = [_ev(100, "Armbar", "Owen Jones", "submission")]
    cand = [_ev(100, "Armbar", "Cammy Donnelly", "submission")]  # wrong actor
    assert match_events(ref, cand, tol=10, ignore_actor=False) == []
    relaxed = match_events(ref, cand, tol=10, ignore_actor=True)
    assert len(relaxed) == 1
    assert relaxed[0].actor_match is False  # still reported, just not required


def test_match_events_duplicate_events_one_to_one() -> None:
    # Two identical reference events, only one candidate event -- exactly one match, the
    # other reference event stays unmatched (never double-counted).
    ref = [_ev(100, "Armbar", "A", "submission"), _ev(101, "Armbar", "A", "submission")]
    cand = [_ev(100, "Armbar", "A", "submission")]
    matches = match_events(ref, cand, tol=10)
    assert len(matches) == 1
    used_cands = {m.cand_idx for m in matches}
    assert used_cands == {0}


def test_match_events_synonym_label_via_node_key() -> None:
    ref = [_ev(100, "Guard Pass", "A", "pass")]
    cand = [_ev(100, "Pass", "A", "pass")]
    assert len(match_events(ref, cand, tol=10)) == 1


def test_fold_actor_resolves_abbreviated_surname_and_initial() -> None:
    roster = {"athlete_a": "Lorenzo Bernardi", "athlete_b": "Henrique Durans"}
    resolved, ok = fold_actor("L. Bernardi", roster)
    assert ok is True
    assert resolved == "Lorenzo Bernardi"

    resolved2, ok2 = fold_actor("H Durans", roster)  # no period, still an initial prefix
    assert ok2 is True
    assert resolved2 == "Henrique Durans"

    # surname alone (no matching first-token initial) does not fold
    resolved3, ok3 = fold_actor("Durans", roster)
    assert ok3 is False
    assert resolved3 == "Durans"

    # wrong surname never folds, even with a plausible initial
    resolved4, ok4 = fold_actor("H. Someone", roster)
    assert ok4 is False


def test_match_events_folds_abbreviated_actor_and_counts_unresolved() -> None:
    roster = {"athlete_a": "Lorenzo Bernardi", "athlete_b": "Lucas Lima"}
    ref = [_ev(100, "Armbar", "Lorenzo Bernardi", "submission")]
    cand_folded = [_ev(101, "Armbar", "L. Bernardi", "submission")]
    matches = match_events(ref, cand_folded, tol=10, reference_bout=roster,
                           candidate_bout=roster)
    assert len(matches) == 1
    assert matches[0].actor_match is True
    # without the roster, the abbreviated form does not fold: strict finds no match at all,
    # and relaxed matches but flags the actor as mismatched
    assert match_events(ref, cand_folded, tol=10) == []
    relaxed = match_events(ref, cand_folded, tol=10, ignore_actor=True)
    assert relaxed[0].actor_match is False

    cand_unresolvable = [_ev(100, "Armbar", "Referee", "submission")]
    assert count_unresolved_actors(cand_unresolvable, roster) == 1
    assert count_unresolved_actors(cand_folded, roster) == 0
    # unresolved actor still keeps its own text and is scored as a mismatch, never dropped
    strict = match_events(ref, cand_unresolvable, tol=10, reference_bout=roster,
                          candidate_bout=roster)
    assert strict == []


def test_score_events_precision_recall_f1() -> None:
    ref = [_ev(10, "Armbar", "A", "submission"), _ev(50, "Guard Pull", "A", "transition")]
    cand = [_ev(11, "Armbar", "A", "submission"), _ev(90, "Takedown", "B", "takedown")]
    scores = score_events(ref, cand, tol=10)
    assert scores["strict"]["tp"] == 1
    assert scores["strict"]["support"] == 2
    assert scores["strict"]["predicted"] == 2
    assert scores["strict"]["precision"] == 0.5
    assert scores["strict"]["recall"] == 0.5


# ------------------------------------------------------------------------- swap-invariant

def test_swap_bout_actors_flips_only_the_two_named_athletes() -> None:
    bout = {"athlete_a": "Lorenzo Bernardi", "athlete_b": "Lucas Lima"}
    events = [_ev(10, "Guard Pull", "Lorenzo Bernardi", "transition"),
             _ev(20, "Turtle Control", "lucas lima", "control"),  # case-insensitive match
             _ev(30, "Sweep", "Referee", "sweep")]  # neither athlete -> unchanged
    swapped = swap_bout_actors(bout, events)
    assert swapped[0]["actor"] == "Lucas Lima"
    assert swapped[1]["actor"] == "Lorenzo Bernardi"
    assert swapped[2]["actor"] == "Referee"


def test_score_events_swap_invariant_picks_the_better_orientation() -> None:
    # Reference names the true actors; the candidate's OWN bout has the athletes swapped
    # (the batch's known defect) and every actor in its events is swapped to match.
    ref = [_ev(100, "Armbar", "Owen Jones", "submission"),
          _ev(150, "Guard Pull", "Owen Jones", "transition")]
    cand_bout = {"athlete_a": "Owen Jones", "athlete_b": "Cammy Donnelly"}
    cand_events = [_ev(101, "Armbar", "Cammy Donnelly", "submission"),  # inverted vs ref
                  _ev(151, "Guard Pull", "Cammy Donnelly", "transition")]  # inverted vs ref

    as_is = score_events(ref, cand_events, tol=10)["strict"]
    assert as_is["tp"] == 0  # actor mismatch on every event, strict as-is finds nothing

    swap_result = score_events_swap_invariant(ref, cand_bout, cand_events, tol=10)
    assert swap_result["flipped"] is True
    assert swap_result["tp"] == 2
    assert swap_result["f1"] == 1.0

    combined = score_events(ref, cand_events, tol=10, candidate_bout=cand_bout)
    assert combined["swap_invariant"]["flipped"] is True
    assert combined["swap_invariant"]["tp"] == 2
    # relaxed is untouched by the swap -- actor is never checked there
    assert combined["relaxed"]["tp"] == 2


def test_score_events_swap_invariant_keeps_as_is_when_that_is_already_better() -> None:
    ref = [_ev(100, "Armbar", "Owen Jones", "submission")]
    cand_bout = {"athlete_a": "Owen Jones", "athlete_b": "Cammy Donnelly"}
    cand_events = [_ev(100, "Armbar", "Owen Jones", "submission")]  # already correct
    result = score_events_swap_invariant(ref, cand_bout, cand_events, tol=10)
    assert result["flipped"] is False
    assert result["tp"] == 1


# ------------------------------------------------------------------------- kit colours

_DURANS_DISCRIMINATOR = (
    "athlete_a = yellow rashguard with green trim + blue shorts, dark short hair, first "
    "walkout t11; athlete_b = black 'Guigo Jiu Jitsu' tee + grey/black camo Venum shorts, "
    "lighter longer hair, walkout t24.")
_LIMA_DISCRIMINATOR = (
    "Body TEAL: teal-front rashguard with black sleeves/back. Body MAROON: maroon 'A4F' "
    "rashguard. Assumed TEAL = Lorenzo Bernardi, MAROON = Lucas Lima.")


def test_extract_kit_colors_athlete_placeholder_shape() -> None:
    colors = extract_kit_colors(_DURANS_DISCRIMINATOR, "Henrique Durans", "Lorenzo Bernardi")
    assert colors is not None
    assert colors[athlete_key("Henrique Durans")] == "yellow"
    assert colors[athlete_key("Lorenzo Bernardi")] == "black"


def test_extract_kit_colors_assumed_name_shape() -> None:
    colors = extract_kit_colors(_LIMA_DISCRIMINATOR, "Lorenzo Bernardi", "Lucas Lima")
    assert colors is not None
    assert colors[athlete_key("Lorenzo Bernardi")] == "teal"
    assert colors[athlete_key("Lucas Lima")] == "maroon"


def test_extract_kit_colors_unparseable_text_returns_none() -> None:
    assert extract_kit_colors("Two athletes in dark gis, hard to tell apart.",
                              "A", "B") is None


def test_kit_winner_agreement_agrees_on_kit_despite_name_swap() -> None:
    reference = {"athlete_a": "Henrique Durans", "athlete_b": "Lorenzo Bernardi",
                "winner": "Henrique Durans", "identity_discriminator": _DURANS_DISCRIMINATOR}
    # Candidate swapped the NAMES (classic whole-bout swap) but still calls the yellow kit
    # the winner -- kit_winner_agreement should say they agree even though winner_match
    # (plain name comparison) would not.
    candidate = {"athlete_a": "Lorenzo Bernardi", "athlete_b": "Henrique Durans",
                "winner": "Henrique Durans",
                "identity_discriminator": ("athlete_a = black shirt; "
                                          "athlete_b = yellow rashguard.")}
    match, reason = kit_winner_agreement(reference, candidate)
    assert match is True
    assert "kit colour" in reason


def test_kit_winner_agreement_skips_when_not_parseable() -> None:
    reference = {"winner": "A", "identity_discriminator": _DURANS_DISCRIMINATOR,
                "athlete_a": "A", "athlete_b": "B"}
    candidate = {"winner": "A", "identity_discriminator": "hard to tell kits apart",
                "athlete_a": "A", "athlete_b": "B"}
    match, reason = kit_winner_agreement(reference, candidate)
    assert match is None
    assert "not cheaply parseable" in reason


# ------------------------------------------------------------------------- metadata

def test_metadata_agreement_winner_and_window() -> None:
    ref = {"winner": "Nicky Ryan", "win_type": "SUBMISSION", "bout_start_seconds": 10,
          "bout_end_seconds": 400}
    cand_ok = {"winner": "nicky ryan", "win_type": "submission", "bout_start_seconds": 12,
              "bout_end_seconds": 398, "identity_discriminator": "black rashguard"}
    m = metadata_agreement(ref, cand_ok)
    assert m["winner_match"] is True
    assert m["win_type_match"] is True
    assert m["bout_window_match"] is True
    assert m["identity_discriminator_present"] is True
    assert m["identity_discriminator_ref_present"] is False  # ref has none here
    assert m["identity_discriminator_both_present"] is False

    cand_missing: dict[str, Any] = {}
    m2 = metadata_agreement(ref, cand_missing)
    assert m2["winner_match"] is None
    assert m2["bout_window_match"] is None
    assert m2["identity_discriminator_present"] is False


def test_metadata_agreement_identity_discriminator_both_present() -> None:
    ref = {"identity_discriminator": "black kit"}
    cand = {"identity_discriminator": "blue kit"}
    m = metadata_agreement(ref, cand)
    assert m["identity_discriminator_ref_present"] is True
    assert m["identity_discriminator_present"] is True
    assert m["identity_discriminator_both_present"] is True


def test_metadata_agreement_carries_kit_winner_fields() -> None:
    ref = {"winner": "A", "athlete_a": "A", "athlete_b": "B",
          "identity_discriminator": "no colours here"}
    cand = {"winner": "A", "athlete_a": "A", "athlete_b": "B",
           "identity_discriminator": "no colours here either"}
    m = metadata_agreement(ref, cand)
    assert m["kit_winner_match"] is None
    assert "kit_winner_reason" in m


def test_metadata_agreement_final_score_map() -> None:
    ref = {"final_score": {"Nicky Ryan": 6, "Owen Jones": 0}}
    same_order = {"final_score": {"nicky ryan": 6, "owen jones": 0}}
    flipped = {"final_score": {"Nicky Ryan": 0, "Owen Jones": 6}}
    assert metadata_agreement(ref, same_order)["final_score_match"] is True
    assert metadata_agreement(ref, flipped)["final_score_match"] is False


# ------------------------------------------------------------------------- grid

def test_thinking_levels_for_defaults_add_low_only_for_flash() -> None:
    assert thinking_levels_for("gemini-3.6-flash", None) == ["high", "low"]
    assert thinking_levels_for("gemini-pro-latest", None) == ["high"]
    assert thinking_levels_for("gemini-3.6-flash", ["medium"]) == ["medium"]


def test_build_grid_call_count_and_repeats_only_apply_above_zero_temp() -> None:
    calls = build_grid(bouts=["lima"], models=["gemini-3.6-flash"],
                       temperatures=[0.0, 0.5], thinking=["high"], inputs=["pdf"], repeats=3)
    # temp=0.0 -> 1 repeat, temp=0.5 -> 3 repeats
    assert len(calls) == 1 + 3
    reps_at_zero = [c.repeat for c in calls if c.temperature == 0.0]
    reps_at_half = sorted(c.repeat for c in calls if c.temperature == 0.5)
    assert reps_at_zero == [1]
    assert reps_at_half == [1, 2, 3]


def test_build_grid_dry_run_call_count_full_default_style_grid() -> None:
    calls = build_grid(bouts=list(BOUTS), models=["gemini-3.6-flash", "gemini-pro-latest"],
                       temperatures=[0.0, 0.5, 1.0], thinking=None, inputs=["pdf"], repeats=1)
    # 4 bouts x (flash: 2 thinking levels, pro: 1 level) x 3 temps x 1 input x 1 repeat
    assert len(calls) == 4 * (2 + 1) * 3
    assert all(isinstance(c, CallSpec) for c in calls)
    # out paths are unique -- no two configs collide on disk
    paths = {c.out_path() for c in calls}
    assert len(paths) == len(calls)


# ------------------------------------------------------------------------- report shape

def _row(bout: str, model: str, temp: float, thinking: str, input_: str, repeat: int,
        tp: int, support: int, predicted: int) -> dict[str, Any]:
    p = tp / predicted if predicted else None
    r = tp / support if support else None
    f1 = (2 * p * r / (p + r)) if p and r else None
    scores: dict[str, Any] = {
        "strict": {"tp": tp, "support": support, "predicted": predicted,
                  "precision": p, "recall": r, "f1": f1},
        "relaxed": {"tp": tp, "support": support, "predicted": predicted,
                   "precision": p, "recall": r, "f1": f1}}
    scores["swap_invariant"] = {**scores["strict"], "flipped": False}
    return {"bout": bout, "file": f"{model}.json", "model": model, "temperature": temp,
           "thinking": thinking, "input": input_, "repeat": repeat, "scores": scores,
           "metadata": {"winner_match": True, "win_type_match": True,
                       "final_score_match": None, "bout_window_match": True,
                       "identity_discriminator_present": True,
                       "identity_discriminator_ref_present": True,
                       "identity_discriminator_both_present": True,
                       "kit_winner_match": None},
           "n_problems": 0, "usage": {"total": 100}, "latency_seconds": 1.5, "events": []}


def test_render_report_empty_rows_says_nothing_to_score() -> None:
    md = render_report([], bouts=["lima"])
    assert "Nothing to score yet" in md


def test_render_report_has_per_config_table_and_ranking() -> None:
    rows = [
        _row("lima", "gemini-3.6-flash", 0.0, "high", "pdf", 1, tp=3, support=4, predicted=4),
        _row("durans", "gemini-3.6-flash", 0.0, "high", "pdf", 1, tp=1, support=4, predicted=6),
        _row("lima", "gemini-pro-latest", 0.0, "high", "pdf", 1, tp=4, support=4, predicted=4),
    ]
    md = render_report(rows, bouts=["lima", "durans"])
    assert "## Per-config (averaged over bouts)" in md
    assert "## Ranked (best strict F1 first)" in md
    assert "## Per-bout" in md
    assert "gemini-pro-latest" in md
    assert "gemini-3.6-flash" in md
    # the perfect config (4/4/4) ranks above the noisier one
    ranked_section = md.split("## Ranked")[1]
    assert ranked_section.index("gemini-pro-latest") < ranked_section.index("gemini-3.6-flash")


def test_aggregate_by_config_micro_averages_across_bouts() -> None:
    rows = [
        _row("lima", "gemini-3.6-flash", 0.0, "high", "pdf", 1, tp=1, support=2, predicted=2),
        _row("durans", "gemini-3.6-flash", 0.0, "high", "pdf", 1, tp=1, support=2, predicted=2),
    ]
    agg = aggregate_by_config(rows)
    key = ("gemini-3.6-flash", 0.0, "high", "pdf")
    assert agg[key]["strict"]["tp"] == 2
    assert agg[key]["strict"]["support"] == 4
    assert agg[key]["strict"]["predicted"] == 4
    assert agg[key]["strict"]["precision"] == 0.5
    assert agg[key]["n"] == 2
