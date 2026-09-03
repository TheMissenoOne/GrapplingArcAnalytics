"""``session_video_jobs`` / ``session_video_analysis`` (alembic 0058) — source-scan.

Same reason as ``test_group_member_trains_here.py``: this suite runs against SQLite
in-memory and never executes a Postgres migration, so RLS/RPC/grant shape is checked by
reading the migration text, not by running it.
"""

from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0058_session_video_analysis.py"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code() -> str:
    """Source with the module docstring stripped — so prose mentioning a word (``is_pro``,
    ``storage.buckets``, ``session-videos``) doesn't false-positive a check for that word in
    the actual SQL/DDL."""
    src = _source()
    first = src.index('"""')
    second = src.index('"""', first + 3)
    return src[second + 3 :]


def test_every_create_policy_has_a_drop_if_exists_first() -> None:
    src = _source()
    for policy in (
        "session_video_jobs_owner_select",
        "session_video_analysis_owner_select",
    ):
        assert f"drop policy if exists {policy}" in src
        assert f"create policy {policy}" in src


def test_no_insert_or_update_policy_on_either_table() -> None:
    """Client writes go through the RPC (session_video_jobs) or nowhere (analysis,
    service-role only) — never a policy `for insert`/`for update`/`for all`."""
    src = _source()
    for bad in ("for insert", "for update", "for all"):
        assert bad not in src.lower()


def test_read_policies_are_owner_only_without_is_pro() -> None:
    """D9: unlike user_performance_snapshots (0021/0023), the read gate here is just
    ownership — an expired entitlement must not revoke access to an analysis already
    generated. Checked on the two `create policy ... for select` bodies specifically, not
    the whole file — the docstring/comments legitimately name `is_pro` when explaining the
    contrast with 0021/0023."""
    code = _code()
    for policy in ("session_video_jobs_owner_select", "session_video_analysis_owner_select"):
        start = code.index(f"create policy {policy}")
        body = code[start : start + 200]
        assert "using (owner_id = auth.uid())" in body
        assert "is_pro" not in body


def test_no_new_storage_bucket() -> None:
    """D1: video stays in session-videos (0024, unchanged path — its bucket policy is
    referenced here only in prose/comments), later artifacts go to user-media (0042) —
    both already in PRIVATE_BUCKETS. This migration inserts no bucket and gates no policy
    on `bucket_id`, so tests/test_private_buckets_parity.py needs no change."""
    code = _code()
    assert "insert into storage.buckets" not in code
    assert "bucket_id" not in code


def test_enqueue_video_job_checks_storage_path_ownership() -> None:
    src = _source()
    assert "security definer" in src.lower()
    assert "split_part(p_storage_path, '/', 1)" in src
    assert "auth.uid()" in src


def test_enqueue_video_job_not_exposed_to_anon() -> None:
    # Written as adjacent string-literal fragments in the migration (see _ENQUEUE_VIDEO_JOB
    # callers below) — check the pieces, not one concatenated literal.
    code = _code()
    assert "revoke all on function public.enqueue_video_job" in code
    assert code.count('"(text, text, text, text, text, jsonb) from public;"') == 1
    assert code.count('"(text, text, text, text, text, jsonb) from anon;"') == 1
    assert "grant execute on function public.enqueue_video_job" in code
    assert '"(text, text, text, text, text, jsonb) to authenticated;"' in code


def test_downgrade_reverses_everything_upgrade_creates() -> None:
    code = _code()
    downgrade = code[code.index("def downgrade") :]

    assert 'drop_table("session_video_analysis")' in downgrade
    assert 'drop_table("session_video_jobs")' in downgrade
    assert 'drop_column("profiles", "face_ref_path")' in downgrade
    assert 'drop_column("profiles", "face_consent_at")' in downgrade
    assert "drop function if exists public.enqueue_video_job" in downgrade
    assert "session_video_jobs_owner_select" in downgrade
    assert "session_video_analysis_owner_select" in downgrade


def test_profiles_column_grant_extends_0023_not_table_level() -> None:
    """0051's convention: a column added after 0023's explicit per-column grant list is
    implicitly ungranted until named — no client can self-grant is_pro-style bypass via a
    table-level `grant update` on profiles."""
    code = _code()
    assert "grant update (face_ref_path, face_consent_at) on public.profiles" in code
    assert "grant update on public.profiles" not in code
