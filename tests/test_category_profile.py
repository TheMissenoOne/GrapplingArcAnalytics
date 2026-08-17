"""analysis.category_profile — pure, in-memory, no DB. Mirrors the cases the spec lists in
docs/superpowers/specs/2026-08-16-relatorio-categoria-tendencias-design.md."""

from __future__ import annotations

from analysis import category_profile as cp


def ev(athlete: str, label: str, typ: str, successful: bool | None = None,
       bout_id: str = "b1") -> dict:
    return {"athlete": athlete, "label": label, "type": typ, "successful": successful,
            "bout_id": bout_id}


# --- flattening ----------------------------------------------------------------------------

def test_flatten_own_events_ignora_evento_do_adversario() -> None:
    bouts_by_athlete = {
        "Ana": [{"bout_id": "b1", "events": [
            {"actor": "Ana", "label": "Armbar", "type": "submission", "successful": True},
            {"actor": "X", "label": "Guard Pull", "type": "guard", "successful": None},
        ]}],
    }
    flat = cp.flatten_own_events(bouts_by_athlete)
    assert len(flat) == 1
    assert flat[0]["athlete"] == "Ana"
    assert flat[0]["label"] == "Armbar"


def test_flatten_bout_events_conta_todos_os_atores() -> None:
    bouts = [{"bout_id": "b1", "events": [
        {"actor": "Ana", "label": "Armbar", "type": "submission"},
        {"actor": "X", "label": "Guard Pull", "type": "guard"},
    ]}]
    flat = cp.flatten_bout_events(bouts)
    assert {e["athlete"] for e in flat} == {"Ana", "X"}


def test_exclude_roster_from_baseline_remove_lutas_do_roster() -> None:
    baseline_bouts = [
        {"bout_id": "b1", "participants": ["Ana", "Fora"], "events": []},
        {"bout_id": "b2", "participants": ["Y", "Z"], "events": []},
    ]
    kept = cp.exclude_roster_from_baseline(baseline_bouts, ["Ana"])
    assert [b["bout_id"] for b in kept] == ["b2"]


# --- concentration / gate --------------------------------------------------------------------

def test_concentracao_corpus_concentrado_vs_distribuido() -> None:
    concentrado = [ev("Dominante", "Armbar", "submission", bout_id=f"b{i}") for i in range(9)]
    concentrado += [ev("Outra", "Armbar", "submission", bout_id="b9")]
    distribuido = [ev(f"Atleta{i}", "Armbar", "submission", bout_id=f"b{i}") for i in range(10)]

    conc = cp.concentration(concentrado)
    dist = cp.concentration(distribuido)
    assert conc["top1_share"] == 0.9
    assert conc["hhi"] > dist["hhi"]
    assert dist["hhi"] == 0.1  # 10 atletas iguais -> 10 * (0.1**2)


def test_sufficient_sample_gate_bloqueia_amostra_gi() -> None:
    # 1 luta (65kg gi real) — bem abaixo de MIN_DIVISION_BOUTS mesmo com 2 atletas.
    events = [ev("Ana", "Armbar", "submission", bout_id="b1"),
              ev("Beatriz", "Kimura", "submission", bout_id="b1")]
    assert cp.sufficient_sample(events) is False


def test_sufficient_sample_gate_passa_com_amostra_no_gi() -> None:
    events = [ev(f"Atleta{i % 3}", "Armbar", "submission", bout_id=f"b{i}") for i in range(10)]
    assert cp.sufficient_sample(events) is True


# --- profile: 3 readings -----------------------------------------------------------------

def test_perfil_peso_igual_diverge_de_ponderado_quando_atleta_domina() -> None:
    # Dominante só finaliza; a outra atleta só passa guarda. Ponderado por evento (a
    # dominante tem 9x mais eventos) fica perto de 100% finalização; peso igual dá 50/50.
    events = [ev("Dominante", "Armbar", "submission", bout_id=f"b{i}") for i in range(9)]
    events += [ev("Outra", "Passagem", "pass", bout_id="b9")]
    profile = cp.profile_by_type(events)
    assert profile["peso_igual"]["submission"] == 0.5
    assert profile["ponderado_por_evento"]["submission"] == 0.9
    assert cp.profile_diverges(profile) is True


