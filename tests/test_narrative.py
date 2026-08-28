"""Tests for the deterministic prose engine (pure dict → sections, no DB)."""

from __future__ import annotations

import re
from typing import Any

from export.narrative import match_narrative, profile_narrative, render_markdown


def _breakdown() -> dict[str, Any]:
    return {
        "meta": {
            "title": "Khamzat Chimaev vs Gilbert Burns",
            "a": {"name": "Khamzat Chimaev"}, "b": {"name": "Gilbert Burns"},
            "year": 2022, "event": "UFC 273", "method": "Decision",
            "winner": {"side": "a", "name": "Khamzat Chimaev"},
        },
        "sequence": [
            {"label": "Double Leg Takedown", "type": "takedown", "side": "a"},
            {"label": "Mount", "type": "control", "side": "a"},
            {"label": "Back Control", "type": "control", "side": "a"},
            {"label": "Triangle", "type": "submission", "side": "b"},
        ],
        "stats": {
            "a": {"takedowns_landed": 4, "takedowns_attempted": 5,
                  "submission_attempts": 1, "submissions_finished": 0,
                  "sweeps": 0, "passes": 2, "escapes": 0, "controls": 5,
                  "transitions": 19, "points": 18,
                  "positional_entries": 6, "positional_conversions": 4,
                  "positional_conversion": 0.71},
            "b": {"takedowns_landed": 1, "takedowns_attempted": 2,
                  "submission_attempts": 3, "submissions_finished": 0,
                  "sweeps": 1, "passes": 0, "escapes": 1, "controls": 1,
                  "transitions": 11, "points": 6,
                  "positional_entries": 4, "positional_conversions": 1,
                  "positional_conversion": 0.38},
            "momentum": {"a": 0.75, "b": 0.25},
            "momentum_series": [1.0, 1.0, 1.0, 0.75],
        },
        "fighters": {
            "a": {"name": "Khamzat Chimaev", "graph_elo": 1884.0,
                  "elo_delta": 46.0, "elo_delta_pct": 2.5, "elo_pct": 1},
            "b": {"name": "Gilbert Burns", "graph_elo": 1791.0,
                  "elo_delta": -31.0, "elo_delta_pct": -1.7, "elo_pct": 4},
        },
    }


def _profile() -> dict[str, Any]:
    return {
        "fighter": {"name": "Gordon Ryan", "elo_rank": 1, "elo_percentile": 1,
                    "finish_rate": 0.6},
        "archetype": "Submission Hunter",
        "style_mix": {"control": 0.32, "guard": 0.05, "pass": 0.20, "submission": 0.18,
                      "takedown": 0.05, "sweep": 0.10, "escape": 0.05, "transition": 0.05,
                      "offense_ratio": 0.33},
        "signature_techniques": [
            {"label": "Back Take", "count": 9, "pct": 0.10},
            {"label": "Ashi Garami", "count": 6, "pct": 0.07},
        ],
        "signature_transitions": [{"from": "Mount", "to": "Back Take", "count": 4}],
        "responses": {
            "taken down": {"total": 5, "moves": [
                {"move": "Open Guard", "count": 3, "pct": 0.6}], "bouts": ["x"]},
        },
        "finishing": {
            "finish_rate": 0.6, "decision_rate": 0.4,
            "submission_family": {"dominant": "Strangles", "shares": {"Strangles": 0.8}},
            "record_vs_elite": {"wins": 3, "losses": 0},
        },
    }


def _flat(sections: list[tuple[str, list[str]]]) -> str:
    return render_markdown(sections)


class TestMatchNarrative:
    def test_headings_and_numbers(self) -> None:
        secs = match_narrative(_breakdown())
        headings = [h for h, _ in secs]
        assert "Overview" in headings
        assert "Positional conversion" in headings
        body = _flat(secs)
        assert "Khamzat Chimaev defeated Gilbert Burns by decision" in body
        assert "71%" in body and "38%" in body
        assert "4 of 5 takedown" in body

    def test_decisive_chain_uses_arrows(self) -> None:
        body = _flat(match_narrative(_breakdown()))
        assert "Double Leg Takedown → Mount → Back Control" in body

    def test_grappling_elo_relative(self) -> None:
        body = _flat(match_narrative(_breakdown()))
        assert "Grappling ELO" in body
        assert "+2.5%" in body and "-1.7%" in body
        assert "1884" not in body  # raw rating never shown

    def test_no_template_leftovers(self) -> None:
        body = _flat(match_narrative(_breakdown()))
        assert "{" not in body and "}" not in body

    def test_sections_conditional(self) -> None:
        bd = _breakdown()
        for side in ("a", "b"):
            for k in ("takedowns_landed", "takedowns_attempted",
                      "submission_attempts", "submissions_finished",
                      "positional_entries"):
                bd["stats"][side][k] = 0
        headings = [h for h, _ in match_narrative(bd)]
        assert "The takedown battle" not in headings
        assert "Positional conversion" not in headings
        assert "Submission threats" not in headings


