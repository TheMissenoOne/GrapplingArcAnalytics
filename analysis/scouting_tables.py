"""Cross-tabs mirroring the manual scouting spreadsheet, built from refined bouts.

The spreadsheet ("Scout - <ATHLETE>") carries one action log plus four pivots that
feed its charts:

    LUTA EM PÉ           who started the bout and how (guard pull / takedown / double pull)
    EFETIVIDADE          action × outcome, once for the athlete and once for the opponents
    TEMPO                minute remaining × outcome, again for both sides

This module rebuilds those from ``scouting_report.collect_bouts`` output. It reads
dumps only — no DB, no network.

Two honest departures from the sheet, both about not inventing a scoreboard:

``PONTO`` and ``VANTAGEM`` are referee awards. A transcript almost never states them,
and the refiner is explicitly forbidden from inferring them from technique events, so
they are filled *only* from a bout's ``score_events``. What we do observe is whether
the action landed, which the sheet's four values cannot express — hence two extra
outcomes: ``CONCRETIZADA`` (landed, scoring unknown) and ``NÃO APURADO`` (outcome
unknown). Collapsing either into ``PONTO`` would be fabrication.

``TEMPO`` needs the bout's length. It comes from ``duration_s``, which
``apply_events`` derives from a sidecar's ``timing.end_ts``. Bouts refined without one
land in ``SEM TEMPO`` rather than being bucketed against a guessed clock.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from analysis.names import _normalize_name, athlete_key

AGENTES = ("PRÓPRIO", "ADVERSÁRIO")

ACOES = (
    "FINALIZAÇÃO", "RASPAGEM", "PASSAGEM", "MONTADA", "COSTAS", "QUEDA",
    "SUBIDA DA CHAMADA DUPLA",
)

#: The sheet's four, plus the two the sheet cannot express without a referee.
EFETIVIDADES = (
    "FINALIZAÇÃO", "PONTO", "VANTAGEM", "CONCRETIZADA", "SEM EFETIVIDADE", "NÃO APURADO",
)

INICIOS = ("CHAMA PRA GUARDA", "QUEDA", "CHAMADA DUPLA")

SEM_TEMPO = "SEM TEMPO"

# _normalize_name drops hyphens without inserting a space: "Three-Quarter Mount"
# normalises to "threequarter mount", "S-Mount" to "smount". Match what it emits.
_MONTADA = {"mount", "threequarter mount", "smount", "technical mount"}
_COSTAS = {"back control", "body triangle", "seatbelt control", "hooks in", "back take"}
_PUXADA = {"guard pull"}

#: Two guard pulls this far apart (seconds) are still one double-pull exchange.
DOUBLE_PULL_WINDOW_S = 15


def _norm(value: Any) -> str:
    """Technique-label normalizer only. Never compare athlete names with this — it strips
    accents instead of deaccenting ("Galvão" -> "galvo"), so "Sarah Galvão" != "Sarah Galvao".
    Use ``_ident`` for names."""
    return _normalize_name(str(value or ""))


def _ident(value: Any) -> str:
    """Athlete-identity key: deaccents + resolves aliases, so "Sarah Galvão" == "Sarah Galvao"
    and manifest/roster aliases ("Mo Black" == "Morgan Black") match."""
    return athlete_key(str(value or ""))


def acao_de(event: Mapping[str, Any]) -> str | None:
    """The sheet's AÇÃO for one event, or None when it is outside the seven it counts."""
    tipo = str(event.get("type") or "").lower()
    label = _norm(event.get("label"))
    if tipo == "submission":
        return "FINALIZAÇÃO"
    if tipo == "sweep":
        return "RASPAGEM"
    if tipo == "pass":
        return "PASSAGEM"
    if tipo == "takedown":
        return "QUEDA"
    if tipo == "control":
        if label in _MONTADA:
            return "MONTADA"
        if label in _COSTAS:
            return "COSTAS"
    return None


def efetividade_de(event: Mapping[str, Any], pontos: int | None = None) -> str:
    """Outcome for one event. ``pontos`` comes from an official score, never from the event."""
    if pontos is not None:
        return "VANTAGEM" if pontos == 1 else "PONTO"
    sucesso = event.get("successful")
    if sucesso is True:
        return "FINALIZAÇÃO" if str(event.get("type") or "").lower() == "submission" \
            else "CONCRETIZADA"
    if sucesso is False:
        return "SEM EFETIVIDADE"
    return "NÃO APURADO"


