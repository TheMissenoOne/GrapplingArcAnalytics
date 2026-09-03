"""Matcher + candidate-selection tests for the zero-shot Gemini baseline
(scripts/gemini_baseline.py).

Pure functions only -- no network, no DB. What actually calls Gemini is exercised by hand
(docs/video_frames_gemini.md's own "Baseline zero-shot" log), same convention as
tests/test_gemini_read_frames.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.gemini_baseline import (
    bout_metrics,
    is_human_reviewed,
    label_key,
    match_bout,
    select_candidates,
)


def test_label_key_folds_synonyms_and_case():
    assert label_key("Pass", "pass") == label_key("Guard Pass", "pass")
    assert label_key("Armbar", "submission") == label_key("Arm Lock", "submission")  # SYNONYMS


def test_is_human_reviewed():
    assert is_human_reviewed("gemini reading, concordance-audited (kept 9/10) 2026-08-25")
    assert is_human_reviewed("frame_registrar (human review over model reading)")
    assert is_human_reviewed("frame_registrar (human)")
    assert not is_human_reviewed("frame_answer_import (returned reading, not yet human-reviewed)")
    assert not is_human_reviewed("gemini_read_frames (gemini-3.6-flash, thinking=high, "
                                 "2026-09-03) — not yet human-reviewed")


def _ev(ts, label, actor, type_, successful=True, confidence=None):
    e = {"ts": ts, "label": label, "actor": actor, "type": type_, "successful": successful}
    if confidence:
        e["confidence"] = confidence
    return e


def test_match_bout_exact():
    human = [_ev(100, "Armbar", "A", "submission")]
    model = [_ev(103, "Armbar", "A", "submission", confidence="high")]
    matches = match_bout(human, model, ts_tolerance=10)
    assert len(matches) == 1
    m = matches[0]
    assert m.human_idx == 0 and m.model_idx == 0
    assert m.ts_diff == 3
    assert m.actor_match is True


def test_match_bout_rejects_wrong_type():
    human = [_ev(100, "Armbar", "A", "submission")]
    model = [_ev(101, "Armbar", "A", "control")]
    assert match_bout(human, model, ts_tolerance=10) == []


def test_match_bout_rejects_outside_tolerance():
    human = [_ev(100, "Armbar", "A", "submission")]
    model = [_ev(115, "Armbar", "A", "submission")]
    assert match_bout(human, model, ts_tolerance=10) == []


def test_match_bout_synonym_label_matches():
    human = [_ev(100, "Guard Pass", "A", "pass")]
    model = [_ev(100, "Pass", "A", "pass")]
    assert len(match_bout(human, model, ts_tolerance=10)) == 1


def test_match_bout_greedy_nearest_ts_no_double_use():
    # two model events both plausibly match one human event -- only the nearer one wins, and
    # the other model event is left unmatched (never double-counted).
    human = [_ev(100, "Armbar", "A", "submission")]
    model = [_ev(105, "Armbar", "A", "submission"), _ev(101, "Armbar", "A", "submission")]
    matches = match_bout(human, model, ts_tolerance=10)
    assert len(matches) == 1
    assert matches[0].model_idx == 1  # the ts=101 one, not ts=105


def test_match_bout_actor_mismatch_still_matches_but_flags_actor():
    human = [_ev(100, "Armbar", "A", "submission")]
    model = [_ev(100, "Armbar", "B", "submission")]
    matches = match_bout(human, model, ts_tolerance=10)
    assert len(matches) == 1
    assert matches[0].actor_match is False


def test_bout_metrics_precision_recall_f1():
    human = [_ev(10, "Armbar", "A", "submission"), _ev(50, "Guard Pull", "A", "transition")]
    model = [_ev(11, "Armbar", "A", "submission", confidence="high"),
             _ev(90, "Takedown", "B", "takedown", confidence="low")]  # false positive, unmatched
    matches = match_bout(human, model, ts_tolerance=10)
    m = bout_metrics(human, model, matches)
    assert m["n_human"] == 2
    assert m["n_model"] == 2
    assert m["n_matched"] == 1
    assert m["by_type"]["submission"]["tp"] == 1
    assert m["by_type"]["submission"]["support"] == 1
    assert m["by_type"]["submission"]["predicted"] == 1
    assert m["by_type"]["transition"]["tp"] == 0
    assert m["by_type"]["transition"]["support"] == 1
    assert m["mean_ts_error"] == 1.0
    assert m["actor_accuracy"] == 1.0
    assert m["confidence_high_rate"] == 0.5


def test_select_candidates(tmp_path: Path):
    repo = tmp_path
    trials = repo / "trials_2023_24"
    trials.mkdir(parents=True)
    answers = trials / "answers"
    answers.mkdir()

    bouts = {
        "url": "https://www.youtube.com/watch?v=pwGbW5GZgfc",
        "bouts": [
            {"start": 0, "end": 830, "label": "Heikki Jussila vs. Daniel Manasoiu",
             "event": "e", "division": "d"},
            {"start": 810, "end": 955, "label": "No Sheet Bout vs. Nobody",
             "event": "e", "division": "d"},
        ],
    }
    (repo / "trials_2023_24_bouts.json").write_text(json.dumps(bouts), encoding="utf-8")

    # bout 1 has both a rendered PDF and a concordance-audited answer -> eligible
    (trials / "heikki-jussila-vs--daniel-manasoiu-pwGbW5GZgfc.pdf").write_bytes(b"%PDF-fake")
    (answers / "heikki-jussila-vs-daniel-manasoiu.events.json").write_text(json.dumps(
        {"bout": {"athlete_a": "Heikki Jussila", "athlete_b": "Daniel Manasoiu"}, "events": [],
         "source": "gemini reading, concordance-audited (kept 1/1) 2026-08-25"}), encoding="utf-8")

    # bout 2 has an answer but no rendered PDF -> not eligible
    (answers / "no-sheet-bout-vs-nobody.events.json").write_text(json.dumps(
        {"bout": {}, "events": [], "source": "gemini reading, concordance-audited (kept 0/0) x"}),
        encoding="utf-8")

    candidates = select_candidates(n=10, trials_dir=trials,
                                   bouts_path=repo / "trials_2023_24_bouts.json")
    assert len(candidates) == 1
    assert candidates[0].slug == "heikki-jussila-vs-daniel-manasoiu"
    assert candidates[0].sheet_path.name == "heikki-jussila-vs--daniel-manasoiu-pwGbW5GZgfc.pdf"


def test_select_candidates_respects_n(tmp_path: Path):
    # n=0 -> no candidates, regardless of what's on disk
    candidates = select_candidates(n=0, trials_dir=tmp_path, bouts_path=tmp_path / "missing.json")
    assert candidates == []
