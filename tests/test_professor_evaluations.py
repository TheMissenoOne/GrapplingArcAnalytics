"""``professor_evaluations`` (alembic 0054) — a avaliação do professor, separada do Elo.

Nunca escreve em ``graphs``/``graph_edges``/``athletes``. É uma opinião do professor sobre o
aluno, não um dado derivado do grafo — por isso é uma tabela própria em vez de uma coluna em
qualquer coisa que já alimenta o rating.

RLS: professor/owner do grupo escrevem e leem tudo do grupo; o aluno lê só as suas. Mesma forma
de ``is_group_owner_or_professor`` que ``class_plan_templates``/``instructionals`` (0050) já
usam para "autorado pelo professor, escopo do grupo".

Varredura de fonte pela mesma razão da 0045/0050: SQLite não executa RLS.
"""

from __future__ import annotations

from pathlib import Path

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"
ARQUIVO = VERSOES / "0054_professor_scoped_rating_and_evaluations.py"


def _texto() -> str:
    return ARQUIVO.read_text(encoding="utf-8")


def test_tabela_existe_e_referencia_grupo_e_pessoas() -> None:
    texto = _texto()
    assert '"professor_evaluations"' in texto
    assert 'sa.ForeignKey("groups.id"' in texto
    assert '"student_id"' in texto
    assert '"professor_id"' in texto
    assert '"rating_note"' in texto
    assert '"score"' in texto


def test_rls_habilitada_e_escopada_por_papel() -> None:
    texto = _texto()
    assert "alter table public.professor_evaluations enable row level security;" in texto

    # Leitura: staff do grupo OU o próprio aluno.
    assert "professor_evaluations_select" in texto
    assert "is_group_owner_or_professor(group_id)" in texto
    assert "student_id = auth.uid()" in texto

    # Escrita: só staff, e só em nome de si mesmo (não pode gravar em nome de outro professor).
    assert "professor_evaluations_insert" in texto
    assert "professor_id = auth.uid()" in texto


def test_nunca_escreve_no_elo() -> None:
    texto = _texto()
    for tabela_proibida in ("graphs", "graph_edges", "athletes"):
        # A tabela não pode aparecer como alvo de update/insert nesta migration — só como
        # leitura dentro das funções de rating, que são funções DIFERENTES desta seção.
        assert f"update public.{tabela_proibida}" not in texto
        assert f"into public.{tabela_proibida}" not in texto
