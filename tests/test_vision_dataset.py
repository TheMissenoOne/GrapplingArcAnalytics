"""Unit tests for the vision dataset: grouped split, provenance gate, Vertex JSONL shape.

The two things that silently ruin this dataset are a split that leaks an athlete across
train/val (the project's measured 93%-vs-21% failure mode) and a model reading laundered
into training data. Both are asserted here, plus the JSONL shape Vertex will reject at
upload time rather than at read time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.vision_dataset import (
    Bout,
    athlete_groups,
    classify_origin,
    cut_split,
    merge_split,
    near_miss_clusters,
    snap_to_frame,
)
from scripts.vision_dataset_export import (
    PDF_MIME,
    TARGET_BOUT_KEYS,
    _answer_from_labels,
    admissible,
)


def _bout(slug: str, *athletes: str) -> Bout:
    return Bout(slug=slug, batch="t", sheet=Path("/dev/null"), athletes=tuple(athletes))


# --------------------------------------------------------------------------- split

def test_split_never_puts_a_bout_on_both_sides() -> None:
    bouts = [_bout(f"b{i}", f"a{i}", f"a{i + 100}") for i in range(20)]
    split = cut_split(bouts, 0.2, seed=0)
    assert set(split["train"]) & set(split["val"]) == set()
    assert len(split["train"]) + len(split["val"]) == len(bouts)


def test_split_never_puts_an_athlete_on_both_sides() -> None:
    # A chain: b0-b1 share x1, b1-b2 share x2 ... one component of 6 bouts, plus singletons.
    chain = [_bout("c0", "x0", "x1"), _bout("c1", "x1", "x2"), _bout("c2", "x2", "x3"),
             _bout("c3", "x3", "x4"), _bout("c4", "x4", "x5"), _bout("c5", "x5", "x6")]
    singles = [_bout(f"s{i}", f"p{i}", f"q{i}") for i in range(14)]
    split = cut_split(chain + singles, 0.2, seed=0)
    side = {s: "train" for s in split["train"]} | {s: "val" for s in split["val"]}
    seen: dict[str, str] = {}
    for b in chain + singles:
        for a in b.athletes:
            assert seen.setdefault(a, side[b.slug]) == side[b.slug], (
                f"athlete {a} appears in both train and val")


def test_split_is_deterministic_for_a_seed() -> None:
    bouts = [_bout(f"b{i}", f"a{i}", f"a{i + 50}") for i in range(30)]
    assert cut_split(bouts, 0.2, 7) == cut_split(bouts, 0.2, 7)


def test_split_val_fraction_is_roughly_honoured() -> None:
    bouts = [_bout(f"b{i}", f"a{i}", f"a{i + 100}") for i in range(50)]
    split = cut_split(bouts, 0.2, seed=0)
    assert 8 <= len(split["val"]) <= 12


def test_athlete_groups_merges_a_shared_athlete() -> None:
    groups = athlete_groups([_bout("b0", "x", "y"), _bout("b1", "y", "z"),
                             _bout("b2", "p", "q")])
    assert groups["b0"] == groups["b1"]
    assert groups["b2"] != groups["b0"]


# ------------------------------------------------------------- frozen published split

def test_merge_split_never_moves_a_published_bout() -> None:
    published = {"train": ["b0", "b1"], "val": ["b2"], "excluded": {}, "groups": {}}
    fresh = {"groups": {"g0": ["b0", "b1"], "g1": ["b2"], "g2": ["b3"]}}
    merged = merge_split(published, fresh)
    assert set(published["train"]) <= set(merged["train"])
    assert set(published["val"]) <= set(merged["val"])
    assert "b3" in merged["train"] + merged["val"]


def test_merge_split_excludes_a_bout_whose_group_straddles_both_sides() -> None:
    published = {"train": ["b0"], "val": ["b1"], "excluded": {}, "groups": {}}
    # b2 shares an athlete with a train bout AND a val bout -> it cannot go anywhere clean.
    fresh = {"groups": {"g0": ["b0", "b1", "b2"]}}
    merged = merge_split(published, fresh)
    assert "b2" not in merged["train"] and "b2" not in merged["val"]
    assert "straddles" in merged["excluded"]["b2"]


# ----------------------------------------------------------------------- provenance

@pytest.mark.parametrize(("source", "expected"), [
    ("gemini reading, concordance-audited (kept 9/9) 2026-08-25", "human"),
    ("frame_registrar (human review over model reading)", "human"),
    ("frame_answer_import (returned reading, not yet human-reviewed)", "gemini"),
    ("gemini_read_frames (gemini-3.6-flash, 2026-09-03) — not yet human-reviewed", "gemini"),
    ("", "gemini"),
])
def test_classify_origin(source: str, expected: str) -> None:
    assert classify_origin({"source": source}, "gemini") == expected


def test_admissible_gates_model_readings_until_reviewed() -> None:
    assert admissible({"source": "human", "review": None})
    assert not admissible({"source": "gemini", "review": None})
    assert not admissible({"source": "gemini", "review": "rejected"})
    assert admissible({"source": "gemini", "review": "accepted"})
    assert admissible({"source": "gemini_ft:job123", "review": "accepted"})


def test_dataset_review_refuses_to_relabel_a_human_line(tmp_path: Path) -> None:
    from scripts.dataset_review import cmd_rule

    labels = tmp_path / "labels"
    labels.mkdir()
    rows = [
        {"label_id": "aaa", "source": "human", "review": "accepted", "ts_ms": 0,
         "bout": "b", "node_key": "k"},
        {"label_id": "bbb", "source": "gemini", "review": None, "ts_ms": 0,
         "bout": "b", "node_key": "k"},
    ]
    (labels / "b.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    cmd_rule(tmp_path, "rejected", ["aaa", "bbb"], "", "tester")
    out = {json.loads(ln)["label_id"]: json.loads(ln)
           for ln in (labels / "b.jsonl").read_text().splitlines()}
    assert out["aaa"]["review"] == "accepted", "a human label must not be overwritten"
    assert out["bbb"]["review"] == "rejected"
    assert out["bbb"]["source"] == "gemini", "source is the origin and is never rewritten"


# ---------------------------------------------------------------------- frame snap

def test_snap_to_frame_takes_the_nearest_sample_within_half_a_step() -> None:
    frames = [100, 105, 110, 115]
    assert snap_to_frame(105, frames) == 105
    assert snap_to_frame(107, frames) == 105
    assert snap_to_frame(108, frames) == 110
    assert snap_to_frame(200, frames) is None
    assert snap_to_frame(100, []) is None


# ----------------------------------------------------------------- vertex jsonl shape

def _example(target: dict[str, Any]) -> dict[str, Any]:
    return {"contents": [
        {"role": "user", "parts": [
            {"fileData": {"mimeType": PDF_MIME, "fileUri": "gs://b/x.pdf"}},
            {"text": "prompt"}]},
        {"role": "model", "parts": [{"text": json.dumps(target)}]}]}


def _real_or_synthetic_example() -> dict[str, Any]:
    """The generated export when the dataset is on this machine, else the synthetic shape.

    Asserting the shape of a literal this test wrote proves nothing about the exporter, so
    the real file wins when it exists; CI has no `data/` and falls back.
    """
    from scripts.vision_dataset import DATASET

    real = DATASET / "exports" / "vertex_sft" / "train.jsonl"
    if real.exists():
        first = real.read_text(encoding="utf-8").splitlines()[0]
        loaded: dict[str, Any] = json.loads(first)
        return loaded
    return _example({"bout": {}, "events": []})


def test_vertex_example_has_the_shape_the_tuning_api_requires() -> None:
    ex = _real_or_synthetic_example()
    assert [c["role"] for c in ex["contents"]] == ["user", "model"]
    user_parts = ex["contents"][0]["parts"]
    assert user_parts[0]["fileData"]["mimeType"] == "application/pdf"
    assert user_parts[0]["fileData"]["fileUri"].startswith("gs://")
    assert isinstance(user_parts[1]["text"], str)
    model_parts = ex["contents"][1]["parts"]
    assert len(model_parts) == 1
    json.loads(model_parts[0]["text"])  # the target must be parseable JSON


def test_target_drops_audit_artefacts_and_keeps_the_pre_audit_discriminator() -> None:
    lines = [{"event_ts": 10, "label": "Armbar", "actor": "A", "actor_key": "a",
              "successful": True, "type": "submission", "node_key": "armbar"}]
    audited = {"bout": {"athlete_a": "A", "athlete_b": "B", "winner": "A",
                        "notes": "audit trail",
                        "identity_verified_by": "concordance audit",
                        "identity_discriminator": "verified frames: x-05.jpg, audit.flags=[]"}}
    raw = {"bout": {"identity_discriminator": "A in black rashguard, B in white"}}
    target = _answer_from_labels("slug", lines, audited, raw)
    assert set(target["bout"]) <= set(TARGET_BOUT_KEYS)
    assert "notes" not in target["bout"]
    assert "identity_verified_by" not in target["bout"]
    assert target["bout"]["identity_discriminator"] == "A in black rashguard, B in white"
    assert target["events"] == [{"ts": 10, "label": "Armbar", "actor": "A",
                                 "successful": True, "type": "submission"}]


def test_target_omits_the_discriminator_when_only_the_audited_one_exists() -> None:
    audited = {"bout": {"athlete_a": "A", "athlete_b": "B",
                        "identity_discriminator": "audit.flags=[] verified frames ..."}}
    target = _answer_from_labels("slug", [], audited, {})
    assert "identity_discriminator" not in target["bout"]


# ------------------------------------------------------------------- near-miss report

def test_near_miss_clusters_groups_confusable_labels_of_one_type() -> None:
    got = near_miss_clusters({"takedown": {"single leg takedown": 10, "low single leg": 1,
                                           "snap down": 3}})
    words = {g["shared_word"] for g in got["takedown"]}
    assert "single" in words
    single = next(g for g in got["takedown"] if g["shared_word"] == "single")
    assert single["labels"] == {"single leg takedown": 10, "low single leg": 1}
    assert single["total"] == 11
    # a label with no confusable neighbour forms no group
    assert all("snap down" not in g["labels"] for g in got["takedown"])
