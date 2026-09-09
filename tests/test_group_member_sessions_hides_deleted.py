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

# `create or replace function public.<nome>(...)` ... até o `$$;` que fecha o corpo.
def _corpos(nome: str, assinatura: str = r"\(\)") -> re.Pattern[str]:
    return re.compile(
        rf"create or replace function public\.{nome}{assinatura}.*?\$\$;",
        re.IGNORECASE | re.DOTALL,
    )


CORPO = _corpos("group_member_sessions")


def _definicoes_por_revisao(padrao: re.Pattern[str] = CORPO) -> list[tuple[str, str]]:
    """Toda definição da função, com a revisão que a escreveu, em ordem de revisão."""
    encontradas: list[tuple[str, str]] = []
    for arquivo in sorted(VERSOES.glob("[0-9][0-9][0-9][0-9]_*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        for corpo in padrao.findall(texto):
            encontradas.append((arquivo.name[:4], corpo))
    return encontradas


def _definicao_vigente(padrao: re.Pattern[str] = CORPO) -> str:
    """O corpo que vale em produção: o `upgrade()` da revisão mais nova que define a função.
    Dentro de uma revisão pode haver duas (upgrade e downgrade); a de upgrade é a primeira."""
    definicoes = _definicoes_por_revisao(padrao)
    assert definicoes, "nenhuma definição encontrada — o regex quebrou?"
    revisao_mais_nova = definicoes[-1][0]
    return next(c for r, c in definicoes if r == revisao_mais_nova)


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
    """A 0044 acrescenta um filtro; não pode ter trocado o que decide QUEM lê.

    A 0060 moveu o predicado para trás de ``can_read_member_row(us.owner_id,
    us.class_session_id)`` para poder abrir UMA aula a um convidado (não-membro que entrou por
    QR). Isso não afrouxa nada **se e somente se** o primeiro disjunto do helper continuar sendo
    literalmente ``shares_group_as_professor``: é ele que lê ``auth.uid()`` por dentro (0025) e
    impede alguém de perguntar por um perfil que não é seu. Então o teste aceita as duas formas —
    predicado inline ou pelo helper — e, na forma com helper, exige o disjunto de dono lá dentro.
    A propriedade protegida é a mesma da 0044; só o lugar onde ela está escrita mudou.
    """
    corpo_vigente = _definicao_vigente()

    inline = "shares_group_as_professor(us.owner_id)" in corpo_vigente
    via_helper = "can_read_member_row(us.owner_id, us.class_session_id)" in corpo_vigente
    assert inline or via_helper, (
        "o predicado de acesso saiu da função. `shares_group_as_professor` lê `auth.uid()` por "
        "dentro (0025) — é o que impede alguém de perguntar por um perfil que não é seu. Se um "
        "novo helper tomou o lugar de `can_read_member_row`, este teste tem de aprender o nome "
        "dele, não ser afrouxado."
    )
    if not via_helper:
        return

    corpo_helper = _definicao_vigente(_corpos("can_read_member_row", r"\([^)]*\)"))
    assert "select shares_group_as_professor(p_profile)" in corpo_helper, (
        "`can_read_member_row` deixou de começar pelo predicado de dono. O caminho do MEMBRO "
        "precisa ser byte-idêntico ao da 0044; o convidado é um segundo disjunto, nunca uma "
        "reescrita do primeiro."
    )
    # O disjunto de convidado é o único acréscimo permitido, e ele é fechado em UMA aula:
    # precisa da equipe daquela aula E de uma linha de consentimento para aquele class_session.
    assert "is_class_session_staff(p_class_session)" in corpo_helper
    assert "cg.class_session_id = p_class_session" in corpo_helper
    assert "cg.profile_id = p_profile" in corpo_helper


def test_a_projecao_de_privacidade_da_0032_continua_de_pe() -> None:
    """Reflexão e notas de round nunca chegam ao professor. A 0044 não afrouxou isso."""
    definicoes = _definicoes_por_revisao()
    revisao_mais_nova = definicoes[-1][0]
    corpo_vigente = next(c for r, c in definicoes if r == revisao_mais_nova)

    assert "- 'reflection'" in corpo_vigente
    assert "- 'notes'" in corpo_vigente
