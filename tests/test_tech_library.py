"""Tech library export tests — pure functions + synthetic effectiveness."""

from __future__ import annotations

import pandas as pd

from export.tech_library import (
    DEFAULT_PT_TRANSLATIONS,
    _generate_variations,
    _normalize_name,
    _resolve_aliases,
    build_effectiveness,
    build_technique_library,
)


def test_normalize_name() -> None:
    assert _normalize_name("  D'Arce Choke! ") == "darce choke"
    assert _normalize_name("Rear-Naked Choke") == "rearnaked choke"
    assert _normalize_name("Heel   Hook") == "heel hook"


def test_resolve_aliases() -> None:
    assert _resolve_aliases("rnc") == "rear naked choke"
    assert _resolve_aliases("mata leao") == "rear naked choke"
    assert _resolve_aliases("inside heel hook") == "heel hook"
    assert _resolve_aliases("armbar") == "armbar"  # passthrough


def _synthetic_adcc() -> pd.DataFrame:
    rows = []
    # 4 RNC wins across stages/classes/sexes, 1 guillotine, 1 no-sub
    for stage, wc, sex, year in [
        ("F", "77", "M", 2017),
        ("SF", "88", "M", 2019),
        ("R1", "66F", "F", 2019),
        ("SPF", "99", "M", 2022),
    ]:
        rows.append(("rear naked choke", stage, wc, sex, year))
    rows.append(("guillotine", "R1", "77", "M", 2017))
    df = pd.DataFrame(rows, columns=["submission", "stage", "weight_class", "sex", "year"])
    df["winner_points"] = [3, 0, -1, 2, 0]
    df.loc[len(df)] = [None, "F", "77", "M", 2017, 0]  # non-sub match ignored
    return df


def test_build_effectiveness_scores() -> None:
    eff = build_effectiveness(_synthetic_adcc())
    assert set(eff) == {"rear naked choke", "guillotine"}
    rnc = eff["rear naked choke"]
    assert rnc["sub_count"] == 4
    assert rnc["weight_class_span"] == 4
    assert rnc["sex_span"] == 2
    assert rnc["finals_rate"] == 0.5  # F + SPF of 4
    assert 0 < rnc["effectiveness_score"] <= 1
    # Below min_sub_count=3 → floored score path
    assert eff["guillotine"]["sub_count"] == 1
    assert eff["guillotine"]["effectiveness_score"] <= rnc["effectiveness_score"]


def test_build_library_dedup_and_sort() -> None:
    tech_df = pd.DataFrame(
        {
            "technique_name": ["Armbar", "Armbar", "Scissor Sweep"],
            "Origin": ["BJJ", "BJJ", "BJJ"],
            "Type": ["Submissions", "Submissions", "Sweeps"],
        }
    )
    eff = build_effectiveness(_synthetic_adcc())
    lib = build_technique_library(tech_df, eff, existing_nodes=[])
    names = [e["translations"]["en"] for e in lib]
    assert names.count("Armbar") == 1  # dedup
    assert "Rear Naked Choke" in names  # ADCC-only added
    # Scored entries sort before unscored
    scored = [i for i, e in enumerate(lib) if "effectiveness" in e]
    unscored = [i for i, e in enumerate(lib) if "effectiveness" not in e]
    assert max(scored) < min(unscored)


def test_adcc_only_entries_marked_submission() -> None:
    eff = build_effectiveness(_synthetic_adcc())
    lib = build_technique_library(pd.DataFrame(columns=["technique_name", "Origin", "Type"]),
                                  eff, existing_nodes=[])
    assert all(e["type"] == "submission" for e in lib)
    assert all(e["source"] == "adcc_submission_data" for e in lib)


def test_pt_translations_for_adcc_short_forms() -> None:
    # Audit fix: short-form keys must exist (doc §2.3)
    for key, pt in [
        ("guillotine", "Guilhotina"),
        ("triangle", "Triângulo"),
        ("ezekiel", "Ezequiel"),
        ("headlock", "Gravata"),
        ("wristlock", "Chave de Pulso"),
        ("leg lock", "Chave de Perna"),
    ]:
        assert DEFAULT_PT_TRANSLATIONS.get(key) == pt, key


def test_generate_variations_short_forms() -> None:
    # Audit fix: short-form alt keys must yield >=2 variations (doc §2.4)
    assert "guillotine choke" in _generate_variations("Guillotine", "Guilhotina")
    assert "triangle choke" in _generate_variations("Triangle", "Triângulo")
    assert "kata gatame" in _generate_variations("Katagatame", "Katagatame")
    assert len(_generate_variations("Headlock", "Gravata")) >= 2
    # dedup: en==pt yields one base entry
    assert _generate_variations("Twister", "Twister")[0] == "twister"