class TestProfileNarrative:
    def test_archetype_and_rank(self) -> None:
        body = _flat(profile_narrative(_profile()))
        assert "submission hunter" in body
        assert "#1 by Grappling ELO" in body

    def test_response_patterns_removed(self) -> None:
        """Response-pattern prose (owner-distrusted, small-sample) is gone entirely —
        both the section heading and its per-situation sentence."""
        secs = profile_narrative(_profile())
        assert "How he responds" not in [h for h, _ in secs]
        body = _flat(secs)
        assert "most often answers with" not in body

    def test_finishing(self) -> None:
        body = _flat(profile_narrative(_profile()))
        assert "60%" in body
        assert "strangles" in body
        assert "3–0 against top-tier" in body

    def test_signature_entries_have_no_percentage(self) -> None:
        """Per-technique conversion-rate-style claims are stripped — names only."""
        body = _flat(profile_narrative(_profile()))
        assert "Back Take" in body
        assert "10%" not in body and "7%" not in body

    def test_no_template_leftovers(self) -> None:
        body = _flat(profile_narrative(_profile()))
        assert "{" not in body and "}" not in body

    def test_systems_section(self) -> None:
        p = _profile()
        p["_systems"] = {
            "system_count": 3, "dominant_type": "submission",
            "systems": [{"name": "Submission (back control)", "hub": "back control",
                         "size": 5, "transition_count": 7}],
        }
        secs = profile_narrative(p)
        assert "The systems" in [h for h, _ in secs]
        body = _flat(secs)
        assert "3" in body and "back control" in body

    def test_systems_section_absent_without_data(self) -> None:
        assert "The systems" not in [h for h, _ in profile_narrative(_profile())]

    def test_dilemma_prose(self) -> None:
        p = _profile()
        p["_dilemmas"] = [
            {"node": "Mount", "branches": [["Armbar", 0.71], ["Triangle", 0.64]]},
        ]
        secs = profile_narrative(p)
        assert "The dilemmas" in [h for h, _ in secs]
        body = _flat(secs)
        assert "Mount" in body and "Armbar" in body and "Triangle" in body
        assert "0.71" not in body  # raw PtV never shown

    def test_dilemma_prose_absent_without_data(self) -> None:
        assert "The dilemmas" not in [h for h, _ in profile_narrative(_profile())]


def _gated_progression_row(**overrides: Any) -> dict[str, Any]:
    """A hand-built ``analysis.rrb_progression.athlete_progression`` row shaped the way
    ``export/site_data.py`` stashes it on ``profile["_progression"]`` — a gate-clearing
    athlete, `off` the dominant phase, `recovery_degenerate` True (the corpus's ordinary
    case, per rrb_progression.py section 5)."""
    row: dict[str, Any] = {
        "athlete": "x", "bouts": 6, "n_valued_transitions": 40,
        "net_total": 0.6, "per_action": 0.015, "per_action_lo": -0.01, "per_action_hi": 0.05,
        "off_share": {"k": 24, "n": 40, "p": 0.6, "lo": 0.44, "hi": 0.74, "half": 0.15,
                      "grade": "moderate", "estimable": True, "coverage": "adequate"},
        "def_share": {"k": 16, "n": 40, "p": 0.4, "lo": 0.26, "hi": 0.56, "half": 0.15,
                      "grade": "moderate", "estimable": True, "coverage": "adequate"},
        "recovery_rate": {"k": 5, "n": 5, "p": 1.0, "lo": None, "hi": None, "half": None,
                          "grade": "none", "estimable": True, "coverage": "adequate"},
        "collapse_rate": {"k": 4, "n": 4, "p": 1.0, "lo": None, "hi": None, "half": None,
                          "grade": "none", "estimable": True, "coverage": "adequate"},
        "recovery_degenerate": True,
        "off_cycles": 6, "def_cycles": 5,
        "mean_off_cycle_len": 2.5, "mean_def_cycle_len": 1.3,
        "valued_steps": 40, "unvalued_steps": 3,
        "gated": True, "coverage": {},
        "_mixed_source": False,
        "_example": {"from_state": "PGD", "to_state": "BTK"},
    }
    row.update(overrides)
    return row


def _prog_text(secs: list[tuple[str, list[str]]], heading: str = "Progression") -> str:
    return next(p[0] for h, p in secs if h == heading)


