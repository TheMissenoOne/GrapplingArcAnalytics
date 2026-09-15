"""``graph_edges.actor`` (alembic 0066) — source-scan, same convention as
``test_technique_nodes_origin.py``: SQLite in-memory never executes a Postgres migration, so the
DDL/function shape is checked by reading the migration text.
"""

from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0066_graph_edge_actor.py"
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
    assert 'revision = "0066"' in code
    assert 'down_revision = "0065"' in code


def test_column_added_nullable() -> None:
    code = _code()
    assert 'sa.Column("actor", sa.Text(), nullable=True)' in code


def test_check_constraint_restricts_values() -> None:
    code = _code()
    assert "ck_graph_edges_actor" in code
    assert "actor in ('you', 'partner', 'both')" in code
    assert "actor is null or" in code  # NULL stays valid (pre-0066 edges, athlete rows)


def test_replace_user_graph_accepts_and_stores_actor() -> None:
    # SQL bodies live in module-level string constants, not inside def upgrade() itself.
    code = _code()
    new_replace = code[code.index("_REPLACE_FN = ") : code.index("_REPLACE_FN_0037 =")]
    assert "nullif(e->>'actor', '')" in new_replace
    assert "actor)" in new_replace  # insert column list carries it
    assert "actor = excluded.actor" in new_replace  # on-conflict update carries it too


def test_group_member_graph_edges_returns_actor() -> None:
    code = _code()
    new_edges = code[code.index("_GRAPH_EDGES_FN = ") : code.index("_GRAPH_EDGES_FN_0057 =")]
    assert "actor text" in new_edges  # new return column
    assert "ge.actor" in new_edges


def test_downgrade_reverses_upgrade() -> None:
    code = _code()
    downgrade = code[code.index("def downgrade") :]
    assert "drop constraint if exists ck_graph_edges_actor" in downgrade
    assert 'drop_column("graph_edges", "actor")' in downgrade
    assert "_REPLACE_FN_0037" in downgrade
    assert "_GRAPH_EDGES_FN_0057" in downgrade


def test_pre_0066_function_bodies_have_no_actor() -> None:
    """The restored-on-downgrade function bodies (0037's `replace_user_graph`, 0057's
    `group_member_graph_edges`) must be byte-identical to what those revisions shipped — if
    either gained an `actor` mention, the downgrade would silently keep the new behaviour."""
    code = _code()
    old_replace = code[code.index("_REPLACE_FN_0037 = ") : code.index("_REPLACE_SIG =")]
    old_edges = code[code.index("_GRAPH_EDGES_FN_0057 = ") : code.index("\n\ndef upgrade")]
    assert "actor" not in old_replace
    assert "actor" not in old_edges


def test_no_new_rls_policy() -> None:
    """Column-level, not table-level — 0037's existing `graph_edges` policies (user-owner /
    athlete-published-read) already cover any column, new or old. Only function grants change,
    which are re-granted (not created fresh) because DROP FUNCTION wipes them."""
    code = _code()
    assert "create policy" not in code


def test_model_mirrors_migration() -> None:
    models_src = (Path(__file__).resolve().parents[1] / "db" / "models.py").read_text(
        encoding="utf-8"
    )
    assert 'actor: Mapped[str | None] = mapped_column(Text, nullable=True)' in models_src
