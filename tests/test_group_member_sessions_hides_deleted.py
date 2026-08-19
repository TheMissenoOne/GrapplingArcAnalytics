"""O professor não lê sessão que o aluno apagou — e isso tem de continuar verdade.

Achado "D" da auditoria do GrapplingArcWeb. A função ``group_member_sessions()`` roda com
direitos de definidor e é a **única** exceção deliberada ao escopo de dono em ``user_sessions``:
0032 já a estreitou para tirar ``reflection`` e o ``notes`` de cada round, e a 0044 a estreita de
novo para sumir com o registro inteiro depois que o titular o retirou (LGPD, Art. 18).

Por que um teste de varredura de fonte e não um teste de banco: a suíte deste repo roda em SQLite
em memória e **nunca executa migration Postgres** — a nota de escopo da 0019 registra isso desde
sempre. Um teste que precisasse do Postgres não rodaria em lugar nenhum, e a alternativa honesta
a "não dá para testar" é testar o que dá: que o predicado está escrito na definição mais recente
da função.

Frágil de propósito. Se alguém reescrever a função sem o predicado, este teste falha e a decisão
de expor sessão apagada volta a ser explícita, com o Art. 18 na mão.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"

# `create or replace function public.group_member_sessions()` ... até o `$$;` que fecha o corpo.
CORPO = re.compile(
    r"create or replace function public\.group_member_sessions\(\).*?\$\$;",
    re.IGNORECASE | re.DOTALL,
)


def _definicoes_por_revisao() -> list[tuple[str, str]]:
    """Toda definição da função, com a revisão que a escreveu, em ordem de revisão."""
    encontradas: list[tuple[str, str]] = []
    for arquivo in sorted(VERSOES.glob("[0-9][0-9][0-9][0-9]_*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        for corpo in CORPO.findall(texto):
            encontradas.append((arquivo.name[:4], corpo))
    return encontradas


def test_a_definicao_mais_recente_esconde_sessao_apagada() -> None:
    definicoes = _definicoes_por_revisao()
    assert definicoes, "nenhuma definição de group_member_sessions encontrada — o regex quebrou?"

    # A última revisão que define a função é a que vale em produção. Dentro de uma revisão pode
    # haver duas (upgrade e downgrade); a de upgrade é a primeira.
    revisao_mais_nova = definicoes[-1][0]
    corpo_vigente = next(c for r, c in definicoes if r == revisao_mais_nova)

    assert "deleted_at is null" in corpo_vigente.lower(), (
        f"a definição da revisão {revisao_mais_nova} não filtra deleted_at. Um professor voltaria "
        "a ler sessão que o aluno apagou (LGPD Art. 18). Se a exposição for intencional, é "
        "decisão documentada, não uma linha que sumiu."
    )


def test_o_predicado_de_dono_continua_sendo_o_controle_de_acesso() -> None:
    """A 0044 acrescenta um filtro; não pode ter trocado o que decide QUEM lê."""
    definicoes = _definicoes_por_revisao()
    revisao_mais_nova = definicoes[-1][0]
    corpo_vigente = next(c for r, c in definicoes if r == revisao_mais_nova)

    assert "shares_group_as_professor(us.owner_id)" in corpo_vigente, (
        "o predicado de acesso saiu da função. `shares_group_as_professor` lê `auth.uid()` por "
        "dentro (0025) — é o que impede alguém de perguntar por um perfil que não é seu."
    )


def test_a_projecao_de_privacidade_da_0032_continua_de_pe() -> None:
    """Reflexão e notas de round nunca chegam ao professor. A 0044 não afrouxou isso."""
    definicoes = _definicoes_por_revisao()
    revisao_mais_nova = definicoes[-1][0]
    corpo_vigente = next(c for r, c in definicoes if r == revisao_mais_nova)

    assert "- 'reflection'" in corpo_vigente
    assert "- 'notes'" in corpo_vigente
