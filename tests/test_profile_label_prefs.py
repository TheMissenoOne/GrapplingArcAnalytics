"""``profiles.label_prefs`` (alembic 0064) — source-scan.

Same reason as ``test_session_video_analysis.py``: this suite runs against SQLite in-memory
and never executes a Postgres migration, so the grant shape is checked by reading the
migration text, not by running it.
"""

from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0064_profile_label_prefs.py"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code() -> str:
    """Source with the module docstring stripped, same convention as
    test_session_video_analysis.py's ``_code`` — prose mentioning a word doesn't
    false-positive a check for that word in the actual DDL."""
    src = _source()
    first = src.index('"""')
    second = src.index('"""', first + 3)
    return src[second + 3 :]


def test_revision_chain() -> None:
    code = _code()
    assert 'revision = "0064"' in code
    assert 'down_revision = "0063"' in code


def test_column_is_not_null_with_empty_object_default() -> None:
    code = _code()
    assert 'sa.Column(\n            "label_prefs", JSONB, nullable=False' in code
    assert "'{}'::jsonb" in code


def test_column_grant_extends_0023_not_table_level() -> None:
    """0051/0058's convention: a column added after 0023's explicit per-column grant list is
    implicitly ungranted until named — no table-level `grant update` on profiles here."""
    code = _code()
    assert "grant update (label_prefs) on public.profiles" in code
    assert "grant update on public.profiles" not in code


def test_no_new_rls_policy() -> None:
    """profiles_update_own (0023) already gates the row to id = auth.uid() — this migration
    adds a column and a grant only, no `create policy`/`alter ... enable row level security`."""
    code = _code()
    assert "create policy" not in code
    assert "row level security" not in code


def test_downgrade_reverses_upgrade() -> None:
    code = _code()
    downgrade = code[code.index("def downgrade") :]
    assert 'drop_column("profiles", "label_prefs")' in downgrade
    assert "revoke update (label_prefs) on public.profiles" in downgrade
