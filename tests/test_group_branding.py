"""alembic 0056 — group branding columns, groups' first UPDATE policy, gym-logos storage.

Source-scan, same reason as ``tests/test_class_planning_and_instructionals.py``: the suite runs
on SQLite in-memory and never executes a Postgres migration, so a policy body or a bucket's
config can only be checked by reading the tracked text of the revision that defines it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0056_group_branding.py"
)


@pytest.fixture()
def source() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


# ── columns + CHECK ───────────────────────────────────────────────────────


def test_adds_branding_columns(source: str) -> None:
    assert '"logo_url"' in source
    assert '"description"' in source
    assert '"accent_color"' in source


def test_accent_color_check_is_hex_and_allows_null(source: str) -> None:
    assert "ck_groups_accent_color_hex" in source
    assert "accent_color is null or accent_color ~ '^#[0-9a-fA-F]{6}$'" in source


# ── groups UPDATE — first one this table ever gets ───────────────────────


def test_groups_gains_owner_only_update_policy(source: str) -> None:
    # Orchestrator decision (2026-09-04): owner-only, same shape every other groups-row write
    # already uses (group_invites_owner_all, set_member_role, transfer_group_ownership) — not
    # is_group_owner_or_professor, which is for CONTENT tables about the group, not this row.
    assert "create policy groups_update_owner on public.groups for update" in source
    body = source.split("create policy groups_update_owner on public.groups")[1].split(";\n")[0]
    assert "owner_id = auth.uid()" in body
    assert "with check (owner_id = auth.uid())" in body
    assert "is_group_owner_or_professor(id)" not in body


# ── storage: gym-logos ─────────────────────────────────────────────────────


def test_gym_logos_bucket_is_public_with_size_and_mime_limits(source: str) -> None:
    assert (
        "insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)\n"
        "    values ('gym-logos', 'gym-logos', true, 1048576, array['image/*'])" in source
    )
    # Not reused as a policy target for a bucket built for a different access question.
    assert "bucket_id = 'session-videos'" not in source
    assert "bucket_id = 'user-media'" not in source
    assert "bucket_id = 'instructional-media'" not in source


def test_gym_logos_writes_are_owner_or_professor_no_select_policy(source: str) -> None:
    for verb, cmd in (
        ("insert", "gym_logos_owner_prof_insert"),
        ("update", "gym_logos_owner_prof_update"),
        ("delete", "gym_logos_owner_prof_delete"),
    ):
        assert f"create policy {cmd} on storage.objects for {verb}" in source
        body = source.split(f"create policy {cmd}")[1].split(";\n")[0]
        assert "is_group_owner_or_professor(((storage.foldername(name))[1])::uuid)" in body

    # Public bucket serves reads via the unauthenticated public-URL route — no SELECT policy
    # needed or written, unlike the private buckets (0024/0042/0050).
    assert "for select" not in source


def test_downgrade_does_not_drop_bucket_or_delete_objects(source: str) -> None:
    downgrade = source.split("def downgrade()")[1]
    assert "delete from storage.buckets" not in downgrade
    assert "drop_column" in downgrade
    assert "drop_constraint" in downgrade