def _relativo(event: Mapping[str, Any], bout: Mapping[str, Any]) -> float | None:
    ts = event.get("ts")
    if not isinstance(ts, int | float) or isinstance(ts, bool):
        return None
    if bout.get("timing_basis") == "video_absolute":
        inicio = bout.get("bout_start_s")
        if not isinstance(inicio, int | float) or isinstance(inicio, bool):
            return None
        return float(ts) - float(inicio)
    return float(ts)


def tempo_bucket(event: Mapping[str, Any], bout: Mapping[str, Any]) -> str:
    """Minute-remaining bucket ("6-5"), counting down like the sheet. SEM_TEMPO when unknown."""
    duracao = bout.get("duration_s")
    if not isinstance(duracao, int | float) or isinstance(duracao, bool) or duracao <= 0:
        return SEM_TEMPO
    rel = _relativo(event, bout)
    if rel is None:
        return SEM_TEMPO
    restante = max(0.0, float(duracao) - rel)
    minuto = max(1, math.ceil(restante / 60))
    return f"{minuto}-{minuto - 1}"


def inicio_de(bout: Mapping[str, Any]) -> tuple[str, str] | None:
    """(agent, INICIO) for how the bout left the feet, or None when nothing shows it.

    A double guard pull is two pulls by different athletes inside
    ``DOUBLE_PULL_WINDOW_S``; the agent is then ``AMBOS``.
    """
    puxadas: list[tuple[float, str]] = []
    quedas: list[tuple[float, str]] = []
    for event in bout.get("events", []):
        rel = _relativo(event, bout)
        ordem = rel if rel is not None else float(event.get("ts") or 0)
        actor = str(event.get("actor") or "")
        if not actor:
            continue
        if _norm(event.get("label")) in _PUXADA:
            puxadas.append((ordem, actor))
        elif str(event.get("type") or "").lower() == "takedown" \
                and event.get("successful") is not False:
            quedas.append((ordem, actor))
    puxadas.sort()
    quedas.sort()
    for i, (ts_a, actor_a) in enumerate(puxadas):
        for ts_b, actor_b in puxadas[i + 1:]:
            if actor_b != actor_a and ts_b - ts_a <= DOUBLE_PULL_WINDOW_S:
                return "AMBOS", "CHAMADA DUPLA"
    candidatos = [(ts, actor, "CHAMA PRA GUARDA") for ts, actor in puxadas]
    candidatos += [(ts, actor, "QUEDA") for ts, actor in quedas]
    if not candidatos:
        return None
    _, actor, tipo = min(candidatos, key=lambda item: item[0])
    return actor, tipo


def _pontos_por_evento(bout: Mapping[str, Any]) -> dict[int, int]:
    """Map event index → official points, matching ``score_events`` on (actor, ts).

    Only an exact match counts. A scoreboard entry that lines up with no observed event
    is left out rather than attached to the nearest one.
    """
    score_events = bout.get("score_events")
    if not isinstance(score_events, list):
        return {}
    por_chave: dict[tuple[str, float], int] = {}
    for item in score_events:
        if not isinstance(item, dict):
            continue
        actor, ts, pontos = item.get("actor"), item.get("ts"), item.get("points")
        if (isinstance(actor, str) and isinstance(ts, int | float)
                and not isinstance(ts, bool)
                and isinstance(pontos, int) and not isinstance(pontos, bool)):
            por_chave[(actor, float(ts))] = pontos
    if not por_chave:
        return {}
    saida: dict[int, int] = {}
    for index, event in enumerate(bout.get("events", [])):
        ts = event.get("ts")
        if not isinstance(ts, int | float) or isinstance(ts, bool):
            continue
        chave = (str(event.get("actor") or ""), float(ts))
        if chave in por_chave:
            saida[index] = por_chave[chave]
    return saida


def _adversario(bout: Mapping[str, Any], athlete: str) -> str | None:
    """The other participant. ``bout["opponent"]`` is the dump's own field, so for a bout
    keyed under the *opponent's* name it holds the athlete themselves."""
    participantes = bout.get("participants")
    if isinstance(participantes, list) and len(participantes) == 2:
        outros = [p for p in participantes if _ident(p) != _ident(athlete)]
        if len(outros) == 1:
            return str(outros[0])
    opponent = bout.get("opponent")
    if isinstance(opponent, str) and _ident(opponent) != _ident(athlete):
        return opponent
    return None


