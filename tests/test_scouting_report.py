from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.scouting_report import (
    GenerationBlocked,
    ManifestError,
    PdfError,
    analyse_athlete,
    audit_manifest,
    build_identity,
    build_matchup,
    chrome_command,
    collect_bouts,
    generate_reports,
    load_manifest,
    main,
    render_html,
    render_json,
    render_pdf,
    write_reports,
)


def event(
    label: str,
    actor: str,
    typ: str = "transition",
    successful: bool | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {"label": label, "type": typ, "actor": actor}
    if successful is not None:
        value["successful"] = successful
    return value


def row(
    athlete: str,
    opponent: str,
    year: int,
    events: list[dict[str, object]],
    **extra: object,
) -> dict[tuple[str, int], dict[str, object]]:
    return {
        (athlete, year): {
            "opponent": opponent,
            "events": events,
            **extra,
        }
    }


def athlete(name: str, aliases: list[str], sources: list[dict[str, object]]) -> dict[str, object]:
    return {
        "name": name,
        "country": "Brasil",
        "qualification": "Teste",
        "aliases": aliases,
        "sources": sources,
    }


def manifest(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "event": "ADCC 2026",
        "divisions": [
            {
                "name": "65 kg",
                "slug": "adcc-2026-65kg",
                "own_athlete": entries[0]["name"],
                "athletes": entries,
            }
        ],
    }


def loader(modules: dict[str, list[dict[tuple[str, int], dict[str, object]]]]):
    def load(name: str) -> SimpleNamespace:
        return SimpleNamespace(RAW=modules[name])

    return load


def test_identity_merges_aliases_and_actor_annotations() -> None:
    data = manifest(
        [
            athlete("Ana Carolina Vieira", ["Ana Carolina Viera"], []),
            athlete("Sarah Galvao", ["Sarah Galvão"], []),
            athlete("Morgan Black", ["Mo Black"], []),
        ]
    )
    identity = build_identity(data)

    assert identity.resolve("Ana Carolina Viera") == "Ana Carolina Vieira"
    assert identity.resolve("Sarah Galvão (Final)") == "Sarah Galvao"
    assert identity.resolve("Mo Black") == "Morgan Black"


def test_collects_only_selected_bouts_and_rejects_unsafe_modules() -> None:
    source = "scripts.dumps.synthetic"
    raw = [
        row("Ana Carolina Viera", "A", 2024, [event("Single Leg", "Ana Carolina Viera")]),
        row("Ana Carolina Vieira", "B", 2025, [event("Body Lock", "Ana Carolina Vieira")]),
    ]
    entry = athlete(
        "Ana Carolina Vieira",
        ["Ana Carolina Viera"],
        [{
            "module": source,
            "bouts": [{"a_name": "Ana Carolina Viera", "opponent": "A", "year": 2024}],
        }],
    )
    data = manifest([entry])

    corpus, issues = collect_bouts(data, loader({source: raw}))

    assert issues == []
    assert [bout["opponent"] for bout in corpus["Ana Carolina Vieira"]] == ["A"]

    entry["sources"] = [{"module": "evil.module"}]
    with pytest.raises(ManifestError, match="scripts.dumps"):
        collect_bouts(data, loader({}))


def test_omitted_selector_uses_only_module_rows_with_athlete_participation() -> None:
    source = "scripts.dumps.synthetic"
    raw = [
        row("Rival", "Mo Black", 2024, [event("Arm Drag", "Mo Black")]),
        row("Someone", "Else", 2024, [event("Sweep", "Someone")]),
    ]
    data = manifest([athlete("Morgan Black", ["Mo Black"], [{"module": source}])])

    corpus, issues = collect_bouts(data, loader({source: raw}))

    assert issues == []
    assert len(corpus["Morgan Black"]) == 1
    assert corpus["Morgan Black"][0]["bout_id"].startswith(f"{source}:")


def test_unknown_actor_is_reported_and_never_attributed() -> None:
    source = "scripts.dumps.synthetic"
    raw = [row("Livia Barasine", "Rival", 2026, [event("Single Leg", "Narrator")])]
    data = manifest([athlete("Livia Barasine", [], [{"module": source}])])

    corpus, issues = collect_bouts(data, loader({source: raw}))

    assert corpus["Livia Barasine"][0]["events"] == []
    assert issues[0]["code"] == "unknown_actor"
    assert issues[0]["actor"] == "Narrator"


def test_outcomes_are_tristate_and_gates_suppress_weak_claims() -> None:
    bouts = [
        {
            "bout_id": "m1",
            "participants": ["Livia Barasine", "A"],
            "events": [
                event("Single Leg", "Livia Barasine", "takedown"),
                event("Arm Drag", "Livia Barasine", successful=True),
            ],
        },
        {
            "bout_id": "m2",
            "participants": ["Livia Barasine", "B"],
            "events": [event("Single Leg", "Livia Barasine", "takedown")],
        },
    ]

    analysis = analyse_athlete("Livia Barasine", bouts)

    single = analysis["raw_support"]["labels"]["Single Leg"]
    assert single["outcomes"] == {"true": 0, "false": 0, "unknown": 2}
    assert not any(f["kind"] == "tendency" for f in analysis["facts"])
    assert not any(f["kind"] == "success_rate" for f in analysis["facts"])


def test_supported_facts_transitions_sections_and_evidence() -> None:
    bouts = []
    for i in range(3):
        bouts.append(
            {
                "bout_id": f"m{i}",
                "participants": ["Livia Barasine", f"Rival {i}"],
                "events": [
                    event("Arm Drag", "Livia Barasine", successful=i != 2),
                    event("Single Leg", "Livia Barasine", "takedown", successful=i != 2),
                    event("Back Control", "Livia Barasine", "control"),
                ],
            }
        )

    analysis = analyse_athlete("Livia Barasine", bouts)
    facts = analysis["facts"]

    assert analysis["raw_support"]["section_counts"]["standing"] == 6
    assert analysis["raw_support"]["section_counts"]["back_and_finishing"] == 3
    assert any(f["kind"] == "tendency" and f["label"] == "Arm Drag" for f in facts)
    assert any(f["kind"] == "transition" and f["from"] == "Arm Drag" for f in facts)
    assert any(f["kind"] == "success_rate" and f["label"] == "Single Leg" for f in facts)
    for fact in facts:
        assert fact["source_bouts"]
        if fact["kind"] in {"tendency", "transition", "predominance"}:
            assert len(fact["source_bouts"]) >= 2


def test_absence_is_observational_not_categorical() -> None:
    bouts = [
        {"bout_id": "m1", "participants": ["Livia", "A"], "events": []},
        {"bout_id": "m2", "participants": ["Livia", "B"], "events": []},
        {"bout_id": "m3", "participants": ["Livia", "C"], "events": []},
    ]
    analysis = analyse_athlete("Livia", bouts)

    assert all("não observado em 3 lutas" in f["statement"] for f in analysis["facts"])
    assert not any("não faz" in f["statement"] for f in analysis["facts"])
    assert analyse_athlete("Livia", [])["facts"] == []


def test_score_and_last_second_require_complete_official_data() -> None:
    base = {
        "bout_id": "m1",
        "participants": ["Ana", "Rival"],
        "events": [],
        "duration_s": 600,
        "official_score": {"Ana": 2, "Rival": 0},
    }
    incomplete = analyse_athlete("Ana", [base])
    assert not any(
        f["kind"] in {"official_score", "last_second_score"} for f in incomplete["facts"]
    )

    complete = analyse_athlete(
        "Ana",
        [{**base, "score_events": [{"actor": "Ana", "points": 2, "ts": 600}]}],
    )
    assert any(f["kind"] == "official_score" for f in complete["facts"])
    assert any(f["kind"] == "last_second_score" for f in complete["facts"])


def test_overtime_and_behavior_claims_only_use_explicit_extensions() -> None:
    bouts = []
    for i in range(3):
        bouts.append(
            {
                "bout_id": f"m{i}",
                "participants": ["Nadia", f"Rival {i}"],
                "events": [event("Single Leg", "Nadia", "takedown")],
                "overtime_start_s": 300,
                "scouting_observations": [
                    {
                        "actor": "Nadia",
                        "kind": "setup",
                        "value": "single leg sem preparo",
                        "phase": "overtime",
                    }
                ],
            }
        )

    facts = analyse_athlete("Nadia", bouts)["facts"]

    assert any(f["kind"] == "observation" and "sem preparo" in f["statement"] for f in facts)
    assert not any(f["kind"] == "observation" for f in analyse_athlete("Nadia", bouts[:1])["facts"])


def test_matchup_emits_response_or_gap_with_structured_evidence() -> None:
    opponent = {
        "athlete": "Rival",
        "facts": [
            {
                "id": "rival:tendency:arm-drag",
                "kind": "tendency",
                "label": "Arm Drag",
                "count": 5,
                "source_bouts": ["r1", "r2", "r3"],
            }
        ],
    }
    own_with_response = {
        "athlete": "Livia",
        "facts": [
            {
                "id": "livia:response:arm-drag:sprawl",
                "kind": "response",
                "condition_label": "Arm Drag",
                "label": "Sprawl",
                "count": 3,
                "source_bouts": ["o1", "o2"],
            },
            {
                "id": "livia:evidence-coverage",
                "kind": "evidence_coverage",
                "count": 15,
                "evidence_grade": "sustentado",
                "source_bouts": ["o1", "o2", "o3"],
            },
        ],
    }

    conclusion = build_matchup(opponent, own_with_response)[0]
    assert conclusion["evidence_grade"] == "limitado"
    assert conclusion["opponent_fact_ids"] == ["rival:tendency:arm-drag"]
    assert conclusion["own_athlete_fact_ids"] == ["livia:response:arm-drag:sprawl"]
    assert conclusion["counterevidence"] == []
    assert set(conclusion["source_bouts"]) == {"r1", "r2", "r3", "o1", "o2"}

    own_coverage = own_with_response["facts"][1]
    gap = build_matchup(opponent, {"athlete": "Livia", "facts": [own_coverage]})[0]
    assert gap["kind"] == "evidence_gap"
    assert "lacuna de evidência" in gap["statement"]
    assert gap["own_athlete_fact_ids"] == ["livia:evidence-coverage"]
    assert all(
        item["opponent_fact_ids"] and item["own_athlete_fact_ids"]
        for item in (conclusion, gap)
    )


def test_matchup_risk_or_opportunity_requires_explicit_outcomes_on_both_sides() -> None:
    threat = {
        "id": "r:tendency:arm-drag",
        "kind": "tendency",
        "label": "Arm Drag",
        "count": 5,
        "source_bouts": ["r1", "r2", "r3"],
    }
    opponent_rate = {
        "id": "r:success-rate:arm-drag",
        "kind": "success_rate",
        "label": "Arm Drag",
        "rate": 0.4,
        "explicit_outcomes": 5,
        "source_bouts": ["r1", "r2", "r3"],
    }
    response = {
        "id": "o:response:arm-drag:sprawl",
        "kind": "response",
        "condition_label": "Arm Drag",
        "label": "Sprawl",
        "count": 5,
        "source_bouts": ["o1", "o2", "o3"],
    }
    own_rate = {
        "id": "o:success-rate:sprawl",
        "kind": "success_rate",
        "label": "Sprawl",
        "rate": 0.8,
        "explicit_outcomes": 5,
        "source_bouts": ["o1", "o2", "o3"],
    }

    without_both = build_matchup(
        {"athlete": "Rival", "facts": [threat, opponent_rate]},
        {"athlete": "Livia", "facts": [response]},
    )
    assert not any(item["kind"] in {"risk", "opportunity"} for item in without_both)

    with_both = build_matchup(
        {"athlete": "Rival", "facts": [threat, opponent_rate]},
        {"athlete": "Livia", "facts": [response, own_rate]},
    )
    assessment = next(item for item in with_both if item["kind"] == "opportunity")
    assert assessment["opponent_fact_ids"] == [opponent_rate["id"]]
    assert assessment["own_athlete_fact_ids"] == [own_rate["id"]]
    assert assessment["source_bouts"] == ["o1", "o2", "o3", "r1", "r2", "r3"]


def test_audit_is_machine_readable_and_generation_gate_blocks() -> None:
    source = "scripts.dumps.synthetic"
    raw = [row("Livia", "A", 2026, [event("Arm Drag", "Livia")])]
    data = manifest([athlete("Livia", [], [{"module": source}])])

    audit = audit_manifest(data, loader({source: raw}))

    assert audit["ready"] is False
    assert audit["athletes"][0]["selected_bouts_with_sequence"] == 1
    assert audit["athletes"][0]["own_events"] == 1
    assert audit["athletes"][0]["section_counts"]["standing"] == 1
    assert audit["athletes"][0]["type_counts"] == {"transition": 1}
    assert audit["athletes"][0]["explicit_outcomes"] == 0
    assert audit["athletes"][0]["unknown_outcomes"] == 1
    assert audit["athletes"][0]["outcome_coverage"] == 0.0
    assert audit["athletes"][0]["missing_fields"] == {
        "successful": 1,
        "timestamp": 1,
        "phase": 1,
        "period": 1,
        "setup": 0,
    }
    assert audit["athletes"][0]["sources"] == [
        {"module": source, "selected_participant_rows": 1}
    ]
    with pytest.raises(GenerationBlocked, match="3 lutas.*15 eventos"):
        generate_reports(data, loader({source: raw}))


def test_json_and_html_are_deterministic_and_semantically_separated() -> None:
    report = {
        "event": "ADCC 2026",
        "division": "65 kg",
        "slug": "adcc-2026-65kg",
        "own_athlete": "Livia Barasine",
        "own_profile": {"facts": [], "raw_support": {}},
        "opponents": [
            {
                "athlete": "Rival",
                "country": "Reino Unido",
                "qualification": "Trials",
                "facts": [],
                "summary": [],
                "conclusions": [],
                "limitations": ["Amostra limitada."],
                "source_bouts": [],
            }
        ],
    }

    assert render_json(report) == render_json(report)
    html = render_html(report)
    assert html == render_html(report)
    assert "Perfil de Livia Barasine" in html
    for heading in (
        "Fatos observados",
        "Resumo factual",
        "Conclusões do sistema",
        "Limitações",
        "Lutas-fonte",
    ):
        assert heading in html
    assert "linear-gradient" not in html
    assert "#000" not in html and "#fff" not in html
    assert "@page" in html


def test_chrome_command_and_errors_without_launching_browser(tmp_path: Path) -> None:
    html = tmp_path / "report.html"
    html.write_text("<html></html>", encoding="utf-8")
    pdf = tmp_path / "report.pdf"

    command = chrome_command(Path("/usr/bin/google-chrome"), html, pdf)

    assert command == [
        "/usr/bin/google-chrome",
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        f"--print-to-pdf={pdf.resolve()}",
        html.resolve().as_uri(),
    ]
    with pytest.raises(PdfError, match="Chrome não encontrado"):
        render_pdf(html, pdf, finder=lambda _: None)

    def failed(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stderr="boom")

    with pytest.raises(PdfError, match="boom"):
        render_pdf(html, pdf, finder=lambda _: "/usr/bin/google-chrome", runner=failed)
    assert html.exists()

    def no_output(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stderr="")

    with pytest.raises(PdfError, match="não produziu o PDF"):
        render_pdf(html, pdf, finder=lambda _: "/usr/bin/google-chrome", runner=no_output)


def test_pdf_stale_final_is_not_proof_and_atomic_success_replaces_it(tmp_path: Path) -> None:
    html = tmp_path / "report.html"
    html.write_text("<html></html>", encoding="utf-8")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"stale")

    def no_new_output(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stderr="")

    with pytest.raises(PdfError, match="não produziu o PDF"):
        render_pdf(
            html,
            pdf,
            finder=lambda _: "/usr/bin/google-chrome",
            runner=no_new_output,
        )
    assert pdf.read_bytes() == b"stale"
    assert set(tmp_path.iterdir()) == {html, pdf}

    def writes_temp(command: list[str], **kwargs: object) -> SimpleNamespace:
        output_arg = next(arg for arg in command if arg.startswith("--print-to-pdf="))
        Path(output_arg.split("=", 1)[1]).write_bytes(b"fresh")
        return SimpleNamespace(returncode=0, stderr="")

    render_pdf(html, pdf, finder=lambda _: "/usr/bin/google-chrome", runner=writes_temp)
    assert pdf.read_bytes() == b"fresh"
    assert set(tmp_path.iterdir()) == {html, pdf}


