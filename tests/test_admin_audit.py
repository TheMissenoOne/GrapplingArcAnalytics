"""Admin audit pages — same TestClient/mock style as tests/test_admin.py. Every filesystem
seam (round root, dictionary audit store) is monkeypatched to tmp_path; nothing here touches
the repo's real data/video/ or data/finetune/."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ADMIN_PASSWORD_HASH", "test-placeholder-not-a-real-hash")

from admin import audit as audit_mod  # noqa: E402
from analysis.names import _normalize_name  # noqa: E402


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """FastAPI TestClient with auth bypassed and DB patched — matches tests/test_admin.py."""
    with (
        patch("admin.server.is_authenticated", return_value=True),
        patch("admin.server.db_session"),
        patch("admin.server._build_node_options", return_value=[]),
    ):
        from admin.server import create_admin_app
        app = create_admin_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def _write_round(root: Path, slug: str, *, with_video: bool = False,
                 events: list[dict[str, Any]] | None = None) -> Path:
    d = root / slug
    (d / "frames").mkdir(parents=True, exist_ok=True)
    events = events if events is not None else [
        {"ts": 60, "label": "Takedown", "actor": "you", "successful": False,
         "type": "takedown", "confidence": "high", "note": "shoots"},
        {"ts": 66, "label": "Sprawl", "actor": "partner", "successful": True,
         "type": "defensive", "confidence": "high"},
    ]
    (d / "read.json").write_text(json.dumps({
        "bout": {"identity_discriminator": "you=black, partner=blue", "notes": "test round"},
        "events": events, "resets": [90],
    }), encoding="utf-8")
    (d / "analysis.json").write_text(json.dumps({
        "difficulty": 4.2,
        "highlights": [{"start": 60.0, "end": 66.0, "label": "Takedown", "score": 2.1}],
    }), encoding="utf-8")
    (d / "frames" / "t00060.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    (d / "frames" / "t00066.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    if with_video:
        (d / "video.mp4").write_bytes(b"fake-mp4-bytes")
    return d


# ── Mode A: rounds list ─────────────────────────────────────────────────────────────────────
def test_rounds_list_shows_counts(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "out"
    _write_round(root, "round-a", with_video=True)
    _write_round(root, "round-b", with_video=False)
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", root)

    resp = client.get("/admin/audit/rounds")

    assert resp.status_code == 200
    assert "round-a" in resp.text
    assert "round-b" in resp.text


def test_rounds_list_empty_dir_renders(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", tmp_path / "nothing-here")
    resp = client.get("/admin/audit/rounds")
    assert resp.status_code == 200


# ── Mode A: round detail (with / without video) ─────────────────────────────────────────────
def test_round_detail_renders_with_video(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "out"
    _write_round(root, "round-a", with_video=True)
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", root)

    resp = client.get("/admin/audit/rounds/round-a")

    assert resp.status_code == 200
    assert '<video id="audit-video"' in resp.text
    assert '"label": "Takedown"' in resp.text  # embedded audit-data JSON; JS renders the rows
    assert '"highlights"' in resp.text and '"start": 60.0' in resp.text  # analysis.json clips


def test_round_detail_degrades_without_video(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "out"
    _write_round(root, "round-b", with_video=False)
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", root)

    resp = client.get("/admin/audit/rounds/round-b")

    assert resp.status_code == 200
    assert "audit-video-missing" in resp.text
    assert '<video id="audit-video"' not in resp.text


def test_round_detail_404_for_unknown_slug(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", tmp_path / "out")
    resp = client.get("/admin/audit/rounds/nope")
    assert resp.status_code == 404


def test_round_video_404_when_missing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "out"
    _write_round(root, "round-b", with_video=False)
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", root)
    resp = client.get("/admin/audit/rounds/round-b/video")
    assert resp.status_code == 404


def test_round_frame_path_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "out"
    _write_round(root, "round-a")
    assert audit_mod.round_frame_path("round-a", "t00060.jpg", root) is not None
    assert audit_mod.round_frame_path("round-a", "../../read.json", root) is None


# ── Mode A: verdict autosave round-trips to verdicts.json ──────────────────────────────────
def test_verdict_post_roundtrips_to_verdicts_json(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "out"
    _write_round(root, "round-a")
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", root)

    payload = {"events": [
        {"id": "0", "verdict": "confirmed", "corrected": None, "note": ""},
        {"id": "1", "verdict": "wrong_actor", "corrected": {"actor": "you"}, "note": "misread"},
    ]}
    resp = client.post("/admin/audit/rounds/round-a/verdicts", json=payload)

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    on_disk = json.loads((root / "round-a" / "verdicts.json").read_text())
    assert on_disk["slug"] == "round-a"
    assert on_disk["events"][1]["corrected"]["actor"] == "you"

    # reload merges the saved verdict back into the event view
    reloaded = audit_mod.load_round("round-a", root)
    assert reloaded is not None
    assert reloaded["events"][0]["verdict"] == "confirmed"
    assert reloaded["events"][1]["verdict"] == "wrong_actor"


def test_verdict_post_404_for_unknown_slug(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", tmp_path / "out")
    resp = client.post("/admin/audit/rounds/nope/verdicts", json={"events": []})
    assert resp.status_code == 404


def test_export_corrected_timeline_drops_not_visible_and_applies_corrections(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "out"
    _write_round(root, "round-a")
    monkeypatch.setattr(audit_mod, "ROUND_ROOT", root)

    payload = {"events": [
        {"id": "0", "verdict": "wrong_time", "corrected": {"ts": 61}, "note": ""},
        {"id": "1", "verdict": "not_visible", "corrected": None, "note": ""},
    ]}
    client.post("/admin/audit/rounds/round-a/verdicts", json=payload)

    resp = client.post("/admin/audit/rounds/round-a/export")

    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 1  # the not_visible one is dropped
    assert events[0]["ts"] == 61  # correction applied
    assert events[0]["label"] == "Takedown"  # uncorrected fields kept from the original
    assert (root / "round-a" / "events_corrected.json").is_file()


# ── Mode B: dictionary queue ─────────────────────────────────────────────────────────────────
def _seed_row(node_key: str = "armbar", candidate: str = "Armbar", bout: str = "b1",
             ts_ms: int = 1000, confidence: str = "high") -> dict[str, Any]:
    return {
        "node_key": node_key, "bout": bout, "ts_ms": ts_ms, "ts": ts_ms // 1000,
        "candidate_label": candidate, "candidate_type": "submission",
        "review_confidence": confidence, "frame": f"data/finetune/audit/gemini_seed/{bout}.jpg",
        "second_opinion": {"corpus_label": "Armbar", "agree": "full", "agree_near": "full"},
    }


def test_dictionary_page_lists_only_qualifying_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _seed_row(),  # candidate present -> qualifies
        {"node_key": "kimura", "bout": "b2-never-read", "ts_ms": 2000, "candidate_label": None},
    ]
    monkeypatch.setattr("scripts.dictionary_audit.load_seed", lambda *a, **k: rows)
    monkeypatch.setattr("scripts.vision_dataset.load_verdicts", lambda *a, **k: [])

    resp = client.get("/admin/audit/dictionary")

    assert resp.status_code == 200
    assert '"candidate_label": "Armbar"' in resp.text
    assert "b2-never-read" not in resp.text  # candidate_label is null -> not a review item


def test_dictionary_page_excludes_already_ruled_rows(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [_seed_row(bout="b1", ts_ms=1000)]
    ruled = [{"bout": "b1", "ts_ms": 1000, "node_key": "armbar"}]
    monkeypatch.setattr("scripts.dictionary_audit.load_seed", lambda *a, **k: rows)
    monkeypatch.setattr("scripts.vision_dataset.load_verdicts", lambda *a, **k: ruled)

    resp = client.get("/admin/audit/dictionary")

    assert resp.status_code == 200
    assert '"candidate_label": "Armbar"' not in resp.text


def test_dictionary_frame_path_confined_to_gemini_seed_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_root = tmp_path / "gemini_seed"
    seed_root.mkdir(parents=True)
    (seed_root / "b1.jpg").write_bytes(b"\xff\xd8fake")
    monkeypatch.setattr(audit_mod, "DICT_SEED_ROOT", seed_root)
    monkeypatch.setattr(audit_mod, "REPO", tmp_path)

    ok = audit_mod.dictionary_frame_path("gemini_seed/b1.jpg")
    assert ok is not None and ok.name == "b1.jpg"

    escaped = audit_mod.dictionary_frame_path("../etc/passwd")
    assert escaped is None


def test_dictionary_verdict_writes_via_the_owned_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_dictionary_verdict -> scripts.dictionary_audit.apply -> record_verdicts, the
    sole writer of verdicts.jsonl (scripts/vision_dataset.py). Every path apply() can touch
    is monkeypatched to tmp_path so this never writes into the real data/finetune/audit/."""
    import scripts.dictionary_audit as da_mod

    monkeypatch.setattr(da_mod, "AUDIT", tmp_path / "audit")
    monkeypatch.setattr(da_mod, "QUEUE_JSON", tmp_path / "audit" / "queue.json")
    monkeypatch.setattr(da_mod, "PROPOSALS", tmp_path / "proposals.json")

    result = audit_mod.apply_dictionary_verdict("armbar", "b1", 1000, "accept", note="clean")

    assert result["written"] is True
    store = (tmp_path / "audit" / "verdicts.jsonl").read_text(encoding="utf-8")
    rec = json.loads(store.strip().splitlines()[0])
    assert rec["bout"] == "b1" and rec["node_key"] == "armbar" and rec["verdict"] == "accepted"