class TestProgressionSection:
    def test_gate_clearing_athlete_gets_the_block(self) -> None:
        p = _profile()
        p["_progression"] = _gated_progression_row()
        secs = profile_narrative(p)
        assert "Progression" in [h for h, _ in secs]
        text = _prog_text(secs)
        assert "guard pull" in text and "back take" in text

    def test_below_gate_athlete_gets_no_block(self) -> None:
        """No `_progression` key (never computed a row, or the athlete's row wasn't
        gated) -> no section. Honest absence, never a filler sentence."""
        assert "Progression" not in [h for h, _ in profile_narrative(_profile())]
        p = _profile()
        p["_progression"] = None
        assert "Progression" not in [h for h, _ in profile_narrative(p)]

    def test_no_banned_vocabulary(self) -> None:
        p = _profile()
        p["_progression"] = _gated_progression_row()
        for lang in ("en", "pt"):
            body = _flat(profile_narrative(p, lang=lang)).lower()
            assert "atriz" not in body
            assert "uniforme" not in body

    def test_never_a_significance_or_prediction_claim(self) -> None:
        """Only 17/441 athletes clear the gate, and of those 2/17 CIs exclude zero — chance
        level. So this prose must never say the trend is significant, predicts anything, or
        quote the raw per_action/CI numbers."""
        p = _profile()
        p["_progression"] = _gated_progression_row()
        text = _prog_text(profile_narrative(p)).lower()
        for banned in ("significant", "predict", "0.015", "-0.01", "0.05", "confidence"):
            assert banned not in text

    def test_recovery_degenerate_rate_never_quoted_as_a_finding(self) -> None:
        """recovery_rate is 1.00 by construction whenever recovery_degenerate is True
        (rrb_progression.py section 5) — this prose must not surface it at all."""
        p = _profile()
        p["_progression"] = _gated_progression_row(recovery_degenerate=True)
        text = _prog_text(profile_narrative(p)).lower()
        assert "recover" not in text and "100%" not in text and "1.00" not in text

    def test_mixed_source_drops_the_submission_anchored_gloss(self) -> None:
        """When the value table fell back to reward_risk_centered for this row's states, the
        prose must not pretend the reading is submission-anchored (root task constraint 3)."""
        p = _profile()
        p["_progression"] = _gated_progression_row(_mixed_source=True)
        text = _prog_text(profile_narrative(p)).lower()
        assert "finish" not in text

    def test_two_to_four_sentences(self) -> None:
        p = _profile()
        p["_progression"] = _gated_progression_row()
        text = _prog_text(profile_narrative(p))
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
        assert 2 <= len(sentences) <= 4


class TestGenderedProse:
    """athletes.gender ('f'|'m'|None, alembic 0049) drives pronoun agreement in the dossier
    body. None (no evidence) must read neutral, never masculine — root task rule."""

    def test_female_athlete_no_masculine_pronouns_en(self) -> None:
        p = _profile()
        p["fighter"]["gender"] = "f"
        body = f" {_flat(profile_narrative(p)).lower()} "
        for bad in (" he ", " his ", " him "):
            assert bad not in body
        assert "she" in body or "her" in body

    def test_female_athlete_no_masculine_pronouns_pt(self) -> None:
        p = _profile()
        p["fighter"]["gender"] = "f"
        body = f" {_flat(profile_narrative(p, lang='pt')).lower()} "
        assert " dele " not in body
        assert "dela" in body

    def test_unknown_gender_no_masculine_pronoun_en(self) -> None:
        p = _profile()  # no "gender" key at all -> unknown
        body = f" {_flat(profile_narrative(p)).lower()} "
        for bad in (" he ", " his ", " him "):
            assert bad not in body

    def test_unknown_gender_no_masculine_pronoun_pt(self) -> None:
        p = _profile()
        body = f" {_flat(profile_narrative(p, lang='pt')).lower()} "
        assert " dele " not in body

    def test_progression_female_no_masculine_pronoun(self) -> None:
        p = _profile()
        p["fighter"]["gender"] = "f"
        p["_progression"] = _gated_progression_row()
        text = f" {_prog_text(profile_narrative(p)).lower()} "
        for bad in (" he ", " his "):
            assert bad not in text

    def test_progression_unknown_no_masculine_pronoun(self) -> None:
        p = _profile()
        p["_progression"] = _gated_progression_row()
        text = f" {_prog_text(profile_narrative(p)).lower()} "
        for bad in (" he ", " his "):
            assert bad not in text


def test_every_section_differs_between_languages() -> None:
    """A missing _t() call shows up as identical EN/PT copy — catch it here rather than
    on the page."""
    from export.narrative import event_narrative

    ep = {"event": "ADCC", "bout_count": 8, "year": 2026, "decided": 6,
          "finish_rate": 0.5, "finishes": 3,
          "headline_bout": {"a": "A", "b": "B", "winner": "A", "method": "Decision"},
          "submissions": [("Heel Hook", 2)], "style_mix": {"submission": 0.5},
          "top_techniques": [("Armbar", 3)], "headliners": ["A", "B"]}
    en = event_narrative(ep, lang="en")
    pt = event_narrative(ep, lang="pt")

    assert len(en) == len(pt)
    for (h_en, p_en), (h_pt, p_pt) in zip(en, pt):
        assert h_en != h_pt or h_en == "Grappling ELO", f"heading not translated: {h_en}"
        assert p_en != p_pt, f"body not translated under {h_en}"
