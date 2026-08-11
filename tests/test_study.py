"""admin/study — local port of the public site's Study pipeline."""

from __future__ import annotations

import pytest

from admin.study import (
    RagIndex,
    StudyError,
    build_analysis,
    fetch_transcript,
    group_captions,
)


def _cap(text: str, start: float, duration: float = 3.5) -> dict:
    return {"text": text, "start": start, "duration": duration}


# ── grouping (port of grouping.ts) ───────────────────────────────────────────


def test_group_captions_single_segment():
    caps = [_cap("alpha", 0.0), _cap("beta", 3.0)]
    segs = group_captions(caps)
    assert len(segs) == 1
    assert segs[0]["id"] == "seg-0"
    assert segs[0]["text"] == "alpha beta"
    assert segs[0]["start"] == 0.0
    assert segs[0]["end"] == pytest.approx(6.5)


def test_group_captions_splits_on_char_limit():
    caps = [_cap("a" * 600, 0.0), _cap("b" * 600, 3.0)]
    segs = group_captions(caps)
    assert len(segs) == 2
    assert segs[0]["id"] == "seg-0"
    assert segs[1]["id"] == "seg-1"
    assert segs[0]["end"] == pytest.approx(3.5)


def test_group_captions_splits_on_time_limit():
    caps = [_cap("short", 0.0, 0.5), _cap("long", 130.0, 0.5)]
    segs = group_captions(caps)
    assert len(segs) == 2
    assert segs[0]["text"] == "short"
    assert segs[1]["start"] == pytest.approx(130.0)


def test_group_captions_empty():
    assert group_captions([]) == []


# ── RagIndex (port of site/study-rag.js) ─────────────────────────────────────


def test_rag_exact_alias_hit():
    rag = RagIndex([
        {"key": "arm-drag", "label": "Arm Drag", "aliases": ["arm drag"], "type": "transition"},
        {"key": "kimura", "label": "Kimura", "aliases": [], "type": "submission"},
    ])
    hits = rag.search("arm drag", limit=6)
    assert hits and hits[0]["nodeKey"] == "arm-drag"
    assert hits[0]["score"] >= 0.65  # label boosts the top hit


def test_rag_pt_alias_matches():
    rag = RagIndex([
        {"key": "armbar", "label": "Armbar", "aliases": ["chave de braço"], "type": "submission"},
        {"key": "triangle", "label": "Triangle", "aliases": [], "type": "submission"},
    ])
    hits = rag.search("chave de braço")
    assert hits and hits[0]["nodeKey"] == "armbar"


def test_rag_empty_returns_empty():
    assert RagIndex([]).search("anything") == []


# ── transcript + synthesis ────────────────────────────────────────────────────


def test_fetch_transcript_invalid_url():
    with pytest.raises(StudyError):
        fetch_transcript("https://example.com/not-youtube")


def test_fetch_transcript_network(monkeypatch):
    """Full path with mocked network: oEmbed + transcript lib."""
    class _FakeResp:
        ok = True

        def json(self):
            return {"title": "My Study", "author_name": "Coach", "thumbnail_url": "http://t"}

    monkeypatch.setattr("admin.study.requests.get", lambda *a, **k: _FakeResp())

    class _FakeAPI:
        @staticmethod
        def get_transcript(vid, languages):
            assert vid == "abcDEFghiJk"
            return [
                {"text": "open guard", "start": 0.0, "duration": 2.0},
                {"text": "sweep", "start": 2.2, "duration": 2.0},
            ]

    monkeypatch.setattr("youtube_transcript_api.YouTubeTranscriptApi", _FakeAPI)
    meta, caps = fetch_transcript("https://www.youtube.com/watch?v=abcDEFghiJk")
    assert meta["id"] == "abcDEFghiJk"
    assert meta["title"] == "My Study"
    assert caps[0]["text"] == "open guard"


def test_build_analysis_with_knowledge(monkeypatch):
    monkeypatch.setattr(
        "admin.study.fetch_transcript",
        lambda url, langs: (
            {"id": "abcDEFghiJk", "title": "My Study", "channel": "Coach",
             "thumbnail": "http://t", "duration": None, "url": "http://u"},
            [
                {"text": "arm drag to the back", "start": 0.0, "duration": 5.0},
                {"text": "back control kimura finish", "start": 6.0, "duration": 5.0},
            ],
        ),
    )
    knowledge = [
        {"key": "arm-drag", "label": "Arm Drag", "aliases": ["arm drag"], "type": "transition"},
        {"key": "kimura", "label": "Kimura", "aliases": [], "type": "submission"},
        {"key": "back-control", "label": "Back Control", "aliases": [], "type": "control"},
    ]
    payload = build_analysis("http://u", knowledge=knowledge)
    assert payload["video"]["title"] == "My Study"
    assert len(payload["segments"]) == 1
    assert payload["segments"][0]["id"] == "seg-0"
    assert payload["nodes"]
    assert payload["quality"]["segments"] == 1
    assert payload["summary"]["overview"]


def test_build_analysis_no_knowledge(monkeypatch):
    monkeypatch.setattr(
        "admin.study.fetch_transcript",
        lambda url, langs: (
            {"id": "abc", "title": "T", "channel": "C", "thumbnail": "",
             "duration": None, "url": "u"},
            [{"text": "free form", "start": 0.0, "duration": 5.0}],
        ),
    )
    payload = build_analysis("u", knowledge=[])
    assert payload["nodes"] == []
    assert any("technique library" in w for w in payload["quality"]["warnings"])
