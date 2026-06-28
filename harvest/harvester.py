"""Compile a fight transcript + appended processing prompt into one file.

Output goes to ``data/harvest/inbox/``. The file is metadata header + the full
transcript + the processing prompt (``harvest/prompt.txt``). A human pastes it into
ChatGPT/Copilot/Deepseek and saves the returned JSON into ``data/harvest/processed/``
(read by ``db.scraped_import.import_scraped_dir``)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from harvest.transcripts import (
    get_transcript,
    get_video_title,
    guess_year,
    parse_fighters,
    playlist_video_urls,
    resolve_to_video_id,
)

logger = logging.getLogger(__name__)


@dataclass
class HarvestResult:
    """Outcome for one input. ``status`` ∈ {ok, no_video, no_transcript, error}."""

    input: str
    status: str
    video_id: str | None = None
    path: Path | None = None
    detail: str = ""

_ROOT = Path(__file__).resolve().parent.parent
HARVEST_INBOX = _ROOT / "data" / "harvest" / "inbox"
HARVEST_PROCESSED = _ROOT / "data" / "harvest" / "processed"
_PROMPT_PATH = Path(__file__).resolve().parent / "prompt.txt"


def _sanitize(text: str) -> str:
    text = re.sub(r'[\\/?%*:|"<>]', "_", text or "")
    return text.strip().replace(" ", "_")[:100] or "untitled"


def _prompt(fighter: str, opponent: str, year: int | None) -> str:
    """The processing prompt with match context filled in.

    ``prompt.txt`` doubles its literal JSON braces (``{{``) so str.format leaves them
    intact while substituting the {fighter}/{opponent}/{year} placeholders."""
    tmpl = _PROMPT_PATH.read_text(encoding="utf-8")
    return tmpl.format(fighter=fighter or "unknown", opponent=opponent or "unknown",
                       year=year if year is not None else "unknown")


def write_harvest_file(
    transcript: str,
    *,
    fighter: str,
    opponent: str,
    year: int | None,
    title: str | None = None,
    url: str | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Write one ``<ts>_<A>_vs_<B>_<year>.harvest.md`` (transcript + appended prompt)."""
    out = out_dir or HARVEST_INBOX
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = _sanitize(f"{fighter}_vs_{opponent}_{year if year is not None else 'NA'}")
    path = out / f"{ts}_{name}.harvest.md"

    header = (
        f"# BJJ MATCH TRANSCRIPT — FOR LLM PROCESSING\n"
        f"# Match: {fighter or 'unknown'} vs {opponent or 'unknown'}"
        f" ({year if year is not None else 'unknown'})\n"
        f"# Title: {title or '—'}\n"
        f"# URL: {url or '—'}\n"
        f"#\n"
        f"# Paste this whole file into ChatGPT / Copilot / Deepseek and follow the\n"
        f"# PROCESSING INSTRUCTIONS at the bottom. Return ONLY the JSON object.\n"
    )
    body = (
        f"{header}\n"
        f"===== TRANSCRIPT =====\n"
        f"{transcript}\n\n"
        f"{_prompt(fighter, opponent, year)}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def harvest_url(
    url: str,
    *,
    fighter: str | None = None,
    opponent: str | None = None,
    year: int | None = None,
    languages: tuple[str, ...] = ("en",),
    out_dir: Path | None = None,
) -> HarvestResult:
    """Resolve one input (video URL/id, search URL, or 'Name vs Name' query), fetch its
    transcript + metadata, and write the harvest file.

    Missing fighter/opponent/year are parsed from the title (or the search query); the
    prompt tells the LLM to confirm them from the transcript."""
    vid, query = resolve_to_video_id(url)
    if not vid:
        logger.warning("No video found for %r", url)
        return HarvestResult(url, "no_video", detail="no video id / search hit")

    transcript = get_transcript(vid, languages=languages)
    if not transcript:
        logger.warning("No transcript for %s (%r)", vid, url)
        return HarvestResult(url, "no_transcript", video_id=vid,
                             detail="no captions or YouTube rate-limited (429)")

    # Title for name/year parsing; fall back to the search query text.
    title = get_video_title(vid) or query
    if not fighter or not opponent:
        pf, po = parse_fighters(title)
        fighter = fighter or pf or ""
        opponent = opponent or po or ""
    if year is None:
        year = guess_year(title)

    path = write_harvest_file(
        transcript, fighter=fighter, opponent=opponent, year=year,
        title=title, url=f"https://www.youtube.com/watch?v={vid}", out_dir=out_dir,
    )
    logger.info("Harvested %s -> %s", vid, path.name)
    return HarvestResult(url, "ok", video_id=vid, path=path)


def harvest_urls(
    urls: list[str],
    *,
    languages: tuple[str, ...] = ("en",),
    out_dir: Path | None = None,
) -> list[HarvestResult]:
    """Harvest many inputs (URLs, search URLs, or queries). One failure never aborts the batch."""
    results: list[HarvestResult] = []
    for u in urls:
        try:
            results.append(harvest_url(u, languages=languages, out_dir=out_dir))
        except Exception as exc:  # one bad input shouldn't kill the batch
            logger.warning("Harvest failed for %s: %s", u, exc)
            results.append(HarvestResult(u, "error", detail=str(exc)))
    return results


def harvest_playlist(
    playlist_url: str,
    *,
    languages: tuple[str, ...] = ("en",),
    out_dir: Path | None = None,
) -> list[HarvestResult]:
    """Harvest every video in a public playlist."""
    return harvest_urls(playlist_video_urls(playlist_url), languages=languages, out_dir=out_dir)