def _resultado(bout: Mapping[str, Any], athlete: str) -> str:
    """VITÓRIA/DERROTA/SEM RESULTADO. A winner that matches neither participant is never
    silently scored as a loss — that would fabricate a result out of a bad name match."""
    vencedor = (bout.get("result") or {}).get("winner")
    if not isinstance(vencedor, str) or not vencedor.strip():
        return "SEM RESULTADO"
    participantes = bout.get("participants")
    if isinstance(participantes, list) and len(participantes) == 2:
        if _ident(vencedor) not in {_ident(p) for p in participantes}:
            return "SEM RESULTADO"
    return "VITÓRIA" if _ident(vencedor) == _ident(athlete) else "DERROTA"


def _vazio(linhas: Sequence[str], colunas: Sequence[str]) -> dict[str, dict[str, int]]:
    return {linha: dict.fromkeys(colunas, 0) for linha in linhas}


def build_tables(athlete: str, bouts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The sheet's log + four pivots for one athlete, over their collected bouts."""
    linhas: list[dict[str, Any]] = []
    resumo = Counter({"lutas": 0, "VITÓRIA": 0, "DERROTA": 0, "SEM RESULTADO": 0})
    luta_em_pe = _vazio((*AGENTES, "AMBOS"), INICIOS)
    efetividade = {agente: _vazio(ACOES, EFETIVIDADES) for agente in AGENTES}
    tempo: dict[str, dict[str, dict[str, int]]] = {agente: {} for agente in AGENTES}
    fora_das_acoes: Counter[str] = Counter()

    for bout in bouts:
        resumo["lutas"] += 1
        resultado = _resultado(bout, athlete)
        resumo[resultado] += 1

        adversario = _adversario(bout, athlete)
        comeco = inicio_de(bout)
        if comeco is not None:
            actor, tipo = comeco
            agente = "AMBOS" if actor == "AMBOS" else (
                "PRÓPRIO" if _ident(actor) == _ident(athlete) else "ADVERSÁRIO"
            )
            luta_em_pe[agente][tipo] += 1

        pontos_por_evento = _pontos_por_evento(bout)
        info = bout.get("result") or {}
        # The sheet keeps POSIÇÃO and GOLPE apart; an event carries one label. The
        # position an action came from is the last guard/control established before it.
        posicao_corrente: str | None = None
        for index, event in enumerate(bout.get("events", [])):
            agente = "PRÓPRIO" if _ident(event.get("actor")) == _ident(athlete) else "ADVERSÁRIO"
            acao = acao_de(event)
            efet = efetividade_de(event, pontos_por_evento.get(index))
            bucket = tempo_bucket(event, bout)
            linhas.append({
                "bout_id": bout.get("bout_id"),
                "adversario": adversario,
                "campeonato": info.get("event"),
                "fase": info.get("stage"),
                "resultado": resultado,
                "agente": agente,
                "acao": acao,
                "efetividade": efet,
                "posicao": posicao_corrente,
                "golpe": event.get("label"),
                "tempo": bucket,
            })
            if str(event.get("type") or "").lower() in {"guard", "control"}:
                posicao_corrente = event.get("label")
            if acao is None:
                fora_das_acoes[str(event.get("type") or "?")] += 1
                continue
            efetividade[agente][acao][efet] += 1
            tempo[agente].setdefault(bucket, dict.fromkeys(EFETIVIDADES, 0))[efet] += 1

    for agente in AGENTES:
        tempo[agente] = dict(sorted(
            tempo[agente].items(),
            key=lambda item: (item[0] == SEM_TEMPO, -_bucket_ordem(item[0])),
        ))

    return {
        "atleta": athlete,
        "resumo": {"lutas": resumo["lutas"], "vitorias": resumo["VITÓRIA"],
                   "derrotas": resumo["DERROTA"], "sem_resultado": resumo["SEM RESULTADO"]},
        "luta_em_pe": luta_em_pe,
        "efetividade": efetividade,
        "tempo": tempo,
        "linhas": linhas,
        "cobertura": {
            "eventos": len(linhas),
            "eventos_fora_das_acoes": dict(fora_das_acoes),
            "eventos_sem_tempo": sum(1 for linha in linhas if linha["tempo"] == SEM_TEMPO),
            "lutas_com_placar": sum(1 for bout in bouts if bout.get("score_events")),
            "resultado_indeterminado": resumo["SEM RESULTADO"],
        },
    }


def _bucket_ordem(bucket: str) -> int:
    return int(bucket.split("-", 1)[0]) if bucket != SEM_TEMPO and "-" in bucket else 0