def test_perfil_leave_one_out_remove_atleta_dominante() -> None:
    events = [ev("Dominante", "Armbar", "submission", bout_id=f"b{i}") for i in range(9)]
    events += [ev("Outra", "Passagem", "pass", bout_id="b9")]
    profile = cp.profile_by_type(events)
    assert profile["atleta_excluida"] == "Dominante"
    assert profile["leave_one_out"]["pass"] == 1.0
    assert profile["leave_one_out"].get("submission", 0.0) == 0.0


# --- lift gate + symmetry -------------------------------------------------------------------

def test_lift_gate_n_menor_que_3_fica_fora_do_ranking() -> None:
    category = [ev("Ana", "Raro", "guard", bout_id="b1"), ev("Ana", "Raro", "guard", bout_id="b2")]
    baseline = [ev("X", "Raro", "guard", bout_id="c1")] * 20
    table = cp.lift_table(category, baseline, weighting="event_weighted")
    assert table["ranking"] == []
    assert table["amostra_insuficiente"] == [{"label": "Raro", "n": 2}]


def test_lift_label_inedito_no_baseline_nao_vira_lift_infinito() -> None:
    category = [ev("Ana", "SoNoRoster", "guard", bout_id=f"b{i}") for i in range(5)]
    baseline = [ev("X", "Outro", "guard", bout_id="c1")] * 20
    table = cp.lift_table(category, baseline, weighting="event_weighted")
    assert table["ranking"] == []
    assert table["inedito_no_baseline"] == [{"label": "SoNoRoster", "n": 5, "p_categoria": 1.0}]


def test_simetria_de_ponderacao_primary_nunca_cruza() -> None:
    # Dominante só finaliza Armbar (9x); Outra só finaliza Kimura (1x). athlete_balanced dá
    # 0.5 (média de 1.0 e 0.0 pelas duas atletas); event_weighted dá 0.9 (9 de 10 eventos).
    # Se lift_table alguma vez lesse a função de share do OUTRO esquema (crossing PRIMARY
    # contra SECONDARY), um dos dois números bateria com o do outro par — eles não batem.
    category = [ev("Dominante", "Armbar", "submission", bout_id=f"b{i}") for i in range(9)]
    category += [ev("Outra", "Kimura", "submission", bout_id="b9")]
    baseline = [ev(f"B{i}", "Armbar", "submission", bout_id=f"c{i}") for i in range(5)]
    baseline += [ev(f"B{i+5}", "Kimura", "submission", bout_id=f"c{i+5}") for i in range(5)]

    primary = cp.lift_table(category, baseline, weighting="athlete_balanced")
    secondary = cp.lift_table(category, baseline, weighting="event_weighted")
    p_primary = next(r["p_categoria"] for r in primary["ranking"] if r["label"] == "Armbar")
    p_secondary = next(r["p_categoria"] for r in secondary["ranking"] if r["label"] == "Armbar")
    assert p_primary == 0.5
    assert p_secondary == 0.9
    assert p_primary != p_secondary


def test_lift_weighting_invalido_leva_erro() -> None:
    try:
        cp.lift_table([], [], weighting="metade_metade")
    except ValueError:
        pass
    else:
        raise AssertionError("esperava ValueError")


# --- bootstrap stability -------------------------------------------------------------------

