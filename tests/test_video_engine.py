"""The contract layer: what an agent may say, and what the renderer refuses to draw.

Nothing here renders. These tests guard the boundary the architecture rests on — the agent
selects the story, and every objective sentence in it resolves to data before a frame exists.
"""
from __future__ import annotations

import pytest

from video_engine.contracts import (
    BeatKind,
    Claim,
    ClaimKind,
    EditorialBreakdown,
    VideoBeat,
    VideoBrief,
    VisualSpec,
)
from video_engine.facts import UnresolvedClaimError, evaluate, lookup, resolve
from video_engine.storyboard import compile_storyboard
from video_engine.style.tokens import THEME
from video_engine.validate import resolved_brief, validate

BREAKDOWN = {
    "sequence": [{"label": f"e{i}"} for i in range(24)],
    "stats": {"a": {"takedowns_landed": 3, "takedowns_attempted": 4, "name": "Galvao"},
              "b": {"takedowns_landed": 1, "takedowns_attempted": 5}},
}
EDITORIAL = EditorialBreakdown(thesis="option reduction",
                               segments={"editorial:14": "This is where it changed."})


def beat(kind: BeatKind = BeatKind.HOOK, duration: float = 3.0,
         headline: str | None = "The finish started earlier.",
         body: str | None = None,
         claims: list[Claim] | None = None,
         visual: VisualSpec | None = None) -> VideoBeat:
    return VideoBeat(kind=kind, duration=duration, headline=headline, body=body,
                      claims=claims if claims is not None else [],
                      visual=visual if visual is not None else VisualSpec())


def brief(beats: list[VideoBeat], target: float | None = None) -> VideoBrief:
    total = sum(b.duration for b in beats)
    return VideoBrief("m", "t", "thesis", target if target is not None else total, beats)


# ── fact resolution ─────────────────────────────────────────────────────────────
def test_fact_claim_resolves_to_the_value_at_its_path() -> None:
    c = resolve(Claim("3 quedas", ClaimKind.FACT, path="stats.a.takedowns_landed"), BREAKDOWN)
    assert c.resolved == 3


def test_fact_claim_with_a_wrong_path_is_refused_not_guessed() -> None:
    with pytest.raises(UnresolvedClaimError, match="no such path"):
        resolve(Claim("x", ClaimKind.FACT, path="stats.a.sweeps"), BREAKDOWN)


def test_derived_claim_is_computed_here_never_by_the_agent() -> None:
    c = resolve(Claim("75% de conversão", ClaimKind.DERIVED,
                      formula="stats.a.takedowns_landed / stats.a.takedowns_attempted"),
                BREAKDOWN)
    assert c.resolved == pytest.approx(0.75)


@pytest.mark.parametrize("formula", [
    "__import__('os').system('rm -rf /')",
    "open('/etc/passwd').read()",
    "stats.a.takedowns_landed.__class__",
    "[x for x in range(10)]",
    "len(sequence)",
])
def test_a_formula_can_never_execute_anything(formula: str) -> None:
    """An agent-authored string reaching eval() is the whole reason this language is tiny."""
    with pytest.raises(UnresolvedClaimError):
        evaluate(BREAKDOWN, formula)


def test_division_by_zero_is_a_refusal_not_an_infinity() -> None:
    with pytest.raises(UnresolvedClaimError, match="division by zero"):
        evaluate(BREAKDOWN, "stats.a.takedowns_landed / 0")


def test_a_path_to_a_non_number_is_refused() -> None:
    with pytest.raises(UnresolvedClaimError, match="not numeric"):
        evaluate(BREAKDOWN, "stats.a.name * 2")


def test_analyst_claim_must_cite_a_segment_that_exists() -> None:
    ok = resolve(Claim("Aqui a luta mudou.", ClaimKind.ANALYST, segment="editorial:14"),
                 BREAKDOWN, EDITORIAL)
    assert ok.resolved == "This is where it changed."
    with pytest.raises(UnresolvedClaimError, match="absent from the editorial"):
        resolve(Claim("x", ClaimKind.ANALYST, segment="editorial:99"), BREAKDOWN, EDITORIAL)


def test_there_is_no_fourth_claim_class() -> None:
    assert {k.value for k in ClaimKind} == {"fact", "derived", "analyst"}


def test_lookup_walks_lists_by_index() -> None:
    assert lookup(BREAKDOWN, "sequence.3.label") == "e3"


# ── brief validation ────────────────────────────────────────────────────────────
def test_a_clean_brief_validates() -> None:
    b = brief([
        beat(),
        beat(kind=BeatKind.STAT_COMPARE, duration=5.0, headline="Galvao landed 3 of 4",
             visual=VisualSpec(metric="takedowns"),
             claims=[Claim("3 de 4 quedas", ClaimKind.FACT,
                           path="stats.a.takedowns_landed")]),
    ])
    assert validate(b, BREAKDOWN, EDITORIAL) == []


