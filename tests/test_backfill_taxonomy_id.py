"""Taxonomy backfill — tier gating, validation, and dry-run safety."""

from __future__ import annotations

import json

import pytest

from scripts.backfill_taxonomy_id import backfill, load_mapping


def _write(tmp_path, proposals, version=2):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"taxonomy_version": version, "proposals": proposals}))
    return p


class _Row:
    def __init__(self, node_key, taxonomy_id=None):
        self.node_key = node_key
        self.taxonomy_id = taxonomy_id


class _Session:
    """Minimal stand-in: `execute(...).scalars()` yields rows, `commit()` is recorded."""

    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    def execute(self, _stmt):
        rows = self._rows

        class _R:
            def scalars(self):
                return iter(rows)

        return _R()

    def commit(self):
        self.committed = True


def test_only_the_requested_tier_is_loaded(tmp_path):
    path = _write(tmp_path, [
        {"node_key": "armbar", "subcategory": "arm-lock", "tier": "auto"},
        {"node_key": "kimura", "subcategory": "shoulder-lock", "tier": "review"},
        {"node_key": "choke", "subcategory": "strangle", "tier": "manual"},
    ])
    assert load_mapping(path, {"auto"}) == {"armbar": "arm-lock"}
    assert set(load_mapping(path, {"auto", "review"})) == {"armbar", "kimura"}


def test_proposals_without_a_subcategory_are_skipped(tmp_path):
    path = _write(tmp_path, [{"node_key": "x", "subcategory": None, "tier": "auto"}])
    assert load_mapping(path, {"auto"}) == {}


def test_unknown_taxonomy_node_is_dropped_not_written(tmp_path):
    """A stale mapping file must not push a non-existent taxonomy id into the DB."""
    path = _write(tmp_path, [
        {"node_key": "armbar", "subcategory": "arm-lock", "tier": "auto"},
        {"node_key": "ghost", "subcategory": "not-a-real-node", "tier": "auto"},
    ])
    assert load_mapping(path, {"auto"}) == {"armbar": "arm-lock"}


def test_version_mismatch_refuses_to_run(tmp_path):
    path = _write(tmp_path, [{"node_key": "armbar", "subcategory": "arm-lock",
                              "tier": "auto"}], version=1)
    with pytest.raises(SystemExit, match="regenerate"):
        load_mapping(path, {"auto"})


def test_dry_run_changes_nothing():
    rows = [_Row("armbar")]
    session = _Session(rows)
    stats = backfill(session, {"armbar": "arm-lock"}, apply=False)
    assert stats["set"] == 1
    assert rows[0].taxonomy_id is None      # untouched
    assert session.committed is False


def test_apply_writes_and_commits():
    rows = [_Row("armbar")]
    session = _Session(rows)
    stats = backfill(session, {"armbar": "arm-lock"}, apply=True)
    assert stats["set"] == 1
    assert rows[0].taxonomy_id == "arm-lock"
    assert session.committed is True


def test_already_correct_rows_are_not_rewritten():
    rows = [_Row("armbar", "arm-lock")]
    stats = backfill(_Session(rows), {"armbar": "arm-lock"}, apply=True)
    assert stats["unchanged"] == 1
    assert stats["set"] == 0


def test_node_key_absent_from_the_db_is_counted_not_fatal():
    stats = backfill(_Session([]), {"armbar": "arm-lock"}, apply=True)
    assert stats["missing"] == 1
    assert stats["set"] == 0


def test_real_mapping_file_yields_the_verified_auto_tier():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "data" / "taxonomy_map.json"
    if not path.exists():
        pytest.skip("mapping not generated in this checkout")
    mapping = load_mapping(path, {"auto"})
    assert len(mapping) == 50
    assert mapping["armbar"] == "arm-lock"
    assert mapping["closed guard"] == "closed-guard"
