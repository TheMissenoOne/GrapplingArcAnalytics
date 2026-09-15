"""Dictionary audit — buckets, candidate grouping, queue order, verdict ingestion.

The interesting assertions are the ones about PROVENANCE: accepting a frame a model
proposed must not mint ``source: "human"`` (the ``frame_registrar`` laundering of
2026-08-24), and a verdict must survive a ``vision_dataset`` rebuild, which rewrites every
``labels/*.jsonl`` from the answer files.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import dictionary_audit as da
from scripts import vision_dataset as vd


def _entry(**kw: Any) -> da.DictEntry:
    base: dict[str, Any] = {"en": "Kimura", "pt": "Kimura", "type": "submission",
                            "variants": [], "node_key": "kimura", "in_node_library": True}
    base.update(kw)
    return da.DictEntry(**base)


# ---------------------------------------------------------------------- bucket rule

def test_bucket_counts_verified_frames_not_lines() -> None:
    e = _entry()
    e.verified_frames = {("a", 1000), ("a", 2000)}
    assert e.bucket(min_human=8) == "thin"
    e.verified_frames |= {("a", i * 1000) for i in range(3, 9)}
    assert e.bucket(min_human=8) == "well-covered"
    assert _entry().bucket(min_human=8) == "zero"


def test_bucket_unreachable_beats_coverage() -> None:
    """No node in the reader's vocabulary => the label can never reach a frame at all."""
    e = _entry(in_node_library=False)
    e.verified_frames = {("a", i) for i in range(20)}
    assert e.bucket(min_human=8) == "unreachable"


def test_bucket_concepts_are_out_of_scope() -> None:
    assert _entry(type="concept").bucket(min_human=8) == "out-of-scope"


def test_curated_dictionary_reachability_is_measured_not_assumed() -> None:
    entries = da.load_dictionary()
    assert len(entries) > 150
    # Reachability is a measured boolean per entry, never assumed: after the node library is
    # regenerated from prod (2026-09-15) every curated entry may be reachable — that is a
    # valid, desirable state, so the test checks the measurement exists, not its value.
    flags = {e.in_node_library for e in entries.values()}
    assert flags <= {True, False} and flags
    assert all(isinstance(e.in_node_library, bool) for e in entries.values())


# -------------------------------------------------------------- candidate grouping

