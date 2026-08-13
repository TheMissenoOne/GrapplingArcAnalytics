"""Cross-tabs mirroring the manual scouting spreadsheet."""

from __future__ import annotations

from typing import Any

import pytest

from analysis.scouting_tables import (
    ACOES,
    EFETIVIDADES,
    SEM_TEMPO,
    acao_de,
    build_tables,
    efetividade_de,
    inicio_de,
    tempo_bucket,
)


def bout(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "bout_id": "m:A|B|2025",
        "opponent": "B",
        "events": [],
        "result": {"winner": "A", "event": "X", "stage": "Final"},
    }
    base.update(over)
    return base


def ev(label: str, tipo: str, actor: str, **over: Any) -> dict[str, Any]:
    return {"label": label, "type": tipo, "actor": actor, **over}


# --- AÇÃO ---------------------------------------------------------------------------

@pytest.mark.parametrize(("tipo", "label", "esperado"), [
    ("submission", "Armbar", "FINALIZAÇÃO"),
    ("sweep", "Hip Bump", "RASPAGEM"),
    ("pass", "Knee Cut Pass", "PASSAGEM"),
    ("takedown", "Single Leg Takedown", "QUEDA"),
    ("control", "Mount", "MONTADA"),
    ("control", "Three-Quarter Mount", "MONTADA"),
    ("control", "Back Control", "COSTAS"),
    ("control", "Body Triangle", "COSTAS"),
])
def test_acao_mapeia_os_sete_eixos(tipo: str, label: str, esperado: str) -> None:
    assert acao_de(ev(label, tipo, "A")) == esperado
    assert esperado in ACOES


@pytest.mark.parametrize(("tipo", "label"), [
    ("guard", "Closed Guard"),        # a planilha não conta guarda como AÇÃO
    ("escape", "Guard Recovery"),
    ("control", "Front Headlock"),    # controle que não é montada nem costas
    ("transition", "Arm Drag"),
])
def test_acao_fora_dos_sete_eixos_e_none(tipo: str, label: str) -> None:
    assert acao_de(ev(label, tipo, "A")) is None


# --- EFETIVIDADE --------------------------------------------------------------------

def test_finalizacao_exige_submissao_concluida() -> None:
    assert efetividade_de(ev("Armbar", "submission", "A", successful=True)) == "FINALIZAÇÃO"
    # uma raspagem que entrou não é finalização, e sem placar não vira ponto
    assert efetividade_de(ev("Sweep", "sweep", "A", successful=True)) == "CONCRETIZADA"


def test_sem_placar_nunca_inventa_ponto_ou_vantagem() -> None:
    for event in (ev("Sweep", "sweep", "A", successful=True),
                  ev("Guard Pass", "pass", "A"),
                  ev("Mount", "control", "A", successful=False)):
        assert efetividade_de(event) not in {"PONTO", "VANTAGEM"}


def test_ponto_e_vantagem_so_vem_do_placar_oficial() -> None:
    event = ev("Sweep", "sweep", "A", successful=True)
    assert efetividade_de(event, 2) == "PONTO"
    assert efetividade_de(event, 1) == "VANTAGEM"


def test_desfecho_desconhecido_fica_nao_apurado() -> None:
    assert efetividade_de(ev("Back Control", "control", "A")) == "NÃO APURADO"
    assert efetividade_de(ev("Heel Hook", "submission", "A", successful=False)) \
        == "SEM EFETIVIDADE"


def test_placar_casa_por_ator_e_ts_exatos() -> None:
    b = bout(
        events=[ev("Sweep", "sweep", "A", ts=100), ev("Guard Pass", "pass", "A", ts=200)],
        score_events=[{"actor": "A", "ts": 100, "points": 2}],
    )
    tabela = build_tables("A", [b])
    assert tabela["efetividade"]["PRÓPRIO"]["RASPAGEM"]["PONTO"] == 1
    # o evento sem entrada no placar não herda pontuação do vizinho
    assert tabela["efetividade"]["PRÓPRIO"]["PASSAGEM"]["PONTO"] == 0
    assert tabela["efetividade"]["PRÓPRIO"]["PASSAGEM"]["NÃO APURADO"] == 1


# --- LUTA EM PÉ ---------------------------------------------------------------------

def test_puxada_unica_e_chamada_pra_guarda() -> None:
    b = bout(events=[ev("Guard Pull", "transition", "A", ts=10)])
    assert inicio_de(b) == ("A", "CHAMA PRA GUARDA")


def test_duas_puxadas_proximas_sao_chamada_dupla() -> None:
    b = bout(events=[ev("Guard Pull", "transition", "A", ts=10),
                     ev("Guard Pull", "transition", "B", ts=12)])
    assert inicio_de(b) == ("AMBOS", "CHAMADA DUPLA")


def test_puxadas_distantes_nao_viram_chamada_dupla() -> None:
    b = bout(events=[ev("Guard Pull", "transition", "A", ts=10),
                     ev("Guard Pull", "transition", "B", ts=400)])
    assert inicio_de(b) == ("A", "CHAMA PRA GUARDA")


def test_duas_puxadas_do_mesmo_atleta_nao_sao_chamada_dupla() -> None:
    b = bout(events=[ev("Guard Pull", "transition", "A", ts=10),
                     ev("Guard Pull", "transition", "A", ts=12)])
    assert inicio_de(b) == ("A", "CHAMA PRA GUARDA")


