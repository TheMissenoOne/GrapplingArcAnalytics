"""Dossier "Systems" section — render_profile_page consumes profile["_systems"] /
profile["_analogues"] (stashed by build_fighters); pure dict → HTML, no DB."""

from __future__ import annotations

from typing import Any

from export.site_data import render_profile_page


def _profile() -> dict[str, Any]:
    return {
        "fighter": {"name": "Gordon Ryan", "slug": "gordon-ryan", "elo_rank": 1,
                    "elo_percentile": 1, "finish_rate": 0.6,
                    "record": {"wins": 10, "losses": 1}},
        "archetype": "Submission Hunter",
        "style_mix": {"control": 0.32, "guard": 0.05, "pass": 0.20, "submission": 0.18,
                      "takedown": 0.05, "sweep": 0.10, "escape": 0.05, "transition": 0.05},
        "signature_techniques": [{"label": "Back Take", "count": 9, "pct": 0.10}],
        "signature_transitions": [],
        "responses": {},
        "finishing": {
            "finish_rate": 0.6, "decision_rate": 0.4,
            "submission_family": {"dominant": "Strangles", "shares": {"Strangles": 0.8}},
            "record_vs_elite": {"wins": 3, "losses": 0},
        },
        "bouts": [],
        "_career_gv": {"nodes": [], "links": []},
    }


def _systems() -> dict[str, Any]:
    return {
        "athlete_name": "Gordon Ryan",
        "system_count": 2,
        "total_techniques": 9,
        "diversity": 1.2,
        "dominant_type": "submission",
        "composition_vector": [],
        "systems": [
            {"name": "Submission (back control)", "hub": "back control",
             "hub_type": "control", "members": ["back control", "rnc", "body triangle",
                                                "mount", "armbar"],
             "type_vector": [], "size": 5, "system_elo": 1520.0,
             "transition_count": 7, "internal_edges": []},
            {"name": "Pass (half guard)", "hub": "half guard",
             "hub_type": "guard", "members": ["half guard", "knee cut", "side control",
                                              "underhook"],
             "type_vector": [], "size": 4, "system_elo": 1140.0,
             "transition_count": 4, "internal_edges": []},
        ],
    }


def test_systems_section_rendered() -> None:
    p = _profile()
    p["_systems"] = _systems()
    p["_analogues"] = [
        {"athlete": "Craig Jones", "aggregate_similarity": 0.87,
         "dominant_type": "submission", "system_count": 2, "best_match": None},
        {"athlete": "Nicholas Meregali", "aggregate_similarity": 0.61,
         "dominant_type": "control", "system_count": 3, "best_match": None},
    ]
    page = render_profile_page(p)
    assert "Submission (back control)" in page
    assert "back control" in page          # hub named
    assert "5" in page and "7" in page     # size + transition count
    assert "grapple-craig-jones.html" in page
    assert "87%" in page                   # similarity as %
    assert "1520" not in page              # raw system elo never shown
    assert "100%" in page and "75%" in page  # strength relative to strongest system


def test_systems_section_absent_without_data() -> None:
    page = render_profile_page(_profile())
    assert "sysgrid" not in page
    assert "Grapples most like" not in page


def test_dilemma_forks_rendered_inside_systems_section() -> None:
    p = _profile()
    p["_systems"] = _systems()
    p["_dilemmas"] = [
        {"node": "Mount", "branches": [["Armbar", 0.71], ["Triangle", 0.64]]},
        {"node": "Closed Guard", "branches": [["Sweep", 0.4], ["Kimura", 0.3]]},
    ]
    page = render_profile_page(p)
    assert "dilemma" in page.lower()
    assert "Armbar" in page and "Triangle" in page
    assert "0.71" not in page  # raw PtV never shown


def test_no_forks_without_dilemma_data() -> None:
    p = _profile()
    p["_systems"] = _systems()
    page = render_profile_page(p)
    assert "fork" not in page.lower()