def _tiny_dataset(tmp_path: Path, lines: list[dict[str, Any]]) -> Path:
    dataset = tmp_path / "finetune"
    (dataset / "labels").mkdir(parents=True)
    by_bout: dict[str, list[dict[str, Any]]] = {}
    for ln in lines:
        by_bout.setdefault(ln["bout"], []).append(ln)
    for bout, rows in by_bout.items():
        (dataset / "labels" / f"{bout}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return dataset


def _label(bout: str, ts: int, label: str, typ: str, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"bout": bout, "ts": ts, "ts_ms": ts * 1000,
                           "node_key": da.node_key_of(label), "label": label, "type": typ,
                           "source": "gemini", "review": None, "actor": "A",
                           "actor_key": "a", "successful": True}
    row.update(kw)
    return row


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(da, "COVERAGE", tmp_path / "coverage.json")
    monkeypatch.setattr(da, "REPORT", tmp_path / "report.md")
    monkeypatch.setattr(da, "QUEUE_JSON", tmp_path / "queue.json")
    monkeypatch.setattr(da, "QUEUE_MD", tmp_path / "queue.md")
    monkeypatch.setattr(da, "SHEETS", tmp_path / "sheets")
    monkeypatch.setattr(da, "SEED", tmp_path / "seed.jsonl")
    monkeypatch.setattr(da, "PROPOSALS", tmp_path / "proposals.json")
    monkeypatch.setattr(da, "AUDIT", tmp_path / "audit")
    empty = tmp_path / "experiments"
    empty.mkdir()
    monkeypatch.setattr(da, "EXPERIMENTS", empty)
    return tmp_path


def test_candidates_group_by_node_key_and_name_their_sources(isolated: Path) -> None:
    dataset = _tiny_dataset(isolated, [
        _label("b1", 10, "Kimura", "submission"),                 # curated
        _label("b1", 20, "Tap", "submission"),                    # candidate, missing
        _label("b1", 30, "tap", "submission"),                    # same node_key
        _label("b2", 40, "Leg Drag", "transition"),               # candidate, type conflict
    ])
    cov = da.measure(dataset, min_human=8, with_db=False)
    cands = {c["node_key"]: c for c in cov["candidates"]}
    assert cands["tap"]["total"] == 2, "two spellings of one label are ONE candidate"
    assert cands["tap"]["by_source"] == {"dataset_model": 2}
    assert cands["tap"]["kind"] == "missing"
    # `Leg Drag` IS in the dictionary — as a `pass`. That is a type conflict needing a
    # different verdict, not a hole in the dictionary.
    assert cands["leg drag"]["kind"] == "type_conflict"
    assert cands["leg drag"]["conflicts_with"] == "leg drag pass"
    assert "kimura" not in cands


def test_measure_counts_accepted_model_labels_as_verified(isolated: Path) -> None:
    dataset = _tiny_dataset(isolated, [
        _label("b1", 10, "Kimura", "submission"),                       # unreviewed
        _label("b1", 20, "Kimura", "submission", review="accepted"),    # human-accepted
        _label("b1", 30, "Kimura", "submission", source="human", review="accepted"),
    ])
    cov = da.measure(dataset, min_human=8, with_db=False)
    kimura = next(e for e in cov["entries"] if e["node_key"] == "kimura")
    assert kimura["verified_frames"] == 2
    assert kimura["human_frames"] == 1
    assert kimura["model_unreviewed"] == 1


def test_report_block_is_regenerated_in_place(isolated: Path) -> None:
    dataset = _tiny_dataset(isolated, [_label("b1", 10, "Kimura", "submission")])
    da.measure(dataset, with_db=False)
    da.REPORT.write_text(da.REPORT.read_text(encoding="utf-8") + "\nhand-written tail\n",
                         encoding="utf-8")
    da.measure(dataset, with_db=False)
    text = da.REPORT.read_text(encoding="utf-8")
    assert text.count(da.BEGIN) == 1
    assert text.rstrip().endswith("hand-written tail")


# ----------------------------------------------------------------------- queue order

def _coverage(entries: list[dict[str, Any]],
              candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"generated": "now", "min_human": 8, "entries": entries,
            "candidates": candidates or []}


def test_queue_orders_zero_by_corpus_then_thin_then_candidates(isolated: Path) -> None:
    dataset = _tiny_dataset(isolated, [])
    cov = _coverage(
        entries=[
            {"node_key": "a", "en": "A", "type": "pass", "bucket": "zero",
             "corpus_events": 5, "verified_frames": 0, "has_definition": False},
            {"node_key": "b", "en": "B", "type": "pass", "bucket": "zero",
             "corpus_events": 50, "verified_frames": 0, "has_definition": False},
            {"node_key": "c", "en": "C", "type": "pass", "bucket": "thin",
             "corpus_events": 900, "verified_frames": 2, "has_definition": False},
            {"node_key": "d", "en": "D", "type": "pass", "bucket": "well-covered",
             "corpus_events": 900, "verified_frames": 20, "has_definition": True},
        ],
        candidates=[{"node_key": "e", "label": "E", "type": "pass", "kind": "missing",
                     "conflicts_with": None, "in_node_library": False, "total": 3,
                     "by_source": {"corpus": 3}, "spellings": {}}])
    out = da.queue(dataset, cap=12, coverage=cov, render=False)
    assert [t["node_key"] for t in out["targets"]] == ["b", "a", "c", "e"], \
        "zero bucket first (by corpus frequency), then thin, then candidates; " \
        "a well-covered technique is not queued at all"


def test_queue_ingests_the_gemini_seed_high_confidence_first(isolated: Path) -> None:
    dataset = _tiny_dataset(isolated, [])
    seed_dir = dataset / "audit" / "gemini_seed" / "kimura"
    seed_dir.mkdir(parents=True)
    for name in ("bout-x__11000.jpg", "bout-x__22000.jpg"):
        (seed_dir / name).write_bytes(b"\xff\xd8\xff")      # presence is what queue checks
    # The shape scripts/dictionary_seed.py:run_ask writes, verbatim: repo-relative frame,
    # corpus_label, the blind model's guess, agreement + review_confidence, no actor name.
    da.SEED.write_text("".join(json.dumps(r) + "\n" for r in [
        {"node_key": "kimura", "bout": "bout-x", "ts_ms": 22000,
         "frame": "data/finetune/audit/gemini_seed/kimura/bout-x__22000.jpg",
         "corpus_label": "Kimura", "model_label": "Americana (Keylock)",
         "model_type": "submission", "actor_role": "top", "model_confidence": "high",
         "agree": "no", "review_confidence": "low"},
        {"node_key": "kimura", "bout": "bout-x", "ts_ms": 11000,
         "frame": "data/finetune/audit/gemini_seed/kimura/bout-x__11000.jpg",
         "corpus_label": "Kimura", "model_label": "Kimura", "model_type": "submission",
         "actor_role": "top", "model_confidence": "high",
         "agree": "full", "review_confidence": "high"},
    ]), encoding="utf-8")

    got = da.gather_proposals(dataset, {"kimura"})
    picked = da.dedupe(got["kimura"], cap=12, seen=set())
    assert [p.ts_ms for p in picked] == [11000, 22000], "high confidence sorts first"
    assert picked[0].caption == "corpus+gemini ✓"
    assert picked[1].caption == "corpus ✗ gemini: Americana (Keylock)"
    assert picked[0].frame == "audit/gemini_seed/kimura/bout-x__11000.jpg"
    assert picked[0].label == "Kimura" and picked[0].actor is None


def test_queue_skips_frames_already_ruled_on(isolated: Path) -> None:
    proposals = [da.Proposal(node_key="k", bout="b", ts_ms=1000, ts=1, rank=0, offset=0,
                             caption="x", frame="f", origin="gemini", label="K",
                             type="submission", actor=None, successful=None)]
    assert da.dedupe(proposals, cap=12, seen=set()) == proposals
    assert da.dedupe(proposals, cap=12, seen={("b", 1000)}) == []


def test_near_frames_returns_the_neighbours_of_the_nearest_sample() -> None:
    frames = [{"ts": t, "ts_ms": t * 1000, "file": f"{t}.jpg"} for t in (0, 5, 10, 15)]
    assert [f["ts"] for _o, f in da.near_frames(9, frames)] == [5, 10, 15]
    assert [o for o, _f in da.near_frames(9, frames)] == [-1, 0, 1]
    assert [f["ts"] for _o, f in da.near_frames(0, frames)] == [0, 5]      # no left edge


# -------------------------------------------------------------------------- verdicts

def test_parse_verdict() -> None:
    assert da.parse_verdict("accept") == ("accept", "")
    assert da.parse_verdict("reject") == ("reject", "")
    assert da.parse_verdict("relabel:Knee Bar") == ("relabel", "knee bar")
    assert da.parse_verdict("alias:leg lock") == ("alias", "leg lock")
    with pytest.raises(ValueError):
        da.parse_verdict("maybe")
    with pytest.raises(ValueError):
        da.parse_verdict("relabel:")


def _queue_file(path: Path, proposal: dict[str, Any]) -> None:
    path.write_text(json.dumps({"targets": [{"node_key": proposal["node_key"],
                                             "proposals": [proposal]}]}), encoding="utf-8")


def test_accept_keeps_the_model_as_the_source_and_survives_a_rebuild(
        isolated: Path) -> None:
    """A human verdict makes a model label ADMISSIBLE; it never makes it human-authored."""
    dataset = isolated / "finetune"
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    _queue_file(da.QUEUE_JSON, {"node_key": "kimura", "bout": "b1", "ts_ms": 10000,
                                "ts": 10, "frame": "frames/b1/000010000.jpg",
                                "origin": "gemini", "label": "Kimura",
                                "type": "submission", "actor": "A", "successful": True})
    verdicts = isolated / "verdicts.jsonl"
    verdicts.write_text(json.dumps({"node_key": "kimura", "bout": "b1", "ts_ms": 10000,
                                    "verdict": "accept", "note": "figure-four closed"})
                        + "\n", encoding="utf-8")

    out = da.apply(verdicts, dataset, write=True, reviewer="tester", rebuild=False)
    assert out["stats"] == {"accepted": 1}

    store = vd.load_verdicts(da.AUDIT / "verdicts.jsonl")
    assert len(store) == 1
    assert store[0]["line"]["source"] == "gemini", "accepting must not launder the origin"
    assert store[0]["line"]["review"] == "accepted"

    # A rebuild rewrites labels from the answers; the store is what puts the verdict back.
    labels: dict[str, list[dict[str, Any]]] = {"b1": [
        {"bout": "b1", "ts_ms": 10000, "node_key": "kimura", "source": "gemini",
         "review": None, "reviewer": None, "reviewed_at": None}]}
    stats = vd.apply_verdicts(labels, da.AUDIT / "verdicts.jsonl")
    assert stats["review_accepted"] == 1
    assert labels["b1"][0]["review"] == "accepted"
    assert labels["b1"][0]["source"] == "gemini"
    assert len(labels["b1"]) == 1, "an existing line is reviewed, never duplicated"


def test_relabel_mints_a_human_line_because_the_human_is_its_origin(
        isolated: Path) -> None:
    dataset = isolated / "finetune"
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    _queue_file(da.QUEUE_JSON, {"node_key": "kimura", "bout": "b1", "ts_ms": 10000,
                                "ts": 10, "frame": "frames/b1/000010000.jpg",
                                "origin": "gemini", "label": "Kimura",
                                "type": "submission", "actor": "A", "successful": True})
    verdicts = isolated / "verdicts.jsonl"
    verdicts.write_text(json.dumps({"node_key": "kimura", "bout": "b1", "ts_ms": 10000,
                                    "verdict": "relabel:Americana (Keylock)",
                                    "note": "it is an americana"}) + "\n",
                        encoding="utf-8")
    out = da.apply(verdicts, dataset, write=True, reviewer="tester", rebuild=False)
    assert out["stats"]["relabelled"] == 1

    store = {(v["node_key"], v["verdict"]): v
             for v in vd.load_verdicts(da.AUDIT / "verdicts.jsonl")}
    assert store[("americana keylock", "accepted")]["line"]["source"] == "human"
    assert store[("kimura", "rejected")]["line"] is None, "the model's claim is rejected"

    labels: dict[str, list[dict[str, Any]]] = {}
    vd.apply_verdicts(labels, da.AUDIT / "verdicts.jsonl")
    minted = labels["b1"]
    assert [ln["node_key"] for ln in minted] == ["americana keylock"]
    assert minted[0]["label"] == "Americana (Keylock)"   # canonical label from the library
    assert minted[0]["claim"] == "technique_presence"


def test_alias_writes_a_proposal_and_never_touches_the_curated_library(
        isolated: Path) -> None:
    dataset = isolated / "finetune"
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    before = da.DICTIONARY.read_bytes()
    verdicts = isolated / "verdicts.jsonl"
    verdicts.write_text(json.dumps({"node_key": "leg drag", "bout": "b1", "ts_ms": 0,
                                    "verdict": "alias:leg drag pass",
                                    "note": "same technique, logged as a transition"})
                        + "\n", encoding="utf-8")
    out = da.apply(verdicts, dataset, write=True, reviewer="tester", rebuild=False)
    assert out["stats"] == {"alias_proposed": 1}
    assert da.DICTIONARY.read_bytes() == before, "a script never edits the dictionary"

    data = json.loads(da.PROPOSALS.read_text(encoding="utf-8"))
    assert data["proposals"] == [{"kind": "alias", "node_key": "leg drag",
                                  "target": "leg drag pass",
                                  "note": "same technique, logged as a transition",
                                  "by": "tester", "at": data["proposals"][0]["at"]}]
    # idempotent: applying the same verdict twice does not duplicate the proposal
    da.apply(verdicts, dataset, write=True, reviewer="tester", rebuild=False)
    assert len(json.loads(da.PROPOSALS.read_text(encoding="utf-8"))["proposals"]) == 1


def test_reject_is_recorded_so_the_frame_is_not_requeued(isolated: Path) -> None:
    dataset = isolated / "finetune"
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    verdicts = isolated / "verdicts.jsonl"
    verdicts.write_text(json.dumps({"node_key": "kimura", "bout": "b1", "ts_ms": 10000,
                                    "verdict": "reject", "note": "not visible"}) + "\n",
                        encoding="utf-8")
    da.apply(verdicts, dataset, write=True, reviewer="tester", rebuild=False)
    ruled = {(v["bout"], v["ts_ms"]) for v in vd.load_verdicts(da.AUDIT / "verdicts.jsonl")}
    assert ("b1", 10000) in ruled
    proposals = [da.Proposal(node_key="kimura", bout="b1", ts_ms=10000, ts=10, rank=0,
                             offset=0, caption="x", frame="f", origin="gemini",
                             label="Kimura", type="submission", actor=None,
                             successful=None)]
    assert da.dedupe(proposals, cap=12, seen=ruled) == []


def test_apply_dry_run_writes_nothing(isolated: Path) -> None:
    dataset = isolated / "finetune"
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    verdicts = isolated / "verdicts.jsonl"
    verdicts.write_text(json.dumps({"node_key": "kimura", "bout": "b1", "ts_ms": 1,
                                    "verdict": "accept"}) + "\n", encoding="utf-8")
    out = da.apply(verdicts, dataset, write=False, reviewer="tester", rebuild=False)
    assert out["written"] is False
    assert not (da.AUDIT / "verdicts.jsonl").exists()
    assert not da.PROPOSALS.exists()


def test_apply_refuses_an_unknown_verdict_without_writing(isolated: Path) -> None:
    dataset = isolated / "finetune"
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    verdicts = isolated / "verdicts.jsonl"
    verdicts.write_text(json.dumps({"node_key": "kimura", "bout": "b1", "ts_ms": 1,
                                    "verdict": "probably"}) + "\n", encoding="utf-8")
    out = da.apply(verdicts, dataset, write=True, reviewer="tester", rebuild=False)
    assert out["problems"] and out["written"] is False
    assert not (da.AUDIT / "verdicts.jsonl").exists()


def test_accepting_a_seed_frame_without_a_queue_file_still_credits_the_model(
        isolated: Path) -> None:
    """The seed is a fallback proposal index — accepting one must not mint `human`."""
    dataset = isolated / "finetune"
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    da.SEED.write_text(json.dumps(
        {"node_key": "kimura", "bout": "b1", "ts_ms": 10000,
         "frame": "data/finetune/audit/gemini_seed/kimura/b1__10000.jpg",
         "corpus_label": "Kimura", "model_label": "Kimura", "agree": "full",
         "review_confidence": "high"}) + "\n", encoding="utf-8")
    verdicts = isolated / "verdicts.jsonl"
    verdicts.write_text(json.dumps({"node_key": "kimura", "bout": "b1", "ts_ms": 10000,
                                    "verdict": "accept"}) + "\n", encoding="utf-8")
    assert not da.QUEUE_JSON.exists()
    da.apply(verdicts, dataset, write=True, reviewer="tester", rebuild=False)
    line = vd.load_verdicts(da.AUDIT / "verdicts.jsonl")[0]["line"]
    assert line["source"] == "gemini"
    assert line["frame"] == "audit/gemini_seed/kimura/b1__10000.jpg"
    assert line["actor"] is None, "the seed carries a role, never an identity"
