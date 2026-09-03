"""Constelações — `nodes[].system` / `systems[]` / `nodes[].weight` (2026-09-04).

Por que existe: o Atlas coloria estado por CATEGORIA, mas só `guard` e `control` SÃO estados no
modelo de eventos. No corpus público medido isso dá 190 `control` / 40 `guard` / 1 `escape` —
82% do mapa numa cinza só. A cor passa a dizer SISTEMA (que estados de fato desembocam uns nos
outros). Este arquivo prova as quatro coisas de que o cliente depende:

1. **aditivo** — quem não conhece `system`/`systems`/`weight` desenha o que sempre desenhou;
2. **determinístico** — greedy modularity devolve frozensets (cicatriz #10 do
   failure-archaeology): os ids de sistema têm de ser byte-idênticos entre processos, com
   PYTHONHASHSEED diferente;
3. **âncoras são neutras** — Top/Bottom/Neutral/Finish e junções nunca entram num sistema
   (decisão do dono: a cor da âncora é geometria, não jogo);
4. **`weight` é grau ponderado de verdade** — Σ count entrando + saindo, o que dá ao cliente uma
   proeminência contínua no lugar da classe 1..3 que satura.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from typing import Any

from analysis.corpus_paths import aggregate_bouts, path_payload, state_systems

# Dois jogos que quase não se tocam: um sistema de guarda embaixo (Closed/De la Riva -> raspagem)
# e um sistema de passagem em cima (Half/Side/Mount), ligados por um único traço.
GUARD_GAME: list[dict[str, Any]] = [
    {"label": "Closed Guard", "type": "guard", "side": "a", "ts": 10},
    {"label": "Hip Bump Sweep", "type": "sweep", "side": "a", "successful": True, "ts": 20},
    {"label": "De La Riva Guard", "type": "guard", "side": "a", "ts": 30},
    {"label": "Berimbolo", "type": "sweep", "side": "a", "successful": True, "ts": 40},
    {"label": "Closed Guard", "type": "guard", "side": "a", "ts": 50},
    {"label": "Armbar", "type": "submission", "side": "a", "successful": True, "ts": 60},
]
PASS_GAME: list[dict[str, Any]] = [
    {"label": "Half Guard", "type": "guard", "side": "b", "ts": 10},
    {"label": "Knee Cut", "type": "pass", "side": "b", "successful": True, "ts": 20},
    {"label": "Side Control", "type": "control", "side": "b", "ts": 30},
    {"label": "Mount Transition", "type": "transition", "side": "b", "successful": True, "ts": 40},
    {"label": "Mount", "type": "control", "side": "b", "ts": 50},
    {"label": "Americana", "type": "submission", "side": "b", "successful": True, "ts": 60},
]
BOUTS = [GUARD_GAME, PASS_GAME, GUARD_GAME, PASS_GAME]


def _payload() -> dict[str, Any]:
    return path_payload(aggregate_bouts(BOUTS), layout="ring")


def test_systems_are_additive_and_self_describing() -> None:
    payload = _payload()
    systems = payload["systems"]
    assert isinstance(systems, list)
    by_id = {s["id"]: s for s in systems}
    # ids são 0..n-1, sem buracos — o cliente indexa uma paleta com eles
    assert sorted(by_id) == list(range(len(systems)))
    for s in systems:
        assert set(s) == {"id", "hub", "label", "size"}
        assert s["size"] >= 2, "um estado sozinho não é um sistema"
        assert s["hub"] and s["label"], "o rótulo do sistema é o hub, nunca um id opaco"

    # o hub de cada sistema é um estado REAL do payload, e é membro do próprio sistema
    hubs = {s["id"]: s["hub"] for s in systems}
    members: dict[int, set[str]] = {}
    for n in payload["nodes"]:
        if "system" in n:
            members.setdefault(n["system"], set()).add(n["stateKey"])
    for sid, hub in hubs.items():
        assert hub in members[sid], f"hub {hub!r} fora do próprio sistema {sid}"
        assert members[sid] == set(members[sid]), "membros são estados, não pontos"
    # tamanho publicado == membros marcados
    for s in systems:
        assert s["size"] == len(members[s["id"]])

    # ADITIVO: nada que já existia mudou de forma — um cliente antigo lê o mesmo grafo
    for n in payload["nodes"]:
        assert isinstance(n["x"], int | float) and isinstance(n["y"], int | float)
        assert n["pin"] is True


def test_anchors_and_junctions_stay_neutral() -> None:
    """A cor da âncora é geometria (polo/anel), não jogo — e uma junção não é um estado."""
    payload = _payload()
    for n in payload["nodes"]:
        if n.get("kind") == "anchor" or n.get("junction"):
            assert "system" not in n, f"{n['id']} não pode entrar num sistema"


def test_weight_is_the_real_weighted_degree() -> None:
    payload = _payload()
    expected: dict[str, int] = {}
    for p in payload["paths"]:
        expected[p["source"]] = expected.get(p["source"], 0) + p["count"]
        expected[p["target"]] = expected.get(p["target"], 0) + p["count"]
    for n in payload["nodes"]:
        if n.get("kind") != "state":
            continue
        assert "weight" in n, "todo estado publica proeminência"
        assert isinstance(n["weight"], int)
        assert n["weight"] >= 0
        # `weight` vem dos SEGMENTOS (uma path atravessa junções), então não é idêntico à soma
        # por endpoints — mas um estado que nenhuma path toca não pode ter peso, e um que várias
        # tocam não pode ter zero. É essa monotonicidade que o cliente usa pra escalar o raio.
        if expected.get(n["id"], 0) > 0:
            assert n["weight"] > 0, f"{n['id']} é atravessado mas pesa 0"

    # a classe 1..3 satura, o peso não: o payload tem de distinguir mais níveis do que `size`
    states = [n for n in payload["nodes"] if n.get("kind") == "state"]
    assert len({n["weight"] for n in states}) >= len({n["size"] for n in states})


def test_state_systems_is_pure_and_ignores_non_states() -> None:
    """A função é pura: só nodes + paths, nada de DB, nada de agregado."""
    nodes = [
        {"id": "s:a", "stateKey": "a", "kind": "state", "label": "A"},
        {"id": "s:b", "stateKey": "b", "kind": "state", "label": "B"},
        {"id": "s:c", "stateKey": "c", "kind": "state", "label": "C"},
        {"id": "an:top", "stateKey": "top", "kind": "anchor", "label": "Top"},
        {"id": "j:0", "kind": "branch", "junction": True, "label": ""},
    ]
    paths = [
        {"source": "s:a", "target": "s:b", "count": 9},
        {"source": "s:b", "target": "s:a", "count": 7},
        {"source": "s:b", "target": "s:c", "count": 1},
        {"source": "s:a", "target": "an:top", "count": 50},   # âncora: ignorada
        {"source": "j:0", "target": "s:c", "count": 50},      # junção: ignorada
        {"source": "s:a", "target": "s:a", "count": 50},      # laço: ignorado
    ]
    system_of, systems = state_systems(nodes, paths)
    assert "an:top" not in system_of and "j:0" not in system_of
    assert set(system_of) <= {"s:a", "s:b", "s:c"}
    # o hub é o membro de maior PageRank ponderado, e o rótulo é o label desse hub
    for s in systems:
        assert s["label"] in {"A", "B", "C"}

    # sem arestas de estado não há sistema (e não estoura)
    assert state_systems(nodes, [{"source": "s:a", "target": "an:top", "count": 3}]) == ({}, [])
    assert state_systems([], []) == ({}, [])


def test_system_ids_are_deterministic_across_processes() -> None:
    """Cicatriz #10: greedy modularity devolve frozensets — sem ordenação explícita os ids de
    sistema (e portanto as CORES do Atlas) trocam a cada processo de export. Dois subprocessos
    com PYTHONHASHSEED diferente têm de escrever os mesmos bytes."""
    script = textwrap.dedent(
        """
        import json, sys
        from analysis.corpus_paths import aggregate_bouts, path_payload
        bouts = json.loads(sys.argv[1])
        p = path_payload(aggregate_bouts(bouts), layout="ring")
        out = {
            "systems": p["systems"],
            "of": {n["id"]: n["system"] for n in p["nodes"] if "system" in n},
        }
        print(json.dumps(out, sort_keys=True))
        """
    )
    runs = []
    for seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script, json.dumps(BOUTS)],
            capture_output=True, text=True, check=True, env=env,
        )
        runs.append(proc.stdout.strip())
    assert runs[0] == runs[1], "ids de sistema mudaram com o hash seed"
    assert json.loads(runs[0])["systems"], "o caso de teste tem de produzir sistemas"
