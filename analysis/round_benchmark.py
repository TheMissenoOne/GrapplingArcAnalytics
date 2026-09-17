"""``score_read`` — scores a Gemini round read against a human truth timeline.

PRIVATE (root ``CLAUDE.md`` / this repo's ``CLAUDE.md``, "Public vs Private Data"): both
inputs are the SAME owner's own footage/labels; purpose is evaluating the automatic reader
for that same owner's product experience. Every caller keeps outputs under
``data/video/owner/`` (gitignored) -- never ``data/finetune``, a CV/vision dataset, the
athlete corpus, an archetype centroid, an athlete's ELO or the ``site/`` export.
**Evaluation set only -- never training data, never a fine-tune corpus.**

Pure module -- no filesystem, no DB, no network. ``scripts/round_audit.py``'s ``benchmark``
subcommand is the only caller today.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from analysis.names import _normalize_name

Event = Mapping[str, Any]


def _percentile(sorted_vals: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile over an already-sorted sequence (``p`` in [0, 1])."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def _offset_histogram(offsets: Sequence[float], window_s: float, n_bins: int = 5) -> dict[str, int]:
    """Fixed-width bins spanning ``[-window_s, +window_s]`` -- every matched pair's offset is
    admissible only inside that range (the matching rule below), so the bins always cover the
    whole distribution. ``n_bins`` odd would center a bin on zero; even is fine too, this is a
    read-quality report, not a statistical test."""
    if not offsets or window_s <= 0:
        return {}
    width = (2 * window_s) / n_bins
    counts = [0] * n_bins
    for o in offsets:
        idx = min(max(int((o + window_s) / width), 0), n_bins - 1)
        counts[idx] += 1
    labels = [
        f"{-window_s + i * width:+.1f}..{-window_s + (i + 1) * width:+.1f}" for i in range(n_bins)
    ]
    return dict(zip(labels, counts, strict=True))


@dataclass
class BenchmarkResult:
    precision: float
    recall: float
    f1: float
    actor_accuracy: float | None  # over matched pairs; None when no pair matched
    success_accuracy: float | None
    offset_median_s: float | None  # median |Δts| over matched pairs
    offset_p90_s: float | None
    offsets_s: list[float] = field(default_factory=list)  # signed read_ts - truth_ts, matched pairs
    offset_histogram: dict[str, int] = field(default_factory=dict)
    n_truth: int = 0
    n_read: int = 0
    n_matched: int = 0
    unmatched_truth: list[dict[str, Any]] = field(default_factory=list)
    unmatched_read: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision, "recall": self.recall, "f1": self.f1,
            "actor_accuracy": self.actor_accuracy, "success_accuracy": self.success_accuracy,
            "offset_median_s": self.offset_median_s, "offset_p90_s": self.offset_p90_s,
            "offsets_s": self.offsets_s, "offset_histogram": self.offset_histogram,
            "n_truth": self.n_truth, "n_read": self.n_read, "n_matched": self.n_matched,
            "unmatched_truth": self.unmatched_truth, "unmatched_read": self.unmatched_read,
        }


def score_read(truth_events: Sequence[Event], read_events: Sequence[Event],
               window_s: float = 5.0) -> BenchmarkResult:
    """Greedy one-to-one matching, nearest ``|Δts|`` first. A pair is admissible when
    ``_normalize_name(label)`` agrees on both sides AND ``|Δts| <= window_s``. Precision/recall
    are over EVENT COUNTS (not label pairs); actor/success accuracy are over matched pairs
    only. A ``truth``/``read`` with 0 events on both sides scores a vacuous 1.0/1.0 (nothing
    to disagree about); 0 truth events but nonempty read is 0 precision (every read event is a
    false positive), mirrored for the reverse.
    """
    truth, read = list(truth_events), list(read_events)

    candidates: list[tuple[float, int, int]] = []
    for ti, te in enumerate(truth):
        t_label = _normalize_name(str(te.get("label") or ""))
        if not t_label:
            continue
        t_ts = float(te.get("ts") or 0.0)
        for ri, re_ in enumerate(read):
            if _normalize_name(str(re_.get("label") or "")) != t_label:
                continue
            delta = float(re_.get("ts") or 0.0) - t_ts
            if abs(delta) <= window_s:
                candidates.append((abs(delta), ti, ri))
    candidates.sort(key=lambda c: c[0])

    matched_truth: set[int] = set()
    matched_read: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for _dist, ti, ri in candidates:
        if ti in matched_truth or ri in matched_read:
            continue
        matched_truth.add(ti)
        matched_read.add(ri)
        pairs.append((ti, ri, float(read[ri].get("ts") or 0.0) - float(truth[ti].get("ts") or 0.0)))

    n_truth, n_read, n_matched = len(truth), len(read), len(pairs)
    precision = (n_matched / n_read) if n_read else (1.0 if n_truth == 0 else 0.0)
    recall = (n_matched / n_truth) if n_truth else (1.0 if n_read == 0 else 0.0)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    actor_hits = [1.0 if str(truth[ti].get("actor")) == str(read[ri].get("actor")) else 0.0
                  for ti, ri, _ in pairs]
    success_hits = [
        1.0 if bool(truth[ti].get("successful")) == bool(read[ri].get("successful")) else 0.0
        for ti, ri, _ in pairs
    ]
    offsets = [delta for _, _, delta in pairs]
    abs_sorted = sorted(abs(d) for d in offsets)

    return BenchmarkResult(
        precision=precision, recall=recall, f1=f1,
        actor_accuracy=(sum(actor_hits) / len(actor_hits)) if actor_hits else None,
        success_accuracy=(sum(success_hits) / len(success_hits)) if success_hits else None,
        offset_median_s=_percentile(abs_sorted, 0.5) if abs_sorted else None,
        offset_p90_s=_percentile(abs_sorted, 0.9) if abs_sorted else None,
        offsets_s=offsets,
        offset_histogram=_offset_histogram(offsets, window_s),
        n_truth=n_truth, n_read=n_read, n_matched=n_matched,
        unmatched_truth=[dict(truth[i]) for i in range(n_truth) if i not in matched_truth],
        unmatched_read=[dict(read[i]) for i in range(n_read) if i not in matched_read],
    )


if __name__ == "__main__":
    # ponytail: manual smoke check, no fixture -- real coverage in tests/test_round_benchmark.py.
    t = [{"ts": 10.0, "label": "armbar", "actor": "you", "successful": True}]
    r = [{"ts": 11.0, "label": "Armbar", "actor": "you", "successful": True}]
    res = score_read(t, r, window_s=5.0)
    assert res.precision == 1.0 and res.recall == 1.0 and res.f1 == 1.0
    assert res.actor_accuracy == 1.0 and res.n_matched == 1
    print("ok")
