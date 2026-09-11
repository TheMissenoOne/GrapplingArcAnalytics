"""Exercises presentation.py's demo() self-check + pins the ADR-14 tier cuts."""

from analysis.rating_v2 import presentation


def test_demo_runs_without_error(capsys):
    presentation.demo()
    out = capsys.readouterr().out
    assert "presentation contract ok" in out


def test_tier_cuts_match_adr14_constants():
    # ADR-14: high <= 100, medium <= 200 (site's SITE_MIN_CONFIDENCE_RD), else low.
    assert presentation.RD_HIGH_CONFIDENCE == 100.0
    assert presentation.RD_PUBLISH_CUTOFF == 200.0
    assert presentation.confidence_tier(presentation.RD_HIGH_CONFIDENCE) == "high"
    assert presentation.confidence_tier(presentation.RD_PUBLISH_CUTOFF) == "medium"