def test_current_manifest_has_two_divisions_and_cli_audits_but_blocks_generation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = Path("data/scouting/adcc_2026_women.json")
    data = load_manifest(path)
    entries = [entry for division in data["divisions"] for entry in division["athletes"]]

    assert [division["slug"] for division in data["divisions"]] == [
        "ADCC-2026-65kg",
        "ADCC-2026-mais-65kg",
    ]
    assert len(entries) == 16
    identity = build_identity(data)
    assert identity.resolve("Ana Carolina Viera") == "Ana Carolina Vieira"
    assert identity.resolve("Sarah Galvao") == "Sarah Galvão"
    assert identity.resolve("Mo Black") == "Morgan Black"
    assert identity.resolve("Paige Ivette Climber") == "Paige Ivette"
    assert identity.resolve("Paige Climber") == "Paige Ivette"
    assert identity.resolve("Jocelyn Molina") == "Joslyn Molina"
    assert identity.resolve("Helena Cravar") == "Helena Crevar"

    assert main(["--manifest", str(path), "--audit"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is False
    assert main(["--manifest", str(path)]) == 2
    assert "geração bloqueada" in capsys.readouterr().err


def test_ready_profile_has_coverage_fact_and_source_evidence() -> None:
    bouts = [
        {
            "bout_id": f"m{i}",
            "participants": ["Livia", f"Rival {i}"],
            "events": (
                [event(f"Move {j}", "Livia") for j in range(8 if i == 0 else 7)]
                if i < 2 else [event("Move", "Rival 2")]
            ),
        }
        for i in range(3)
    ]
    coverage = next(
        fact for fact in analyse_athlete("Livia", bouts)["facts"]
        if fact["kind"] == "evidence_coverage"
    )
    assert coverage["bout_count"] == 3
    assert coverage["own_event_count"] == 15
    assert coverage["evidence_grade"] == "sustentado"
    assert coverage["source_bouts"] == ["m0", "m1", "m2"]


def test_audit_flags_declared_source_without_participant_rows() -> None:
    source = "scripts.dumps.empty_for_athlete"
    raw = [row("Someone", "Else", 2026, [event("Sweep", "Someone")])]
    data = manifest([athlete("Livia", [], [{"module": source}])])

    audit = audit_manifest(data, loader({source: raw}))

    assert audit["ready"] is False
    assert audit["athletes"][0]["sources"] == [
        {"module": source, "selected_participant_rows": 0}
    ]
    assert audit["issues"] == [
        {"code": "source_no_participant_bouts", "athlete": "Livia", "module": source}
    ]


@pytest.mark.parametrize(
    "selector",
    [
        {"a_name": "", "opponent": "A", "year": 2024},
        {"a_name": "Livia", "opponent": 3, "year": 2024},
        {"a_name": "Livia", "opponent": "A", "year": True},
    ],
)
def test_selector_fields_are_strict(selector: dict[str, object]) -> None:
    source = "scripts.dumps.synthetic"
    entry = athlete("Livia", [], [{"module": source, "bouts": [selector]}])
    with pytest.raises(ManifestError, match="seletor"):
        collect_bouts(manifest([entry]), loader({source: []}))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"duration_s": 0}, False),
        ({"official_score": {"Ana": 2}}, False),
        ({"official_score": {"Ana": 2, "Other": 0}}, False),
        ({"official_score": {"Ana": 2, "Rival": -1}}, False),
        ({"score_events": [{"actor": "Other", "points": 2, "ts": 10}]}, False),
        ({"score_events": [{"actor": "Ana", "points": -2, "ts": 10}]}, False),
        ({"score_events": [{"actor": "Ana", "points": 2, "ts": -1}]}, False),
        ({}, True),
    ],
)
def test_official_score_requires_strict_complete_shape(
    overrides: dict[str, object], expected: bool
) -> None:
    bout = {
        "bout_id": "m1",
        "participants": ["Ana", "Rival"],
        "events": [],
        "duration_s": 600,
        "official_score": {"Ana": 2, "Rival": 0},
        "score_events": [{"actor": "Ana", "points": 2, "ts": 600}],
        **overrides,
    }
    found = any(
        fact["kind"] == "official_score" for fact in analyse_athlete("Ana", [bout])["facts"]
    )
    assert found is expected


