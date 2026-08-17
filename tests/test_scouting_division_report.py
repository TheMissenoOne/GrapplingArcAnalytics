"""Category-level scouting report: scope filter, aggregation over athletes, dump-vs-DB
dedup and CSV shape. All in-memory — no DB, no dump modules, no PDF rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.scouting_division_report import (
    augment_corpus,
    build_division_report,
    infer_uniform,
    write_division_csv,
)

DIVISION = {
    "name": "65 kg",
    "slug": "test-65kg",
    "athletes": [{"name": "Atleta A"}, {"name": "Atleta B"}],
}
MANIFEST = {"event": "Teste", "divisions": [DIVISION]}


def ev(
    label: str, actor: str, typ: str = "submission", successful: bool | None = True
) -> dict[str, Any]:
    return {"label": label, "type": typ, "actor": actor, "successful": successful}


def bout(
    bout_id: str, participants: list[str], year: int, uniform: str,
    events: list[dict[str, Any]], winner: str | None = None, origem: str = "dump",
) -> dict[str, Any]:
    return {
        "bout_id": bout_id, "participants": participants, "year": year, "uniform": uniform,
        "events": events, "result": {"winner": winner}, "origem": origem,
    }


# --- infer_uniform --------------------------------------------------------------------

def test_infer_uniform_tabela_explicita() -> None:
    assert infer_uniform("ADCC Trials 2024") == "no_gi"
    assert infer_uniform("CJI Day 1") == "no_gi"
    assert infer_uniform("IBJJF Worlds 2023") == "gi"
    assert infer_uniform("IBJJF Pan 2022") == "gi"
    assert infer_uniform("IBJJF 2025 Top 10") == "unknown"
    assert infer_uniform("") == "unknown"
    assert infer_uniform(None) == "unknown"


def test_infer_uniform_no_gi_vence_sobre_worlds() -> None:
    assert infer_uniform("IBJJF No-Gi Worlds") == "no_gi"


# --- filtro de escopo -------------------------------------------------------------------

def test_escopo_filtra_gi_fora_do_no_gi_mas_dentro_do_unificado() -> None:
    corpus = {
        "Atleta A": [
            bout("d1", ["Atleta A", "X"], 2024, "no_gi", [ev("Armbar", "Atleta A")]),
            bout("d2", ["Atleta A", "Y"], 2024, "gi", [ev("Kimura", "Atleta A")]),
        ],
        "Atleta B": [],
    }
    report_nogi = build_division_report(MANIFEST, corpus, "no_gi", {"descartadas": 0})
    report_gi = build_division_report(MANIFEST, corpus, "gi", {"descartadas": 0})
    report_uni = build_division_report(MANIFEST, corpus, "unificado", {"descartadas": 0})
    assert report_nogi["divisoes"][0]["tabelas"]["resumo"]["lutas"] == 1
    assert report_gi["divisoes"][0]["tabelas"]["resumo"]["lutas"] == 1
    assert report_uni["divisoes"][0]["tabelas"]["resumo"]["lutas"] == 2


# --- agregação sobre 2 atletas ------------------------------------------------------------

def test_soma_agregacao_sobre_dois_atletas_e_renomeia_agentes() -> None:
    corpus = {
        "Atleta A": [bout("d1", ["Atleta A", "X"], 2024, "no_gi", [ev("Armbar", "Atleta A")])],
        "Atleta B": [bout("d2", ["Atleta B", "Y"], 2024, "no_gi", [ev("Armbar", "Atleta B")])],
    }
    report = build_division_report(MANIFEST, corpus, "no_gi", {"descartadas": 0})
    tabelas = report["divisoes"][0]["tabelas"]
    assert tabelas["resumo"]["lutas"] == 2
    assert tabelas["efetividade"]["ATLETAS DA CATEGORIA"]["FINALIZAÇÃO"]["FINALIZAÇÃO"] == 2
    assert "PRÓPRIO" not in tabelas["efetividade"]
    assert "linhas" not in tabelas
    assert "cobertura" not in tabelas  # division-level cobertura lives one level up


def test_cobertura_conta_amostra_insuficiente() -> None:
    corpus = {
        "Atleta A": [bout("d1", ["Atleta A", "X"], 2024, "no_gi", [ev("Armbar", "Atleta A")])],
        "Atleta B": [],
    }
    report = build_division_report(MANIFEST, corpus, "no_gi", {"descartadas": 0})
    por_atleta = {item["atleta"]: item for item in report["divisoes"][0]["cobertura"]["por_atleta"]}
    assert por_atleta["Atleta A"]["amostra_insuficiente"] is True  # 1 luta < MIN_SEQUENCE_BOUTS
    assert por_atleta["Atleta B"]["amostra_insuficiente"] is True  # 0 lutas


# --- dedup dump vs banco ---------------------------------------------------------------

def test_dedup_dump_vence_sobre_banco() -> None:
    corpus = {
        "Atleta A": [bout("d1", ["Atleta A", "Atleta B"], 2024, "no_gi", [])],
        "Atleta B": [],
    }
    db_bout = bout("db:1", ["Atleta A", "Atleta B"], 2024, "no_gi", [], origem="db")
    db_rows = [("Atleta A", "Atleta B", db_bout, ["Atleta A", "Atleta B"])]
    merged, stats = augment_corpus(corpus, db_rows)
    assert stats == {"descartadas": 1, "adicionadas": 0}
    assert merged["Atleta A"] == corpus["Atleta A"]
    assert merged["Atleta B"] == []


def test_banco_entra_quando_nao_ha_duplicata_e_conta_dos_dois_lados() -> None:
    corpus = {"Atleta A": [], "Atleta B": []}
    db_bout = bout("db:2", ["Atleta A", "Atleta B"], 2025, "no_gi", [], origem="db")
    db_rows = [("Atleta A", "Atleta B", db_bout, ["Atleta A", "Atleta B"])]
    merged, stats = augment_corpus(corpus, db_rows)
    assert stats == {"descartadas": 0, "adicionadas": 2}
    assert merged["Atleta A"] == [db_bout]
    assert merged["Atleta B"] == [db_bout]


def test_dedup_pega_alias_quando_nome_ja_esta_canonicalizado() -> None:
    # scripts.scouting_division_report._augment_with_db resolves DB rows to the roster's
    # canonical spelling (via _resolve_roster_athletes' "focus") before calling augment_corpus
    # — this is what makes a DB row under a manifest alias dedupe against the dump's canonical
    # entry. augment_corpus itself only compares whatever names it is handed.
    corpus = {
        "Morgan Black": [bout("d1", ["Morgan Black", "Sophia Delgado"], 2024, "no_gi", [])],
    }
    db_bout = bout("db:1", ["Morgan Black", "Sophia Delgado"], 2024, "no_gi", [], origem="db")
    db_rows = [("Morgan Black", "Sophia Delgado", db_bout, ["Morgan Black"])]
    _merged, stats = augment_corpus(corpus, db_rows)
    assert stats == {"descartadas": 1, "adicionadas": 0}


def test_dedup_nao_pega_quando_nome_cru_do_banco_diverge_por_alias() -> None:
    # Contrast case: a caller that (like the pre-fix _augment_with_db) hands augment_corpus
    # the raw DB alias spelling instead of the roster canonical name gets a false negative —
    # the duplicate slips through and inflates the bout count. This is the failure the
    # canonicalization step in _augment_with_db exists to close.
    corpus = {
        "Morgan Black": [bout("d1", ["Morgan Black", "Sophia Delgado"], 2024, "no_gi", [])],
    }
    db_bout = bout("db:1", ["Mo Black", "Sophia Delgado"], 2024, "no_gi", [], origem="db")
    db_rows = [("Mo Black", "Sophia Delgado", db_bout, ["Morgan Black"])]
    _merged, stats = augment_corpus(corpus, db_rows)
    assert stats == {"descartadas": 0, "adicionadas": 1}


def test_banco_dedup_ignora_ano_diferente() -> None:
    corpus = {
        "Atleta A": [bout("d1", ["Atleta A", "Atleta B"], 2024, "no_gi", [])],
        "Atleta B": [],
    }
    db_bout = bout("db:3", ["Atleta A", "Atleta B"], 2025, "no_gi", [], origem="db")
    db_rows = [("Atleta A", "Atleta B", db_bout, ["Atleta A", "Atleta B"])]
    _merged, stats = augment_corpus(corpus, db_rows)
    assert stats == {"descartadas": 0, "adicionadas": 2}


# --- CSV -----------------------------------------------------------------------------

def test_csv_linhas_tidy(tmp_path: Path) -> None:
    corpus = {
        "Atleta A": [bout("d1", ["Atleta A", "X"], 2024, "no_gi", [ev("Armbar", "Atleta A")])],
        "Atleta B": [],
    }
    report = build_division_report(MANIFEST, corpus, "no_gi", {"descartadas": 0})
    out = tmp_path / "out.csv"
    write_division_csv(report, out)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "divisao,escopo,tabela,agente,linha,coluna,valor"
    tabelas_presentes = {line.split(",")[2] for line in lines[1:]}
    assert tabelas_presentes == {
        "resumo", "luta_em_pe", "efetividade", "tempo", "cobertura", "cobertura_atleta",
    }
    assert all(line.split(",")[0] == "test-65kg" for line in lines[1:])
    assert all(line.split(",")[1] == "no_gi" for line in lines[1:])
