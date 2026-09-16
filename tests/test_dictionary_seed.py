"""Pure logic only -- no network, no DB, no ffmpeg/yt-dlp. plan sampling (spread across
bouts), ts arithmetic + evidence-based reclassification, the alignment stage, the agreement
rule, coverage-driven priority, preverify's six local screens, and report shape."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("google.genai")

from scripts.dictionary_seed import (
    WINDOW_OFFSETS,
    absolute_ts,
    ask_gemini,
    build_plan,
    build_preverify_skip_seed_row,
    build_preverify_summary,
    build_report,
    cap_plan,
    chosen_frame_paths,
    classify_ts_origin,
    compute_review_confidence,
    count_persons,
    is_blank,
    is_no_people,
    is_static_window,
    load_coverage,
    partition_for_ask,
    preverify_candidate,
    prioritize_curated,
    regrade_seed_near,
    regrade_seed_review,
    resolve_offset,
    run_ask,
    run_preverify,
    score_agreement,
    select_candidates,
    strip_frame_path,
    ts_class_matches_flag,
    ts_in_intro,
    ts_out_of_range,
    vocabulary_text,
)

CURATED: list[dict[str, Any]] = [
    {"en": "Armbar", "pt": "Armlock", "type": "submission", "variants": ["arm bar"]},
    {"en": "Closed Guard", "pt": "Guarda Fechada", "type": "guard", "variants": []},
]


# ── ts arithmetic ────────────────────────────────────────────────────────────────
def test_absolute_ts_video_absolute_ignores_start() -> None:
    assert absolute_ts(120, "video_absolute", 340) == 340.0


def test_absolute_ts_absolute_from_zero_same_as_video_absolute() -> None:
    assert absolute_ts(None, "absolute_from_zero", 90) == 90.0


def test_absolute_ts_bout_relative_adds_start() -> None:
    assert absolute_ts(120, "bout_relative", 30) == 150.0


def test_absolute_ts_bout_relative_missing_start_defaults_zero() -> None:
    assert absolute_ts(None, "bout_relative", 30) == 30.0


def test_absolute_ts_unknown_origin_is_unlocatable() -> None:
    assert absolute_ts(120, None, 30) is None
    assert absolute_ts(120, "unknown", 30) is None


def test_absolute_ts_missing_ts_is_unlocatable() -> None:
    assert absolute_ts(120, "video_absolute", None) is None


# ── evidence-based ts reclassification ────────────────────────────────────────────
def test_classify_ts_origin_bout_relative_when_all_events_below_start() -> None:
    # the WNO 30 case: video_start=6746, event ts run 5..397 -- all below start.
    assert classify_ts_origin(6746, [5, 120, 397], None) == "bout_relative"


def test_classify_ts_origin_video_absolute_when_all_events_at_or_after_start() -> None:
    assert classify_ts_origin(100, [110, 250, 900], None) == "video_absolute"


def test_classify_ts_origin_mixed_is_unknown() -> None:
    # some events below start, some at/after -- neither convention explains it.
    assert classify_ts_origin(100, [50, 150], None) == "unknown"


def test_classify_ts_origin_small_start_is_not_evidence() -> None:
    # start <= 60s is noise, not proof of bout-relative -- falls through to "no duration ->
    # unknown" rather than misreading a near-zero start as bout-relative.
    assert classify_ts_origin(45, [10, 20], None) == "unknown"


def test_classify_ts_origin_absolute_from_zero_when_start_missing_but_fits_duration() -> None:
    assert classify_ts_origin(None, [10, 300], 600.0) == "absolute_from_zero"
    assert classify_ts_origin(0, [10, 300], 600.0) == "absolute_from_zero"


def test_classify_ts_origin_zero_start_exceeding_duration_is_unknown() -> None:
    assert classify_ts_origin(None, [10, 700], 600.0) == "unknown"


def test_classify_ts_origin_zero_start_no_duration_is_unknown() -> None:
    assert classify_ts_origin(None, [10, 300], None) == "unknown"


def test_classify_ts_origin_no_events_is_unknown() -> None:
    assert classify_ts_origin(100, [], None) == "unknown"


def test_ts_class_matches_flag_video_absolute_agrees_with_both_absolute_classes() -> None:
    assert ts_class_matches_flag("video_absolute", "video_absolute")
    assert ts_class_matches_flag("video_absolute", "absolute_from_zero")
    assert not ts_class_matches_flag("video_absolute", "bout_relative")


def test_ts_class_matches_flag_bout_relative_agrees_only_with_bout_relative() -> None:
    assert ts_class_matches_flag("bout_relative", "bout_relative")
    assert not ts_class_matches_flag("bout_relative", "video_absolute")


def test_ts_class_matches_flag_null_flag_never_agreed() -> None:
    assert not ts_class_matches_flag(None, "video_absolute")
    assert not ts_class_matches_flag(None, "bout_relative")


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
    assert row.ts_class == "video_absolute"


def test_build_plan_reclassifies_against_db_flag_bout_relative_evidence() -> None:
    # DB flag says video_absolute (as WNO 30's really did), but every event ts is well below
    # video_start -- the evidence-based classifier overrides the flag.
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 5},
    ])]
    plan, _counts = build_plan(matches, CURATED, per_technique=6)
    assert len(plan) == 1
    assert plan[0].ts_class == "bout_relative"
    assert plan[0].ts == 105.0  # video_start(100) + ts(5), NOT the raw ts=5


def test_build_plan_drops_events_with_no_locatable_ts() -> None:
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice"},   # no ts
    ])]
    plan, counts = build_plan(matches, CURATED, per_technique=6)
    assert counts["Armbar"] == 0
    assert plan == []


def test_build_plan_zero_start_uses_probe_duration_fn() -> None:
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 300},
    ])]
    matches[0]["video_start_seconds"] = None
    plan, _counts = build_plan(matches, CURATED, per_technique=6,
                               probe_duration_fn=lambda _url: 600.0)
    assert len(plan) == 1
    assert plan[0].ts_class == "absolute_from_zero"
    assert plan[0].ts == 300.0


def test_build_plan_zero_start_without_probe_is_unknown() -> None:
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 300},
    ])]
    matches[0]["video_start_seconds"] = None
    plan, counts = build_plan(matches, CURATED, per_technique=6)   # no probe_duration_fn
    assert plan == []
    assert counts["Armbar"] == 0


def test_build_plan_min_events_filters_out_thin_techniques() -> None:
    matches = [_match("m1", "Alice", "Bob", 2025, [
        {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 340},
    ])]
    plan, _counts = build_plan(matches, CURATED, per_technique=6, min_events=2)
    assert plan == []


def test_cap_plan_drops_whole_techniques_not_partial() -> None:
    plan, _ = build_plan(
        [_match("m1", "Alice", "Bob", 2025, [
            {"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 340},
            {"label": "Armbar", "type": "submission", "actor": "Bob", "ts": 350},
            {"label": "Closed Guard", "type": "guard", "actor": "Alice", "ts": 360},
        ])],
        CURATED, per_technique=6)
    capped = cap_plan(plan, 2)
    assert len(capped) == 2
    assert {r.label for r in capped} == {"Armbar"}


def test_cap_plan_none_is_noop() -> None:
    plan, _ = build_plan(
        [_match("m1", "Alice", "Bob", 2025,
               [{"label": "Armbar", "type": "submission", "actor": "Alice", "ts": 340}])],
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


# ── stage-A offset resolution ──────────────────────────────────────────────────────
def test_resolve_offset_exact_match() -> None:
    assert resolve_offset({"offset": 10}) == 10
    assert resolve_offset({"offset": -30}) == -30


def test_resolve_offset_snaps_to_nearest() -> None:
    assert resolve_offset({"offset": 12}) == 10
    assert resolve_offset({"offset": 7}) == 5


def test_resolve_offset_missing_or_unparsable_defaults_zero() -> None:
    assert resolve_offset({}) == 0
    assert resolve_offset({"offset": "not a number"}) == 0
    assert resolve_offset({"offset": None}) == 0


def test_strip_and_chosen_frame_paths_encode_offset_sign() -> None:
    p = strip_frame_path("armbar", "a-vs-b-2025", 1000, -10)
    assert "strip_m10" in p.name
    p2 = strip_frame_path("armbar", "a-vs-b-2025", 1000, 10)
    assert "strip_p10" in p2.name
    chosen = chosen_frame_paths("armbar", "a-vs-b-2025", 1000, 0)
    assert "chosen_p0" in chosen["center"].name
    assert chosen["m2"].name.endswith("_m2.jpg")
    assert chosen["p2"].name.endswith("_p2.jpg")


def test_window_offsets_are_symmetric_and_include_zero() -> None:
    assert 0 in WINDOW_OFFSETS
    assert sorted(WINDOW_OFFSETS) == list(WINDOW_OFFSETS)
    assert sorted(-o for o in WINDOW_OFFSETS) == list(WINDOW_OFFSETS)


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
    # Closed Guard/Mount are a different curated `type` (guard/control) and a different
    # submission family than Armbar (`_sub_family`), so they stay `no` even with the near
    # tier -- unlike "Kimura", which is now legitimately `near` (see
    # test_score_agreement_near_same_submission_family below).
    agree, conf = score_agreement(
        "armbar", {"label": "Closed Guard", "type": "guard", "alternative": "Mount"})
    assert (agree, conf) == ("no", "low")


def test_score_agreement_empty_answer_is_no() -> None:
    agree, conf = score_agreement("armbar", {})
    assert (agree, conf) == ("no", "low")


# ── agreement rule: near tier (2026-09-16) ─────────────────────────────────────────
def test_score_agreement_near_same_curated_type() -> None:
    # Lasso Guard vs Closed Guard: two different curated entries, both `type: guard` --
    # a generic-vs-specific pair within one family (technique_library.json `type`).
    agree, conf = score_agreement("lasso guard", {"label": "Closed Guard", "type": "guard"})
    assert (agree, conf) == ("near", "medium")


def test_score_agreement_near_same_submission_family() -> None:
    # Both `type: submission` AND both `style_profile_core._sub_family` == "armlock".
    agree, conf = score_agreement("armbar", {"label": "Kimura", "type": "submission"})
    assert (agree, conf) == ("near", "medium")


def test_score_agreement_no_when_submission_type_but_different_family() -> None:
    # Both `type: submission`, but Heel Hook is a leglock and Kimura is an armlock -- a bare
    # `type` match would wrongly call these near; `_sub_family` is what keeps them apart.
    agree, conf = score_agreement("heel hook", {"label": "Kimura", "type": "submission"})
    assert (agree, conf) == ("no", "low")


def test_score_agreement_near_arrived_at_state() -> None:
    # Guard Pass is an ACTION (`kind_of_entry`) whose curated `type` ("pass") declares a
    # `top` landing orientation (`data/taxonomy/inference_table.json`
    # `action_exit_orientation`); Side Control reads `top` too (`orientation_for_inference`).
    agree, conf = score_agreement("guard pass", {"label": "Side Control", "type": "control"})
    assert (agree, conf) == ("near", "medium")


def test_score_agreement_no_when_action_exit_orientation_disagrees() -> None:
    # A pass's declared landing is `top`; Closed Guard reads `bottom` -- not the state the
    # action's own exit-orientation row declares.
    agree, conf = score_agreement("guard pass", {"label": "Closed Guard", "type": "guard"})
    assert (agree, conf) == ("no", "low")


# ── regrade_seed_near: re-grade an already-scored batch, no Gemini call ───────────
def test_regrade_seed_near_keeps_full_and_partial_untouched() -> None:
    seed = [{"node_key": "armbar", "agree": "full", "model_label": "Armbar"},
           {"node_key": "armbar", "agree": "partial", "model_label": "Kimura"}]
    out = regrade_seed_near(seed)
    assert [r["agree_near"] for r in out] == ["full", "partial"]
    # strict `agree` is never dropped
    assert [r["agree"] for r in out] == ["full", "partial"]


def test_regrade_seed_near_promotes_a_no_row_that_is_near() -> None:
    seed = [{"node_key": "lasso guard", "agree": "no",
            "model_label": "Closed Guard", "model_type": "guard"}]
    out = regrade_seed_near(seed)
    assert out[0]["agree_near"] == "near"


def test_regrade_seed_near_keeps_a_genuinely_wrong_no() -> None:
    seed = [{"node_key": "heel hook", "agree": "no",
            "model_label": "Kimura", "model_type": "submission"}]
    out = regrade_seed_near(seed)
    assert out[0]["agree_near"] == "no"


def test_regrade_seed_near_no_row_with_no_model_label() -> None:
    seed = [{"node_key": "armbar", "agree": "no", "model_label": None}]
    out = regrade_seed_near(seed)
    assert out[0]["agree_near"] == "no"


# ── build_report: near summary + preverify block ──────────────────────────────────
def test_build_report_strict_vs_near_table() -> None:
    seed = [_seed_row(), _seed_row(model_label="Closed Guard", agree="no",
                      review_confidence="low", agree_near="near")]
    report = build_report(seed, {"Armbar": 2}, CURATED)
    assert "Strict vs near agreement" in report
    assert "full 1, partial 0, no 1" in report   # strict row unaffected by near
    assert "| with near (`agree_near`) | 1 | 0 | 1 | 0 |" in report


def test_build_report_preverify_block() -> None:
    seed = [_seed_row()]
    summary = {"total": 91, "verdicts": {"ok": 64, "skip": 27},
              "reasons": {"missing_frames": 22, "ts_out_of_range": 22, "duplicate_frame": 5},
              "warnings": {"no_people": 14}}
    report = build_report(seed, {"Armbar": 1}, CURATED, preverify_summary=summary)
    assert "## Preverify" in report
    assert "ok: 64" in report and "skip: 27" in report
    assert "no_people 14" in report


def test_build_report_frame_check_section() -> None:
    report = build_report([], {"Armbar": 0}, CURATED)
    assert "Frame check (orchestrator, 2026-09-16)" in report


# ── vocabulary text ───────────────────────────────────────────────────────────────
def test_vocabulary_text_lists_every_curated_label() -> None:
    text = vocabulary_text(CURATED)
    assert "Armbar" in text
    assert "Closed Guard" in text
    assert "Allowed labels" in text


# ── report shape ──────────────────────────────────────────────────────────────────
def _seed_row(**over: Any) -> dict[str, Any]:
    base = {
        "node_key": "armbar", "corpus_label": "Armbar", "model_label": "Armbar",
        "agree": "full", "review_confidence": "high", "chosen_offset": 0, "visible": True,
        "usage": {"prompt": 100, "candidates": 20, "thoughts": 5, "total": 125},
    }
    base.update(over)
    return base


def test_build_report_never_drops_disagreements() -> None:
    seed = [
        _seed_row(),
        _seed_row(model_label="Kimura", agree="no", review_confidence="low"),
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


def test_build_report_offset_histogram_and_not_visible() -> None:
    seed = [
        _seed_row(chosen_offset=10),
        _seed_row(chosen_offset=10),
        _seed_row(visible=False, agree="no", review_confidence="low",
                  model_label=None, align_reason="not in frame"),
    ]
    report = build_report(seed, {"Armbar": 3}, CURATED)
    assert "+10" in report
    assert "Not visible in any window frame: 1 of 3" in report


def test_build_report_alignment_and_before_after() -> None:
    seed = [_seed_row()]
    before = [_seed_row(agree="no", review_confidence="low", model_label="Side Control")]
    report = build_report(seed, {"Armbar": 1}, CURATED,
                          alignment={"misaligned": 40, "of_total": 96},
                          before_seed=before)
    assert "40" in report and "96" in report
    assert "Before vs after realignment" in report


# ── ask_gemini / ask_alignment: a JSON array response must not crash the batch ────
def test_ask_gemini_unwraps_a_one_element_array_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
          "a_name": "A", "b_name": "B", "chosen_offset": 0}
    center = chosen_frame_paths("armbar", "a-vs-b-2025", 1000, 0)["center"]
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
          "a_name": "A", "b_name": "B", "chosen_offset": 0}
    center = chosen_frame_paths("armbar", "a-vs-b-2025", 1000, 0)["center"]
    center.parent.mkdir(parents=True, exist_ok=True)
    center.write_bytes(b"fake")

    resp = MagicMock()
    resp.text = json.dumps("not an object")
    resp.usage_metadata = None
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp

    result = ask_gemini(row, "vocab", fake_client)
    assert result["parsed"] == {}


def test_ask_gemini_reads_the_chosen_offset_frame_not_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
          "a_name": "A", "b_name": "B", "chosen_offset": 20}
    chosen_frame_paths("armbar", "a-vs-b-2025", 1000, 0)["center"].parent.mkdir(
        parents=True, exist_ok=True)
    # only the offset=0 default exists -- reading offset=20 (never extracted) must not crash,
    # it just sends zero image parts.
    resp = MagicMock()
    resp.text = json.dumps({"label": "Armbar"})
    resp.usage_metadata = None
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp

    result = ask_gemini(row, "vocab", fake_client)
    assert result["parsed"]["label"] == "Armbar"
    _, kwargs = fake_client.models.generate_content.call_args
    # contents = [] image parts + 1 text part, since the offset-20 frame doesn't exist
    assert len(kwargs["contents"]) == 1


# ── run_ask: extraction failure must not spend a call on an empty prompt ─────────
def test_run_ask_skips_gemini_when_no_strip_frames_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    row = {"node_key": "armbar", "label": "Armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
          "a_name": "A", "b_name": "B", "match_id": "m1", "ts_class": "video_absolute"}
    # no frames written on disk at all -- extraction "failed" for this candidate's match

    calls = {"n": 0}

    def _boom(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        raise AssertionError("must not call Gemini with no extracted frames")

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = _boom

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("google.genai.Client", lambda **_kw: fake_client)
        mp.setenv("GEMINI_API_KEY", "test-key")
        seed = run_ask([row], dry_run=False)

    assert calls["n"] == 0
    assert len(seed) == 1
    assert seed[0]["visible"] is False
    assert seed[0]["align_reason"] == "extraction_failed"
    assert seed[0]["agree"] == "no"
    assert seed[0]["review_confidence"] == "low"


# ── run_ask: an API error on one candidate must not lose the whole batch ─────────
def test_run_ask_survives_a_quota_error_and_keeps_grading_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.genai import errors

    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    rows: list[dict[str, Any]] = [
        {"node_key": "armbar", "label": "Armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
         "a_name": "A", "b_name": "B", "match_id": "m1", "ts_class": "video_absolute"},
        {"node_key": "closed guard", "label": "Closed Guard", "bout": "a-vs-b-2025",
         "ts_ms": 2000, "a_name": "A", "b_name": "B", "match_id": "m1",
         "ts_class": "video_absolute"},
    ]
    for row in rows:
        for o in WINDOW_OFFSETS:
            p = strip_frame_path(row["node_key"], row["bout"], row["ts_ms"], o)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"fake")
        center = chosen_frame_paths(row["node_key"], row["bout"], row["ts_ms"], 0)["center"]
        center.write_bytes(b"fake")

    quota_error = errors.ClientError(429, {"error": {"message": "quota exceeded"}})
    good_align = MagicMock()
    good_align.text = json.dumps({"offset": 0, "visible": "yes", "reason": "ok"})
    good_align.usage_metadata = None
    good_blind = MagicMock()
    good_blind.text = json.dumps({"label": "Closed Guard"})
    good_blind.usage_metadata = None

    fake_client = MagicMock()
    # candidate 1's stage A AND retry both quota-error; candidate 2's stage A + stage B
    # both succeed -- the batch must still return BOTH graded candidates.
    fake_client.models.generate_content.side_effect = [
        quota_error, quota_error,      # row 1 stage A (initial + thinking-retry)
        good_align,                     # row 2 stage A
        good_blind,                     # row 2 stage B
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("google.genai.Client", lambda **_kw: fake_client)
        mp.setenv("GEMINI_API_KEY", "test-key")
        seed = run_ask(rows, dry_run=False, align=True)

    assert len(seed) == 2   # neither candidate lost, despite the 429
    assert seed[0]["agree"] == "no"
    assert seed[0]["visible"] is False
    assert "ClientError" in (seed[0]["align_reason"] or "")
    assert seed[1]["model_label"] == "Closed Guard"
    assert seed[1]["agree"] == "full"


# ── preverify: local, cheap screens ───────────────────────────────────────────────
def _gray(std: float, mean: float, shape: tuple[int, int] = (36, 64), seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    arr = rng.normal(loc=mean, scale=std, size=shape)
    return np.clip(arr, 0, 255)


def test_is_blank_low_std_is_blank() -> None:
    assert is_blank(_gray(std=1.0, mean=120.0))


def test_is_blank_low_mean_is_blank() -> None:
    assert is_blank(_gray(std=1.0, mean=2.0))


def test_is_blank_normal_frame_is_not_blank() -> None:
    assert not is_blank(_gray(std=40.0, mean=120.0))


def test_is_static_window_identical_frames_is_static() -> None:
    frame = _gray(std=40.0, mean=120.0)
    assert is_static_window([frame.copy() for _ in range(9)])


def test_is_static_window_changing_frames_is_not_static() -> None:
    frames = [np.full((36, 64), float(i) * 40, dtype=np.float64) for i in range(9)]
    assert not is_static_window(frames)


def test_is_static_window_fewer_than_two_frames_is_not_static() -> None:
    assert not is_static_window([_gray(std=1.0, mean=120.0)])


def test_ts_in_intro() -> None:
    assert ts_in_intro(5000)
    assert not ts_in_intro(30000)


def test_ts_out_of_range() -> None:
    assert ts_out_of_range(700_000, 600.0)
    assert not ts_out_of_range(500_000, 600.0)


def test_ts_out_of_range_no_duration_never_flags() -> None:
    assert not ts_out_of_range(999_999_000, None)


def test_count_persons_filters_small_boxes() -> None:
    # frame 100x100 -- a 5x5 box is 0.25% of area (noise), a 50x50 box is 25% (a real person)
    persons, areas = count_persons([(0, 0, 5, 5), (0, 0, 50, 50)], frame_w=100, frame_h=100)
    assert persons == 1
    assert areas[0] == pytest.approx(0.25)


def test_is_no_people_thresholds() -> None:
    # MIN_COUNT=1 -- zero detected persons is still a miss (empty mat), but ONE is enough
    # (an entangled ground position often merges two grapplers into a single YOLO box).
    assert is_no_people(0)
    assert not is_no_people(1)
    assert not is_no_people(2)


class _FakeDetector:
    """Injected in place of the real YOLO model -- returns fixed pixel boxes."""

    def __init__(self, boxes: list[tuple[float, float, float, float]]) -> None:
        self.boxes = boxes

    def predict(self, *_a: Any, **_k: Any) -> list[Any]:
        result = MagicMock()
        result.boxes.xyxy.cpu.return_value.numpy.return_value = np.array(self.boxes)
        return [result]


def _write_candidate_frames(node_key: str, bout: str, ts_ms: int, *,
                            std: float = 40.0, mean: float = 120.0,
                            static: bool = False) -> None:
    center = chosen_frame_paths(node_key, bout, ts_ms, 0)["center"]
    center.parent.mkdir(parents=True, exist_ok=True)
    frame = Image.fromarray(_gray(std, mean, shape=(360, 640), seed=1).astype("uint8"))
    frame.save(center)
    for i, o in enumerate(WINDOW_OFFSETS):
        strip_arr = (np.full((360, 640), mean, dtype=np.float64) if static
                    else _gray(std, mean, shape=(360, 640), seed=100 + i))
        Image.fromarray(strip_arr.astype("uint8")).save(strip_frame_path(node_key, bout, ts_ms, o))


def test_preverify_candidate_missing_frames_is_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000}
    result = preverify_candidate(row, duration=None, detector=None)
    assert result["verdict"] == "skip"
    assert "missing_frames" in result["reasons"]


def test_preverify_candidate_tolerates_a_few_missing_strips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 8/9 strips + a good centre (the heel hook helena-crevar-vs-aurelie-le-vern-2024 @182s
    # case) -- stage A (ask_alignment) only sends whichever strips exist, so this is usable.
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 30_000)
    strip_frame_path("armbar", "a-vs-b-2025", 30_000, WINDOW_OFFSETS[0]).unlink()
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 30_000}
    result = preverify_candidate(row, duration=None, detector=None)
    assert "missing_frames" not in result["reasons"]
    assert result["verdict"] == "ok"


def test_preverify_candidate_too_few_strips_is_missing_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # only 4/9 strips present -- below STRIPS_MIN_PRESENT=5, not enough window left to trust
    # an alignment choice.
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 1000)
    for o in WINDOW_OFFSETS[:5]:
        strip_frame_path("armbar", "a-vs-b-2025", 1000, o).unlink()
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000}
    result = preverify_candidate(row, duration=None, detector=None)
    assert "missing_frames" in result["reasons"]
    assert result["verdict"] == "skip"


def test_preverify_candidate_blank_center_is_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 1000, std=1.0, mean=120.0)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000}
    result = preverify_candidate(row, duration=None, detector=None)
    assert "blank_frame" in result["reasons"]


def test_preverify_candidate_static_window_is_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 1000, static=True)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000}
    result = preverify_candidate(row, duration=None, detector=None)
    assert "static_window" in result["reasons"]


def test_preverify_candidate_ts_in_intro_is_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 5000)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 5000}
    result = preverify_candidate(row, duration=None, detector=None)
    assert "ts_in_intro" in result["reasons"]


def test_preverify_candidate_ts_out_of_range_is_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 700_000)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 700_000}
    result = preverify_candidate(row, duration=600.0, detector=None)
    assert "ts_out_of_range" in result["reasons"]


def test_preverify_candidate_no_detector_records_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 30_000)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 30_000}
    result = preverify_candidate(row, duration=None, detector=None)
    assert result["metrics"]["detector"] == "unavailable"
    assert "no_people" not in result["reasons"]
    assert result["warnings"] == []


def test_preverify_candidate_no_people_is_warning_not_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a general-purpose detector finding zero people is advisory only (measured 2026-09-16:
    # not reliable enough on this domain to gate) -- it must never reach `reasons`/`skip`.
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 30_000)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 30_000}
    detector = _FakeDetector([(0, 0, 5, 5)])   # one tiny box -- 0 people >= 1% area
    result = preverify_candidate(row, duration=None, detector=detector)
    assert "no_people" not in result["reasons"]
    assert result["reasons"] == []
    assert result["warnings"] == ["no_people"]
    assert result["verdict"] == "ok"
    assert result["metrics"]["persons"] == 0


def test_preverify_candidate_two_people_is_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 30_000)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 30_000}
    detector = _FakeDetector([(0, 0, 200, 200), (300, 0, 500, 200)])   # both >= 1% of 640x360
    result = preverify_candidate(row, duration=None, detector=detector)
    assert result["verdict"] == "ok"
    assert result["metrics"]["persons"] == 2


def test_preverify_candidate_one_merged_box_is_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # an entangled ground position often reads as ONE box (YOLO merges the two grapplers) --
    # that's still a real position, not a no_people miss.
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 30_000)
    row = {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 30_000}
    detector = _FakeDetector([(0, 0, 300, 300)])   # one large merged box, >= 1% of 640x360
    result = preverify_candidate(row, duration=None, detector=detector)
    assert result["verdict"] == "ok"
    assert result["metrics"]["persons"] == 1


def test_run_preverify_flags_duplicate_centre_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 30_000)
    # second candidate points at the SAME frame files (duplicate ts resolution) -- copy the
    # first candidate's exact bytes under a different ts_ms.
    src_center = chosen_frame_paths("armbar", "a-vs-b-2025", 30_000, 0)["center"]
    dst_center = chosen_frame_paths("armbar", "a-vs-b-2025", 31_000, 0)["center"]
    dst_center.write_bytes(src_center.read_bytes())
    for o in WINDOW_OFFSETS:
        strip_frame_path("armbar", "a-vs-b-2025", 31_000, o).write_bytes(
            strip_frame_path("armbar", "a-vs-b-2025", 30_000, o).read_bytes())

    plan_rows = [
        {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 30_000},
        {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 31_000},
    ]
    results = run_preverify(plan_rows, probe_duration_fn=None, detector=None)
    assert results[0]["verdict"] == "ok"
    assert results[1]["verdict"] == "skip"
    assert "duplicate_frame" in results[1]["reasons"]


def test_run_preverify_only_probes_duration_when_row_has_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    _write_candidate_frames("armbar", "a-vs-b-2025", 30_000)
    calls: list[str] = []

    def _probe(url: str) -> float:
        calls.append(url)
        return 600.0

    plan_rows = [{"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 30_000}]  # no video_url
    run_preverify(plan_rows, probe_duration_fn=_probe, detector=None)
    assert calls == []


def test_build_preverify_summary_counts_verdicts_reasons_and_warnings() -> None:
    results = [
        {"verdict": "ok", "reasons": [], "warnings": ["no_people"]},
        {"verdict": "skip", "reasons": ["blank_frame"], "warnings": []},
        {"verdict": "skip", "reasons": ["blank_frame", "ts_in_intro"], "warnings": ["no_people"]},
    ]
    summary = build_preverify_summary(results)
    assert summary["total"] == 3
    assert summary["verdicts"] == {"ok": 1, "skip": 2}
    assert summary["reasons"] == {"blank_frame": 2, "ts_in_intro": 1}
    assert summary["warnings"] == {"no_people": 2}


# ── ask wiring: preverify skip -> seed row, resume-safe ───────────────────────────
def test_build_preverify_skip_seed_row_shape() -> None:
    row = {"node_key": "armbar", "label": "Armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
          "ts_class": "video_absolute"}
    seed_row = build_preverify_skip_seed_row(row, ["blank_frame", "no_people"])
    assert seed_row["visible"] is False
    assert seed_row["agree"] == "no"
    assert seed_row["review_confidence"] == "low"
    assert seed_row["reason"] == "preverify:blank_frame+no_people"
    assert seed_row["usage"] == {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}
    assert seed_row["node_key"] == "armbar"
    assert seed_row["ts_ms"] == 1000


def test_partition_for_ask_routes_preverify_skips_away_from_gemini() -> None:
    plan_rows = [
        {"node_key": "armbar", "label": "Armbar", "bout": "a-vs-b-2025", "ts_ms": 1000},
        {"node_key": "kimura", "label": "Kimura", "bout": "a-vs-b-2025", "ts_ms": 2000},
    ]
    preverify_rows = [
        {"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
         "verdict": "skip", "reasons": ["blank_frame"]},
        {"node_key": "kimura", "bout": "a-vs-b-2025", "ts_ms": 2000,
         "verdict": "ok", "reasons": []},
    ]
    to_ask, skip_seed = partition_for_ask(plan_rows, [], preverify_rows)
    assert [r["node_key"] for r in to_ask] == ["kimura"]
    assert len(skip_seed) == 1
    assert skip_seed[0]["node_key"] == "armbar"
    assert skip_seed[0]["reason"] == "preverify:blank_frame"


def test_partition_for_ask_skips_candidates_already_in_seed() -> None:
    plan_rows = [{"node_key": "armbar", "label": "Armbar", "bout": "a-vs-b-2025", "ts_ms": 1000}]
    existing_seed = [{"node_key": "armbar", "bout": "a-vs-b-2025", "ts_ms": 1000, "agree": "full"}]
    to_ask, skip_seed = partition_for_ask(plan_rows, existing_seed, [])
    assert to_ask == []
    assert skip_seed == []


def test_partition_for_ask_no_preverify_file_asks_everything() -> None:
    plan_rows = [{"node_key": "armbar", "label": "Armbar", "bout": "a-vs-b-2025", "ts_ms": 1000}]
    to_ask, skip_seed = partition_for_ask(plan_rows, [], [])
    assert len(to_ask) == 1
    assert skip_seed == []


# ── review_confidence: candidate-first rule (2026-09-16) ─────────────────────────
def test_compute_review_confidence_not_read_is_always_low() -> None:
    # preverify skip / not visible / error -- never actually read, regardless of agree.
    assert compute_review_confidence("full", "high", read=False) == "low"


def test_compute_review_confidence_high_on_full_or_partial_agreement() -> None:
    assert compute_review_confidence("full", None, read=True) == "high"
    assert compute_review_confidence("partial", None, read=True) == "high"


def test_compute_review_confidence_medium_on_near_tier() -> None:
    assert compute_review_confidence("near", None, read=True) == "medium"


def test_compute_review_confidence_medium_on_model_high_confidence_even_with_no_agreement() -> None:
    assert compute_review_confidence("no", "high", read=True) == "medium"


def test_compute_review_confidence_low_otherwise() -> None:
    assert compute_review_confidence("no", "low", read=True) == "low"
    assert compute_review_confidence("no", "medium", read=True) == "low"
    assert compute_review_confidence("no", None, read=True) == "low"


# ── regrade_seed_review: backfill candidate fields, no Gemini call ───────────────
def test_regrade_seed_review_backfills_candidate_label_when_read() -> None:
    seed = [{"node_key": "armbar", "corpus_label": "Armbar", "model_label": "Armbar",
            "model_type": "submission", "agree": "full", "model_confidence": "high"}]
    out = regrade_seed_review(seed)
    assert out[0]["candidate_label"] == "Armbar"
    assert out[0]["candidate_type"] == "submission"
    assert out[0]["second_opinion"] == {"corpus_label": "Armbar", "agree": "full",
                                        "agree_near": "full"}
    assert out[0]["review_confidence"] == "high"


def test_regrade_seed_review_null_candidate_when_never_read() -> None:
    seed = [{"node_key": "armbar", "corpus_label": "Armbar", "model_label": None,
            "agree": "no"}]
    out = regrade_seed_review(seed)
    assert out[0]["candidate_label"] is None
    assert out[0]["candidate_type"] is None
    assert out[0]["review_confidence"] == "low"


def test_regrade_seed_review_uses_agree_near_when_present() -> None:
    seed = [{"node_key": "lasso guard", "corpus_label": "Lasso Guard",
            "model_label": "Closed Guard", "model_type": "guard", "agree": "no",
            "agree_near": "near"}]
    out = regrade_seed_review(seed)
    assert out[0]["second_opinion"]["agree_near"] == "near"
    assert out[0]["review_confidence"] == "medium"


def test_regrade_seed_review_is_idempotent() -> None:
    seed = [{"node_key": "armbar", "corpus_label": "Armbar", "model_label": "Armbar",
            "model_type": "submission", "agree": "full"}]
    once = regrade_seed_review(seed)
    twice = regrade_seed_review(once)
    assert once == twice


# ── run_ask default (no --align): exactly one Gemini call per candidate ──────────
def test_run_ask_default_makes_one_call_per_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("scripts.dictionary_seed.OUT_DIR", tmp_path)
    rows: list[dict[str, Any]] = [
        {"node_key": "armbar", "label": "Armbar", "bout": "a-vs-b-2025", "ts_ms": 1000,
         "a_name": "A", "b_name": "B", "match_id": "m1", "ts_class": "video_absolute"},
        {"node_key": "closed guard", "label": "Closed Guard", "bout": "a-vs-b-2025",
         "ts_ms": 2000, "a_name": "A", "b_name": "B", "match_id": "m1",
         "ts_class": "video_absolute"},
    ]
    for row in rows:
        center = chosen_frame_paths(row["node_key"], row["bout"], row["ts_ms"], 0)["center"]
        center.parent.mkdir(parents=True, exist_ok=True)
        center.write_bytes(b"fake")

    resp = MagicMock()
    resp.text = json.dumps({"label": "Armbar", "type": "submission", "confidence": "high"})
    resp.usage_metadata = None
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = resp

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("google.genai.Client", lambda **_kw: fake_client)
        mp.setenv("GEMINI_API_KEY", "test-key")
        seed = run_ask(rows, dry_run=False)   # align defaults False

    assert fake_client.models.generate_content.call_count == len(rows)
    assert len(seed) == 2
    for row in seed:
        assert row["candidate_label"] == "Armbar"
        assert row["chosen_offset"] == 0
        assert row["align_reason"] is None