def test_bootstrap_distingue_tendencia_distribuida_de_concentrada() -> None:
    # Mesma frequência total (10 ocorrências de "Passagem X"), mas espalhada por 10 atletas
    # num corpus e concentrada em 1 atleta no outro. Baseline tem o label em baixa frequência
    # (2 de 20) para o lift ser finito, não "inédito no baseline".
    baseline = [ev(f"B{i}", "Outra", "guard", bout_id=f"c{i}") for i in range(18)]
    baseline += [ev("B18", "Passagem X", "pass", bout_id="c18"),
                 ev("B19", "Passagem X", "pass", bout_id="c19")]

    distribuido = [ev(f"Atleta{i}", "Passagem X", "pass", bout_id=f"b{i}") for i in range(10)]
    distribuido += [ev(f"Atleta{i}", "Outra", "guard", bout_id=f"d{i}") for i in range(10)]

    concentrado = [ev("Dominante", "Passagem X", "pass", bout_id=f"b{i}") for i in range(10)]
    concentrado += [ev(f"Atleta{i}", "Outra", "guard", bout_id=f"d{i}") for i in range(9)]

    stab_dist = cp.bootstrap_lift_stability(
        distribuido, baseline, "Passagem X", weighting="event_weighted", seed=1,
    )
    stab_conc = cp.bootstrap_lift_stability(
        concentrado, baseline, "Passagem X", weighting="event_weighted", seed=1,
    )
    assert stab_dist["fracao_estavel"] > stab_conc["fracao_estavel"]
    assert stab_dist["estabilidade"] == "alta"
    assert stab_conc["estabilidade"] == "baixa"


def test_lift_with_stability_preenche_estabilidade_no_ranking() -> None:
    category = [ev(f"Atleta{i}", "Passagem X", "pass", bout_id=f"b{i}") for i in range(6)]
    baseline = [ev(f"B{i}", "Outra", "guard", bout_id=f"c{i}") for i in range(8)]
    baseline += [ev("B8", "Passagem X", "pass", bout_id="c8")]
    table = cp.lift_with_stability(category, baseline, weighting="event_weighted", iterations=20)
    row = next(r for r in table["ranking"] if r["label"] == "Passagem X")
    assert row["estabilidade"] in {"alta", "baixa"}


# --- effectiveness (resolved only) ----------------------------------------------------------

def test_efetividade_so_sobre_resolvidos() -> None:
    events = [
        ev("Ana", "Armbar", "submission", True, bout_id="b1"),
        ev("Ana", "Armbar", "submission", False, bout_id="b2"),
        ev("Ana", "Armbar", "submission", None, bout_id="b3"),
        ev("Ana", "Armbar", "submission", None, bout_id="b4"),
    ]
    rows = cp.effectiveness(events)
    row = rows[0]
    assert row["n"] == 4
    assert row["resolvido_pct"] == 0.5
    assert row["taxa_sucesso"] == 0.5


def test_efetividade_base_nao_apurada_nao_gera_taxa() -> None:
    events = [ev("Ana", "Queda", "takedown", None, bout_id=f"b{i}") for i in range(4)]
    rows = cp.effectiveness(events)
    assert rows[0]["resolvido_pct"] == 0.0
    assert "taxa_sucesso" not in rows[0]


# --- time gate --------------------------------------------------------------------------

def test_time_available_gate() -> None:
    assert cp.time_available({"eventos": 100, "eventos_sem_tempo": 100}) is False  # 65kg-like
    assert cp.time_available({"eventos": 100, "eventos_sem_tempo": 60}) is True   # 40% >= 30%
    assert cp.time_available({"eventos": 100, "eventos_sem_tempo": 75}) is False  # 25% < 30%
    assert cp.time_available({"eventos": 0, "eventos_sem_tempo": 0}) is False


# --- coverage marginal value --------------------------------------------------------------

def test_coverage_marginal_value_ordena_zeradas_primeiro() -> None:
    events = [ev("Ana", "Armbar", "submission", bout_id="b1")] * 5
    rows = cp.coverage_marginal_value(["Ana", "Beatriz", "Carla"], events)
    assert [r["atleta"] for r in rows[:2]] == ["Beatriz", "Carla"]
    assert rows[-1] == {"atleta": "Ana", "eventos_proprios": 5}
