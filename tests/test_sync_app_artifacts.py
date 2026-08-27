"""`scripts/sync_app_artifacts.py` — file-copy diffing, ontology sanity gate, and score
injection. All temp-dir/in-memory; no DB, no network (`load_fresh_scores` shells out to a
DB-backed process and is exercised manually, not here).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.sync_app_artifacts import (
    inject_scores,
    sync_nodes_library,
    sync_text_file,
    verify_ontology_seed,
)


# ── sync_text_file ────────────────────────────────────────────────────────────────
def test_sync_text_file_unchanged_when_identical(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    dst = tmp_path / "dst.json"
    src.write_text("same\n", encoding="utf-8")
    dst.write_text("same\n", encoding="utf-8")

    changed, msg = sync_text_file(src, dst, check=False, label="x")

    assert changed is False
    assert "unchanged" in msg


def test_sync_text_file_check_mode_reports_drift_and_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    dst = tmp_path / "dst.json"
    src.write_text("new\n", encoding="utf-8")
    dst.write_text("old\n", encoding="utf-8")

    changed, msg = sync_text_file(src, dst, check=True, label="x")

    assert changed is True
    assert "DRIFT" in msg
    assert dst.read_text(encoding="utf-8") == "old\n"  # untouched


def test_sync_text_file_writes_when_not_check(tmp_path: Path) -> None:
    src = tmp_path / "src.json"
    dst = tmp_path / "dst.json"
    src.write_text("new\n", encoding="utf-8")
    dst.write_text("old\n", encoding="utf-8")

    changed, _msg = sync_text_file(src, dst, check=False, label="x")

    assert changed is True
    assert dst.read_text(encoding="utf-8") == "new\n"


def test_sync_text_file_missing_source_aborts(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        sync_text_file(tmp_path / "missing.json", tmp_path / "dst.json", check=False, label="x")


def test_sync_text_file_second_run_is_a_noop(tmp_path: Path) -> None:
    """Deterministic: syncing twice in a row reports no changes the second time."""
    src = tmp_path / "src.json"
    dst = tmp_path / "dst.json"
    src.write_text("v1\n", encoding="utf-8")
    dst.write_text("v0\n", encoding="utf-8")

    sync_text_file(src, dst, check=False, label="x")
    changed, _msg = sync_text_file(src, dst, check=False, label="x")

    assert changed is False


# ── verify_ontology_seed ────────────────────────────────────────────────────────────
def test_verify_ontology_seed_accepts_populated_doc() -> None:
    doc = {"position_decision_space": {"a": {}, "b": {}}, "athlete_profiles": [{}]}
    assert verify_ontology_seed(doc) == (2, 1)


@pytest.mark.parametrize(
    "doc",
    [
        {"position_decision_space": {}, "athlete_profiles": [{}]},
        {"position_decision_space": {"a": {}}, "athlete_profiles": []},
        {"position_decision_space": {}, "athlete_profiles": []},
        {},
    ],
)
def test_verify_ontology_seed_aborts_on_degenerate_doc(doc: dict[str, object]) -> None:
    with pytest.raises(SystemExit):
        verify_ontology_seed(doc)


# ── inject_scores ────────────────────────────────────────────────────────────────
def test_inject_scores_sets_matched_fields() -> None:
    nodes = [{"name": "Heel Hook", "type": "submission"}]
    scores = {"heel hook": {"rrb": 0.6, "eloPercentile": 75.0}}

    out, counts = inject_scores(nodes, scores)

    assert out[0]["rrb"] == 0.6
    assert out[0]["eloPercentile"] == 75.0
    assert counts == {"changed": 1, "with_rrb": 1, "with_elo_percentile": 1}


def test_inject_scores_leaves_unmatched_node_untouched_no_null() -> None:
    nodes = [{"name": "Made Up Technique", "type": "concept"}]
    scores = {"heel hook": {"rrb": 0.6}}

    out, counts = inject_scores(nodes, scores)

    assert "rrb" not in out[0]
    assert "eloPercentile" not in out[0]
    assert counts == {"changed": 0, "with_rrb": 0, "with_elo_percentile": 0}


def test_inject_scores_only_sets_fields_present_in_entry() -> None:
    nodes = [{"name": "Twister", "type": "submission"}]
    scores = {"twister": {"eloPercentile": 33.0}}  # no rrb

    out, _counts = inject_scores(nodes, scores)

    assert "rrb" not in out[0]
    assert out[0]["eloPercentile"] == 33.0


def test_inject_scores_drops_stale_field_no_longer_in_entry() -> None:
    """A node that carried `rrb` from a previous sync loses it if the fresh corpus
    no longer supports it — the file must reflect CURRENT Analytics output, not
    accumulate history."""
    nodes = [{"name": "Twister", "type": "submission", "rrb": 0.4}]
    scores = {"twister": {"eloPercentile": 33.0}}

    out, _counts = inject_scores(nodes, scores)

    assert "rrb" not in out[0]
    assert out[0]["eloPercentile"] == 33.0


def test_inject_scores_matches_via_translations_and_variations() -> None:
    nodes = [{
        "name": "Guarda Fechada", "type": "guard",
        "translations": {"en": "Closed Guard"},
        "variations": ["full guard"],
    }]
    scores = {"closed guard": {"rrb": 0.2}}

    out, counts = inject_scores(nodes, scores)

    assert out[0]["rrb"] == 0.2
    assert counts["with_rrb"] == 1


def test_inject_scores_is_deterministic() -> None:
    nodes = [{"name": "Armbar", "type": "submission"}, {"name": "Guard Pass", "type": "pass"}]
    scores = {"armbar": {"rrb": 0.5}, "guard pass": {"eloPercentile": 40.0}}

    first, _ = inject_scores(nodes, scores)
    second, _ = inject_scores(nodes, scores)

    assert first == second


# ── sync_nodes_library (file-level, temp-dir) ───────────────────────────────────────
def test_sync_nodes_library_check_mode_reports_drift_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "nodes.json"
    path.write_text('[\n  {\n    "name": "Heel Hook",\n    "type": "submission"\n  }\n]\n',
                     encoding="utf-8")
    scores = {"heel hook": {"rrb": 0.6}}

    changed, msg = sync_nodes_library(path, scores, check=True)

    assert changed is True
    assert "DRIFT" in msg
    assert '"rrb"' not in path.read_text(encoding="utf-8")  # untouched on disk


def test_sync_nodes_library_second_run_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "nodes.json"
    path.write_text('[\n  {\n    "name": "Heel Hook",\n    "type": "submission"\n  }\n]\n',
                     encoding="utf-8")
    scores = {"heel hook": {"rrb": 0.6}}

    changed1, _ = sync_nodes_library(path, scores, check=False)
    changed2, msg2 = sync_nodes_library(path, scores, check=False)

    assert changed1 is True
    assert changed2 is False
    assert "unchanged" in msg2
