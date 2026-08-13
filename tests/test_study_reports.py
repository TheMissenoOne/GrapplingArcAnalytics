"""Local Study report persistence tests."""

from __future__ import annotations

import json

from admin.study import list_reports, save_report


def _payload(video_id: str = "abcDEFghiJk", title: str = "Study video") -> dict:
    return {
        "video": {
            "id": video_id,
            "title": title,
            "channel": "Coach",
            "url": f"https://www.youtube.com/watch?v={video_id}",
        },
        "summary": {"overview": "1 segment"},
        "segments": [{"id": "seg-0", "text": "Arm drag", "start": 0, "end": 4}],
        "snippets": [{"id": "cap-0", "text": "Arm drag", "start": 0}],
        "nodes": [],
        "relationships": [],
        "quality": {"segments": 1},
    }


def test_save_report_writes_json_and_standalone_html(tmp_path):
    report = save_report(_payload(), tmp_path)

    assert report["id"].startswith("abcDEFghiJk-")
    json_path = tmp_path / report["json_name"]
    html_path = tmp_path / report["html_name"]
    assert json.loads(json_path.read_text()) == _payload()
    html = html_path.read_text()
    assert "Study video" in html
    assert "Arm drag" in html
    assert "<style>" in html


def test_list_reports_returns_newest_first_and_ignores_partial_files(tmp_path):
    first = save_report(_payload("firstVideo01", "First"), tmp_path)
    second = save_report(_payload("secondVideo", "Second"), tmp_path)
    (tmp_path / "broken.json").write_text("{")

    reports = list_reports(tmp_path)

    assert [r["id"] for r in reports] == [second["id"], first["id"]]
    assert reports[0]["title"] == "Second"


def test_save_report_rejects_missing_video_identity(tmp_path):
    payload = _payload()
    payload["video"]["id"] = ""

    try:
        save_report(payload, tmp_path)
    except ValueError as exc:
        assert "video.id" in str(exc)
    else:
        raise AssertionError("save_report accepted a payload without video.id")


def test_save_report_rejects_non_object_video(tmp_path):
    payload = _payload()
    payload["video"] = []

    try:
        save_report(payload, tmp_path)
    except ValueError as exc:
        assert "payload.video" in str(exc)
    else:
        raise AssertionError("save_report accepted a non-object video")
