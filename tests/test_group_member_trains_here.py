"""``trains_here`` (alembic 0055): join_group() sets it per-invite, set_trains_here() is self-only.

Source-scan, same reason as ``test_group_member_sessions_hides_deleted.py`` — this suite runs
against SQLite and never executes a Postgres migration.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _corpo(nome_funcao: str, aridade: str) -> str:
    padrao = re.compile(
        rf"create or replace function public\.{nome_funcao}\({aridade}\).*?\$\$;",
        re.IGNORECASE | re.DOTALL,
    )
    for arquivo in sorted(VERSOES.glob("[0-9][0-9][0-9][0-9]_*.py"), reverse=True):
        achados: list[str] = padrao.findall(arquivo.read_text(encoding="utf-8"))
        if achados:
            return achados[0]
    raise AssertionError(f"nenhuma definição de {nome_funcao} encontrada")


def test_join_group_seta_trains_here_a_partir_do_papel_do_convite() -> None:
    corpo = _corpo("join_group", "invite_code text")
    assert "trains_here" in corpo
    assert "target_role = 'student'" in corpo


def test_set_trains_here_escreve_so_a_propria_linha() -> None:
    corpo = _corpo("set_trains_here", "p_group_id uuid, p_trains_here boolean")
    assert "profile_id = auth.uid()" in corpo
    assert "security definer" in corpo.lower()
    assert "search_path" in corpo.lower()


def test_set_trains_here_nao_exposto_a_anon() -> None:
    arquivo = (VERSOES / "0055_group_member_trains_here.py").read_text(encoding="utf-8")
    assert "revoke all on function public.set_trains_here(uuid, boolean) from public" in arquivo
    assert "revoke all on function public.set_trains_here(uuid, boolean) from anon" in arquivo
    assert (
        "grant execute on function public.set_trains_here(uuid, boolean) to authenticated"
        in arquivo
    )
