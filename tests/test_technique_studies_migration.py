"""0067 contract: technique_studies table shape/RLS + the matches grant revoke."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

from db import models
from db.base import Base

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0067_technique_studies_and_matches_rls.py"
).read_text()


def test_migration_enables_rls_and_creates_the_pro_select_policy() -> None:
    assert "alter table public.technique_studies enable row level security" in MIGRATION
    assert "drop policy if exists technique_studies_pro_select" in MIGRATION
    assert "create policy technique_studies_pro_select" in MIGRATION
    assert "p.is_pro = true" in MIGRATION
    assert "grant select on public.technique_studies to authenticated" in MIGRATION


def test_migration_revokes_the_dead_matches_grants_and_creates_no_policy() -> None:
    assert "revoke insert, select, update, delete on public.matches from anon, authenticated" in (
        MIGRATION
    )
    assert "create policy" not in MIGRATION.split("revoke insert, select, update, delete")[1]
    # athlete_matches doesn't exist in this schema (probed 2026-09-18) — nothing to revoke there.
    assert "athlete_matches" not in MIGRATION.split('"""')[0]


def test_downgrade_does_not_regrant_matches() -> None:
    downgrade = MIGRATION.split("def downgrade")[1]
    assert 'grant select on public.matches' not in downgrade.lower()
    assert "drop_table(\"technique_studies\")" in downgrade
    assert "matches" in downgrade  # the explanatory comment, not a grant statement


def test_model_matches_the_migration_shape() -> None:
    table = models.TechniqueStudy.__table__
    assert table.name == "technique_studies"
    assert table.c.node_key.primary_key
    assert {"schema_version", "payload", "generated_at"} <= set(table.c.keys())
    assert not table.foreign_keys


def _sqlite_session() -> Session:
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_technique_study_round_trips_through_sqlite() -> None:
    with _sqlite_session() as db:
        db.add(
            models.TechniqueStudy(
                node_key="closed guard",
                schema_version=1,
                payload={"stats": {"frequency": 42}},
            )
        )
        db.flush()

        row = db.execute(
            select(models.TechniqueStudy).where(models.TechniqueStudy.node_key == "closed guard")
        ).scalar_one()
        assert row.payload["stats"]["frequency"] == 42
        assert row.generated_at is not None
