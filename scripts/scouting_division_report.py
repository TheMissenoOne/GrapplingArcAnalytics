"""Category-level (not per-athlete) scouting report for one manifest's divisions.

Sources bouts from the manifest's dumps (``analysis.scouting_report.collect_bouts``) plus,
unless ``--sem-banco``, a read-only complement from ``matches`` for the manifest's own roster
(same name-resolution as ``scripts.scouting_matches_csv``). No per-athlete gate — the coverage
table (not ``generate_reports``' 3-bouts/15-events gate, which protects claims about ONE
athlete) is what tells the reader where the category-level sample is thin.

    uv run python -m scripts.scouting_division_report \\
        --manifest data/scouting/adcc_2026_women.json --out reports/adcc-2026-categoria

Writes, per requested scope, ``{out}-{nogi,gi,unificado}.{html,pdf,csv}`` next to ``--out``'s
parent directory. A Chrome/PDF failure for one scope preserves that scope's HTML, warns on
stderr, and does not stop the remaining scopes.
"""

# ruff: noqa: E501 -- HTML/CSS template strings stay readable as one block.

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from analysis import category_constellations as ccon
from analysis import category_profile as cp
from analysis import report_charts as rc
from analysis.names import athlete_key
from analysis.network_metrics import network_from_sequences
from analysis.scouting_report import (
    REPORT_CSS,
    PdfError,
    collect_bouts,
    load_manifest,
    render_pdf,
)
from analysis.scouting_tables import build_tables
from analysis.style_profile_core import MIN_DOSSIER_EVENTS, MIN_SEQUENCE_BOUTS
from db.base import get_session_factory
from db.models import Athlete, Match
from scripts.scouting_matches_csv import _roster_keys, _suspect

SCOPES = ("no_gi", "gi", "unificado")
SCOPE_SUFFIX = {"no_gi": "nogi", "gi": "gi", "unificado": "unificado"}
SCOPE_LABEL = {"no_gi": "No-Gi", "gi": "Gi", "unificado": "Unificado (todos os uniformes)"}

AGENT_LABELS = {"PRÓPRIO": "ATLETAS DA CATEGORIA", "ADVERSÁRIO": "ADVERSÁRIOS"}

CSV_COLUMNS = ["divisao", "escopo", "tabela", "agente", "linha", "coluna", "valor"]

# Explicit uniform inference from a DB match's event name. This table IS the documentation —
# extend it here, never with heuristics elsewhere. Checked in order: no-gi keywords first
# (an event containing both, e.g. "IBJJF No-Gi Worlds", is no-gi). Anything unresolved
# (including an empty event, and "IBJJF 2025 Top 10") stays "unknown" — never guessed.
NO_GI_EVENT_KEYWORDS = (
    "adcc", "cji", "wno", "polaris", "no-gi", "nogi", "pan no-gi", "women who fight",
)
GI_EVENT_KEYWORDS = ("ibjjf worlds", "ibjjf pan", "worlds")

#: Baseline corpus for the trend report (sections 1-9 in
#: docs/superpowers/specs/2026-08-16-relatorio-categoria-tendencias-design.md): elite no-gi,
#: never called "metagame global" in report text — it mixes sex, weight and eras, it is not
#: global in a statistical sense. Narrower than NO_GI_EVENT_KEYWORDS on purpose (no "no-gi"/
#: "pan no-gi"/"women who fight" — those aren't necessarily elite-caliber). Measured against
#: prod 2026-08-16: 488 matches, 7854 events.
ELITE_NO_GI_EVENT_KEYWORDS = ("adcc", "cji", "wno", "polaris")


def is_elite_no_gi_event(event: str | None) -> bool:
    text = (event or "").lower()
    return any(word in text for word in ELITE_NO_GI_EVENT_KEYWORDS)


def infer_uniform(event: str | None) -> str:
    text = (event or "").lower()
    if any(word in text for word in NO_GI_EVENT_KEYWORDS):
        return "no_gi"
    if any(word in text for word in GI_EVENT_KEYWORDS):
        return "gi"
    return "unknown"


# ── DB complement ────────────────────────────────────────────────────────────────────

def _resolve_roster_athletes(session: Any, manifest: Mapping[str, Any]) -> dict[str, str]:
    """DB Athlete.id -> canonical roster name, via the same exact+suspect match as the CSV."""
    keys = _roster_keys(manifest)
    focus: dict[str, str] = {}
    unmatched = []
    for ath in session.execute(select(Athlete)).scalars():
        hit = keys.get(athlete_key(ath.name))
        if hit:
            focus[ath.id] = hit[0]
        else:
            unmatched.append(ath)
    for ath in unmatched:
        for rkey, hit in keys.items():
            if _suspect(rkey, athlete_key(ath.name)):
                focus[ath.id] = hit[0]
                break
    return focus


def _match_to_bout(m: Match, name_a: str, name_b: str, names: Mapping[str, str]) -> dict[str, Any]:
    events = []
    for raw in m.sequence or []:
        actor = names.get(raw.get("actor_id"))
        if not actor:
            continue
        item = {"label": raw.get("label"), "type": raw.get("type"), "actor": actor,
                "successful": raw.get("successful")}
        if "ts" in raw:
            item["ts"] = raw["ts"]
        events.append(item)
    return {
        "bout_id": f"db:{m.id}",
        "participants": [name_a, name_b],
        "events": events,
        "result": {
            "winner": names.get(m.winner_id) if m.winner_id else None,
            "event": m.event, "stage": m.stage, "win_type": m.win_type,
            "submission": m.submission, "weight_class": m.weight_class,
        },
        "uniform": infer_uniform(m.event),
        "year": m.year,
        "origem": "db",
    }


