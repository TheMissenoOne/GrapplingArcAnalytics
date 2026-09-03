"""``--guidance`` page-by-page reading: page splitting, state/action tracking across pages,
and the guided call sequence -- all offline, no network, no DB (the Markov model is built
in-process from a tiny hand-written corpus, not fetched).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.genai")
pytest.importorskip("pypdf")

from pypdf import PdfReader

from analysis.next_moves import MarkovNextMoves, decision_points
from scripts.gemini_read_frames import (
    GUIDANCE_HISTORY_N,
    LIBRARY_PAGE_MARKER,
    _advance_guidance_context,
    read_frames_guided,
    split_sheet_pages,
    usage_totals,
)


def _fitted_model() -> MarkovNextMoves:
    seq = [
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Armbar", "type": "submission", "actor_id": "A"},
        {"label": "Closed Guard", "type": "guard", "actor_id": "A"},
        {"label": "Triangle Choke", "type": "submission", "actor_id": "A"},
    ]
    pts = decision_points(seq, "b1")
    return MarkovNextMoves(["Armbar", "Triangle Choke", "Heel Hook"]).fit(pts)


# ── page splitting ───────────────────────────────────────────────────────────────


def _build_sheet(tmp_path: Path, n_library_pages: int, n_grid_pages: int) -> Path:
    """A minimal PDF shaped like ``frame_pdf.py``'s own sheet: one context page, then
    ``n_library_pages`` pages each carrying the real library banner text, then
    ``n_grid_pages`` plain pages with no banner -- exactly the text cues
    ``split_sheet_pages`` reads."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    path = tmp_path / "sheet.pdf"
    c = Canvas(str(path), pagesize=A4)
    c.drawString(50, 750, "Context page — video url, title, time range")
    c.showPage()
    for i in range(n_library_pages):
        c.drawString(50, 750, LIBRARY_PAGE_MARKER + " — use one of these, verbatim")
        c.drawString(50, 700, f"library page {i}")
        c.showPage()
    for i in range(n_grid_pages):
        c.drawString(50, 750, f"0:0{i}0   ({i * 10}s)")
        c.showPage()
    c.save()
    return path


def test_split_sheet_pages_boundaries(tmp_path):
    sheet = _build_sheet(tmp_path, n_library_pages=2, n_grid_pages=3)
    context_bytes, library_bytes, grid_pages = split_sheet_pages(sheet)

    assert context_bytes.startswith(b"%PDF")
    assert library_bytes.startswith(b"%PDF")
    assert len(grid_pages) == 3
    for g in grid_pages:
        assert g.startswith(b"%PDF")

    assert len(PdfReader(io.BytesIO(context_bytes)).pages) == 1
    assert len(PdfReader(io.BytesIO(library_bytes)).pages) == 2
    for g in grid_pages:
        assert len(PdfReader(io.BytesIO(g)).pages) == 1


def test_split_sheet_pages_no_library(tmp_path):
    sheet = _build_sheet(tmp_path, n_library_pages=0, n_grid_pages=2)
    context_bytes, library_bytes, grid_pages = split_sheet_pages(sheet)
    assert library_bytes == b""
    assert len(grid_pages) == 2


def test_split_sheet_pages_no_grid_pages_raises(tmp_path):
    # context + library only -- nothing to read page-by-page.
    sheet = _build_sheet(tmp_path, n_library_pages=1, n_grid_pages=0)
    with pytest.raises(ValueError, match="no frame-grid pages"):
        split_sheet_pages(sheet)


# ── state/action tracking across pages ─────────────────────────────────────────


def test_advance_guidance_context_splits_state_and_action():
    events = [
        {"ts": 10, "label": "Closed Guard", "type": "guard"},
        {"ts": 15, "label": "Armbar", "type": "submission"},
        {"ts": 20, "label": "Mount", "type": "control"},
    ]
    state, history = _advance_guidance_context("start", [], events)
    assert state == "Mount"           # the LAST state event wins
    assert history == ["Armbar"]      # only the action event is remembered


