"""Presentation contract for a V2 rating — the one place RD becomes words.

Doc 07 of the plan bundle (``07_APP_PORT_PLAN.md``, §Product presentation) asks for
``rating + confidence`` instead of raw RD everywhere. This module is that contract, and
it is deliberately the ONLY place a deviation is turned into a label: two consumers
inventing their own thresholds is how "alta confiança" ends up meaning two different
numbers on the site and in the App.

**Tiers.** Three, cut at the numbers this repo already committed to editorially, not at
fresh ones invented here:

- ``high``   — RD <= 100. Half the publish cutoff; the athlete has a dense, recent record.
- ``medium`` — 100 < RD <= 200. ``SITE_MIN_CONFIDENCE_RD`` (wave 8) is the upper edge, so
  everything the public site publishes is exactly ``high`` or ``medium`` by construction.
- ``low``    — RD > 200. Below the publish gate: still processed, still weighted into
  aggregates (``analysis/confidence_weight.py``), never given a public page.

Reusing 200 matters: if this module picked its own cut, an athlete could be published and
simultaneously labelled "baixa confiança", which is the site contradicting its own gate.

**The band is the honest Glicko interval, not RD.** ``interval_95`` returns
``1.96 * RD`` — Glickman's own convention for a rating interval. Doc 07's illustrative
"1420 ± 90" would be RD itself (~68%), which reads as more precision than the model
claims; this module refuses to ship that particular framing. The consequence is that the
band is WIDE (RD 150 -> +/-294) and that is the true statement. Which is also why the
public site does not show a band at all: per the product rule already in force, it shows
relative position + percentile under the label "Grappling ELO" and never a raw rating, so
the tier label is all it needs from here. The band exists for the App, where a user is
looking at their OWN number and the uncertainty is the point.

Nothing here reads the DB. Callers pass a deviation they already loaded with an explicit
``run_id`` (ADR-02).
"""

from __future__ import annotations

from typing import Literal

Tier = Literal["high", "medium", "low"]

# Both cuts are inherited, not invented: 200 is export/site_data.py's
# SITE_MIN_CONFIDENCE_RD (wave 8's measured editorial cutoff), 100 is half of it. Moving
# either one changes what the site says about an athlete it has already decided to
# publish -- change them here, never inline at a call site.
RD_HIGH_CONFIDENCE = 100.0
RD_PUBLISH_CUTOFF = 200.0

_Z95 = 1.96  # Glickman's 95% rating interval multiplier

_LABELS_PT: dict[str, str] = {
    "high": "alta confiança",
    "medium": "confiança média",
    "low": "baixa confiança",
}


def confidence_tier(rd: float) -> Tier:
    """Rating deviation -> the published confidence tier. Boundaries are inclusive on the
    stronger side (RD exactly 100 is ``high``, exactly 200 is ``medium``) so an athlete
    sitting on the publish cutoff is never labelled below the gate that let them through.
    """
    if rd <= RD_HIGH_CONFIDENCE:
        return "high"
    if rd <= RD_PUBLISH_CUTOFF:
        return "medium"
    return "low"


def confidence_label(rd: float, lang: str = "pt") -> str:
    """Tier as display text. ``pt`` (the product's language) or the bare English tier."""
    tier = confidence_tier(rd)
    return _LABELS_PT[tier] if lang == "pt" else tier


def interval_95(rating: float, rd: float) -> tuple[float, float]:
    """``(low, high)`` 95% rating interval — ``rating +/- 1.96*RD``, Glickman's convention.

    Deliberately not ``rating +/- RD``: that is a ~68% interval and reading it as a range
    of plausible ratings overstates the model's certainty by a lot.
    """
    half = _Z95 * rd
    return (rating - half, rating + half)


def describe(rating: float, rd: float, lang: str = "pt") -> str:
    """The full contract in one string: ``"1420 · alta confiança"``. Rounded to whole
    rating points — a tenth of an Elo point is noise at any RD this model produces."""
    return f"{round(rating)} · {confidence_label(rd, lang)}"


def demo() -> None:
    """ponytail: runnable self-check instead of a test module for four pure functions."""
    assert confidence_tier(0) == "high"
    assert confidence_tier(100.0) == "high", "the cut is inclusive on the stronger side"
    assert confidence_tier(100.01) == "medium"
    assert confidence_tier(200.0) == "medium", "an athlete on the publish gate is not 'low'"
    assert confidence_tier(200.01) == "low"
    # every published athlete is high or medium, by construction
    assert confidence_tier(RD_PUBLISH_CUTOFF) != "low"
    lo, hi = interval_95(1420, 150)
    assert round(hi - lo) == round(2 * _Z95 * 150) == 588
    assert lo < 1420 < hi
    assert describe(1420.4, 90) == "1420 · alta confiança"
    assert describe(1420, 250, lang="en") == "1420 · low"
    print("presentation contract ok")


if __name__ == "__main__":
    demo()
