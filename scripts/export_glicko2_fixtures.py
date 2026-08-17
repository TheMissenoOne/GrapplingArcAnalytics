"""Fixtures douradas do core Glicko-2 — o contrato numérico entre Python e TypeScript.

Wave 9 etapa 1 (``docs/rating_v2/02_PLANO_DE_EXECUCAO.md``). Gera casos determinísticos a partir
de ``analysis/rating_v2/glicko2.py`` e grava nos DOIS repositórios: aqui e no App. A fixture é o
contrato — contrato que mora só de um lado não é contrato.

Quando o teste do App falhar contra esta fixture, o significado é **a matemática divergiu entre as
duas implementações**. O conserto é regenerar dos dois lados depois de achar a causa, nunca ajustar
o esperado até passar.

    uv run python -m scripts.export_glicko2_fixtures

Sem banco, sem rede, sem relógio: reexecutar produz byte-idêntico.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from analysis.rating_v2.config import EngineConfig
from analysis.rating_v2.glicko2 import update_period
from analysis.rating_v2.models import Observation, RatingState

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_OUT = ROOT / "data" / "rating_v2" / "glicko2_golden.json"
APP_OUT = ROOT.parent / "GrapplingArcApp" / "src" / "services" / "__fixtures__" / "glicko2Golden.json"

TAU = 0.5

#: (nome, estado inicial, observações). Cada caso existe para pegar uma classe de erro
#: diferente — o exemplo publicado sozinho não prova quase nada, um porte pode acertá-lo
#: e errar tudo o mais.
CASES: list[tuple[str, RatingState, list[Observation]]] = [
    # O gate do doc 06 do bundle: 1464.06 / 151.52 / 0.059996.
    (
        "published_example",
        RatingState(1500.0, 200.0, 0.06),
        [
            Observation(1400.0, 30.0, 1.0),
            Observation(1550.0, 100.0, 0.0),
            Observation(1700.0, 300.0, 0.0),
        ],
    ),
    # Período sem observação: só o alargamento de RD por inatividade (ADR-09).
    ("inactive_period", RatingState(1750.0, 250.0, 0.06), []),
    ("single_win", RatingState(1750.0, 250.0, 0.06), [Observation(1750.0, 250.0, 1.0)]),
    ("single_loss", RatingState(1750.0, 250.0, 0.06), [Observation(1750.0, 250.0, 0.0)]),
    ("single_draw", RatingState(1750.0, 250.0, 0.06), [Observation(1750.0, 250.0, 0.5)]),
    # Peso != 1 (ADR-03: evidência de nó entra reduzida).
    ("weighted_observation", RatingState(1750.0, 250.0, 0.06),
     [Observation(1800.0, 120.0, 1.0, 0.25)]),
    # RD baixo: a ponta da escala onde o corpus real tem os atletas medidos (Gordon Ryan ~58).
    ("low_rd_state", RatingState(2200.0, 58.0, 0.06), [Observation(1900.0, 150.0, 1.0)]),
    # Mesmo conjunto em duas ordens — o esperado é idêntico. Se divergir, o porte quebrou a
    # semântica de período (todos usam o estado do início do período).
    (
        "order_a",
        RatingState(1750.0, 250.0, 0.06),
        [Observation(1600.0, 100.0, 1.0), Observation(1900.0, 80.0, 0.0),
         Observation(1750.0, 200.0, 0.5)],
    ),
    (
        "order_b",
        RatingState(1750.0, 250.0, 0.06),
        [Observation(1750.0, 200.0, 0.5), Observation(1900.0, 80.0, 0.0),
         Observation(1600.0, 100.0, 1.0)],
    ),
]

#: Encadeado: erro de acumulação só aparece acumulando.
CHAIN_START = RatingState(1750.0, 250.0, 0.06)
CHAIN_PERIODS: list[list[Observation]] = [
    [Observation(1700.0, 120.0, 1.0)],
    [],  # inatividade no meio da carreira
    [Observation(1850.0, 90.0, 0.0), Observation(1650.0, 140.0, 1.0)],
    [Observation(2000.0, 70.0, 0.5)],
]


def _state(s: RatingState) -> dict[str, float]:
    return {"rating": s.rating, "deviation": s.deviation, "volatility": s.volatility}


def build_fixture(tau: float = TAU) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, start, obs in CASES:
        cases.append({
            "name": name,
            "input": {"state": _state(start), "observations": [asdict(o) for o in obs]},
            "expected": _state(update_period(start, obs, tau=tau)),
        })

    steps: list[dict[str, Any]] = []
    state = CHAIN_START
    for index, obs in enumerate(CHAIN_PERIODS):
        state = update_period(state, obs, tau=tau)
        steps.append({
            "period": index,
            "observations": [asdict(o) for o in obs],
            "expected": _state(state),
        })
    cases.append({
        "name": "chained_periods",
        "input": {"state": _state(CHAIN_START), "periods": [s["observations"] for s in steps]},
        "expected_per_period": [s["expected"] for s in steps],
    })

    return {
        "engine_version": EngineConfig().engine_version,
        "generated_from": "GrapplingArcAnalytics/scripts/export_glicko2_fixtures.py",
        "tau": tau,
        "cases": cases,
    }


def render(fixture: dict[str, Any]) -> str:
    # sort_keys + indent fixo: o arquivo é diffável e a regeneração é byte-idêntica.
    return json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="não escreve; falha se o que está em disco divergir do gerado")
    args = ap.parse_args()

    text = render(build_fixture())
    targets = [ANALYTICS_OUT, APP_OUT]
    if args.check:
        for path in targets:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                print(f"DIVERGENTE: {path}")
                return 1
        print("fixtures em dia")
        return 0
    for path in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"escrito {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
