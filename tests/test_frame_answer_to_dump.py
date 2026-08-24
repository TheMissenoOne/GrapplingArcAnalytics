"""Frame answer -> dump conversion (step 7, docs/frame_pdf_reading.md §4.7).

Five things were measured against the real corpus and must hold: the review gate (unreviewed
files never convert), actor/winner resolution never guesses, a "/" label is refused rather
than mangled by _normalize_name, ts_origin/video_start_seconds survive the seam, and the
output event shape is exactly the six keys the importer keeps.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.dump_import import build_matches
from scripts.frame_answer_to_dump import (
    EVENT_KEEP,
    convert_all,
    convert_file,
    render_dump,
)

REVIEWED = "frame_registrar (human review over model reading)"
UNREVIEWED = "frame_answer_import (returned reading, not yet human-reviewed)"


def _write(d: Path, bout: dict[str, Any], events: list[dict[str, Any]], source: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / "events.json"
    f.write_text(json.dumps({"bout": bout, "events": events, "source": source}), encoding="utf-8")
    return f


def _bout(**over: Any) -> dict[str, Any]:
    base = {"athlete_a": "Anabel Lopez", "athlete_b": "Aurelie Le Vern", "year": 2024,
            "event": "European No-Gi", "winner": "Aurelie Le Vern", "win_type": "Armbar",
            "bout_start_seconds": 6}
    base.update(over)
    return base


def test_unreviewed_file_is_skipped(tmp_path: Path) -> None:
    f = _write(tmp_path / "bout1", _bout(), [], UNREVIEWED)
    report, block = convert_file(f)
    assert report.status == "skipped_unreviewed"
    assert block is None


def test_reviewed_clean_file_converts(tmp_path: Path) -> None:
    events = [
        {"ts": 10, "label": "Pull Guard", "actor": "Anabel Lopez", "successful": True,
         "type": "guard"},
        {"ts": 120, "label": "Armbar", "actor": "Aurelie Le Vern", "successful": True,
         "type": "submission"},
    ]
    f = _write(tmp_path / "bout1", _bout(), events, REVIEWED)
    report, block = convert_file(f)
    assert report.status == "converted"
    assert report.events_kept == 2
    assert report.refused == []
    assert block is not None
    ((name, year), m), = block.items()
    assert name == "Anabel Lopez" and year == 2024
    assert m["events"] == events  # every key already in EVENT_KEEP, nothing dropped
    assert m["winner"] == "Aurelie Le Vern"


def test_unresolved_actor_is_refused_not_guessed(tmp_path: Path) -> None:
    events = [
        {"ts": 10, "label": "Pull Guard", "actor": "Someone Else", "successful": True,
         "type": "guard"},
        {"ts": 20, "label": "Armbar", "actor": "Aurelie Le Vern", "successful": True,
         "type": "submission"},
    ]
    f = _write(tmp_path / "bout1", _bout(), events, REVIEWED)
    report, block = convert_file(f)
    assert report.status == "converted"
    assert report.events_kept == 1  # only the resolvable one survives
    assert any("unresolved actor" in r for r in report.refused)
    assert block is not None
    m = next(iter(block.values()))
    assert len(m["events"]) == 1 and m["events"][0]["actor"] == "Aurelie Le Vern"


def test_unresolved_winner_is_refused(tmp_path: Path) -> None:
    f = _write(tmp_path / "bout1", _bout(winner="Someone Else"), [], REVIEWED)
    report, block = convert_file(f)
    assert any("unresolved winner" in r for r in report.refused)
    assert block is not None
    assert next(iter(block.values()))["winner"] is None


def test_slash_label_is_refused_not_mangled(tmp_path: Path) -> None:
    events = [
        {"ts": 10, "label": "Reset / Stalemate", "actor": "Anabel Lopez", "successful": True,
         "type": "transition"},
        {"ts": 20, "label": "Armbar", "actor": "Aurelie Le Vern", "successful": True,
         "type": "submission"},
    ]
    f = _write(tmp_path / "bout1", _bout(), events, REVIEWED)
    report, block = convert_file(f)
    assert any("slash_label" in r for r in report.refused)
    assert block is not None
    m = next(iter(block.values()))
    assert all("/" not in e["label"] for e in m["events"])
    assert len(m["events"]) == 1


def test_ts_origin_and_video_start_seconds_carried(tmp_path: Path) -> None:
    f = _write(tmp_path / "bout1", _bout(bout_start_seconds=6), [], REVIEWED)
    _report, block = convert_file(f)
    assert block is not None
    m = next(iter(block.values()))
    assert m["ts_origin"] == "video_absolute"
    assert m["video_start_seconds"] == 6

    # And the seam holds one layer further in: dump_import.build_matches must carry the
    # same two fields onto CanonicalMatch, which is what run_dump reads to fill the
    # matches.ts_origin/video_start_seconds columns (alembic 0047).
    cm, = build_matches([block])
    assert cm.ts_origin == "video_absolute"
    assert cm.video_start_seconds == 6


def test_dropped_keys_are_stripped_and_counted(tmp_path: Path) -> None:
    events = [
        {"ts": 10, "label": "Pull Guard", "actor": "Anabel Lopez", "successful": True,
         "type": "guard", "confidence": "low", "note": "hard to see", "new_label": False},
    ]
    f = _write(tmp_path / "bout1", _bout(), events, REVIEWED)
    report, block = convert_file(f)
    assert report.dropped_keys == 3
    assert block is not None
    m = next(iter(block.values()))
    assert set(m["events"][0]) <= set(EVENT_KEEP)
    assert "confidence" not in m["events"][0]
    assert "note" not in m["events"][0]
    assert "new_label" not in m["events"][0]


def test_convert_all_over_real_corpus_converts_zero_today() -> None:
    """Measured 2026-08-24: every file under data/frame_pdf/out/ still carries the
    unreviewed stamp, so a real run converts nothing. That is the gate working, not a
    defect -- this pins the behavior so a future change can't silently relax the gate.
    data/ is gitignored, so on a fresh checkout (CI) the corpus is absent: skip, like
    test_grapplemap's data-presence gate."""
    import pytest

    from scripts.frame_answer_to_dump import OUT

    if not OUT.exists():
        pytest.skip("data/frame_pdf/out/ not present on this checkout")
    reports, blocks = convert_all()
    assert reports  # the real corpus fixtures actually exist
    assert all(r.status == "skipped_unreviewed" for r in reports)
    assert blocks == []


def test_render_dump_is_valid_python_matching_the_existing_shape(tmp_path: Path) -> None:
    events = [{"ts": 10, "label": "Pull Guard", "actor": "Anabel Lopez", "successful": True,
               "type": "guard"}]
    f = _write(tmp_path / "bout1", _bout(), events, REVIEWED)
    _report, block = convert_file(f)
    assert block is not None
    src = render_dump([block])
    ns: dict[str, Any] = {}
    exec(compile(src, "<dump>", "exec"), ns)  # noqa: S102 - test-only, our own generated source
    raw = ns["RAW"]
    assert isinstance(raw, list) and len(raw) == 1
    (name, year), m = next(iter(raw[0].items()))
    assert name == "Anabel Lopez" and year == 2024
    assert m["ts_origin"] == "video_absolute"
