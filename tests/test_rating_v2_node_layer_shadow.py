"""A camada de nó do rating V2 fica em sombra, e isso é decisão medida — não descuido.

ADR-03 (``docs/rating_v2/01_DECISOES.md``) fixou o critério de aceitação ANTES do sweep e o
sweep rodou em 2026-08-17 (``reports/rating_v2/node-sweep.json``, 36 combinações, split
temporal treina ≤2024 / prediz 2025). O resultado não foi o pessimista "nenhum peso melhora":
as 36 melhoram o log loss. Foi outra coisa, e pior de resolver — **o critério 2 não tem dado**.

Nenhum nó atinge RD < 150 em 34 das 36 células; a única com amostra tem n=6. Fixar parâmetro de
produção em seis observações de calibração é inventar evidência para justificar a feature, que é
exatamente o que o ADR-03 proíbe. Some-se a isso a mediana de **1 luta observada por nó** e 84%
dos nós vistos uma única vez.

Então as três tabelas da alembic 0036 — ``athlete_node_rating_states_v2``,
``athlete_constellations_v2``, ``athlete_constellation_members_v2`` — são **esquema reservado**,
não estado. Os escritores existem (``analysis/rating_v2/persist.py``) e são chamados só por
testes. Nada no repo produz a lista ``node_states`` que eles consomem: ``run_node_replay``
devolve ``{coverage, total_node_bouts, sweep}``, que é o estudo de calibração, não estado por
atleta.

Este teste é o que impede a ambiguidade de voltar. Se alguém ligar um produtor, ele falha, e
reabrir a camada de nó vira decisão explícita com o ADR-03 na mão — o que exige o corpus ter
crescido: mediana de lutas por nó acima de 1 e nós com RD < 150 em número suficiente para
testar calibração de verdade.
"""

from __future__ import annotations

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ESCRITORES = ("persist_node_states", "persist_constellations")


def test_as_tabelas_de_no_nao_tem_produtor_fora_de_teste() -> None:
    """Só ``persist.py`` (que define) pode mencionar os escritores em código de produção."""
    ofensores = [
        str(arquivo.relative_to(RAIZ))
        for diretorio in ("analysis", "scripts", "export", "grapplemap", "realtime")
        for arquivo in (RAIZ / diretorio).rglob("*.py")
        if arquivo.name != "persist.py"
        and any(escritor in arquivo.read_text(encoding="utf-8") for escritor in ESCRITORES)
    ]

    assert ofensores == [], (
        "a camada de nó do rating V2 está em sombra por decisão do ADR-03 (critério 2 sem dado, "
        "mediana de 1 luta por nó). Ligar um produtor exige reabrir o ADR. "
        f"Encontrado em: {ofensores}"
    )


def test_o_sweep_do_adr_03_continua_dizendo_o_que_a_decisao_afirma() -> None:
    """O artefato medido é a base da decisão; se ele mudar, a decisão precisa ser revista.

    Não trava os números exatos — trava as DUAS afirmações em que o ADR-03 se apoia: o log loss
    melhora em toda a grade, e a calibração continua sem amostra suficiente para desempatar.
    """
    sweep = json.loads((RAIZ / "reports" / "rating_v2" / "node-sweep.json").read_text())["sweep"]

    assert len(sweep) == 36, "a grade do ADR-03 é 4 pesos × 3 RD iniciais × 3 tau"

    melhoram = [c for c in sweep if c["criterion_1_log_loss"]["improves"]]
    assert len(melhoram) == len(sweep), (
        "o ADR-03 registra que TODAS as combinações melhoram o log loss; se isso deixou de valer, "
        "a decisão de manter a camada em sombra precisa ser reescrita, não só reconfirmada"
    )

    # Critério 2: o desempate que não existe. Enquanto nenhuma célula tiver amostra de verdade,
    # não há vencedor a fixar.
    com_amostra = [
        c for c in sweep if c["criterion_2_calibration"]["low_rd_node_observations"] >= 30
    ]
    assert com_amostra == [], (
        "alguma célula passou a ter amostra de calibração utilizável — é a condição que o ADR-03 "
        "nomeia para reabrir a camada de nó. Reabra o ADR em vez de deixar este teste passar."
    )

    # Critério 4: a razão de fundo. Um nó visto uma vez não sustenta rating próprio.
    medianas = {c["criterion_4_fraction_at_prior"]["median_bouts_observed"] for c in sweep}
    assert medianas == {1}, (
        "a mediana de lutas por nó saiu de 1 — a outra condição de reabertura do ADR-03"
    )