def test_an_objective_beat_with_no_claim_is_refused() -> None:
    """The specific failure this engine exists to prevent: a plausible sentence with nothing
    behind it, rendered as though it were measured."""
    b = brief([beat(kind=BeatKind.STAT_COMPARE, duration=5.0,
                    headline="She won 80% of the wrestling",
                    visual=VisualSpec(metric="wrestling"))])
    problems = validate(b, BREAKDOWN, EDITORIAL)
    assert any("no claim behind it" in p for p in problems)


def test_a_stat_comparison_cannot_rest_on_opinion_alone() -> None:
    b = brief([beat(kind=BeatKind.STAT_COMPARE, duration=5.0, headline="She was dominating",
                    visual=VisualSpec(metric="wrestling"),
                    claims=[Claim("She was dominating", ClaimKind.ANALYST,
                                  segment="editorial:14")])])
    assert any("backed only by analyst opinion" in p
               for p in validate(b, BREAKDOWN, EDITORIAL))


def test_a_beat_citing_an_event_outside_the_match_is_refused() -> None:
    b = brief([beat(kind=BeatKind.SEQUENCE, duration=8.0, headline="The sequence",
                    visual=VisualSpec(events=[18, 19, 900]),
                    claims=[Claim("e18", ClaimKind.FACT, path="sequence.18.label")])])
    assert any("outside 0..23" in p for p in validate(b, BREAKDOWN, EDITORIAL))


def test_a_data_beat_must_say_which_data() -> None:
    b = brief([beat(kind=BeatKind.TRANSITION_GRAPH, duration=8.0, headline="Why it worked",
                    claims=[Claim("x", ClaimKind.FACT, path="stats.a.takedowns_landed")])])
    assert any("visual.focus_nodes" in p for p in validate(b, BREAKDOWN, EDITORIAL))


def test_headline_limits_exist_because_the_phone_is_muted() -> None:
    long_ = brief([beat(headline="x" * 80)])
    assert any("over 55" in p for p in validate(long_, BREAKDOWN, EDITORIAL))
    short = brief([beat(headline="oi")])
    assert any("too short" in p for p in validate(short, BREAKDOWN, EDITORIAL))


def test_duration_must_land_near_its_own_target() -> None:
    b = brief([beat(duration=3.0)], target=45.0)
    assert any("outside" in p for p in validate(b, BREAKDOWN, EDITORIAL))


def test_resolved_brief_refuses_rather_than_partially_rendering() -> None:
    bad = brief([beat(kind=BeatKind.STAT_COMPARE, duration=5.0, headline="Something happened",
                      visual=VisualSpec(metric="m"),
                      claims=[Claim("x", ClaimKind.FACT, path="nope.nope")])])
    with pytest.raises(UnresolvedClaimError, match="does not validate"):
        resolved_brief(bad, BREAKDOWN, EDITORIAL)


# ── storyboard ──────────────────────────────────────────────────────────────────
def test_scenes_are_laid_end_to_end_with_no_gap() -> None:
    b = brief([beat(duration=3.0), beat(kind=BeatKind.CONCLUSION, duration=4.0,
                                        headline="Control came from removing options.")])
    scenes = compile_storyboard(b, THEME)
    assert [s.start for s in scenes] == [0.0, 3.0]
    assert scenes[-1].start + scenes[-1].duration == pytest.approx(b.duration)


def test_sequence_cues_stay_inside_their_scene() -> None:
    b = brief([beat(kind=BeatKind.SEQUENCE, duration=8.0, headline="The sequence",
                    visual=VisualSpec(events=[18, 19, 20, 21]),
                    claims=[Claim("e18", ClaimKind.FACT, path="sequence.18.label")])])
    scene = compile_storyboard(b, THEME)[0]
    assert scene.cues == sorted(scene.cues, key=lambda c: c.at)
    assert all(0 <= c.at <= scene.duration for c in scene.cues)
    assert sum(1 for c in scene.cues if c.action == "node_in") == 4


def test_scene_keys_are_stable_and_input_sensitive() -> None:
    """The render cache depends on this: same inputs must hash the same, and any change to
    what a scene shows must hash differently, or a stale clip gets reused."""
    b1 = brief([beat(duration=3.0)])
    b2 = brief([beat(duration=3.0)])
    b3 = brief([beat(duration=3.0, headline="A different headline here")])
    k1 = compile_storyboard(b1, THEME)[0].key
    assert k1 == compile_storyboard(b2, THEME)[0].key
    assert k1 != compile_storyboard(b3, THEME)[0].key


def test_theme_keeps_the_athlete_colours_apart() -> None:
    assert THEME.athlete_a != THEME.athlete_b
    assert THEME.width == 1080 and THEME.height == 1920 and THEME.fps == 30
    assert THEME.safe_bottom / THEME.height > 0.15, "Reels controls cover the bottom fifth"