def test_dictionary_verdict_route_rejects_missing_fields(client: TestClient) -> None:
    resp = client.post("/admin/audit/dictionary/verdict", json={"bout": "b1"})
    assert resp.status_code == 400


# ── Mode B: pair tier (item 32, 2026-09-16) ─────────────────────────────────────────────────
def test_dictionary_queue_surfaces_pair_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [{
        "node_key": "guard pass", "bout": "b1", "ts_ms": 5000, "ts": 5,
        "candidate_label": "Side Control", "candidate_type": "control",
        "review_confidence": "high", "frame": "data/finetune/audit/gemini_seed/b1.jpg",
        "agree_near": "pair",
        "second_opinion": {"corpus_label": "Guard Pass", "agree": "no", "agree_near": "pair"},
        "labels": [
            {"node_key": "guard pass", "kind": "action", "source": "corpus"},
            {"node_key": "side control", "kind": "state", "source": "gemini"},
        ],
    }]
    monkeypatch.setattr("scripts.dictionary_audit.load_seed", lambda *a, **k: rows)
    monkeypatch.setattr("scripts.vision_dataset.load_verdicts", lambda *a, **k: [])

    items = audit_mod.dictionary_queue()

    assert len(items) == 1
    item = items[0]
    assert item["node_key"] == "side control"   # candidate is still the primary identity
    assert item["kind"] == "state"
    assert item["pair_node_key"] == "guard pass"
    assert item["pair_label"] == "Guard Pass"
    assert item["pair_kind"] == "action"


