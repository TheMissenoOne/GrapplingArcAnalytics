"""A fixture dourada só vale se for reprodutível — senão ela vira o esperado de si mesma."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.export_glicko2_fixtures import ANALYTICS_OUT, APP_OUT, build_fixture, render


def test_regeneracao_e_byte_identica() -> None:
    assert render(build_fixture()) == render(build_fixture())


def test_arquivos_em_disco_batem_com_o_gerador() -> None:
    """Se falhar: rode `uv run python -m scripts.export_glicko2_fixtures`.

    Analytics always owns/validates its checked-in copy. When the sibling App
    repo is present (local workspace), validate that copy too; a single-repo CI
    checkout cannot assert a file it never fetched.
    """
    esperado = render(build_fixture())
    analytics_path = Path(ANALYTICS_OUT)
    assert analytics_path.is_file(), f"fixture ausente: {analytics_path}"
    assert analytics_path.read_text(encoding="utf-8") == esperado, (
        f"desatualizada: {analytics_path}"
    )

    app_path = Path(APP_OUT)
    if app_path.is_file():
        assert app_path.read_text(encoding="utf-8") == esperado, f"desatualizada: {app_path}"


def test_as_duas_copias_sao_identicas() -> None:
    """O contrato mora nos dois repos; compare quando ambos estão no workspace."""
    app_path = Path(APP_OUT)
    if not app_path.is_file():
        pytest.skip("GrapplingArcApp sibling repo is not part of this CI checkout")
    assert Path(ANALYTICS_OUT).read_text(encoding="utf-8") == app_path.read_text(
        encoding="utf-8"
    )


def test_gate_do_exemplo_publicado() -> None:
    caso = next(c for c in build_fixture()["cases"] if c["name"] == "published_example")
    assert abs(caso["expected"]["rating"] - 1464.06) < 0.05
    assert abs(caso["expected"]["deviation"] - 151.52) < 0.05
    assert abs(caso["expected"]["volatility"] - 0.059996) < 1e-5


def test_ordem_das_observacoes_nao_altera_o_periodo() -> None:
    casos = {c["name"]: c for c in build_fixture()["cases"]}
    assert casos["order_a"]["expected"] == casos["order_b"]["expected"]


def test_periodo_sem_observacao_alarga_rd_e_mantem_rating() -> None:
    caso = next(c for c in build_fixture()["cases"] if c["name"] == "inactive_period")
    assert caso["expected"]["rating"] == caso["input"]["state"]["rating"]
    assert caso["expected"]["deviation"] > caso["input"]["state"]["deviation"]


def test_fixture_e_json_valido_e_ordenado() -> None:
    texto = Path(ANALYTICS_OUT).read_text(encoding="utf-8")
    dados = json.loads(texto)
    assert dados["cases"], "fixture vazia"
    assert texto == json.dumps(dados, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
