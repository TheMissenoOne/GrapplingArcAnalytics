"""Unit tests for ``analysis.rating_v2.rrb_dominance`` — the production RRB-dominance signal
(ADR-17, ``docs/rating_v2/``).

Golden round-trip against the committed artefacts (no DB, no network): the shipped calibration
(`data/rating/rrb_dominance_calibration.json`) and the shipped cross-repo fixture
(`data/fixtures/rrbDominanceGolden.json`, written by `scripts/export_rrb_dominance_fixtures.py`
directly off the lower-level functions this module also exports). The fixture's own
``elo_offset`` is UNCLAMPED (a deliberate, documented gap — see that generator's module
docstring); this module's composed ``round_dominance`` DOES clamp, so the golden comparison
clamps the fixture's number the same way before comparing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from analysis.rating_v2.glicko2 import expected_score
from analysis.rating_v2.rrb_dominance import (
    DEFAULT_CALIBRATION_PATH,
    clamp_elo,
    elo_offset,
    load_calibration,
    round_dominance,
)

REPO = Path(__file__).resolve().parents[1]
GOLDEN_PATH = REPO / "data" / "fixtures" / "rrbDominanceGolden.json"

CALIB = load_calibration()
WEIGHTS = CALIB["definition"]["value_table"]
GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
CLAMP = CALIB["clamp_elo"]["value"]


def test_load_calibration_reads_the_committed_artefact() -> None:
    assert load_calibration().keys() >= {"definition", "calibration", "clamp_elo"}
    assert load_calibration(DEFAULT_CALIBRATION_PATH) == CALIB


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_golden_round_trip(case: dict[str, Any]) -> None:
    result = round_dominance(case["entries"], weights=WEIGHTS, calibration=CALIB)

    if case["n_mapped"] == 0:
        assert result == {
            "p": None,
            "p_calibrated": None,
            "elo_offset": None,
            "contributions": [],
            "coverage": 0.0,
        }
        return

    assert result["p"] == pytest.approx(case["raw_p"], abs=1e-9)
    assert result["p_calibrated"] == pytest.approx(case["calibrated_p"], abs=1e-9)
    # The fixture's own `elo_offset` is unclamped (documented gap); `round_dominance` clamps.
    assert result["elo_offset"] == pytest.approx(clamp_elo(case["elo_offset"], CLAMP), abs=1e-6)
    got_codes = [c["code"] for c in result["contributions"]]
    want_codes = [c["code"] for c in case["contributions"]]
    assert got_codes == want_codes
    for got, want in zip(result["contributions"], case["contributions"], strict=True):
        assert got["own"] == want["own"]
        assert got["c_k"] == pytest.approx(want["c_k"], abs=1e-9)
    assert result["coverage"] == pytest.approx(case["n_mapped"] / len(case["entries"]))


def test_neutral_offset_on_zero_coverage() -> None:
    """No entry maps to a Lamas code — no calibrated P, no offset, no contribution, and coverage
    is a number (never NaN) so a caller can log/threshold it without a NaN check."""
    result = round_dominance(
        [{"type": "guard", "label": "Quatro Apoios", "actor": "you", "successful": None}],
        weights=WEIGHTS,
        calibration=CALIB,
    )
    assert result == {
        "p": None,
        "p_calibrated": None,
        "elo_offset": None,
        "contributions": [],
        "coverage": 0.0,
    }

    empty = round_dominance([], weights=WEIGHTS, calibration=CALIB)
    assert empty["coverage"] == 0.0


def test_determinism() -> None:
    entries = [
        {"type": "takedown", "label": "Queda de Perna Única", "actor": "you", "successful": True},
        {"type": "submission", "label": "Rear Naked Choke", "actor": "you", "successful": True},
    ]
    a = round_dominance(entries, weights=WEIGHTS, calibration=CALIB)
    b = round_dominance(entries, weights=WEIGHTS, calibration=CALIB)
    assert a == b


def test_clamp_is_a_symmetric_bound() -> None:
    assert clamp_elo(-900.0, 400.0) == -400.0
    assert clamp_elo(900.0, 400.0) == 400.0
    assert clamp_elo(50.0, 400.0) == 50.0


# ── the identity that keeps dominance out of the observation score ───────────────


_SEED = 1500.0
_RD = 220.0


def test_self_cancellation_identity_dominance_is_never_the_score() -> None:
    """``expected_score(seed, rd, seed + elo_offset(p), rd) == p`` for every ``p`` — the identity
    (study §4) that makes dominance safe ONLY as the Glicko-2 OPPONENT offset. If dominance were
    ALSO used as the score, ``s - E`` would be identically zero and the round would carry no
    information; the caller's own ``successful`` flag must stay the score.
    """
    for p in (0.05, 0.2, 0.4, 0.5, 0.6, 0.8, 0.95):
        e = expected_score(_SEED, _RD, _SEED + elo_offset(p), 0.0)
        assert abs(p - e) < 1e-6


def test_round_dominance_never_returns_a_score_key() -> None:
    """Contract check: the composed function has no ``score``/``s`` field to accidentally wire
    into the observation score — only the opponent-side fields."""
    mapped_case = next(c for c in GOLDEN["cases"] if c["n_mapped"] > 0)
    result = round_dominance(mapped_case["entries"], weights=WEIGHTS, calibration=CALIB)
    assert set(result) == {"p", "p_calibrated", "elo_offset", "contributions", "coverage"}
    assert "score" not in result