def test_dictionary_queue_non_pair_rows_carry_no_pair_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("scripts.dictionary_audit.load_seed", lambda *a, **k: [_seed_row()])
    monkeypatch.setattr("scripts.vision_dataset.load_verdicts", lambda *a, **k: [])

    item = audit_mod.dictionary_queue()[0]
    assert item["pair_node_key"] is None
    assert item["kind"] is None


def test_apply_dictionary_verdict_accept_writes_two_records_for_a_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.dictionary_audit as da_mod

    monkeypatch.setattr(da_mod, "AUDIT", tmp_path / "audit")
    monkeypatch.setattr(da_mod, "QUEUE_JSON", tmp_path / "audit" / "queue.json")
    monkeypatch.setattr(da_mod, "PROPOSALS", tmp_path / "proposals.json")
    monkeypatch.setattr(
        da_mod, "load_seed",
        lambda *a, **k: [{
            "node_key": "guard pass", "bout": "b1", "ts_ms": 5000,
            "candidate_label": "Side Control", "candidate_type": "control",
            "agree_near": "pair",
            "second_opinion": {"corpus_label": "Guard Pass", "agree": "no", "agree_near": "pair"},
        }],
    )

    result = audit_mod.apply_dictionary_verdict(
        "side control", "b1", 5000, "accept", pair_node_key="guard pass"
    )

    assert result["written"] is True
    assert result["stats"] == {"accepted": 2}
    recs = [json.loads(ln) for ln in
            (tmp_path / "audit" / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()]
    node_keys = {r["node_key"] for r in recs}
    assert node_keys == {"side control", "guard pass"}
    assert all(r["bout"] == "b1" and r["ts_ms"] == 5000 for r in recs)


def test_apply_dictionary_verdict_relabel_never_touches_the_pair_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card's route layer only ever forwards `pair_node_key` for accept/reject -- relabel
    stays a single line, replacing only the candidate's own claim."""
    import scripts.dictionary_audit as da_mod

    monkeypatch.setattr(da_mod, "AUDIT", tmp_path / "audit")
    monkeypatch.setattr(da_mod, "QUEUE_JSON", tmp_path / "audit" / "queue.json")
    monkeypatch.setattr(da_mod, "PROPOSALS", tmp_path / "proposals.json")
    monkeypatch.setattr(da_mod, "load_seed", lambda *a, **k: [])

    result = audit_mod.apply_dictionary_verdict(
        "armbar", "b1", 5000, "relabel:kimura", pair_node_key="guard pass"
    )

    assert result["stats"]["relabelled"] == 1
    recs = [json.loads(ln) for ln in
            (tmp_path / "audit" / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["node_key"] for r in recs} == {"kimura", "armbar"}, \
        "relabel only ever touches its own candidate claim, never `pair_node_key`"


# ── Mode C: technique definitions review ────────────────────────────────────────────────────

_DEF_CURATED = [
    {"en": "Armbar", "pt": "Chave de Braço", "type": "submission", "variants": []},
    {"en": "Closed Guard", "pt": "Guarda Fechada", "type": "guard", "variants": []},
    {"en": "Rear Naked Choke", "pt": "Mata Leão", "type": "submission", "variants": []},
]
_DEF_DEFINITIONS = {
    "armbar": {"en": "Straightens the elbow.", "pt": "Estica o cotovelo.",
               "source": "draft", "reviewed": False},
    "closed guard": {"en": "Legs locked around the torso.",
                      "pt": "Pernas travadas no tronco.", "source": "draft", "reviewed": True},
    "rear naked choke": {"en": "Choke from the back.", "pt": "Estrangulamento pelas costas.",
                          "source": "draft", "reviewed": False},
}


def _write_definitions_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n: int = 3,
) -> tuple[Path, Path]:
    """Small curated library + matching definitions file, same node_key convention
    (`analysis.names._normalize_name(en)`) as the real 211-entry pair. Every filesystem
    seam Mode C touches is monkeypatched to tmp_path -- nothing here writes into the
    repo's real analysis/data/ or the App checkout."""
    curated = _DEF_CURATED[:n]
    keys = {_normalize_name(str(c["en"])) for c in curated}
    lib_path = tmp_path / "technique_library.json"
    lib_path.write_text(json.dumps(curated), encoding="utf-8")
    def_path = tmp_path / "technique_definitions.json"
    def_path.write_text(
        json.dumps({k: v for k, v in _DEF_DEFINITIONS.items() if k in keys}), encoding="utf-8"
    )
    monkeypatch.setattr(audit_mod, "DICTIONARY_LIBRARY", lib_path)
    monkeypatch.setattr(audit_mod, "DEFINITIONS_PATH", def_path)
    monkeypatch.setattr(audit_mod, "APP_NODES_LIB", tmp_path / "nonexistent_nodes.json")
    monkeypatch.setattr(audit_mod, "APP_DEFINITIONS_DST", tmp_path / "app_definitions.json")
    monkeypatch.setattr(audit_mod, "GRAPPLEMAP_ICON_INDEX_TS", tmp_path / "no_icons.ts")
    return lib_path, def_path


def test_definitions_page_lists_all_curated_rows(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_definitions_fixture(tmp_path, monkeypatch)

    resp = client.get("/admin/audit/definitions")

    assert resp.status_code == 200
    assert '"en": "Armbar"' in resp.text
    assert '"en": "Closed Guard"' in resp.text
    assert '"en": "Rear Naked Choke"' in resp.text
    assert "1 / 3 revisadas" in resp.text  # only closed guard is reviewed: true


def test_definitions_queue_filters_by_type_reviewed_and_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_definitions_fixture(tmp_path, monkeypatch)

    assert len(audit_mod.definitions_queue()) == 3
    assert len(audit_mod.definitions_queue(type_filter="submission")) == 2
    assert len(audit_mod.definitions_queue(type_filter="guard")) == 1
    assert len(audit_mod.definitions_queue(reviewed="reviewed")) == 1
    assert len(audit_mod.definitions_queue(reviewed="unreviewed")) == 2
    hits = audit_mod.definitions_queue(search="mata")
    assert len(hits) == 1 and hits[0]["node_key"] == "rear naked choke"


def test_save_definition_roundtrips_flips_source_to_human_and_regenerates_app_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, def_path = _write_definitions_fixture(tmp_path, monkeypatch, n=1)

    entry = audit_mod.save_definition("armbar", "New EN text.", "Novo texto PT.", True)

    assert entry["source"] == "human"
    assert entry["reviewed"] is True
    assert "reviewed_at" in entry

    on_disk = json.loads(def_path.read_text(encoding="utf-8"))
    assert list(on_disk.keys()) == ["armbar"]  # key order preserved
    assert on_disk["armbar"]["en"] == "New EN text."
    assert on_disk["armbar"]["source"] == "human"

    app_dst = audit_mod.APP_DEFINITIONS_DST
    assert app_dst.is_file(), "App copy must be regenerated on every save"
    app_doc = json.loads(app_dst.read_text(encoding="utf-8"))
    assert app_doc["armbar"]["en"] == "New EN text."


def test_save_definition_unmarked_reviewed_stays_draft_unless_text_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_definitions_fixture(tmp_path, monkeypatch, n=1)

    entry = audit_mod.save_definition(
        "armbar", "Straightens the elbow.", "Estica o cotovelo.", False,
    )

    assert entry["source"] == "draft"  # unchanged text, not reviewed -> still a draft
    assert entry["reviewed"] is False
    assert "reviewed_at" not in entry


def test_save_definition_unknown_node_key_raises_keyerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_definitions_fixture(tmp_path, monkeypatch, n=1)
    with pytest.raises(KeyError):
        audit_mod.save_definition("kimura", "x", "y", False)


def test_definitions_save_route_roundtrips(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_definitions_fixture(tmp_path, monkeypatch, n=1)

    resp = client.post("/admin/audit/definitions/save", json={
        "node_key": "armbar", "def_en": "E", "def_pt": "P", "reviewed": True,
    })

    assert resp.status_code == 200
    assert resp.json()["source"] == "human"


def test_definitions_save_route_404_for_unknown_node_key(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_definitions_fixture(tmp_path, monkeypatch, n=1)

    resp = client.post("/admin/audit/definitions/save", json={
        "node_key": "nope", "def_en": "E", "def_pt": "P", "reviewed": False,
    })

    assert resp.status_code == 404


def test_definition_icon_path_resolves_through_generated_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    icons_dir = tmp_path / "grapplemap_icons"
    icons_dir.mkdir()
    (icons_dir / "chave.png").write_bytes(b"\x89PNGfake")
    index_ts = tmp_path / "grapplemapIconIndex.ts"
    index_ts.write_text(
        'export const GRAPPLEMAP_ICONS: Record<string, number> = {\n'
        '  "armbar": require("../assets/grapplemap_icons/chave.png"),\n'
        '};\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "GRAPPLEMAP_ICONS_DIR", icons_dir)
    monkeypatch.setattr(audit_mod, "GRAPPLEMAP_ICON_INDEX_TS", index_ts)

    assert audit_mod.definition_icon_path("armbar") == icons_dir / "chave.png"
    assert audit_mod.definition_icon_path("missing key") is None


def test_definitions_icon_route(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    icons_dir = tmp_path / "grapplemap_icons"
    icons_dir.mkdir()
    (icons_dir / "chave.png").write_bytes(b"\x89PNGfake")
    index_ts = tmp_path / "grapplemapIconIndex.ts"
    index_ts.write_text(
        '"armbar": require("../assets/grapplemap_icons/chave.png"),\n', encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "GRAPPLEMAP_ICONS_DIR", icons_dir)
    monkeypatch.setattr(audit_mod, "GRAPPLEMAP_ICON_INDEX_TS", index_ts)

    assert client.get("/admin/audit/definitions/icon/armbar").status_code == 200
    assert client.get("/admin/audit/definitions/icon/missing").status_code == 404
