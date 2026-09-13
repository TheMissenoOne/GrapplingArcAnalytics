"""`scripts/sync_app_artifacts.py` — file-copy diffing, ontology sanity gate, and score
injection. All temp-dir/in-memory; no DB, no network (`load_fresh_scores` shells out to a
DB-backed process and is exercised manually, not here).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.sync_app_artifacts import (
    inject_scores,
    merge_curated_identity,
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
# curated=[] in these: they exercise score injection only, decoupled from the real
# curated library (merge_curated_identity has its own tests below).
def test_sync_nodes_library_check_mode_reports_drift_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "nodes.json"
    path.write_text('[\n  {\n    "name": "Heel Hook",\n    "type": "submission"\n  }\n]\n',
                     encoding="utf-8")
    scores = {"heel hook": {"rrb": 0.6}}

    changed, msg = sync_nodes_library(path, scores, [], check=True)

    assert changed is True
    assert "DRIFT" in msg
    assert '"rrb"' not in path.read_text(encoding="utf-8")  # untouched on disk


def test_sync_nodes_library_second_run_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "nodes.json"
    path.write_text('[\n  {\n    "name": "Heel Hook",\n    "type": "submission"\n  }\n]\n',
                     encoding="utf-8")
    scores = {"heel hook": {"rrb": 0.6}}

    changed1, _ = sync_nodes_library(path, scores, [], check=False)
    changed2, msg2 = sync_nodes_library(path, scores, [], check=False)

    assert changed1 is True
    assert changed2 is False
    assert "unchanged" in msg2


def test_sync_nodes_library_applies_curated_identity_then_scores(tmp_path: Path) -> None:
    """The curated merge and the score injection compose: a curated-only entry
    reaches the file AND picks up a score in one sync call."""
    path = tmp_path / "nodes.json"
    path.write_text('[]\n', encoding="utf-8")
    curated = [{
        "en": "Armbar", "pt": "Chave de Braço", "type": "submission", "variants": ["armbar"],
    }]
    scores = {"armbar": {"rrb": 0.5}}

    changed, _msg = sync_nodes_library(path, scores, curated, check=False)

    assert changed is True
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written[0]["name"] == "Chave de Braço"
    assert written[0]["rrb"] == 0.5


# ── merge_curated_identity ───────────────────────────────────────────────────────
def test_merge_new_curated_entry_gets_deterministic_id_and_fixed_date() -> None:
    curated = [{
        "en": "Armbar", "pt": "Chave de Braço", "type": "submission", "variants": ["armbar"],
    }]

    out1, report1 = merge_curated_identity(curated, [])
    out2, report2 = merge_curated_identity(curated, [])

    assert out1 == out2  # deterministic across runs, no wall-clock
    assert report1 == report2
    node = out1[0]
    # New entry: `name` = curated `pt` (no existing App row to preserve one from —
    # see the "existing entry keeps its own name" test below for that case).
    assert node["name"] == "Chave de Braço"
    assert node["type"] == "submission"
    assert node["translations"] == {"pt": "Chave de Braço", "en": "Armbar"}
    assert node["variations"] == ["armbar"]
    assert node["_id"]["$oid"] and len(node["_id"]["$oid"]) == 24
    assert report1 == {
        "curated_count": 1, "matched": 0, "new_count": 1,
        "app_only_count": 0, "app_only_names": [],
    }


def test_merge_existing_entry_keeps_id_and_created_at_curated_wins_rest() -> None:
    existing = [{
        "_id": {"$oid": "abc123"},
        "name": "Chave de Braço Velha",
        "createdAt": {"$date": "2020-01-01T00:00:00.000Z"},
        "type": "control",  # stale type — curated overrides
        "translations": {"pt": "Chave de Braço Velha", "en": "Armbar"},
        "updatedAt": {"$date": "2020-01-01T00:00:00.000Z"},
        "variations": ["old variant"],
        "rrb": 0.9,  # untouched by the merge, only score injection may change it
    }]
    curated = [{
        "en": "Armbar", "pt": "Chave de Braço", "type": "submission", "variants": ["armbar"],
    }]

    out, report = merge_curated_identity(curated, existing)

    assert len(out) == 1
    node = out[0]
    assert node["_id"] == {"$oid": "abc123"}  # preserved
    assert node["createdAt"] == {"$date": "2020-01-01T00:00:00.000Z"}  # preserved
    assert node["name"] == "Chave de Braço Velha"  # preserved — not curated's to overwrite
    assert node["type"] == "submission"  # curated wins
    assert node["translations"] == {"pt": "Chave de Braço", "en": "Armbar"}  # curated wins
    assert node["variations"] == ["armbar"]  # curated wins
    assert node["rrb"] == 0.9  # untouched field carried over
    assert report["matched"] == 1
    assert report["new_count"] == 0


def test_merge_app_only_entry_appended_after_curated_block_never_dropped() -> None:
    existing = [{
        "name": "Electric Chair", "type": "control",
        "translations": {"pt": "Electric Chair", "en": "Electric Chair"},
        "variations": ["electric chair"],
    }]
    curated = [{
        "en": "Armbar", "pt": "Chave de Braço", "type": "submission", "variants": ["armbar"],
    }]

    out, report = merge_curated_identity(curated, existing)

    assert [n["translations"]["en"] for n in out] == ["Armbar", "Electric Chair"]
    assert report["app_only_count"] == 1
    assert report["app_only_names"] == ["Electric Chair"]


def test_merge_preserves_curated_order_for_collision_priority() -> None:
    """First-writer-wins lookups (App `buildCategoryResolver`, Analytics
    `technique_match._index`) both resolve a shared alias to whichever library
    entry comes FIRST — curated order must survive into the App file so both
    sides pick the same one."""
    curated = [
        {"en": "Zebra Lock", "pt": "Zebra", "type": "submission",
         "variants": ["shared alias"]},
        {"en": "Aardvark Choke", "pt": "Aardvark", "type": "submission",
         "variants": ["shared alias"]},
    ]

    out, _report = merge_curated_identity(curated, [])

    assert [n["translations"]["en"] for n in out] == ["Zebra Lock", "Aardvark Choke"]


# ── CI: App file identity in sync with curated (no DB — scores excluded) ────────────
# `main()`'s full `--check` also re-fetches `rrb`/`eloPercentile` from a live corpus
# query (`load_fresh_scores`, needs `DATABASE_URL`) — not something CI can assert. This
# is the DB-free half of that contract: re-derive identity from the checked-in curated
# file and diff it against the checked-in App file with score fields stripped from
# both sides. Registered here rather than `tests/test_cross_repo_fixtures.py` — that
# file's `GENERATORS` list is for byte-identical fixture PAIRS with a `--check`-flagged
# generator module; this is a one-sided merge into an existing file, a different shape.
def _without_scores(node: dict) -> dict:
    return {k: v for k, v in node.items() if k not in ("rrb", "eloPercentile")}


def test_app_nodes_library_identity_matches_curated_source() -> None:
    from scripts.sync_app_artifacts import APP, CURATED_LIB, NODES_LIB

    if not APP.is_dir():
        pytest.skip("GrapplingArcApp não está ao lado deste repo")
    curated = json.loads(CURATED_LIB.read_text(encoding="utf-8"))
    on_disk = json.loads(NODES_LIB.read_text(encoding="utf-8"))

    expected, _report = merge_curated_identity(curated, on_disk)

    assert [_without_scores(n) for n in expected] == [_without_scores(n) for n in on_disk], (
        "grappling-arch.nodes.json identity has drifted from the curated library — "
        "run `uv run python -m scripts.sync_app_artifacts` (App side is GENERATED, "
        "never hand-edited)"
    )
