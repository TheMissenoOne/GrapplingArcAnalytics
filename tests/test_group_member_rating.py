"""O professor lê o RATING atual do aluno (grafo próprio), nunca o corpus público.

``group_member_rating``/``group_member_graph_edges`` (alembic 0054) seguem a forma da 0045: função
SECURITY DEFINER que projeta só o que a decisão do dono autoriza — membro do grupo ⇒ vê rating +
grafo, escopado a ESTE grupo (``p_group_id``) e nunca "qualquer grupo que compartilhamos"
(``shares_group_as_professor`` sozinho, sem ``gm.group_id = p_group_id``, é a falha viva medida em
GA-31 no roadmap; as duas novas funções não repetem o padrão errado).

Nenhuma das duas toca ``owner_kind = 'athlete'`` — a leitura é sempre do grafo PRÓPRIO do
usuário, nunca do corpus público (item C do decision log: sem sangramento de escopo).

Varredura de fonte porque a suíte roda em SQLite e nunca executa migration Postgres (nota de
escopo da 0019, mesma forma da 0045).
"""

from __future__ import annotations

import re
from pathlib import Path

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _corpo(nome_funcao: str) -> str:
    padrao = re.compile(
        rf"create or replace function public\.{nome_funcao}\(.*?\$\$;",
        re.IGNORECASE | re.DOTALL,
    )
    for arquivo in sorted(VERSOES.glob("[0-9][0-9][0-9][0-9]_*.py"), reverse=True):
        achados: list[str] = padrao.findall(arquivo.read_text(encoding="utf-8"))
        if achados:
            return achados[0]
    raise AssertionError(f"nenhuma definição de {nome_funcao} encontrada")


def test_group_member_rating_projeta_so_user_elo() -> None:
    corpo = _corpo("group_member_rating")
    assinatura = corpo.split("as $$")[0]

    assert "user_elo" in assinatura
    for coluna in ("embedding", "archetype_report", "archetype_id", "schema_version"):
        assert coluna not in corpo, (
            f"'{coluna}' entrou na projeção de group_member_rating — a função só deve expor o "
            "número relativo, não o resto da linha de graphs."
        )


def test_group_member_graph_edges_nao_le_grafo_de_atleta() -> None:
    corpo = _corpo("group_member_graph_edges")

    assert "owner_kind = 'user'" in corpo, (
        "group_member_graph_edges deve ler só o grafo PRÓPRIO do aluno (owner_kind='user') — "
        "sem isso a leitura pode vazar para o corpus público de atletas."
    )
    assert "embedding" not in corpo


def test_ambas_escopam_por_grupo_e_nao_so_por_relacao() -> None:
    """GA-31 do roadmap: ``shares_group_as_professor`` sozinho responde 'compartilhamos ALGUM
    grupo', nunca 'este aluno está NESTE grupo'. As duas funções novas devem filtrar por
    ``gm.group_id = p_group_id`` além da checagem de papel."""

    for nome in ("group_member_rating", "group_member_graph_edges"):
        corpo = _corpo(nome)
        assert "gm.group_id = p_group_id" in corpo, f"{nome} não escopa por p_group_id"
        assert "shares_group_as_professor(" in corpo, f"{nome} não checa o papel do leitor"
        assert "security definer" in corpo.lower()
        assert "search_path" in corpo.lower()


def test_funcoes_nao_expostas_a_anon() -> None:
    """0028 registra que revogar só de PUBLIC não remove o grant padrão do Supabase para
    ``anon`` — a 0054 revoga as duas funções novas de ambos, em loop sobre a mesma lista que
    concede a ``authenticated`` (mesma forma que a 0050 usa para ``create_group``/
    ``set_member_role``, ao contrário da 0045/0032 que escrevem cada revoke por extenso)."""

    arquivo = (VERSOES / "0054_professor_scoped_rating_and_evaluations.py").read_text(
        encoding="utf-8"
    )
    assert '"public.group_member_rating(uuid, uuid)"' in arquivo
    assert '"public.group_member_graph_edges(uuid, uuid)"' in arquivo
    assert 'revoke all on function {fn} from public' in arquivo
    assert 'revoke all on function {fn} from anon' in arquivo


def test_group_member_graph_edges_projeta_o_node_canonico() -> None:
    """0057: o professor lê um aluno com rótulos em PT — sem ``canonical_node_key`` a view do
    professor nunca casa esse nó com o corpus (mesmo gap que ``corpusInsights.ts`` já resolve no
    lado do próprio atleta). ``sn``/``tn`` já estão joinados; a projeção só precisa das colunas
    que já estão na mesa, sem join novo."""
    corpo = _corpo("group_member_graph_edges")
    assinatura = corpo.split("as $$")[0]

    assert "source_canonical text" in assinatura
    assert "target_canonical text" in assinatura
    assert "sn.canonical_node_key" in corpo
    assert "tn.canonical_node_key" in corpo


def test_0057_recoloca_os_grants_do_graph_edges_apos_o_drop() -> None:
    """Mesma razão que o teste equivalente de ``group_member_names``: mudar as colunas de
    ``returns table`` exige DROP FUNCTION, que apaga os grants da 0054 — a 0057 tem que
    reconceder, não só recriar o corpo."""
    arquivo = (VERSOES / "0057_group_member_belt_and_canonical.py").read_text(encoding="utf-8")

    assert "drop function if exists public.group_member_graph_edges(uuid, uuid);" in arquivo
    assert (
        "grant execute on function public.group_member_graph_edges(uuid, uuid) to authenticated;"
        in arquivo
    )
