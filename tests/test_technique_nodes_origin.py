"""``technique_nodes.origin`` (alembic 0065) — source-scan, same convention as
``test_profile_label_prefs.py``: SQLite in-memory never executes a Postgres migration, so the
DDL shape is checked by reading the migration text.
"""

from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0065_technique_nodes_origin.py"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code() -> str:
    """Source with the module docstring stripped — prose mentioning a word doesn't
    false-positive a check for that word in the actual DDL."""
    src = _source()
    first = src.index('"""')
    second = src.index('"""', first + 3)
    return src[second + 3 :]


def test_revision_chain() -> None:
    code = _code()
    assert 'revision = "0065"' in code
    assert 'down_revision = "0064"' in code


def test_column_added_nullable() -> None:
    code = _code()
    assert 'sa.Column("origin", sa.Text(), nullable=True)' in code


def test_check_constraint_restricts_values() -> None:
    code = _code()
    assert "ck_technique_nodes_origin" in code
    assert "origin in ('curated', 'dataset', 'corpus')" in code
    assert "origin is null or" in code  # NULL stays valid (pre-backfill)


def test_no_new_rls_policy_or_grant() -> None:
    """0004's table-level `grant select ... to anon, authenticated` and
    `technique_nodes_public_read using (true)` already cover any column, new or old."""
    code = _code()
    assert "create policy" not in code
    assert "grant " not in code


def test_downgrade_reverses_upgrade() -> None:
    code = _code()
    downgrade = code[code.index("def downgrade") :]
    assert "drop constraint if exists ck_technique_nodes_origin" in downgrade
    assert 'drop_column("technique_nodes", "origin")' in downgrade
    assert "drop index if exists idx_technique_nodes_origin" in downgrade


def test_model_mirrors_migration() -> None:
    models_src = (
        Path(__file__).resolve().parents[1] / "db" / "models.py"
    ).read_text(encoding="utf-8")
    assert 'origin: Mapped[str | None] = mapped_column(Text, nullable=True)' in models_src
