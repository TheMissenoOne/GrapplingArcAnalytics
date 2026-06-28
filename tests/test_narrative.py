"""Tests for the deterministic prose engine (pure dict → sections, no DB)."""

from __future__ import annotations

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
            "a": {"name": "Khamzat Chimaev", "graph_elo": 1884.0, "elo_delta": 46.0},
            "b": {"name": "Gilbert Burns", "graph_elo": 1791.0, "elo_delta": -31.0},
        },
    }


def _profile() -> dict[str, Any]:
    return {
        "fighter": {"name": "Gordon Ryan", "elo_rank": 1, "finish_rate": 0.6},
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
        assert "#1 on the leaderboard" in body

    def test_response_and_finishing(self) -> None:
        body = _flat(profile_narrative(_profile()))
        assert "When taken down, Gordon Ryan most often answers with Open Guard" in body
        assert "60%" in body
        assert "strangles" in body
        assert "3–0 against top-tier" in body

    def test_no_template_leftovers(self) -> None:
        body = _flat(profile_narrative(_profile()))
        assert "{" not in body and "}" not in body
