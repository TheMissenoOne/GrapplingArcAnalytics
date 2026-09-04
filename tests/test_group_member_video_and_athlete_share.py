"""``share_video_analysis``/``share_athlete_link`` (alembic 0059) — self-only setters, gated
projections. Same molds: `trains_here`/`set_trains_here` (0055) for the boolean + self-write
RPC, `group_member_rating`/`group_member_graph_edges` (0054, tightened by 0057) for the
group-scoped SECURITY DEFINER projection.

Source-scan, same reason as ``test_group_member_trains_here.py``/``test_session_video_
analysis.py``: this suite runs against SQLite in-memory and never executes a Postgres migration.
"""

from __future__ import annotations

import re
from pathlib import Path

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSOES / "0059_group_member_video_and_athlete_share.py"


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _corpo(nome_funcao: str, aridade: str) -> str:
    padrao = re.compile(
        rf"create or replace function public\.{nome_funcao}\({aridade}\).*?\$\$;",
        re.IGNORECASE | re.DOTALL,
    )
    achados: list[str] = padrao.findall(_source())
    if not achados:
        raise AssertionError(f"nenhuma definição de {nome_funcao} encontrada em 0059")
    return achados[0]


# ── self-only setters ────────────────────────────────────────────────────────────────


def test_set_share_video_analysis_escreve_so_a_propria_linha() -> None:
    corpo = _corpo("set_share_video_analysis", "p_group_id uuid, p_share boolean")
    assert "profile_id = auth.uid()" in corpo
    assert "security definer" in corpo.lower()
    assert "search_path" in corpo.lower()
    assert "set share_video_analysis = p_share" in corpo


def test_set_share_athlete_link_escreve_so_a_propria_linha() -> None:
    corpo = _corpo("set_share_athlete_link", "p_group_id uuid, p_share boolean")
    assert "profile_id = auth.uid()" in corpo
    assert "security definer" in corpo.lower()
    assert "search_path" in corpo.lower()
    assert "set share_athlete_link = p_share" in corpo


# ── gated projections ────────────────────────────────────────────────────────────────


def test_group_member_video_analysis_projeta_so_as_colunas_publicas_do_plano() -> None:
    corpo = _corpo("group_member_video_analysis", "p_group_id uuid, p_profile_id uuid")
    assinatura = corpo.split("as $$")[0]

    for coluna in (
        "session_id",
        "round_id",
        "generated_at",
        "events",
        "sequences",
        "difficulty_derived",
        "confidence",
        "highlights",
    ):
        assert coluna in assinatura, f"'{coluna}' falta na projeção de group_member_video_analysis"

    for proibida in ("pdf_path", "clip_paths", "motion", "schema_version", "difficulty_inputs"):
        assert proibida not in corpo, (
            f"'{proibida}' vazou em group_member_video_analysis — user-media/derivação interna "
            "não deve chegar ao professor (0042/0058 D9)."
        )


def test_group_member_video_analysis_le_a_tabela_privada_certa_e_escopa_por_grupo() -> None:
    corpo = _corpo("group_member_video_analysis", "p_group_id uuid, p_profile_id uuid")
    assert "public.session_video_analysis" in corpo
    assert "gm.group_id = p_group_id" in corpo
    assert "gm.profile_id = p_profile_id" in corpo
    assert "gm.share_video_analysis" in corpo
    assert "shares_group_as_professor(p_profile_id)" in corpo


def test_group_member_athlete_projeta_id_e_nome_do_atleta_publico() -> None:
    corpo = _corpo("group_member_athlete", "p_group_id uuid, p_profile_id uuid")
    assinatura = corpo.split("as $$")[0]

    assert "athlete_id" in assinatura
    assert re.search(r"\bname\b", assinatura)
    for proibida in ("nickname", "team", "weight_class", "belt", "elo", "gender"):
        assert proibida not in corpo, f"'{proibida}' vazou em group_member_athlete"


def test_group_member_athlete_le_via_profiles_athlete_id_e_escopa_por_grupo() -> None:
    corpo = _corpo("group_member_athlete", "p_group_id uuid, p_profile_id uuid")
    assert "public.profiles p" in corpo
    assert "public.athletes a" in corpo
    assert "a.id = p.athlete_id" in corpo
    assert "gm.group_id = p_group_id" in corpo
    assert "gm.profile_id = p_profile_id" in corpo
    assert "gm.share_athlete_link" in corpo
    assert "shares_group_as_professor(p_profile_id)" in corpo


# ── grants ────────────────────────────────────────────────────────────────────────────


def test_todas_as_quatro_funcoes_revogadas_de_public_e_anon_concedidas_a_authenticated() -> None:
    src = _source()
    for fn in (
        "public.set_share_video_analysis(uuid, boolean)",
        "public.set_share_athlete_link(uuid, boolean)",
        "public.group_member_video_analysis(uuid, uuid)",
        "public.group_member_athlete(uuid, uuid)",
    ):
        assert f'"{fn}"' in src, f"{fn} não está na lista revogada/concedida"

    assert "revoke all on function {fn} from public" in src
    assert "revoke all on function {fn} from anon" in src
    assert "grant execute on function {fn} to authenticated" in src


def test_downgrade_reverte_tudo_que_upgrade_cria() -> None:
    src = _source()
    downgrade = src[src.index("def downgrade") :]

    assert 'drop_column("group_members", "share_athlete_link")' in downgrade
    assert 'drop_column("group_members", "share_video_analysis")' in downgrade
    assert "drop function if exists {fn}" in downgrade
