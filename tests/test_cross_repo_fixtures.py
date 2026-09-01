"""Toda fixture dourada cross-repo, num lugar só: `--check` verde e bytes idênticos nos DOIS
repositórios.

O contrato do workspace (root `CLAUDE.md`) diz "golden byte-idêntico nos dois repos". Cada gerador
já sabe escrever nos dois lados; o que faltava era UM teste que prova que ninguém editou um lado à
mão, e que o arquivo em disco ainda é o que o gerador produz hoje. Um golden obsoleto não falha
sozinho — ele silenciosamente para de ser um contrato.

Dois testes por gerador, e eles pegam coisas diferentes:

- `--check` verde: o arquivo em disco é o que o código de HOJE gera (pega um golden obsoleto);
- bytes idênticos: os dois repositórios carregam o mesmo arquivo (pega uma edição à mão de um
  lado só, e a regeneração parcial de quem rodou o gerador com o App ausente).

`export_glicko2_fixtures` e `export_markov_weight_fixtures` já têm o seu em
`tests/test_glicko2_fixtures.py` / `tests/test_markov_weights.py` e não são duplicados aqui;
`export_taxonomy_kind_fixtures` idem, em `tests/test_taxonomy_kind.py`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT.parent / "GrapplingArcApp"
APP_FIXTURES = APP / "src" / "services" / "__fixtures__"

#: ``(generator module, analytics path, app path)`` — every Fase 5 golden of the
#: "aresta = caminho" contract (`docs/taxonomy/03_ARESTA_COMO_CAMINHO.md`).
GENERATORS: list[tuple[str, Path, Path]] = [
    (
        "scripts.export_chain_compiler_fixtures",
        ROOT / "data" / "rating" / "chain_compiler_golden.json",
        APP_FIXTURES / "chainCompilerGolden.json",
    ),
    (
        "scripts.export_path_bundling_fixtures",
        ROOT / "data" / "rating" / "path_bundling_golden.json",
        APP_FIXTURES / "pathBundlingGolden.json",
    ),
    (
        "scripts.export_path_metrics_fixtures",
        ROOT / "data" / "rating" / "path_metrics_golden.json",
        APP_FIXTURES / "pathMetricsGolden.json",
    ),
    (
        "scripts.export_flow_layout_fixtures",
        ROOT / "data" / "rating" / "flow_layout_golden.json",
        APP_FIXTURES / "flowLayoutGolden.json",
    ),
    (
        "scripts.export_map_aggregate_fixtures",
        ROOT / "data" / "rating" / "map_aggregate_golden.json",
        APP_FIXTURES / "mapAggregateGolden.json",
    ),
    (
        "scripts.export_node_key_fixtures",
        ROOT / "data" / "rating" / "node_key_golden.json",
        APP / "src" / "utils" / "__fixtures__" / "nodeKeyGolden.json",
    ),
    (
        "scripts.export_actions_parity_fixtures",
        ROOT / "data" / "rating" / "actions_parity_golden.json",
        APP_FIXTURES / "actionsParityGolden.json",
    ),
]

_IDS = [module.rsplit(".", 1)[-1] for module, _, _ in GENERATORS]


@pytest.mark.parametrize(("module", "analytics_path", "app_path"), GENERATORS, ids=_IDS)
def test_generator_check_flag_is_green(module: str, analytics_path: Path,
                                        app_path: Path) -> None:
    """A stale golden is a defect this test exists to catch: `--check` regenerates in memory and
    fails on any divergence from what is on disk."""
    del analytics_path
    if not APP.is_dir():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    del app_path
    result = subprocess.run(
        [sys.executable, "-m", module, "--check"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("module", "analytics_path", "app_path"), GENERATORS, ids=_IDS)
def test_both_repos_carry_the_same_fixture_bytes(module: str, analytics_path: Path,
                                                  app_path: Path) -> None:
    del module
    if not app_path.is_file():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    assert analytics_path.is_file(), f"golden ausente do lado Analytics: {analytics_path}"
    assert app_path.read_text(encoding="utf-8") == analytics_path.read_text(encoding="utf-8")
