"""Unit and workflow-contract tests for the PostgreSQL session-identity verifier."""

from __future__ import annotations

from pathlib import Path

import pytest

import ci.verify_user_sessions_identity as verifier
from ci.verify_user_sessions_identity import compact_sql, require_local_ci_database

ROOT = Path(__file__).parent.parent


def test_require_local_ci_database_accepts_workflow_url() -> None:
    url = "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci"

    assert require_local_ci_database(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:secret@db.example.com:5432/grapplingarc_ci",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci?host=db.example.com",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci?hostaddr=203.0.113.10",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci?service=production",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci?sslmode=require",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci?",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci#host=db.example.com",
        "postgresql://postgres:postgres@localhost:5432/grapplingarc_ci#",
        "postgresql+psycopg://postgres:postgres@localhost:5432/grapplingarc_ci",
        "not-a-postgres-url",
    ],
)
def test_require_local_ci_database_rejects_non_ci_targets(url: str) -> None:
    with pytest.raises(ValueError, match="local CI PostgreSQL"):
        require_local_ci_database(url)


def test_alembic_restore_revalidates_actual_connection_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_upgrade(*args: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        "ci.verify_user_sessions_identity.command.upgrade", unexpected_upgrade
    )

    with pytest.raises(ValueError, match="local CI PostgreSQL"):
        verifier.ensure_head(
            "postgresql://postgres:postgres@localhost/grapplingarc_ci?host=db.example.com"
        )

    assert not called


def test_fixture_is_run_scoped() -> None:
    first = verifier.Fixture.create()
    second = verifier.Fixture.create()

    assert first.owners[0] != first.owners[1]
    assert set(first.owners).isdisjoint(second.owners)
    assert first.session_id != second.session_id


def test_run_verification_restores_cleans_and_asserts_after_primary_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = RuntimeError("exercise failed")
    fixture = verifier.Fixture.create()

    def fail_verification(url: str, actual_fixture: verifier.Fixture) -> None:
        assert url == "validated-url"
        assert actual_fixture is fixture
        calls.append("verify")
        raise original

    monkeypatch.setattr(verifier, "verify_database", fail_verification)
    monkeypatch.setattr(verifier, "ensure_head", lambda url: calls.append("restore"))
    monkeypatch.setattr(
        verifier,
        "cleanup",
        lambda url, actual_fixture: calls.append("cleanup"),
    )
    monkeypatch.setattr(verifier, "assert_head", lambda url: calls.append("assert"))

    with pytest.raises(RuntimeError, match="exercise failed") as caught:
        verifier.run_verification("validated-url", fixture)

    assert caught.value is original
    assert calls == ["verify", "restore", "cleanup", "assert"]


def test_run_verification_preserves_primary_failure_when_recovery_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = RuntimeError("downgrade failed unexpectedly")
    fixture = verifier.Fixture.create()

    def fail_verification(url: str, actual_fixture: verifier.Fixture) -> None:
        calls.append("verify")
        raise original

    def fail_restore(url: str) -> None:
        calls.append("restore")
        raise RuntimeError("restore failed")

    monkeypatch.setattr(verifier, "verify_database", fail_verification)
    monkeypatch.setattr(verifier, "ensure_head", fail_restore)
    monkeypatch.setattr(
        verifier,
        "cleanup",
        lambda url, actual_fixture: calls.append("cleanup"),
    )
    monkeypatch.setattr(verifier, "assert_head", lambda url: calls.append("assert"))

    with pytest.raises(RuntimeError, match="downgrade failed unexpectedly") as caught:
        verifier.run_verification("validated-url", fixture)

    assert caught.value is original
    assert isinstance(caught.value.__cause__, ExceptionGroup)
    assert "restore failed" in str(caught.value.__cause__.exceptions[0])
    assert calls == ["verify", "restore", "cleanup", "assert"]


def test_compact_sql_ignores_catalog_whitespace_and_parentheses() -> None:
    assert compact_sql(" ( owner_id = auth.uid() ) ") == "owner_id=auth.uid()"


def test_workflow_runs_verifier_immediately_after_upgrade() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    named_steps = [
        line.strip() for line in workflow.splitlines() if line.startswith("      - name:")
    ]

    upgrade = "- name: alembic upgrade head (empty DB)"
    verify = "- name: verify user_sessions identity on PostgreSQL"
    assert named_steps.index(verify) == named_steps.index(upgrade) + 1
    assert "uv run python ci/verify_user_sessions_identity.py" in workflow


def test_supabase_scaffold_supports_profile_provision_trigger() -> None:
    scaffold = (ROOT / "ci/supabase_scaffold.sql").read_text()
    assert "raw_user_meta_data jsonb" in scaffold


def test_supabase_scaffold_supports_storage_migrations() -> None:
    scaffold = (ROOT / "ci/supabase_scaffold.sql").read_text()
    assert "CREATE SCHEMA IF NOT EXISTS storage" in scaffold
    assert "CREATE TABLE IF NOT EXISTS storage.buckets" in scaffold
    assert "CREATE TABLE IF NOT EXISTS storage.objects" in scaffold
    assert "FUNCTION storage.foldername" in scaffold
