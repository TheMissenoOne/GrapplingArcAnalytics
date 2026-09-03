#!/usr/bin/env python
"""Send a rendered frame sheet to Gemini and save its reading as ``events.json``.

    uv run python -m scripts.gemini_read_frames \\
        --sheets data/frame_pdf/out/owner_20260725/sheets \\
        --out data/frame_pdf/out/owner_20260725/events.json

The prompt is ``docs/PROMPT_gemini_frame_reading.md`` verbatim -- the same contract
``scripts/frame_answer_import.py`` consumes (a ``{"bout": ..., "events": [...]}`` object). This
script only adds the ``source`` field that same contract requires for provenance (§1 of
``docs/frame_pdf_reading.md``): the answer is a raw model reading, "not yet human-reviewed",
same as ``frame_answer_import.py``'s own stamp -- neither this script nor that one opens a
database connection or writes anything a human hasn't looked at yet.

No API key (``GEMINI_API_KEY``, read from the environment / ``.env``) -> automatic
``--dry-run``: prints the prompt and the file list it would have sent, and writes an
empty-but-correctly-shaped ``events.json`` so a caller checking for the file's existence does
not need a second code path.

## ``--guidance``: page-by-page reading with a corpus-statistics prompt hint

Default mode sends every page of the sheet in ONE call ("whole sheet"). ``--guidance`` reads
ONE frame-grid page per call instead -- the context page and every "Allowed labels" vocabulary
page are re-attached on every call (:func:`split_sheet_pages` finds the boundary by page TEXT,
not by layout metadata, so it needs no change to ``scripts/frame_pdf.py``). Before each call,
the LAST state read on the previous page (an event whose ``analysis.taxonomy_kind.kind_of_entry``
resolves to ``"state"`` -- guard/control, same split ``analysis/next_moves.py`` fits on) plus
the last 2 actions feed ``analysis.next_moves_embed.guidance_block``, a Markov-only ("corpus
statistics, not ground truth, NOT a constraint") top-8 hint appended to that page's prompt. Page
1 has no prior page, so it reads from state ``"start"`` -- ``MarkovNextMoves`` backs off to the
unigram for an unseen state, so this is a real (if uninformative) guidance block, not a crash.
Requires exactly one ``.pdf`` under ``--sheets`` (the multi-file whole-sheet input shape does
not carry page boundaries to split). See ``docs/next_moves.md`` §7-8 for the guidance model.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.next_moves import (  # noqa: E402
    OWN,
    MarkovNextMoves,
    build_vocab,
    corpus_points,
    library_actions,
)
from analysis.next_moves_embed import guidance_block  # noqa: E402
from analysis.taxonomy_kind import kind_of_entry  # noqa: E402
from analysis.technique_match import clean_label  # noqa: E402

logger = logging.getLogger("gemini_read_frames")

PROMPT_PATH = REPO / "docs" / "PROMPT_gemini_frame_reading.md"
DEFAULT_MODEL = "gemini-3.6-flash"
_THINKING_LEVELS = ("low", "medium", "high")
_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg"}

#: The exact banner ``scripts/frame_pdf.py:draw_library_pages`` prints at the top of every
#: vocabulary page. A substring match on extracted page text is what tells
#: :func:`split_sheet_pages` a "Allowed labels" page from a frame-grid page, without frame_pdf.py
#: having to record page boundaries anywhere -- the sheet's own text already carries them.
LIBRARY_PAGE_MARKER = "Allowed labels"

#: "últimas 2 ações" per the guidance-round brief -- narrower than ``next_moves.HISTORY_N`` (3),
#: which is the ranking evaluation's own context window, a different question.
GUIDANCE_HISTORY_N = 2

PAGE_NOTICE = (
    "\n\nNOTE: this call sends only ONE frame-grid page of a longer bout (the context page and "
    "the full vocabulary are still attached above). Only report events your frames on THIS page "
    "actually show; leave bout-level fields (winner, final_score, ...) you cannot support from "
    "this page's frames empty rather than guessing."
)


def load_prompt() -> str:
    """The prompt body only -- the doc's own ``---``-delimited middle section, not its title,
    intro or the "Measured performance" appendix below it. Keeps this script and the doc
    provably in sync: change the prompt here by changing the doc, nowhere else."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    parts = re.split(r"\n---\n", text)
    if len(parts) < 3:
        logger.warning("%s has no --- delimited prompt section; sending the whole file",
                       PROMPT_PATH)
        return text.strip()
    return parts[1].strip()


def find_sheets(sheets_dir: Path) -> list[Path]:
    files = sorted(p for p in sheets_dir.iterdir() if p.suffix.lower() in _MIME)
    if not files:
        raise FileNotFoundError(f"no .pdf/.png/.jpg under {sheets_dir}")
    return files


def empty_answer(reason: str) -> dict[str, Any]:
    return {"bout": {}, "events": [], "source": f"gemini_read_frames (dry-run: {reason})"}


