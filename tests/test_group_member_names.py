"""O professor lê o NOME do aluno, e só o nome.

``group_member_names`` (alembic 0045) existe porque a alternativa óbvia está errada: abrir uma
política de SELECT em ``profiles`` para o professor entrega a LINHA inteira — ``belt_rank``,
``is_pro``, ``archetype_id`` — já que RLS é por linha, não por coluna. "A gente só seleciona o
nome no cliente" não é fronteira, é hábito, e a próxima consulta quebra.

Mesma forma que a 0032 escolheu para o mesmo problema: função SECURITY DEFINER que projeta só o
que precisa, com a pergunta de acesso respondida por dentro.

Varredura de fonte porque a suíte roda em SQLite e nunca executa migration Postgres (nota de
escopo da 0019). Frágil de propósito: se alguém acrescentar coluna à projeção, isso falha e a
decisão de privacidade volta a ser explícita.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"

CORPO = re.compile(
    r"create or replace function public\.group_member_names\(.*?\$\$;",
    re.IGNORECASE | re.DOTALL,
)


def _corpo_vigente() -> str:
    for arquivo in sorted(VERSOES.glob("[0-9][0-9][0-9][0-9]_*.py"), reverse=True):
        achados: list[str] = CORPO.findall(arquivo.read_text(encoding="utf-8"))
        if achados:
            return achados[0]
    raise AssertionError("nenhuma definição de group_member_names encontrada")


def test_projeta_apenas_id_e_nome() -> None:
    corpo = _corpo_vigente()
    assinatura = corpo.split("as $$")[0]

    assert "profile_id uuid" in assinatura
    assert "full_name text" in assinatura

    for coluna in ("belt_rank", "belt_degrees", "is_pro", "archetype_id", "is_guest"):
        assert coluna not in corpo, (
            f"'{coluna}' entrou na projeção. RLS é por linha; esta função é a fronteira de "
            "coluna. Acrescentar campo aqui é decisão de privacidade, com revisão própria."
        )


def test_o_acesso_e_decidido_por_shares_group_as_professor() -> None:
    """A pergunta de acesso não pode vir de parâmetro — `shares_group_as_professor` lê
    `auth.uid()` por dentro (0025), então o id de grupo estreita QUAIS alunos, nunca DE QUEM."""
    corpo = _corpo_vigente()

    assert "shares_group_as_professor(" in corpo
    assert "security definer" in corpo.lower()
    # `set search_path` fixo: sem isso um schema no caminho de busca do chamador poderia
    # sequestrar `shares_group_as_professor` dentro de uma função com direitos de definidor.
    assert "search_path" in corpo.lower()


def test_a_funcao_nao_e_exposta_a_anon() -> None:
    arquivo = (VERSOES / "0045_group_member_names.py").read_text(encoding="utf-8")

    assert "revoke all on function public.group_member_names(uuid) from anon;" in arquivo
    assert "revoke all on function public.group_member_names(uuid) from public;" in arquivo
    assert "grant execute on function public.group_member_names(uuid) to authenticated;" in arquivo
