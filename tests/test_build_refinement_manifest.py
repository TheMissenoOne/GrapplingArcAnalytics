"""Refinement manifest selector -- pure logic only, no DB (see docstring in the script).

Covers: family classification, the missing-score-info test, the wholesale + singles
exclusion, the trials/base-event token collision the transcript matcher must not fall into,
and the null-start shared-vs-single-bout-url branch.
"""
from __future__ import annotations

from collections import Counter

from scripts.build_refinement_manifest import (
    already_framed,
    build_entry,
    family_of,
    has_score_info,
    resolve_start,
    transcript_candidates,
)


def test_family_of() -> None:
    assert family_of("ADCC 2022") == "adcc"
    assert family_of("CJI 2 - Day 1") == "adcc"
    assert family_of("IBJJF Worlds 2023") == "ibjjf"
    assert family_of("Pan No-Gi 2025") == "ibjjf"
    assert family_of("WNO 24") is None          # Who's Number One, not IBJJF
    assert family_of("Polaris 36") is None
    assert family_of(None) is None


def test_has_score_info() -> None:
    assert not has_score_info(None)
    assert not has_score_info([{"label": "Guard Pass", "type": "pass"}])
    assert has_score_info([{"label": "Guard Pass", "points": 2}])


def test_already_framed_event_wholesale() -> None:
    assert already_framed("European No-Gi 2024", "Ana Lopez", "Maca Vicentini", 2024)
    assert not already_framed("ADCC 2022", "Ana Lopez", "Maca Vicentini", 2022)


def test_already_framed_single_order_independent() -> None:
    assert already_framed("Pan No-Gi 2025", "Anabel Lopez", "Erin Harpe", 2025)
    assert already_framed("Pan No-Gi 2025", "Erin Harpe", "Anabel Lopez", 2025)   # order flip
    assert already_framed("Polaris 36", "Anabel Lopez", "Kendall Reusing", 2026)
    # same pair, wrong year -- not the audited bout
    assert not already_framed("Pan No-Gi 2025", "Anabel Lopez", "Erin Harpe", 2024)


def test_transcript_candidates_trials_vs_base_event() -> None:
    # measured collision (2026-08-25): a naive token-subset match pulls the Trials transcript
    # into "ADCC 2022"'s candidate list because 'adcc'+'2022' is a subset of its tokens too.
    files = [
        "ADCC2022-88kg", "ADCC2022-99kg", "ADCC2022-ABS", "ADCC2022-Finals", "ADCC2022Women",
        "ADCCTrials2022SouthAmericaFinals",
    ]
    cands = transcript_candidates("ADCC 2022", files)
    assert "ADCCTrials2022SouthAmericaFinals" not in cands
    assert len(cands) == 5   # genuinely ambiguous (no weight class on the match row)

    cands2 = transcript_candidates("ADCC Trials 2022 South America", files)
    assert cands2 == ["ADCCTrials2022SouthAmericaFinals"]


def test_transcript_candidates_unambiguous_reorders_ok() -> None:
    # "IBJJF Worlds 2023" (event) vs "IBJJF2023-Worlds-BlackBeltFinals" (filename) -- year and
    # "worlds" appear in a different order, token-set matching must not care.
    files = ["IBJJF2023-Worlds-BlackBeltFinals", "CJI"]
    assert transcript_candidates("IBJJF Worlds 2023", files) == ["IBJJF2023-Worlds-BlackBeltFinals"]


def test_resolve_start_db_start_wins() -> None:
    assert resolve_start(120, "u1", Counter({"u1": 1})) == (120.0, "db_start")


def test_resolve_start_single_bout_url_defaults_zero() -> None:
    assert resolve_start(None, "u1", Counter({"u1": 1})) == (0.0, "single_bout_url_defaulted")


def test_resolve_start_shared_url_refuses() -> None:
    start, reason = resolve_start(None, "u1", Counter({"u1": 3}))
    assert start is None
    assert reason == "shared_url_no_start"


def test_build_entry_shape(tmp_path: object) -> None:
    row = {
        "event": "CJI", "year": 2022, "win_type": "DECISION", "video_url": "https://x/y",
        "video_start_seconds": 60, "sequence": [{"label": "Takedown", "type": "takedown"}],
        "athlete_a": "Josh Hinger", "athlete_b": "Tye Ruotolo", "family": "adcc",
    }
    entry, reason = build_entry(row, Counter({"https://x/y": 1}))
    assert entry is not None
    assert reason == "db_start"
    assert entry["start"] == 60.0
    assert entry["end"] == 60.0 + 900
    assert entry["label"] == "Josh Hinger vs Tye Ruotolo"
    assert entry["kind"] == "full_match"
    assert "1 events" in entry["note"]


def test_build_entry_shared_url_no_start_refused() -> None:
    row = {
        "event": "ADCC 2022", "year": 2022, "win_type": "POINTS", "video_url": "https://x/y",
        "video_start_seconds": None, "sequence": None,
        "athlete_a": "A", "athlete_b": "B", "family": "adcc",
    }
    entry, reason = build_entry(row, Counter({"https://x/y": 4}))
    assert entry is None
    assert reason == "shared_url_no_start"
