"""analysis/round_benchmark.py -- score_read's matching is the whole risk, pure fixtures only."""
from __future__ import annotations

from typing import Any

from analysis.round_benchmark import score_read


def _ev(ts: float, label: str, actor: str = "you", successful: bool = True) -> dict[str, Any]:
    return {"ts": ts, "label": label, "actor": actor, "successful": successful, "type": "control"}


def test_exact_match_is_perfect() -> None:
    truth = [_ev(10.0, "armbar"), _ev(30.0, "triangle choke", actor="partner")]
    read = [_ev(10.0, "armbar"), _ev(30.0, "triangle choke", actor="partner")]
    r = score_read(truth, read, window_s=5.0)
    assert r.precision == 1.0
    assert r.recall == 1.0
    assert r.f1 == 1.0
    assert r.actor_accuracy == 1.0
    assert r.success_accuracy == 1.0
    assert r.n_matched == 2


def test_no_events_either_side_is_vacuous_perfect() -> None:
    r = score_read([], [], window_s=5.0)
    assert r.precision == 1.0
    assert r.recall == 1.0
    assert r.f1 == 1.0


def test_shift_inside_window_still_matches() -> None:
    truth = [_ev(10.0, "armbar")]
    read = [_ev(14.9, "armbar")]  # |Δts|=4.9 < 5.0
    r = score_read(truth, read, window_s=5.0)
    assert r.n_matched == 1
    assert r.recall == 1.0


def test_shift_outside_window_does_not_match() -> None:
    truth = [_ev(10.0, "armbar")]
    read = [_ev(15.1, "armbar")]  # |Δts|=5.1 > 5.0
    r = score_read(truth, read, window_s=5.0)
    assert r.n_matched == 0
    assert r.recall == 0.0
    assert r.precision == 0.0
    assert len(r.unmatched_truth) == 1
    assert len(r.unmatched_read) == 1


def test_shift_exactly_at_window_boundary_matches() -> None:
    truth = [_ev(10.0, "armbar")]
    read = [_ev(15.0, "armbar")]  # |Δts|=5.0 == window, admissible (<=)
    r = score_read(truth, read, window_s=5.0)
    assert r.n_matched == 1


def test_duplicate_label_inside_window_matched_once() -> None:
    truth = [_ev(10.0, "armbar")]
    read = [_ev(10.5, "armbar"), _ev(11.0, "armbar")]  # both within window
    r = score_read(truth, read, window_s=5.0)
    assert r.n_matched == 1
    assert r.n_read == 2
    assert r.precision == 0.5  # 1 matched / 2 read
    assert r.recall == 1.0
    assert len(r.unmatched_read) == 1


def test_actor_swap_leaves_f1_unchanged_but_drops_actor_accuracy() -> None:
    truth = [_ev(10.0, "armbar", actor="you")]
    read = [_ev(10.0, "armbar", actor="partner")]
    r = score_read(truth, read, window_s=5.0)
    assert r.f1 == 1.0
    assert r.actor_accuracy == 0.0
    assert r.success_accuracy == 1.0  # successful unchanged, unaffected by actor swap


def test_success_flag_mismatch_drops_success_accuracy_only() -> None:
    truth = [_ev(10.0, "armbar", successful=True)]
    read = [_ev(10.0, "armbar", successful=False)]
    r = score_read(truth, read, window_s=5.0)
    assert r.f1 == 1.0
    assert r.success_accuracy == 0.0
    assert r.actor_accuracy == 1.0


def test_label_mismatch_never_matches_regardless_of_ts() -> None:
    truth = [_ev(10.0, "armbar")]
    read = [_ev(10.0, "triangle choke")]
    r = score_read(truth, read, window_s=5.0)
    assert r.n_matched == 0


def test_matching_is_case_and_punctuation_insensitive() -> None:
    truth = [_ev(10.0, "Rear Naked Choke")]
    read = [_ev(10.0, "  REAR NAKED CHOKE!!  ")]
    r = score_read(truth, read, window_s=5.0)
    assert r.n_matched == 1


def test_no_actor_or_success_accuracy_when_nothing_matched() -> None:
    r = score_read([_ev(10.0, "armbar")], [_ev(90.0, "armbar")], window_s=5.0)
    assert r.n_matched == 0
    assert r.actor_accuracy is None
    assert r.success_accuracy is None
    assert r.offset_median_s is None
    assert r.offset_p90_s is None


def test_offset_histogram_sums_to_matched_count() -> None:
    truth = [_ev(10.0, "armbar"), _ev(50.0, "triangle choke")]
    read = [_ev(13.0, "armbar"), _ev(48.0, "triangle choke")]
    r = score_read(truth, read, window_s=5.0)
    assert sum(r.offset_histogram.values()) == r.n_matched == 2