def test_advance_guidance_context_caps_at_guidance_history_n():
    events = [{"ts": i, "label": lb, "type": "submission"}
             for i, lb in enumerate(["Armbar", "Triangle Choke", "Heel Hook"])]
    _, history = _advance_guidance_context("Closed Guard", [], events)
    assert len(history) == GUIDANCE_HISTORY_N
    assert history == ["Triangle Choke", "Heel Hook"]  # most recent 2, order kept


def test_advance_guidance_context_ignores_unclassifiable_labels():
    # an empty/garbage label must not crash or silently become a state/history entry.
    events = [{"ts": 1, "label": "", "type": "guard"}]
    state, history = _advance_guidance_context("start", ["Armbar"], events)
    assert state == "start"
    assert history == ["Armbar"]


# ── usage_totals ────────────────────────────────────────────────────────────────


def test_usage_totals_none():
    assert usage_totals(None) == {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}


def test_usage_totals_reads_sdk_object():
    u = MagicMock(prompt_token_count=10, candidates_token_count=2, thoughts_token_count=3,
                  total_token_count=15)
    assert usage_totals(u) == {"prompt": 10, "candidates": 2, "thoughts": 3, "total": 15}


# ── read_frames_guided: one call per grid page, guidance threaded across pages ──


@pytest.fixture(autouse=True)
def _fake_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def _page_response(events: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.text = json.dumps({"bout": {"athlete_a": "A", "athlete_b": "B"}, "events": events})
    resp.usage_metadata = MagicMock(prompt_token_count=100, candidates_token_count=10,
                                    thoughts_token_count=5, total_token_count=115)
    return resp


def test_read_frames_guided_one_call_per_grid_page_with_threaded_state(tmp_path):
    sheet = _build_sheet(tmp_path, n_library_pages=1, n_grid_pages=2)
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = [
        _page_response([{"ts": 10, "label": "Closed Guard", "type": "guard"},
                       {"ts": 15, "label": "Armbar", "type": "submission"}]),
        _page_response([{"ts": 40, "label": "Mount", "type": "control"}]),
    ]
    model = _fitted_model()

    with patch("google.genai.Client", return_value=fake_client):
        answer, pages = read_frames_guided(sheet, "PROMPT BODY", "gemini-3.6-flash", "high",
                                           markov_model=model)

    assert fake_client.models.generate_content.call_count == 2
    assert len(pages) == 2
    assert len(answer["events"]) == 3
    assert "guidance" in answer["source"]
    assert answer["bout"] == {"athlete_a": "A", "athlete_b": "B"}

    # Page 1 (the first call) has no prior page -- guidance is built from state "start", but
    # the function still produces a real, non-empty block (graceful backoff, not a skip).
    assert pages[0]["guidance"] != ""
    assert "Likely next moves" in pages[0]["guidance"]

    # Page 2's guidance is built from what page 1 actually reported: state -> "Closed Guard"
    # (its only state event), history -> ["Armbar"] (its only action event).
    assert "Closed Guard" in pages[1]["guidance"]

    # Every call attaches context + library + exactly this page's own grid page: 4 parts
    # (context, library, grid page, prompt text).
    for call in fake_client.models.generate_content.call_args_list:
        assert len(call.kwargs["contents"]) == 4

    assert pages[0]["usage"] == {"prompt": 100, "candidates": 10, "thoughts": 5, "total": 115}


def test_read_frames_guided_without_markov_model_sends_no_guidance(tmp_path):
    sheet = _build_sheet(tmp_path, n_library_pages=0, n_grid_pages=1)
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _page_response([])

    with patch("google.genai.Client", return_value=fake_client):
        answer, pages = read_frames_guided(sheet, "PROMPT BODY", "gemini-3.6-flash", "high",
                                           markov_model=None)

    assert pages[0]["guidance"] == ""
    # no library page in this sheet -> 3 parts (context, grid page, prompt text).
    call = fake_client.models.generate_content.call_args_list[0]
    assert len(call.kwargs["contents"]) == 3
