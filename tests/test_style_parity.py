"""Parity guard: analysis.style_profile.reduce_style_events vs the App port
(src/services/styleProfile.ts:buildStyleProfile, tested separately in
GrapplingArcApp's styleProfileParity.test.ts against the SAME fixture).

Comparable fields only (per the cross-module parity contract):
  signature_techniques, style_mix (axis intersection with the App's 7-axis
  radar), responses (situation/total/moves — NOT the bouts array, DB-only),
  submission_family (per-family count+pct), submissions landed/attempted,
  favorite_finishes.

EXCLUDED — different semantics/inputs, do NOT assert equal here or in the App
test: finishing.record (App round-outcome proxy vs Python match win/loss),
finish_rate/decision_rate, fingerprint, signature_transitions, dominant, and
all fighter/elo/rank/bouts/DB-only fields. Nobody should "fix" a failure here
by comparing those — they're apples/oranges by design.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from analysis.style_profile import reduce_style_events

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "style_parity.json").read_text()
)


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return math.isclose(a, b, abs_tol=tol)


def test_reduce_style_events_matches_fixture() -> None:
    reduced = reduce_style_events(FIXTURE["events"])
    expected = FIXTURE["expected"]

    assert len(reduced["signature_techniques"]) == len(expected["signature_techniques"])
    for got, exp in zip(reduced["signature_techniques"], expected["signature_techniques"]):
        assert got["label"] == exp["label"]
        assert got["count"] == exp["count"]
        assert _approx(got["pct"], exp["pct"], tol=1e-3)

    for axis, pct in expected["style_mix"].items():
        assert _approx(reduced["style_mix"][axis], pct, tol=1e-3), axis

    for sit, exp in expected["responses"].items():
        got = reduced["responses"][sit]
        assert got["total"] == exp["total"]
        assert len(got["moves"]) == len(exp["moves"])
        for got_mv, exp_mv in zip(got["moves"], exp["moves"]):
            assert got_mv["move"] == exp_mv["move"]
            assert got_mv["count"] == exp_mv["count"]
            assert _approx(got_mv["pct"], exp_mv["pct"], tol=1e-3)

    fam_counts = reduced["submission_family"]["counts"]
    fam_shares = reduced["submission_family"]["shares"]
    for fam in expected["submission_family"]:
        assert fam_counts[fam["family"]] == fam["count"]
        assert _approx(fam_shares[fam["family"]], fam["pct"])

    assert reduced["submissions_attempted"] == expected["submissions_attempted"]
    assert reduced["submissions_landed"] == expected["submissions_landed"]
    assert reduced["favorite_finishes"] == expected["favorite_finishes"]
