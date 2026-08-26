"""Fixtures douradas do contrato Markov — mapeamento de ação + vetor de shares.

O contrato entre os dois repositórios tem exatamente DUAS peças, e esta fixture fixa as duas:

1. **``lamas_state``** — ``(type, label, successful)`` → um dos doze códigos de Lamas, ou
   ``null``. O App porta essa função em ``src/services/markovActionWeights.ts``; a tabela de
   tokens é copiada, e cópia sem fixture deriva em silêncio.
2. **``relative_shares``** — códigos + bloco de pesos → vetor que soma 1. Cada engine
   multiplica esse vetor pelo SEU próprio escalar (``athlete_elo`` por ``len(unique)``, o App
   pelo movimento da rodada); o escalar NÃO faz parte do contrato e por isso não está aqui.

O bloco de pesos usado nos casos é SINTÉTICO e vive dentro deste arquivo. Isso é deliberado:
``data/rating/markov_action_weights.json`` é um artefato medido que vai mudar quando o corpus
mudar, e uma fixture que dependesse dele falharia a cada regeração dos pesos sem que nenhuma
implementação tivesse divergido. A fixture testa a MECÂNICA, não os números do dia.

    uv run python -m scripts.export_markov_weight_fixtures
    uv run python -m scripts.export_markov_weight_fixtures --check

Sem banco, sem rede, sem relógio: reexecutar produz byte-idêntico.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from analysis.lamas_chain import STATES, lamas_state
from analysis.markov_weights import relative_shares

ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_OUT = ROOT / "data" / "rating" / "markov_weights_golden.json"
APP_OUT = (
    ROOT.parent
    / "GrapplingArcApp"
    / "src"
    / "services"
    / "__fixtures__"
    / "markovWeightsGolden.json"
)

#: Bloco sintético, um peso distinto por estado, escolhido para que nenhum par de códigos
#: possa trocar de lugar sem mudar o vetor. Valores em ordem crescente sobre ``STATES``.
SYNTHETIC_BLOCK: dict[str, float] = {
    code: round(0.25 + 0.15 * i, 4) for i, code in enumerate(STATES)
}

#: Bloco degenerado: todos os pesos iguais. O vetor tem que sair uniforme — é a identidade
#: que prova que ligar os pesos não mexe em nada quando eles não distinguem nada.
FLAT_BLOCK: dict[str, float] = {code: 2.0 for code in STATES}

#: Bloco parcial: só três códigos. Tudo o mais cai na média do bloco (regra 1).
PARTIAL_BLOCK: dict[str, float] = {"SUB": 5.0, "GPS": 3.0, "TKD": 1.0}

#: Cada caso existe para pegar uma classe de erro diferente do porte. Os rótulos são reais
#: (enumerados do corpus, ver ``lamas_chain``), não inventados.
CASES: list[tuple[str, list[dict[str, Any]], dict[str, float] | None]] = [
    # Caminho por TIPO: as quatro famílias que o tipo do corpus nomeia, attempt vs success.
    (
        "type_families_attempt_and_success",
        [
            {"type": "takedown", "label": "Single Leg", "successful": True},
            {"type": "takedown", "label": "Single Leg"},
            {"type": "sweep", "label": "Butterfly Sweep", "successful": True},
            {"type": "pass", "label": "Leg Drag", "successful": False},
            {"type": "submission", "label": "Rear Naked Choke", "successful": True},
        ],
        SYNTHETIC_BLOCK,
    ),
    # Caminho por RÓTULO: os três estados que o vocabulário de tipos não tem palavra para.
    (
        "label_paths_back_pull_clinch",
        [
            {"type": "control", "label": "Back Control", "successful": True},
            {"type": "control", "label": "Back Control"},
            {"type": "guard", "label": "Guard Pull"},
            {"type": "control", "label": "Front Headlock"},
            {"type": "transition", "label": "Arm Drag"},
        ],
        SYNTHETIC_BLOCK,
    ),
    # Overrides medidos + o gate de tipo: `escape` nunca é lido no nível do rótulo, então
    # `Back Escape` NÃO pode virar pegada de costas.
    (
        "overrides_and_type_gate",
        [
            {"type": "control", "label": "Top Control Body Lock"},
            {"type": "control", "label": "Body Triangle (Bottom)"},
            {"type": "control", "label": "Body Triangle"},
            {"type": "escape", "label": "Back Escape"},
            {"type": "guard", "label": "Closed Guard"},
        ],
        SYNTHETIC_BLOCK,
    ),
    # Acentos e pontuação: `_key` de-acentua ANTES de normalizar, e `normalizeLabel` do App
    # não faz isso — o porte precisa da etapa extra. Este caso é a única coisa que a pega.
    (
        "accents_and_punctuation",
        [
            {"type": "control", "label": "Bôdy Triângle"},
            {"type": "transition", "label": "Back-Take!"},
            {"type": "guard", "label": "Pull  Guard / Inversion"},
        ],
        SYNTHETIC_BLOCK,
    ),
    # Só ações sem código: o vetor tem que sair uniforme pela regra 1 (média do bloco),
    # não zerado.
    (
        "all_unmapped_is_uniform",
        [
            {"type": "guard", "label": "Half Guard"},
            {"type": "escape", "label": "Escape to Turtle"},
            {"type": "concept", "label": "Pressure"},
        ],
        SYNTHETIC_BLOCK,
    ),
    # A identidade: pesos iguais ⇒ vetor uniforme.
    (
        "flat_block_is_uniform",
        [
            {"type": "submission", "label": "Heel Hook", "successful": True},
            {"type": "guard", "label": "Half Guard"},
            {"type": "pass", "label": "Leg Drag"},
        ],
        FLAT_BLOCK,
    ),
    # Bloco parcial: código presente usa o próprio peso, ausente usa a média do bloco (3.0).
    (
        "partial_block_falls_back_to_mean",
        [
            {"type": "submission", "label": "Heel Hook", "successful": True},
            {"type": "sweep", "label": "Butterfly Sweep", "successful": True},
            {"type": "guard", "label": "Half Guard"},
        ],
        PARTIAL_BLOCK,
    ),
    # Artefato ausente: sem bloco, vetor uniforme, comportamento anterior preservado.
    (
        "absent_block_is_uniform",
        [
            {"type": "submission", "label": "Heel Hook", "successful": True},
            {"type": "guard", "label": "Half Guard"},
        ],
        None,
    ),
    # Vazio: nem divisão por zero nem exceção.
    ("empty_sequence", [], SYNTHETIC_BLOCK),
]


def build_fixture() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, events, block in CASES:
        codes = [lamas_state(e) for e in events]
        cases.append({
            "name": name,
            "events": events,
            "block": block,
            "expected_codes": codes,
            "expected_shares": [round(s, 12) for s in relative_shares(codes, block)],
        })
    return {
        "generated_from": "GrapplingArcAnalytics/scripts/export_markov_weight_fixtures.py",
        "contract": (
            "lamas_state(type,label,successful) -> code | null; "
            "relative_shares(codes, block) -> vector summing to 1. "
            "The per-engine scalar applied to that vector is NOT part of this contract."
        ),
        "states": list(STATES),
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
        print(f"escrito: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
