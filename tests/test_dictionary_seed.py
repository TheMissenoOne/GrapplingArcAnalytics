"""Pure logic only -- no network, no DB, no ffmpeg/yt-dlp. plan sampling (spread across
bouts), ts arithmetic with ts_origin, the agreement rule, coverage-driven priority, and
report shape."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("google.genai")

from scripts.dictionary_seed import (
    absolute_ts,
    ask_gemini,
    build_plan,
    build_report,
    cap_plan,
    frame_paths,
    load_coverage,
    prioritize_curated,
    score_agreement,
    select_candidates,
    vocabulary_text,
)

CURATED: list[dict[str, Any]] = [
    {"en": "Armbar", "pt": "Armlock", "type": "submission", "variants": ["arm bar"]},
    {"en": "Closed Guard", "pt": "Guarda Fechada", "type": "guard", "variants": []},
]


# ── ts arithmetic ────────────────────────────────────────────────────────────────
def test_absolute_ts_video_absolute_ignores_start() -> None:
    assert absolute_ts(120, "video_absolute", 340) == 340.0


def test_absolute_ts_bout_relative_adds_start() -> None:
    assert absolute_ts(120, "bout_relative", 30) == 150.0


def test_absolute_ts_bout_relative_missing_start_defaults_zero() -> None:
    assert absolute_ts(None, "bout_relative", 30) == 30.0


def test_absolute_ts_unknown_origin_is_unlocatable() -> None:
    assert absolute_ts(120, None, 30) is None
    assert absolute_ts(120, "something_else", 30) is None


def test_absolute_ts_missing_ts_is_unlocatable() -> None:
    assert absolute_ts(120, "video_absolute", None) is None


# ── candidate sampling ───────────────────────────────────────────────────────────
def _cand(mid: str, ts: float, type_: str = "submission") -> dict[str, Any]:
    return {"match_id": mid, "ts": ts, "type": type_}


def test_select_candidates_spreads_across_bouts_before_repeating_one() -> None:
    # bout "a" has 3 events, bout "b" has 1 -- with per_technique=2, the second slot must
    # go to bout "b", not a's second event.
    candidates = [_cand("a", 10), _cand("a", 20), _cand("a", 30), _cand("b", 5)]
    picked = select_candidates(candidates, 2)
    assert {c["match_id"] for c in picked} == {"a", "b"}


def test_select_candidates_prefers_state_type_within_a_bout() -> None:
    candidates = [_cand("a", 10, "submission"), _cand("a", 5, "guard")]
    picked = select_candidates(candidates, 1)
    assert picked[0]["type"] == "guard"


def test_select_candidates_caps_at_pool_size() -> None:
    candidates = [_cand("a", 10)]
    assert len(select_candidates(candidates, 6)) == 1


def test_select_candidates_zero_per_technique() -> None:
    assert select_candidates([_cand("a", 10)], 0) == []


# ── plan sampling (end to end, pure) ─────────────────────────────────────────────
def _match(mid: str, a: str, b: str, year: int, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "match_id": mid, "video_url": f"https://example.com/{mid}", "video_start_seconds": 100,
        "ts_origin": "video_absolute", "event": "Test Event", "year": year,
        "a_name": a, "b_name": b, "sequence": events,
    }


def test_build_plan_matches_only_curated_labels_and_resolves_ts() -> None:
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 340},
        {"label": "Unknown Technique", "type": "submission", "actor": "Bob", "ts": 350},
    ])]
    plan, counts = build_plan(matches, CURATED, per_technique=6)
    assert counts == {"Armbar": 1, "Closed Guard": 0}
    assert len(plan) == 1
    row = plan[0]
    assert row.label == "Armbar"
    assert row.ts == 340.0
    assert row.ts_ms == 340000
    assert row.match_id == "m1"
    assert row.bout


def test_build_plan_drops_events_with_no_locatable_ts() -> None:
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice"},   # no ts
    ])]
    matches[0]["ts_origin"] = None
    plan, counts = build_plan(matches, CURATED, per_technique=6)
    assert counts["Armbar"] == 0
    assert plan == []


def test_build_plan_min_events_filters_out_thin_techniques() -> None:
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 10},
    ])]
    plan, _counts = build_plan(matches, CURATED, per_technique=6, min_events=2)
    assert plan == []


def test_cap_plan_drops_whole_techniques_not_partial() -> None:
    plan, _ = build_plan(
        [_match("m1", "Alice", "Bob", 2025, [
            {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 10},
            {"label": "Armbar", "type": "submission", "actor": "Bob", "ts": 20},
            {"label": "Closed Guard", "type": "guard", "actor": "Alice", "ts": 30},
        ])],
        CURATED, per_technique=6)
    capped = cap_plan(plan, 2)
    assert len(capped) == 2
    assert {r.label for r in capped} == {"Armbar"}


def test_cap_plan_none_is_noop() -> None:
    plan, _ = build_plan(
        [_match("m1", "Alice", "Bob", 2025,
               [{"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 10}])],
        CURATED, per_technique=6)
    assert cap_plan(plan, None) == plan


# ── coverage-driven priority ──────────────────────────────────────────────────────
def test_prioritize_curated_puts_zero_bucket_first_by_corpus_events() -> None:
    curated = [
        {"en": "Armbar"}, {"en": "Leg Drag Pass"}, {"en": "Closed Guard"},
    ]
    coverage = {
        "Armbar": {"bucket": "well-covered", "corpus_events": 900},
        "Leg Drag Pass": {"bucket": "zero", "corpus_events": 58},
        "Closed Guard": {"bucket": "thin", "corpus_events": 12},
    }
    ordered = prioritize_curated(curated, coverage)
    assert [t["en"] for t in ordered] == ["Leg Drag Pass", "Closed Guard", "Armbar"]


def test_prioritize_curated_ranks_zero_by_corpus_events_descending() -> None:
    curated = [{"en": "A"}, {"en": "B"}]
    coverage = {
        "A": {"bucket": "zero", "corpus_events": 10},
        "B": {"bucket": "zero", "corpus_events": 50},
    }
    ordered = prioritize_curated(curated, coverage)
    assert [t["en"] for t in ordered] == ["B", "A"]


def test_prioritize_curated_missing_entry_ranks_last() -> None:
    curated = [{"en": "Known"}, {"en": "Not In Coverage"}]
    coverage = {"Known": {"bucket": "zero", "corpus_events": 5}}
    ordered = prioritize_curated(curated, coverage)
    assert [t["en"] for t in ordered] == ["Known", "Not In Coverage"]


def test_prioritize_curated_no_coverage_is_identity() -> None:
    curated = [{"en": "Armbar"}, {"en": "Closed Guard"}]
    assert prioritize_curated(curated, None) == curated


def test_load_coverage_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_coverage(tmp_path / "no-such-coverage.json") is None


def test_load_coverage_indexes_entries_by_en(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"entries": [
        {"en": "Armbar", "bucket": "zero", "corpus_events": 3},
        {"en": "", "bucket": "zero", "corpus_events": 1},  # no `en` -- must not crash/index
    ]}), encoding="utf-8")
    coverage = load_coverage(path)
    assert coverage is not None
    assert coverage["Armbar"]["bucket"] == "zero"
    assert len(coverage) == 1


# ── agreement rule ───────────────────────────────────────────────────────────────
def test_score_agreement_full() -> None:
    agree, conf = score_agreement("armbar", {"label": "Armbar"})
    assert (agree, conf) == ("full", "high")


def test_score_agreement_full_via_variant_spelling() -> None:
    # "arm bar" is a curated variant of "Armbar" -- clean_label canonicalises it first.
    agree, conf = score_agreement("armbar", {"label": "arm bar"})
    assert (agree, conf) == ("full", "high")


def test_score_agreement_partial_via_alternative() -> None:
    agree, conf = score_agreement("armbar", {"label": "Kimura", "alternative": "Armbar"})
    assert (agree, conf) == ("partial", "low")


def test_score_agreement_no_match() -> None:
    agree, conf = score_agreement("armbar", {"label": "Kimura", "alternative": "Triangle"})
    assert (agree, conf) == ("no", "low")


def test_score_agreement_empty_answer_is_no() -> None:
    agree, conf = score_agreement("armbar", {})
    assert (agree, conf) == ("no", "low")


# ── vocabulary text ───────────────────────────────────────────────────────────────
def test_vocabulary_text_lists_every_curated_label() -> None:
    text = vocabulary_text(CURATED)
    assert "Armbar" in text
    assert "Closed Guard" in text
    assert "Allowed labels" in text


# ── report shape ──────────────────────────────────────────────────────────────────
def test_build_report_never_drops_disagreements() -> None:
    seed: list[dict[str, Any]] = [
        {"node_key": "armbar", "corpus_label": "Armbar", "model_label": "Armbar",
         "agree": "full", "review_confidence": "high",
         "usage": {"prompt": 100, "candidates": 20, "thoughts": 5, "total": 125}},
        {"node_key": "armbar", "corpus_label": "Armbar", "model_label": "Kimura",
         "agree": "no", "review_confidence": "low",
         "usage": {"prompt": 100, "candidates": 20, "thoughts": 5, "total": 125}},
    ]
    counts = {"Armbar": 5, "Closed Guard": 0}
    report = build_report(seed, counts, CURATED)
    assert "Total candidates asked: 2" in report
    assert "full 1, partial 0, no 1" in report
    assert "Closed Guard" in report  # zero video-backed section
    assert "Armbar" in report
    assert "Kimura" in report        # top confusions keeps the disagreement visible
    assert "$" in report             # cost estimate present


def test_build_report_empty_seed_does_not_crash() -> None:
    report = build_report([], {"Armbar": 0, "Closed Guard": 0}, CURATED)
    assert "Total candidates asked: 0" in report


# ── ask_gemini: a JSON array response must not crash the batch ───────────────────
def test_ask_gemini_unwraps_a_one_element_array_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
          "a_name": "A", "b_name": "B"}
    center = frame_paths("armbar", "a-vs-b-2025", 1000)["center"]
    center.parent.mkdir(parents=True, exist_ok=True)
    center.write_bytes(b"fake")

    resp = MagicMock()
    resp.text = json.dumps([{"label": "Armbar", "confidence": "high"}])
    resp.usage_metadata = None
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp

    result = ask_gemini(row, "vocab", fake_client)
    assert result["parsed"]["label"] == "Armbar"


def test_ask_gemini_non_dict_non_list_response_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
          "a_name": "A", "b_name": "B"}
    center = frame_paths("armbar", "a-vs-b-2025", 1000)["center"]
    center.parent.mkdir(parents=True, exist_ok=True)
    center.write_bytes(b"fake")

    resp = MagicMock()
    resp.text = json.dumps("not an object")
    resp.usage_metadata = None
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp

    result = ask_gemini(row, "vocab", fake_client)
    assert result["parsed"] == {}