def build_generate_config(thinking: str | None) -> Any:
    """Shared by both attempts in ``read_frames`` -- with or without ``thinking_config``.
    ``automatic_function_calling`` disabled: we never pass tools, so the AFC branch (and its
    "Direct use of AFC..." log warning) has nothing to do here."""
    from google.genai import types

    kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if thinking:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking.upper())
    return types.GenerateContentConfig(**kwargs)


def _generate_with_retry(client: Any, parts: list[Any], model: str,
                         thinking: str | None) -> tuple[Any, str | None]:
    """One ``generate_content`` call, with the thinking-level retry both callers need.
    Returns ``(response, thinking_actually_used)`` -- ``None`` when a 400 forced the retry."""
    from google.genai import errors

    try:
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config(thinking))
        return resp, thinking
    except errors.ClientError:
        if not thinking:
            raise
        logger.warning("model %s rejected thinking_level=%s, retrying without thinking",
                       model, thinking)
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config(None))
        return resp, None


def read_frames(sheets: list[Path], prompt: str, model: str,
                thinking: str | None) -> tuple[dict[str, Any], Any]:
    """Sends the sheets + prompt to Gemini, returns (parsed_answer_with_source, raw_response).
    A model that rejects ``thinking_level`` (400) is retried once without it, with a warning --
    not every model on this account is guaranteed to support it."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type=_MIME[p.suffix.lower()])
            for p in sheets]
    parts.append(types.Part.from_text(text=prompt))

    resp, thinking = _generate_with_retry(client, parts, model, thinking)

    answer = json.loads(resp.text)
    answer["source"] = (f"gemini_read_frames ({model}, thinking={thinking or 'none'}, "
                        f"{datetime.now(UTC).date().isoformat()}) — not yet human-reviewed")
    return answer, resp


def usage_totals(usage_metadata: Any) -> dict[str, int]:
    """SDK usage object (or ``None``) -> the 4-field dict this repo's cost accounting sums
    across calls -- shared shape so a guided (multi-call) bout and a whole-sheet (one-call)
    bout add up the same way in ``scripts/gemini_baseline.py``."""
    if usage_metadata is None:
        return {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}
    return {
        "prompt": usage_metadata.prompt_token_count or 0,
        "candidates": usage_metadata.candidates_token_count or 0,
        "thoughts": usage_metadata.thoughts_token_count or 0,
        "total": usage_metadata.total_token_count or 0,
    }


def split_sheet_pages(pdf_path: Path) -> tuple[bytes, bytes, list[bytes]]:
    """ONE frame-sheet PDF -> ``(context_page_pdf, vocabulary_pages_pdf, [grid_page_pdf, ...])``,
    each a standalone one-or-more-page PDF ready to hand to Gemini as its own ``Part``.

    Boundary is found by page TEXT, not layout metadata (``scripts/frame_pdf.py`` records no
    page map): page 0 is always the context page (``draw_context_page`` runs first and once);
    every following page whose extracted text carries :data:`LIBRARY_PAGE_MARKER` is a
    vocabulary page (``draw_library_pages`` prints that banner on each of its pages, including
    continuations); everything after that is a frame-grid page, in order.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    pages = reader.pages
    if not pages:
        raise ValueError(f"{pdf_path} has no pages")

    def _subset(idxs: list[int]) -> bytes:
        writer = PdfWriter()
        for i in idxs:
            writer.add_page(pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()

    i = 1
    lib_idx: list[int] = []
    while i < len(pages) and LIBRARY_PAGE_MARKER in (pages[i].extract_text() or ""):
        lib_idx.append(i)
        i += 1
    grid_idx = list(range(i, len(pages)))
    if not grid_idx:
        raise ValueError(f"{pdf_path}: no frame-grid pages found after context + vocabulary")

    return _subset([0]), (_subset(lib_idx) if lib_idx else b""), [_subset([j]) for j in grid_idx]


def fit_markov_model() -> MarkovNextMoves:
    """The next-move Markov prior, fitted on the FULL public corpus (not the train/val split --
    that split is specific to ``scripts/eval_next_moves.py``'s ranking evaluation;
    ``docs/next_moves.md`` §8's own guidance-block example fits on every point the same way).
    Reads ``data/next_moves/corpus.json`` if cached, else pulls ``matches`` once (read-only)."""
    from scripts.eval_next_moves import load_corpus

    bouts = load_corpus()
    points, _ = corpus_points(bouts)
    vocab = build_vocab(points, library_actions())
    return MarkovNextMoves(vocab).fit(points)


def _advance_guidance_context(state: str, history: list[str],
                              events: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """This page's own events -> the ``(state, history)`` the NEXT page's guidance is built
    from. Same state/action split ``analysis.next_moves.decision_points`` fits the model on,
    so the events feeding the guidance are read the same way the guidance itself was trained."""
    for ev in events:
        etype = str(ev.get("type", ""))
        label = clean_label(str(ev.get("label", "")), etype)
        if not label:
            continue
        kind = kind_of_entry(label, etype)
        if kind == "state":
            state = label
        elif kind == "action":
            history.append(label)
    return state, history[-GUIDANCE_HISTORY_N:]


def read_frames_guided(pdf_path: Path, prompt: str, model: str, thinking: str | None, *,
                       markov_model: MarkovNextMoves | None) -> tuple[dict[str, Any],
                                                                      list[dict[str, Any]]]:
    """Page-by-page read of ONE sheet PDF -- one Gemini call per frame-grid page, context +
    vocabulary pages re-attached every call, each call's prompt carrying a Markov guidance
    block built from the state/actions the PREVIOUS page's own answer reported (page 1 reads
    from state ``"start"``). Returns ``(merged_answer_with_source, per_page_records)`` -- the
    per-page list (``page``, ``guidance``, ``n_events``, ``text``, ``usage``) is this call's own
    provenance, written to ``gemini_raw.json`` by the caller."""
    from google import genai
    from google.genai import types

    context_bytes, library_bytes, grid_pages = split_sheet_pages(pdf_path)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    state: str = "start"
    history: list[str] = []
    bout: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    used_thinking = thinking

    for i, page_bytes in enumerate(grid_pages):
        block = (guidance_block(state, history, OWN, k=8, model=markov_model)
                if markov_model is not None else "")
        page_prompt = prompt + PAGE_NOTICE + (f"\n\n{block}" if block else "")
        parts = [types.Part.from_bytes(data=context_bytes, mime_type="application/pdf")]
        if library_bytes:
            parts.append(types.Part.from_bytes(data=library_bytes, mime_type="application/pdf"))
        parts.append(types.Part.from_bytes(data=page_bytes, mime_type="application/pdf"))
        parts.append(types.Part.from_text(text=page_prompt))

        resp, used_thinking = _generate_with_retry(client, parts, model, used_thinking)
        page_answer = json.loads(resp.text)
        page_events = page_answer.get("events") or []
        events.extend(page_events)
        if not bout:
            bout = page_answer.get("bout") or {}
        pages.append({"page": i, "guidance": block, "n_events": len(page_events),
                      "text": resp.text, "usage": usage_totals(resp.usage_metadata)})
        state, history = _advance_guidance_context(state, history, page_events)

    answer = {"bout": bout, "events": events,
             "source": (f"gemini_read_frames (guidance, {model}, "
                       f"thinking={used_thinking or 'none'}, "
                       f"{datetime.now(UTC).date().isoformat()}) — not yet human-reviewed")}
    return answer, pages


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sheets", type=Path, required=True,
                    help="directory of .pdf/.png/.jpg frame sheets to send")
    ap.add_argument("--out", type=Path, required=True, help="events.json path to write")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--thinking", choices=_THINKING_LEVELS, default="high")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--guidance", action="store_true",
                    help="page-by-page reading with a Markov next-move prompt hint "
                         "(see module docstring); needs exactly one .pdf under --sheets")
    a = ap.parse_args()

    sheets = find_sheets(a.sheets)
    prompt = load_prompt()

    if a.guidance and [p.suffix.lower() for p in sheets] != [".pdf"]:
        raise ValueError("--guidance needs exactly one .pdf under --sheets "
                          f"(got {[p.name for p in sheets]}) -- page splitting needs page "
                          "boundaries a single multi-page PDF carries")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    dry_run = a.dry_run or not api_key
    if dry_run:
        reason = "no GEMINI_API_KEY" if not api_key else "--dry-run"
        logger.info("dry run (%s). Would send %d file(s)%s:", reason, len(sheets),
                    " (--guidance)" if a.guidance else "")
        for p in sheets:
            logger.info("  %s (%d bytes)", p, p.stat().st_size)
        print("--- prompt ---")
        print(prompt)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(empty_answer(reason), indent=2), encoding="utf-8")
        logger.info("wrote empty-shaped %s", a.out)
        return 0

    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.guidance:
        markov_model = fit_markov_model()
        answer, pages = read_frames_guided(sheets[0], prompt, a.model, a.thinking,
                                           markov_model=markov_model)
        a.out.write_text(json.dumps(answer, indent=2), encoding="utf-8")
        (a.out.parent / "gemini_raw.json").write_text(
            json.dumps({"model": a.model, "thinking": a.thinking, "guidance": True,
                       "pages": pages}, indent=2),
            encoding="utf-8")
        logger.info("wrote %s (%d events, %d page calls)", a.out, len(answer.get("events", [])),
                    len(pages))
        return 0

    answer, resp = read_frames(sheets, prompt, a.model, a.thinking)
    a.out.write_text(json.dumps(answer, indent=2), encoding="utf-8")
    usage = resp.usage_metadata
    (a.out.parent / "gemini_raw.json").write_text(
        json.dumps({"model": a.model, "thinking": a.thinking, "text": resp.text,
                    "usage_metadata": usage.model_dump(mode="json") if usage else None},
                   indent=2),
        encoding="utf-8")
    logger.info("wrote %s (%d events)", a.out, len(answer.get("events", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