def test_invalid_overtime_cannot_support_observation() -> None:
    bout = {
        "bout_id": "m1",
        "participants": ["Nadia", "Rival"],
        "events": [],
        "duration_s": 300,
        "overtime_start_s": 301,
        "scouting_observations": [
            {"actor": "Nadia", "kind": "setup", "value": "sem preparo", "phase": "overtime"}
        ],
    }
    facts = analyse_athlete("Nadia", [bout, {**bout, "bout_id": "m2"},
                                              {**bout, "bout_id": "m3"}])["facts"]
    assert not any(fact["kind"] == "observation" for fact in facts)


def test_extensions_normalize_annotated_participant_actors() -> None:
    source = "scripts.dumps.synthetic"
    raw = [row(
        "Ana",
        "Rival",
        2026,
        [],
        duration_s=600,
        official_score={"Ana (Final)": 2, "Rival": 0},
        score_events=[{"actor": "Ana (Final)", "points": 2, "ts": 600}],
        scouting_observations=[
            {"actor": "Ana (Final)", "kind": "posture", "value": "ereta", "phase": "regular"}
        ],
    )]
    data = manifest([athlete("Ana", [], [{"module": source}])])

    corpus, issues = collect_bouts(data, loader({source: raw}))
    bout = corpus["Ana"][0]

    assert issues == []
    assert bout["official_score"] == {"Ana": 2, "Rival": 0}
    assert bout["score_events"][0]["actor"] == "Ana"
    assert bout["scouting_observations"][0]["actor"] == "Ana"
    assert any(fact["kind"] == "official_score" for fact in analyse_athlete("Ana", [bout])["facts"])


def test_slug_validation_and_write_containment_precede_mkdir(tmp_path: Path) -> None:
    data = manifest([athlete("Livia", [], [])])
    data["divisions"][0]["slug"] = "../escape"
    with pytest.raises(ManifestError, match="slug"):
        build_identity(data)

    report = {"slug": "../escape"}
    out = tmp_path / "reports"
    with pytest.raises(ManifestError, match="slug"):
        write_reports([report], out)
    assert not out.exists()