def test_queda_vence_quando_acontece_antes() -> None:
    b = bout(events=[ev("Double Leg Takedown", "takedown", "B", ts=5),
                     ev("Guard Pull", "transition", "A", ts=90)])
    assert inicio_de(b) == ("B", "QUEDA")


def test_queda_falhada_nao_tira_a_luta_do_pe() -> None:
    b = bout(events=[ev("Throw", "takedown", "B", ts=5, successful=False),
                     ev("Guard Pull", "transition", "A", ts=90)])
    assert inicio_de(b) == ("A", "CHAMA PRA GUARDA")


def test_sem_evidencia_de_comeco_retorna_none() -> None:
    assert inicio_de(bout(events=[ev("Closed Guard", "guard", "A", ts=30)])) is None


# --- TEMPO --------------------------------------------------------------------------

def test_bucket_conta_de_tras_pra_frente() -> None:
    b = bout(duration_s=600)
    assert tempo_bucket(ev("x", "pass", "A", ts=30), b) == "10-9"
    assert tempo_bucket(ev("x", "pass", "A", ts=599), b) == "1-0"


def test_bucket_desconta_o_inicio_quando_o_ts_e_absoluto() -> None:
    b = bout(duration_s=600, timing_basis="video_absolute", bout_start_s=3000)
    assert tempo_bucket(ev("x", "pass", "A", ts=3030), b) == "10-9"


def test_sem_duracao_nao_chuta_relogio() -> None:
    assert tempo_bucket(ev("x", "pass", "A", ts=30), bout()) == SEM_TEMPO


def test_evento_depois_do_fim_nao_estoura_o_bucket() -> None:
    assert tempo_bucket(ev("x", "pass", "A", ts=900), bout(duration_s=600)) == "1-0"


# --- agregado -----------------------------------------------------------------------

def test_resumo_separa_vitoria_derrota_e_sem_resultado() -> None:
    bouts = [
        bout(result={"winner": "A"}),
        bout(result={"winner": "B"}),
        bout(result={}),
    ]
    assert build_tables("A", bouts)["resumo"] == {
        "lutas": 3, "vitorias": 1, "derrotas": 1, "sem_resultado": 1,
    }


def test_agente_separa_o_atleta_do_adversario() -> None:
    b = bout(events=[ev("Armbar", "submission", "A", successful=True),
                     ev("Guard Pass", "pass", "B", successful=False)])
    tabela = build_tables("A", [b])
    assert tabela["efetividade"]["PRÓPRIO"]["FINALIZAÇÃO"]["FINALIZAÇÃO"] == 1
    assert tabela["efetividade"]["ADVERSÁRIO"]["PASSAGEM"]["SEM EFETIVIDADE"] == 1
    assert tabela["efetividade"]["ADVERSÁRIO"]["FINALIZAÇÃO"]["FINALIZAÇÃO"] == 0


def test_posicao_e_a_ultima_guarda_ou_controle_estabelecida() -> None:
    b = bout(events=[ev("Closed Guard", "guard", "A", ts=10),
                     ev("Armbar", "submission", "A", ts=20, successful=False)])
    linhas = build_tables("A", [b])["linhas"]
    assert linhas[0]["posicao"] is None          # nada estabelecido ainda
    assert linhas[1]["posicao"] == "Closed Guard"
    assert linhas[1]["golpe"] == "Armbar"


def test_eventos_fora_dos_sete_eixos_sao_contados_nao_descartados() -> None:
    b = bout(events=[ev("Closed Guard", "guard", "A", ts=10),
                     ev("Guard Recovery", "escape", "B", ts=20)])
    tabela = build_tables("A", [b])
    assert tabela["cobertura"]["eventos"] == 2
    assert tabela["cobertura"]["eventos_fora_das_acoes"] == {"guard": 1, "escape": 1}
    assert all(sum(v.values()) == 0 for v in tabela["efetividade"]["PRÓPRIO"].values())


def test_tempo_ordena_do_maior_para_o_menor_com_sem_tempo_no_fim() -> None:
    b = bout(duration_s=600, events=[
        ev("Guard Pass", "pass", "A", ts=30),     # 10-9
        ev("Sweep", "sweep", "A", ts=560),        # 1-0
    ])
    b2 = bout(events=[ev("Armbar", "submission", "A", ts=5)])   # sem duração
    assert list(build_tables("A", [b, b2])["tempo"]["PRÓPRIO"]) == ["10-9", "1-0", SEM_TEMPO]


def test_todas_as_efetividades_aparecem_em_toda_celula() -> None:
    tabela = build_tables("A", [bout()])
    for acao in ACOES:
        assert set(tabela["efetividade"]["PRÓPRIO"][acao]) == set(EFETIVIDADES)


def test_adversario_e_o_outro_participante_nao_o_campo_do_dump() -> None:
    # a luta está no dump sob o nome de B, então bout["opponent"] é a própria A
    b = bout(opponent="A", participants=["B", "A"],
             events=[ev("Armbar", "submission", "A", ts=10, successful=True)])
    assert build_tables("A", [b])["linhas"][0]["adversario"] == "B"
    assert build_tables("B", [b])["linhas"][0]["adversario"] == "A"
