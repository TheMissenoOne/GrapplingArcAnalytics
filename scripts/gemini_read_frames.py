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
"""
from __future__ import annotations

import argparse
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

logger = logging.getLogger("gemini_read_frames")

PROMPT_PATH = REPO / "docs" / "PROMPT_gemini_frame_reading.md"
DEFAULT_MODEL = "gemini-3.6-flash-high"
_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg"}


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


def read_frames(sheets: list[Path], prompt: str, model: str) -> tuple[dict[str, Any], Any]:
    """Sends the sheets + prompt to Gemini, returns (parsed_answer_with_source, raw_response)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type=_MIME[p.suffix.lower()])
            for p in sheets]
    parts.append(types.Part.from_text(text=prompt))
    resp = client.models.generate_content(
        model=model, contents=parts,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    answer = json.loads(resp.text)
    answer["source"] = (f"gemini_read_frames ({model}, "
                        f"{datetime.now(UTC).date().isoformat()}) — not yet human-reviewed")
    return answer, resp


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
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    sheets = find_sheets(a.sheets)
    prompt = load_prompt()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    dry_run = a.dry_run or not api_key
    if dry_run:
        reason = "no GEMINI_API_KEY" if not api_key else "--dry-run"
        logger.info("dry run (%s). Would send %d file(s):", reason, len(sheets))
        for p in sheets:
            logger.info("  %s (%d bytes)", p, p.stat().st_size)
        print("--- prompt ---")
        print(prompt)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(empty_answer(reason), indent=2), encoding="utf-8")
        logger.info("wrote empty-shaped %s", a.out)
        return 0

    answer, resp = read_frames(sheets, prompt, a.model)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(answer, indent=2), encoding="utf-8")
    (a.out.parent / "gemini_raw.json").write_text(
        json.dumps({"model": a.model, "text": resp.text}, indent=2), encoding="utf-8")
    logger.info("wrote %s (%d events)", a.out, len(answer.get("events", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