def augment_corpus(
    corpus: Mapping[str, list[dict[str, Any]]],
    db_rows: Sequence[tuple[str, str, dict[str, Any], list[str]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Pure merge/dedup: ``db_rows`` = (name_a, name_b, bout, focus_names) per DB match.

    A DB match is discarded when its unordered participant pair + year already exists among
    the dump bouts (any athlete) — the dump wins. Otherwise it is appended to every roster
    athlete's list in ``focus_names`` (both, when the match is internal to the roster).
    """
    dump_pairs = {
        (frozenset(athlete_key(p) for p in bout["participants"]), bout.get("year"))
        for bouts in corpus.values() for bout in bouts
    }
    merged = {name: list(bouts) for name, bouts in corpus.items()}
    stats = {"descartadas": 0, "adicionadas": 0}
    for name_a, name_b, bout, focus_names in db_rows:
        key = (frozenset({athlete_key(name_a), athlete_key(name_b)}), bout.get("year"))
        if key in dump_pairs:
            stats["descartadas"] += 1
            continue
        for canonical in focus_names:
            merged.setdefault(canonical, []).append(bout)
            stats["adicionadas"] += 1
    return merged, stats


def _augment_with_db(
    manifest: Mapping[str, Any], corpus: Mapping[str, list[dict[str, Any]]]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    with get_session_factory()() as session:
        focus = _resolve_roster_athletes(session, manifest)
        if not focus:
            return dict(corpus), {"descartadas": 0, "adicionadas": 0}
        ids = list(focus)
        matches = session.execute(
            select(Match).where(or_(Match.athlete_a_id.in_(ids), Match.athlete_b_id.in_(ids)))
        ).scalars().all()
        all_ids = {m.athlete_a_id for m in matches} | {m.athlete_b_id for m in matches}
        names: dict[str, str] = {
            row[0]: row[1]
            for row in session.execute(
                select(Athlete.id, Athlete.name).where(Athlete.id.in_(all_ids))
            ).all()
        }
    # Roster athletes (id in focus) get their canonical manifest name — the raw DB name can
    # diverge on accent or be a manifest alias ("Sarah Galvao" vs "Sarah Galvão", "Jocelyn
    # Molina" vs roster "Joslyn Molina"). A non-roster opponent keeps the DB name as-is.
    canonical = {aid: focus.get(aid, name) for aid, name in names.items()}
    db_rows = []
    for m in matches:
        name_a, name_b = canonical.get(m.athlete_a_id), canonical.get(m.athlete_b_id)
        if not name_a or not name_b:
            continue
        focus_names = [focus[i] for i in (m.athlete_a_id, m.athlete_b_id) if i in focus]
        db_rows.append((name_a, name_b, _match_to_bout(m, name_a, name_b, canonical), focus_names))
    return augment_corpus(corpus, db_rows)


def _load_baseline_bouts(session: Any) -> list[dict[str, Any]]:
    """Elite no-gi corpus for the trend sections (ELITE_NO_GI_EVENT_KEYWORDS, ``final`` only) —
    every match, not just the manifest's roster. Roster exclusion happens per-division later
    (``category_profile.exclude_roster_from_baseline``), since each division has a different
    roster to exclude."""
    matches = session.execute(
        select(Match).where(Match.status == "final")
    ).scalars().all()
    matches = [m for m in matches if is_elite_no_gi_event(m.event)]
    if not matches:
        return []
    ids = {m.athlete_a_id for m in matches} | {m.athlete_b_id for m in matches}
    names = dict(session.execute(select(Athlete.id, Athlete.name).where(Athlete.id.in_(ids))).all())
    bouts = []
    for m in matches:
        name_a, name_b = names.get(m.athlete_a_id), names.get(m.athlete_b_id)
        if not name_a or not name_b:
            continue
        bouts.append(_match_to_bout(m, name_a, name_b, names))
    return bouts


# ── aggregation ──────────────────────────────────────────────────────────────────────

def _in_scope(bout: Mapping[str, Any], scope: str) -> bool:
    return scope == "unificado" or bout.get("uniform") == scope


def _merge_nested(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum leaves across dicts sharing the same nested-key shape (ints at leaves)."""
    out: dict[str, Any] = {}
    for item in items:
        for key, value in item.items():
            if isinstance(value, dict):
                out[key] = _merge_nested([out.get(key, {}), value])
            else:
                out[key] = out.get(key, 0) + value
    return out


def _rename_agents(d: Mapping[str, Any]) -> dict[str, Any]:
    return {AGENT_LABELS.get(k, k): v for k, v in d.items()}


def _division_tables(
    division: Mapping[str, Any], corpus: Mapping[str, list[dict[str, Any]]], scope: str
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    per_athlete_bouts: dict[str, list[dict[str, Any]]] = {}
    per_athlete_tabelas: dict[str, dict[str, Any]] = {}
    for entry in division["athletes"]:
        name = str(entry["name"])
        bouts = [bout for bout in corpus.get(name, []) if _in_scope(bout, scope)]
        per_athlete_bouts[name] = bouts
        per_athlete_tabelas[name] = build_tables(name, bouts)
    tabelas = {
        "resumo": _merge_nested([t["resumo"] for t in per_athlete_tabelas.values()]),
        "luta_em_pe": _rename_agents(
            _merge_nested([t["luta_em_pe"] for t in per_athlete_tabelas.values()])
        ),
        "efetividade": _rename_agents(
            _merge_nested([t["efetividade"] for t in per_athlete_tabelas.values()])
        ),
        "tempo": _rename_agents(_merge_nested([t["tempo"] for t in per_athlete_tabelas.values()])),
        # Section 8's time gate reuses this instead of re-deriving it from bouts.
        "cobertura_eventos": _merge_nested([t["cobertura"] for t in per_athlete_tabelas.values()]),
    }
    return tabelas, per_athlete_bouts


def _unique_bouts(per_athlete_bouts: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """An intra-division bout is stored once per participant's name; the transition network
    (section 6) needs each bout counted exactly once, so dedupe by ``bout_id``."""
    seen: dict[str, dict[str, Any]] = {}
    for bouts in per_athlete_bouts.values():
        for bout in bouts:
            seen.setdefault(str(bout.get("bout_id")), bout)
    return list(seen.values())


def _division_coverage(
    division: Mapping[str, Any], per_athlete_bouts: Mapping[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    roster_keys = {athlete_key(str(entry["name"])) for entry in division["athletes"]}
    all_bouts = [bout for bouts in per_athlete_bouts.values() for bout in bouts]
    por_origem = Counter(str(bout.get("origem", "dump")) for bout in all_bouts)
    internal = sum(
        1 for bouts in per_athlete_bouts.values() for bout in bouts
        if all(athlete_key(str(p)) in roster_keys for p in bout.get("participants", []))
    )
    por_atleta = []
    for entry in division["athletes"]:
        name = str(entry["name"])
        bouts = per_athlete_bouts.get(name, [])
        seq_bouts = [bout for bout in bouts if bout.get("events")]
        own_events = sum(
            1 for bout in bouts for event in bout.get("events", [])
            if event.get("actor") == name
        )
        por_atleta.append({
            "atleta": name, "lutas": len(bouts), "lutas_com_sequencia": len(seq_bouts),
            "eventos_proprios": own_events,
            "origem": dict(Counter(str(bout.get("origem", "dump")) for bout in bouts)),
            "amostra_insuficiente": (
                len(seq_bouts) < MIN_SEQUENCE_BOUTS or own_events < MIN_DOSSIER_EVENTS
            ),
        })
    return {
        "total_lutas": len(all_bouts),
        "por_origem": dict(por_origem),
        "lutas_com_sequencia": sum(1 for bout in all_bouts if bout.get("events")),
        "eventos": sum(len(bout.get("events", [])) for bout in all_bouts),
        "lutas_uniforme_desconhecido": sum(
            1 for bout in all_bouts if bout.get("uniform") == "unknown"
        ),
        "lutas_intra_categoria_contadas_duas_vezes": internal,
        "por_atleta": por_atleta,
    }


def _to_sequences(bouts: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    """Bout events (``actor`` = resolved name) -> the ``actor_id`` shape
    ``network_metrics.network_from_sequences`` expects. Any consistent per-athlete id works —
    the name itself is fine here (never leaves this process)."""
    return [
        [{**event, "actor_id": event.get("actor")} for event in bout.get("events", [])]
        for bout in bouts
    ]


def _division_network(
    unique_bouts: Sequence[Mapping[str, Any]],
    baseline_bouts: Sequence[Mapping[str, Any]],
    *, top: int = 12, scatter_limit: int = 15, min_occ: int = 3,
) -> dict[str, Any] | None:
    """Section 6: top transitions colored by lift vs baseline + reward-risk scatter with a
    baseline crosshair. Orchestration glue (not a ``category_profile`` pure unit) — it only
    reads ``network_metrics.network_from_sequences``'s graph attrs."""
    if not unique_bouts:
        return None
    category_graph = network_from_sequences(_to_sequences(unique_bouts))
    baseline_graph = network_from_sequences(_to_sequences(baseline_bouts)) if baseline_bouts else None
    category_total = sum(d["weight"] for _, _, d in category_graph.edges(data=True)) or 1
    baseline_total = (
        sum(d["weight"] for _, _, d in baseline_graph.edges(data=True))
        if baseline_graph is not None else 0
    )
    edges = sorted(category_graph.edges(data=True), key=lambda item: -item[2]["weight"])[:top]
    transicoes = []
    for a, b, d in edges:
        p_categoria = d["weight"] / category_total
        p_baseline, log2_lift = 0.0, None
        if baseline_graph is not None and baseline_total and baseline_graph.has_edge(a, b):
            p_baseline = baseline_graph[a][b]["weight"] / baseline_total
            log2_lift = round(math.log2(p_categoria / p_baseline), 3) if p_baseline else None
        transicoes.append({"de": a, "para": b, "weight": d["weight"],
                           "p_categoria": round(p_categoria, 4), "p_baseline": round(p_baseline, 4),
                           "log2_lift": log2_lift})

    scatter = []
    for node, d in category_graph.nodes(data=True):
        if d.get("occ", 0) < min_occ or not d.get("denom", 0):
            continue
        scatter.append((node, d["risk"] / d["denom"], d["reward"] / d["denom"], d["occ"]))
    scatter = sorted(scatter, key=lambda item: -item[3])[:scatter_limit]

    baseline_point = None
    if baseline_graph is not None and baseline_graph.number_of_nodes():
        nodes_ok = [d for _, d in baseline_graph.nodes(data=True)
                   if d.get("denom", 0) and d.get("occ", 0) >= 5]
        if nodes_ok:
            baseline_point = (
                sum(d["risk"] / d["denom"] for d in nodes_ok) / len(nodes_ok),
                sum(d["reward"] / d["denom"] for d in nodes_ok) / len(nodes_ok),
            )
    return {"transicoes": transicoes, "scatter": scatter, "baseline_point": baseline_point}


def _division_trend(
    division: Mapping[str, Any],
    per_athlete_bouts: Mapping[str, list[dict[str, Any]]],
    tabelas: Mapping[str, Any],
    baseline_bouts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Sections 1-4 and 6-9 (never 5 — constellations, wave 4; never rating_v2, different
    layer). ``baseline_bouts`` should already be scoped to ``no_gi`` by the caller — the
    elite-no-gi baseline is not meaningful compared against a gi or mixed-uniform category."""
    roster = [str(entry["name"]) for entry in division["athletes"]]
    own_events = cp.flatten_own_events(per_athlete_bouts)
    if not cp.sufficient_sample(own_events):
        return {"suficiente": False}

    roster_baseline_bouts = cp.exclude_roster_from_baseline(baseline_bouts, roster)
    baseline_events = cp.flatten_bout_events(roster_baseline_bouts)

    perfil = cp.profile_by_type(own_events)
    perfil_baseline = cp.profile_by_type(baseline_events) if baseline_events else None
    desvio_primary = desvio_secondary = None
    if baseline_events:
        desvio_primary = cp.lift_with_stability(own_events, baseline_events,
                                                weighting="athlete_balanced")
        desvio_secondary = cp.lift_with_stability(own_events, baseline_events,
                                                  weighting="event_weighted")

    unique_bouts = _unique_bouts(per_athlete_bouts)
    cobertura_eventos = tabelas["cobertura_eventos"]
    eventos = int(cobertura_eventos.get("eventos", 0))
    sem_tempo = int(cobertura_eventos.get("eventos_sem_tempo", 0))
    return {
        "suficiente": True,
        "concentracao": cp.concentration(own_events),
        "perfil": perfil,
        "perfil_baseline": perfil_baseline,
        "perfil_diverge": cp.profile_diverges(perfil),
        "distribuicao": cp.distribution(own_events),
        "desvio_primary": desvio_primary,
        "desvio_secondary": desvio_secondary,
        # Seção 5 recebe o MESMO per_athlete_bouts das outras seções. Resolver o roster de novo
        # contra o banco daria outro corpus e o relatório se contradiria entre as seções 1 e 5.
        "constelacoes": ccon.division_constellations(per_athlete_bouts, roster_baseline_bouts),
        "rede": _division_network(unique_bouts, roster_baseline_bouts),
        "efetividade": cp.effectiveness(own_events),
        "tempo_disponivel": cp.time_available(cobertura_eventos),
        "tempo_pct": round((eventos - sem_tempo) / eventos, 3) if eventos else 0.0,
        "valor_marginal": cp.coverage_marginal_value(roster, own_events),
    }


def build_division_report(
    manifest: Mapping[str, Any],
    corpus: Mapping[str, list[dict[str, Any]]],
    scope: str,
    db_stats: Mapping[str, int],
    baseline_bouts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    divisoes = []
    for division in manifest["divisions"]:
        tabelas, per_athlete_bouts = _division_tables(division, corpus, scope)
        cobertura = _division_coverage(division, per_athlete_bouts)
        tendencias = _division_trend(division, per_athlete_bouts, tabelas, baseline_bouts)
        divisoes.append({
            "nome": str(division["name"]), "slug": str(division["slug"]),
            "tabelas": tabelas, "cobertura": cobertura, "tendencias": tendencias,
        })
    return {
        "event": str(manifest.get("event", "ADCC 2026")),
        "escopo": scope,
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "descartadas_do_banco": int(db_stats.get("descartadas", 0)),
        "divisoes": divisoes,
    }


# ── CSV ──────────────────────────────────────────────────────────────────────────────

def _rows_flat(divisao: str, escopo: str, tabela: str, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"divisao": divisao, "escopo": escopo, "tabela": tabela, "agente": "", "linha": k,
         "coluna": "", "valor": v}
        for k, v in data.items()
    ]


def _rows_2level(divisao: str, escopo: str, tabela: str, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"divisao": divisao, "escopo": escopo, "tabela": tabela, "agente": agente, "linha": linha,
         "coluna": "", "valor": valor}
        for agente, linhas in data.items() for linha, valor in linhas.items()
    ]


def _rows_3level(divisao: str, escopo: str, tabela: str, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"divisao": divisao, "escopo": escopo, "tabela": tabela, "agente": agente, "linha": linha,
         "coluna": coluna, "valor": valor}
        for agente, linhas in data.items()
        for linha, colunas in linhas.items()
        for coluna, valor in colunas.items()
    ]


def _rows_cobertura(divisao: str, escopo: str, cobertura: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"divisao": divisao, "escopo": escopo, "tabela": "cobertura", "agente": "", "linha": key,
         "coluna": "", "valor": cobertura[key]}
        for key in ("total_lutas", "lutas_com_sequencia", "eventos", "lutas_uniforme_desconhecido",
                    "lutas_intra_categoria_contadas_duas_vezes")
    ]
    rows += [
        {"divisao": divisao, "escopo": escopo, "tabela": "cobertura", "agente": "",
         "linha": "por_origem", "coluna": origem, "valor": count}
        for origem, count in cobertura["por_origem"].items()
    ]
    return rows


def _rows_cobertura_atleta(divisao: str, escopo: str, por_atleta: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in por_atleta:
        for field in ("lutas", "lutas_com_sequencia", "eventos_proprios", "amostra_insuficiente"):
            rows.append({"divisao": divisao, "escopo": escopo, "tabela": "cobertura_atleta",
                        "agente": item["atleta"], "linha": field, "coluna": "", "valor": item[field]})
        for origem, count in item["origem"].items():
            rows.append({"divisao": divisao, "escopo": escopo, "tabela": "cobertura_atleta",
                        "agente": item["atleta"], "linha": "origem", "coluna": origem, "valor": count})
    return rows


# ── CSV: trend tables (sections 1-4, 6, 9) ──────────────────────────────────────────────

def _rows_concentracao(divisao: str, escopo: str, concentracao: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"divisao": divisao, "escopo": escopo, "tabela": "concentracao", "agente": "",
         "linha": key, "coluna": "", "valor": value}
        for key, value in concentracao.items()
    ]


def _rows_perfil_medio(
    divisao: str, escopo: str, perfil: Mapping[str, Any], perfil_baseline: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    rows = [
        {"divisao": divisao, "escopo": escopo, "tabela": "perfil_medio",
         "agente": f"categoria_{serie}", "linha": key, "coluna": "", "valor": value}
        for serie in ("peso_igual", "ponderado_por_evento", "leave_one_out")
        for key, value in perfil.get(serie, {}).items()
    ]
    if perfil_baseline:
        rows += [
            {"divisao": divisao, "escopo": escopo, "tabela": "perfil_medio",
             "agente": f"baseline_{serie}", "linha": key, "coluna": "", "valor": value}
            for serie in ("peso_igual", "ponderado_por_evento")
            for key, value in perfil_baseline.get(serie, {}).items()
        ]
    return rows


def _rows_distribuicao(divisao: str, escopo: str, distribuicao: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in distribuicao:
        rows.append({"divisao": divisao, "escopo": escopo, "tabela": "distribuicao", "agente": "",
                     "linha": item["label"], "coluna": "n", "valor": item["n"]})
        rows.append({"divisao": divisao, "escopo": escopo, "tabela": "distribuicao", "agente": "",
                     "linha": item["label"], "coluna": "share", "valor": item["share"]})
    return rows


def _rows_desvio(divisao: str, escopo: str, agente: str, tabela: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not tabela:
        return []
    rows = []
    for item in tabela["ranking"]:
        for coluna in ("n", "p_categoria", "p_baseline", "log2_lift", "estabilidade"):
            rows.append({"divisao": divisao, "escopo": escopo, "tabela": "desvio", "agente": agente,
                        "linha": item["label"], "coluna": coluna, "valor": item[coluna]})
    for status, items in (("amostra_insuficiente", tabela["amostra_insuficiente"]),
                          ("inedito_no_baseline", tabela["inedito_no_baseline"])):
        for item in items:
            rows.append({"divisao": divisao, "escopo": escopo, "tabela": "desvio", "agente": agente,
                        "linha": item["label"], "coluna": "status", "valor": status})
            rows.append({"divisao": divisao, "escopo": escopo, "tabela": "desvio", "agente": agente,
                        "linha": item["label"], "coluna": "n", "valor": item["n"]})
    return rows


def _rows_rede(divisao: str, escopo: str, rede: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not rede:
        return []
    rows = []
    for item in rede["transicoes"]:
        linha = f"{item['de']} -> {item['para']}"
        for coluna in ("weight", "p_categoria", "p_baseline", "log2_lift"):
            rows.append({"divisao": divisao, "escopo": escopo, "tabela": "rede", "agente": "",
                        "linha": linha, "coluna": coluna, "valor": item[coluna]})
    return rows


def _rows_constelacoes(
    divisao: str, escopo: str, dados: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for c in (dados or {}).get("constelacoes") or []:
        for coluna in ("prevalencia_categoria", "prevalencia_baseline", "log2_lift",
                       "mean_jaccard", "classification", "support_athletes", "driver_athlete",
                       "passa_gate", "internal_edges", "support"):
            rows.append({"divisao": divisao, "escopo": escopo, "tabela": "constelacoes",
                         "agente": "", "linha": c["hub"], "coluna": coluna, "valor": c[coluna]})
        rows.append({"divisao": divisao, "escopo": escopo, "tabela": "constelacoes", "agente": "",
                     "linha": c["hub"], "coluna": "membros", "valor": " | ".join(c["members"])})
    return rows


def _rows_valor_marginal(divisao: str, escopo: str, valor_marginal: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"divisao": divisao, "escopo": escopo, "tabela": "valor_marginal", "agente": "",
         "linha": item["atleta"], "coluna": "eventos_proprios", "valor": item["eventos_proprios"]}
        for item in valor_marginal
    ]


def write_division_csv(report: Mapping[str, Any], path: Path) -> None:
    escopo = str(report["escopo"])
    rows: list[dict[str, Any]] = []
    for div in report["divisoes"]:
        slug = str(div["slug"])
        rows += _rows_flat(slug, escopo, "resumo", div["tabelas"]["resumo"])
        rows += _rows_2level(slug, escopo, "luta_em_pe", div["tabelas"]["luta_em_pe"])
        rows += _rows_3level(slug, escopo, "efetividade", div["tabelas"]["efetividade"])
        rows += _rows_3level(slug, escopo, "tempo", div["tabelas"]["tempo"])
        rows += _rows_cobertura(slug, escopo, div["cobertura"])
        rows += _rows_cobertura_atleta(slug, escopo, div["cobertura"]["por_atleta"])
        tendencias = div["tendencias"]
        if tendencias.get("suficiente"):
            rows += _rows_concentracao(slug, escopo, tendencias["concentracao"])
            rows += _rows_perfil_medio(slug, escopo, tendencias["perfil"], tendencias["perfil_baseline"])
            rows += _rows_distribuicao(slug, escopo, tendencias["distribuicao"])
            rows += _rows_desvio(slug, escopo, "primary", tendencias["desvio_primary"])
            rows += _rows_desvio(slug, escopo, "secondary", tendencias["desvio_secondary"])
            rows += _rows_constelacoes(slug, escopo, tendencias.get("constelacoes"))
            rows += _rows_rede(slug, escopo, tendencias["rede"])
            rows += _rows_valor_marginal(slug, escopo, tendencias["valor_marginal"])
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


# ── HTML ─────────────────────────────────────────────────────────────────────────────

_TABLE_CSS = """
table { width: 100%; border-collapse: collapse; margin: 0 0 var(--space-md); }
th, td { text-align: left; padding: 4px 10px; border-bottom: 1px solid var(--rule); font-size: 0.82rem; }
th { color: var(--muted); font-weight: 600; }
.section { break-inside: avoid; margin-bottom: var(--space-lg); }
"""


def _html_resumo(resumo: Mapping[str, Any]) -> str:
    rows = "".join(f"<tr><td>{escape(str(k))}</td><td>{v}</td></tr>" for k, v in resumo.items())
    return f'<section class="section"><h3>Resumo</h3><table>{rows}</table></section>'


def _html_2level(title: str, data: Mapping[str, Any]) -> str:
    tipos = sorted({tipo for tipos in data.values() for tipo in tipos})
    head = "<tr><th>Agente</th>" + "".join(f"<th>{escape(t)}</th>" for t in tipos) + "</tr>"
    body = "".join(
        f"<tr><td>{escape(agente)}</td>"
        + "".join(f"<td>{tipos_valores.get(t, 0)}</td>" for t in tipos)
        + "</tr>"
        for agente, tipos_valores in data.items()
    )
    return f'<section class="section"><h3>{escape(title)}</h3><table>{head}{body}</table></section>'


def _html_3level(title: str, data: Mapping[str, Any]) -> str:
    blocks = []
    for agente, linhas in data.items():
        colunas = sorted({c for cols in linhas.values() for c in cols})
        head = "<tr><th></th>" + "".join(f"<th>{escape(c)}</th>" for c in colunas) + "</tr>"
        body = "".join(
            f"<tr><td>{escape(str(linha))}</td>"
            + "".join(f"<td>{cols.get(c, 0)}</td>" for c in colunas)
            + "</tr>"
            for linha, cols in linhas.items()
        )
        blocks.append(f"<h4>{escape(agente)}</h4><table>{head}{body}</table>")
    return f'<section class="section"><h3>{escape(title)}</h3>{"".join(blocks)}</section>'


def _html_cobertura(cobertura: Mapping[str, Any]) -> str:
    origem = ", ".join(f"{k}: {v}" for k, v in cobertura["por_origem"].items()) or "nenhuma"
    linhas = "".join(
        f"<tr><td>{escape(str(item['atleta']))}</td><td>{item['lutas']}</td>"
        f"<td>{item['lutas_com_sequencia']}</td><td>{item['eventos_proprios']}</td>"
        f"<td>{escape(', '.join(f'{k}: {v}' for k, v in item['origem'].items()) or '—')}</td>"
        f"<td>{'amostra insuficiente para leitura individual' if item['amostra_insuficiente'] else 'ok'}</td></tr>"
        for item in cobertura["por_atleta"]
    )
    return f"""<section class="section"><h3>10. Cobertura</h3>
<p>{cobertura['total_lutas']} lutas ({escape(origem)}) · {cobertura['lutas_com_sequencia']} com sequência ·
{cobertura['eventos']} eventos próprios · {cobertura['lutas_uniforme_desconhecido']} com uniforme desconhecido ·
{cobertura['lutas_intra_categoria_contadas_duas_vezes']} lutas intra-categoria contadas duas vezes
(uma por perspectiva).</p>
<table><tr><th>Atleta</th><th>Lutas</th><th>Com sequência</th><th>Eventos próprios</th><th>Origem</th><th>Leitura</th></tr>
{linhas}</table></section>"""


# ── HTML: trend sections (1-4, 6-9) ─────────────────────────────────────────────────────

def _html_concentracao(concentracao: Mapping[str, Any]) -> str:
    return f"""<section class="section"><h3>1. Concentração da amostra</h3>
{rc.stacked_bar_single(concentracao['top1_share'], str(concentracao['top1_atleta'] or '—'))}
<p class="dek">HHI {concentracao['hhi']:.3f} sobre {concentracao['atletas']} atletas ·
{concentracao['total_eventos']} eventos próprios.</p></section>"""


def _html_perfil(perfil: Mapping[str, Any], perfil_baseline: Mapping[str, Any] | None,
                  diverge: bool) -> str:
    tipos = sorted(perfil["peso_igual"])
    nota = ("As três leituras divergem — ver seção 4 (Metagame)."
            if diverge else "As três leituras concordam.")
    return f"""<section class="section"><h3>2. Perfil médio da categoria</h3>
{rc.profile_bars(tipos, perfil, perfil_baseline)}
<p class="dek">{nota} Peso igual por atleta (PRIMARY), ponderado por evento (SECONDARY),
leave-one-out da atleta dominante (robustez). Traço = baseline, na mesma leitura.</p></section>"""


def _html_distribuicao(distribuicao: Sequence[Mapping[str, Any]]) -> str:
    rows = [(str(item["label"]), int(item["n"])) for item in distribuicao]
    return f"""<section class="section"><h3>3. Distribuição de técnicas</h3>
{rc.ranked_bars(rows)}</section>"""


def _html_desvio_par(titulo: str, tabela: Mapping[str, Any] | None) -> str:
    if not tabela:
        return f"<h4>{escape(titulo)}</h4><p class='empty'>Sem baseline disponível para este escopo.</p>"
    rows = [(str(r["label"]), float(r["log2_lift"]), r["estabilidade"]) for r in tabela["ranking"][:12]]
    insuficiente = ", ".join(f"{i['label']} (n={i['n']})" for i in tabela["amostra_insuficiente"][:8]) or "nenhuma"
    inedito = ", ".join(f"{i['label']} (n={i['n']})" for i in tabela["inedito_no_baseline"][:8]) or "nenhuma"
    return f"""<h4>{escape(titulo)}</h4>{rc.divergent_lollipop(rows)}
<p class="dek">Amostra insuficiente (n&lt;3, fora do ranking numérico): {escape(insuficiente)}.
Inédito no baseline: {escape(inedito)}.</p>"""


def _html_desvio(primary: Mapping[str, Any] | None, secondary: Mapping[str, Any] | None) -> str:
    return f"""<section class="section"><h3>4. Metagame: o que desvia</h3>
{_html_desvio_par("PRIMARY — athlete-balanced vs baseline athlete-balanced", primary)}
{_html_desvio_par("SECONDARY — event-weighted vs baseline event-weighted", secondary)}
</section>"""


_CLASSIF_ROTULO = {
    "STABLE": "estável",
    "PARTIALLY_STABLE": "parcialmente estável",
    "ATHLETE_DRIVEN": "sustentada por uma atleta",
}


def _html_constelacoes(dados: Mapping[str, Any] | None) -> str:
    """Seção 5. Detector compartilhado com a Rating Engine V2 — rating não participa da
    membership, só topologia e frequência (ver docs/rating_v2/README.md)."""
    constelacoes = (dados or {}).get("constelacoes") or []
    if not constelacoes:
        corpo = ('<p class="empty">Nenhuma constelação com mais de um nó foi detectada neste '
                 'escopo.</p>')
    else:
        max_prev = max(c["prevalencia_categoria"] for c in constelacoes) or 1.0
        blocos = []
        for c in constelacoes:
            lift = "—" if c["log2_lift"] is None else f"{c['log2_lift']:+.2f}"
            if c["inedito_no_baseline"]:
                lift = "inédito no baseline"
            transicoes = " · ".join(
                f"{escape(t['de'])} → {escape(t['para'])}"
                for t in c["transicoes_caracteristicas"]
            ) or "—"
            marca = "" if c["passa_gate"] else ' <span class="flag">fora do gate</span>'
            blocos.append(
                f'<tr><td>{escape(c["hub"])}{marca}<br><small>{escape(transicoes)}</small></td>'
                f'<td>{rc.inline_bar(c["prevalencia_categoria"], max_prev)}'
                f'{c["prevalencia_categoria"]*100:.1f}%</td>'
                f'<td>{lift}</td>'
                f'<td>{_CLASSIF_ROTULO.get(c["classification"], c["classification"])}'
                f'<br><small>Jaccard {c["mean_jaccard"]:.2f} · {c["support_athletes"]} atleta(s)</small></td>'
                f'</tr><tr class="nota"><td colspan="4"><small>{escape(c["texto"])}</small></td></tr>'
            )
        corpo = (
            '<table><thead><tr><th>Constelação / transições características</th>'
            '<th>Prevalência na categoria</th><th>log2 lift vs elite no-gi</th>'
            '<th>Robustez</th></tr></thead><tbody>' + "".join(blocos) + "</tbody></table>"
        )
    rodape = ""
    if dados:
        rodape = (f'<p class="nota-metodo"><small>Modularidade {dados.get("modularity", 0):.3f} · '
                  f'comunidades quebradas pelo gate de conectividade: '
                  f'{dados.get("rejected_rate", 0):.1%}. Detector compartilhado com a Rating '
                  f'Engine V2; rating não participa da formação das comunidades.</small></p>')
    return (f'<section class="section"><h3>5. Constelações e estrutura do metagame</h3>'
            f'{corpo}{rodape}</section>')


def _html_rede(rede: Mapping[str, Any] | None) -> str:
    transicoes = rede["transicoes"] if rede else []
    if rede is None or not transicoes:
        corpo = '<p class="empty">Sem dados de transição suficientes.</p>'
    else:
        max_weight = max(item["weight"] for item in transicoes)
        linhas = []
        for item in transicoes:
            lift_texto = "—" if item["log2_lift"] is None else f"{item['log2_lift']:+.2f}"
            linhas.append(
                f"<tr><td>{escape(item['de'])} → {escape(item['para'])}</td>"
                f"<td>{item['weight']}</td>"
                f"<td>{rc.inline_bar(item['weight'], max_weight)}</td>"
                f"<td>{lift_texto}</td></tr>"
            )
        tabela_html = ("<table><tr><th>Transição</th><th>n</th><th></th>"
                       f"<th>log2 lift vs baseline</th></tr>{''.join(linhas)}</table>")
        scatter_svg = rc.scatter_reward_risk(rede["scatter"], rede["baseline_point"])
        corpo = f"{tabela_html}<p class='dek'>Reward−risk das posições da categoria (pontilhado = baseline):</p>{scatter_svg}"
    return f'<section class="section"><h3>6. Rede de transições</h3>{corpo}</section>'


def _html_efetividade_trend(efetividade: Sequence[Mapping[str, Any]]) -> str:
    linhas = []
    for item in efetividade[:20]:
        taxa = f"{item['taxa_sucesso']:.0%}" if "taxa_sucesso" in item else "sem taxa"
        linhas.append(f"<tr><td>{escape(str(item['label']))}</td><td>{item['n']}</td>"
                      f"<td>resolvido {item['resolvido_pct']:.0%}</td><td>{taxa}</td></tr>")
    return f"""<section class="section"><h3>7. Efetividade</h3>
<table><tr><th>Ação</th><th>n</th><th>Base apurada</th><th>Taxa (só sobre resolvidos)</th></tr>
{''.join(linhas)}</table></section>"""


def _html_tempo_trend(disponivel: bool, pct: float) -> str:
    if disponivel:
        texto = f"Base de tempo suficiente ({pct:.0%} dos eventos) — ver tabela Tempo no Anexo."
        classe = "dek"
    else:
        texto = f"Sem base de tempo suficiente ({pct:.0%} dos eventos com tempo resolvido, abaixo de 30%)."
        classe = "empty"
    return f'<section class="section"><h3>8. Tempo</h3><p class="{classe}">{texto}</p></section>'


def _html_valor_marginal(valor_marginal: Sequence[Mapping[str, Any]]) -> str:
    linhas = "".join(f"<tr><td>{escape(item['atleta'])}</td><td>{item['eventos_proprios']}</td></tr>"
                     for item in valor_marginal[:8])
    return f"""<section class="section"><h3>9. Valor marginal de cobertura</h3>
<p class="dek">Atletas do roster cuja análise reduziria mais a concentração hoje (menos eventos
próprios primeiro).</p>
<table><tr><th>Atleta</th><th>Eventos próprios hoje</th></tr>{linhas}</table></section>"""


def _html_trend(tendencias: Mapping[str, Any]) -> str:
    if not tendencias.get("suficiente"):
        return ('<section class="section"><h3>Análise de tendências</h3>'
                '<p class="empty">Amostra insuficiente para leitura de categoria neste escopo '
                '— ver Cobertura e Anexo abaixo.</p></section>')
    return "".join([
        _html_concentracao(tendencias["concentracao"]),
        _html_perfil(tendencias["perfil"], tendencias["perfil_baseline"], tendencias["perfil_diverge"]),
        _html_distribuicao(tendencias["distribuicao"]),
        _html_desvio(tendencias["desvio_primary"], tendencias["desvio_secondary"]),
        _html_constelacoes(tendencias.get("constelacoes")),
        _html_rede(tendencias["rede"]),
        _html_efetividade_trend(tendencias["efetividade"]),
        _html_tempo_trend(tendencias["tempo_disponivel"], tendencias["tempo_pct"]),
        _html_valor_marginal(tendencias["valor_marginal"]),
    ])


def _html_division(div: Mapping[str, Any]) -> str:
    tabelas = div["tabelas"]
    return f"""<article class="opponent">
<header><p class="eyebrow">Divisão</p><h2>{escape(str(div['nome']))}</h2></header>
{_html_trend(div['tendencias'])}
{_html_cobertura(div['cobertura'])}
<section class="section"><h3>11. Anexo — cross-tabs de scout</h3></section>
{_html_resumo(tabelas['resumo'])}
{_html_2level("Luta em pé", tabelas['luta_em_pe'])}
{_html_3level("Efetividade (anexo)", tabelas['efetividade'])}
{_html_3level("Tempo (anexo)", tabelas['tempo'])}
</article>"""


def render_division_html(report: Mapping[str, Any]) -> str:
    escopo_label = SCOPE_LABEL[str(report["escopo"])]
    divisions_html = "".join(_html_division(d) for d in report["divisoes"])
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(str(report['event']))} · Categoria · {escape(escopo_label)}</title>
<style>{REPORT_CSS}{_TABLE_CSS}</style></head><body><main>
<section class="cover"><p class="eyebrow">Relatório de scouting por categoria</p>
<h1>{escape(str(report['event']))}<br>Escopo: {escape(escopo_label)}</h1>
<p class="dek">Gerado em {escape(str(report['gerado_em']))}. Relatório agregado no nível da
categoria — sem afirmação sobre uma atleta individual; ver Cobertura por divisão para o que
sustenta cada número. {report['descartadas_do_banco']} luta(s) do banco descartada(s) por
duplicidade com os dumps (o dump prevalece).</p></section>
{divisions_html}</main></body></html>
"""


# ── CLI ──────────────────────────────────────────────────────────────────────────────

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("reports/adcc-2026-categoria"))
    parser.add_argument("--sem-banco", action="store_true")
    parser.add_argument("--escopo", choices=SCOPES, action="append")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    corpus, _issues = collect_bouts(manifest)
    for bouts in corpus.values():
        for bout in bouts:
            bout["origem"] = "dump"
    db_stats = {"descartadas": 0, "adicionadas": 0}
    baseline_bouts: list[dict[str, Any]] = []
    if not args.sem_banco:
        corpus, db_stats = _augment_with_db(manifest, corpus)
        with get_session_factory()() as session:
            baseline_bouts = _load_baseline_bouts(session)

    scopes = args.escopo or list(SCOPES)
    # `--out` names a DIRECTORY, and the files inside are named after it. It used to be read as
    # a path STEM, so the documented `--out reports/adcc-2026-categoria` wrote
    # `reports/adcc-2026-categoria-gi.html` — a sibling of the directory the committed artefacts
    # actually live in. Two copies of one report and no way to tell which is live is not a
    # papercut worth keeping.
    args.out.mkdir(parents=True, exist_ok=True)
    for scope in scopes:
        # The elite no-gi baseline only means something against a no-gi category corpus —
        # comparing it to a gi or mixed-uniform ("unificado") corpus would mix two games, the
        # exact mistake the baseline exists to avoid (see the spec's Decisões table).
        scope_baseline = baseline_bouts if scope == "no_gi" else []
        report = build_division_report(manifest, corpus, scope, db_stats, scope_baseline)
        base = args.out / f"{args.out.name}-{SCOPE_SUFFIX[scope]}"
        html_path, pdf_path, csv_path = (base.with_suffix(s) for s in (".html", ".pdf", ".csv"))
        html_path.write_text(render_division_html(report), encoding="utf-8")
        try:
            render_pdf(html_path, pdf_path)
        except PdfError as exc:
            print(f"AVISO ({scope}): {exc}", file=sys.stderr)
        write_division_csv(report, csv_path)
        print(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
