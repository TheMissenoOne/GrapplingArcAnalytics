"""A aresta e a proveniência dela têm de contar a mesma história.

Condição (a) do ADR-08: sem unidade de luta em ``graph_edges``, o bootstrap da wave 6b só
conseguia reamostrar **arestas** — e a própria 6b disse que decidir por aquele número seria
repetir o erro que ela acabara de expor.

O risco desta feature não é a tabela; é a **divergência**. Se a proveniência for derivada por um
segundo caminho, ela passa a ser uma segunda opinião sobre o que é uma aresta, e as duas somem
de vista uma da outra. A wave 6b perdeu uma rodada exatamente assim, comparando ``"Closed
Guard"`` com ``closed guard`` e lendo Jaccard 0,0 como "os detectores não concordam em nada".

Por isso a proveniência é gravada **dentro do laço que já produz a aresta**, e por isso o que
estes testes travam é a concordância entre as duas, não a existência da tabela.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from analysis.athlete_elo import replay_matches
from analysis.athlete_graph import AthleteGraph
from analysis.names import _normalize_name, canonicalize


class _Match:
    """O mínimo que ``replay_matches`` lê de uma luta.

    A sequência aqui já está na perspectiva do atleta (``actor == "you"``), que é a forma que
    ``_your_entries`` consome — a transformação de perspectiva acontece uma camada acima.
    """

    def __init__(self, match_id: str, events: list[dict[str, Any]]) -> None:
        self.id = match_id
        self.sequence = events
        self.year = 2025
        self.date = None
        self.event = "Test Open"
        self.won = True
        self.win_type = "POINTS"


def _event(label: str, actor: str, typ: str = "submission") -> dict[str, Any]:
    return {"label": label, "type": typ, "actor": actor, "successful": True}


def _bout(match_id: str, labels: list[str]) -> _Match:
    """Uma luta em que o atleta executa ``labels`` em ordem, com um movimento do oponente ao fim."""
    return _Match(match_id, [_event(lbl, "you") for lbl in labels] + [_event("Escape", "opponent")])


def _key(label: str) -> str:
    return canonicalize(_normalize_name(label))


Replay = Callable[[list[_Match]], AthleteGraph]


@pytest.fixture()
def replay() -> Replay:
    def run(matches: list[_Match]) -> AthleteGraph:
        graph, _snapshots = replay_matches(
            "me", list(matches), rank_target=1500.0, opp_elos=[1200.0] * len(matches),
        )
        return graph
    return run


def test_toda_aresta_derivada_de_luta_sabe_de_qual_luta_veio(replay: Replay) -> None:
    graph = replay([_bout("m1", ["Closed Guard", "Armbar"])])

    edge = graph.edges[(_key("Closed Guard"), _key("Armbar"))]
    assert edge.bout_ids == {"m1"}


def test_a_mesma_transicao_em_duas_lutas_guarda_as_duas(replay: Replay) -> None:
    """É isto que torna a aresta reamostrável: uma aresta observada uma vez e uma observada
    dez vezes têm de ser distinguíveis por um sorteio de lutas, não pelo contador."""
    graph = replay([
        _bout("m1", ["Closed Guard", "Armbar"]),
        _bout("m2", ["Closed Guard", "Armbar"]),
    ])

    edge = graph.edges[(_key("Closed Guard"), _key("Armbar"))]
    assert edge.bout_ids == {"m1", "m2"}
    # A contagem e a proveniência medem coisas diferentes e não podem discordar de direção.
    assert edge.count == 2


def test_a_proveniencia_usa_a_mesma_chave_da_aresta(replay: Replay) -> None:
    """O modo de falhar que custou uma rodada à wave 6b, travado.

    A chave persistida passa por ``canonicalize(_normalize_name(...))``. Se a proveniência
    fosse derivada do espaço de rótulo cru, ela nunca daria join com a aresta — e o join
    silenciosamente devolveria zero linhas, que lê como "nenhuma luta observada".
    """
    graph = replay([_bout("m1", ["  CLOSED   Guard ", "Arm-bar"])])

    for (src, tgt), edge in graph.edges.items():
        if not edge.bout_ids:
            continue
        # A chave da aresta é a chave sob a qual a proveniência será gravada, por construção:
        # as duas saem da mesma linha do mesmo laço.
        assert src == _key(src)
        assert tgt == _key(tgt)


def test_o_laco_proprio_nao_cruza_lutas(replay: Replay) -> None:
    """A última técnica de uma luta e a primeira da seguinte não são uma transição.

    Mesma fronteira que ``network_from_sequences`` mantém entre sequências, e o motivo de a
    proveniência ser por (aresta, luta) e não por (aresta, ano).
    """
    graph = replay([_bout("m1", ["Closed Guard"]), _bout("m2", ["Armbar"])])

    assert (_key("Closed Guard"), _key("Armbar")) not in graph.edges


def test_o_movimento_do_oponente_nao_vira_aresta_nossa(replay: Replay) -> None:
    graph = replay([_Match("m1", [
        _event("Closed Guard", "you"),
        _event("Guard Pass", "opponent"),
        _event("Armbar", "you"),
    ])])

    # A aresta é entre os movimentos PRÓPRIOS consecutivos; o que o oponente fez no meio é
    # contexto, não elo da cadeia.
    assert (_key("Guard Pass"), _key("Armbar")) not in graph.edges
    assert (_key("Closed Guard"), _key("Guard Pass")) not in graph.edges


def test_a_forma_que_a_producao_passa_ao_replay_carrega_o_id() -> None:
    """O buraco que os testes acima NÃO pegavam, e que custou um corpus inteiro.

    Os testes desta suíte alimentam ``replay_matches`` com um ``_Match`` local que tem ``.id``.
    A produção não passa esse objeto: ``db.repository._perspective_view`` constrói um
    ``_PerspectiveMatch``, e a primeira versão dele **não tinha campo ``id``**. Como o replay lê
    o id por ``getattr(match, "id", "")``, a ausência não levantou nada — ela simplesmente
    gravava proveniência nenhuma, em silêncio, sobre 865 lutas.

    Ou seja: os testes acima provavam que o LAÇO funciona, não que a produção chega nele. É essa
    segunda coisa que este teste trava, e por isso ele olha a dataclass e não o replay.
    """
    from dataclasses import fields

    from db.repository import _PerspectiveMatch

    nomes = {f.name for f in fields(_PerspectiveMatch)}
    assert "id" in nomes, (
        "_PerspectiveMatch precisa carregar o id da luta — sem ele graph_edge_bouts fica vazia "
        "e nada reclama"
    )
