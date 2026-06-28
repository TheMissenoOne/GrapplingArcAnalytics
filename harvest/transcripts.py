"""YouTube transcript + metadata fetch (no LLM, no API key).

Transcripts via ``youtube-transcript-api``; video title via the keyless oEmbed
endpoint; playlists via the public RSS feed (``feedparser``). Fighter names / year
are parsed from the title (best-effort — the processing prompt tells the LLM to
correct them from the transcript)."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger(__name__)

_OEMBED = "https://www.youtube.com/oembed"
_PLAYLIST_FEED = "https://www.youtube.com/feeds/videos.xml"


def extract_video_id(url: str) -> str | None:
    """Pull the 11-char video id from any common YouTube URL form (or a bare id)."""
    if not url:
        return None
    url = url.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None
    if "youtube" in host:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        m = re.search(r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})", parsed.path)
        if m:
            return m.group(1)
    return None


def get_video_title(url_or_id: str) -> str | None:
    """Video title via the keyless oEmbed endpoint (also returns author)."""
    vid = extract_video_id(url_or_id)
    if not vid:
        return None
    try:
        r = requests.get(
            _OEMBED,
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=15,
        )
        if r.ok:
            return str(r.json().get("title") or "") or None
    except Exception as exc:  # network/Json issues are non-fatal — title is optional
        logger.warning("oEmbed title fetch failed for %s: %s", vid, exc)
    return None


def clean_transcript(text: str) -> str:
    """Collapse whitespace and drop bracketed cues ([Music], [Applause])."""
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_transcript(url_or_id: str, languages: tuple[str, ...] = ("en",)) -> str | None:
    """Full cleaned transcript text, or None if unavailable/disabled."""
    vid = extract_video_id(url_or_id)
    if not vid:
        logger.warning("Could not extract a video id from %r", url_or_id)
        return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.error("youtube-transcript-api not installed (uv sync)")
        return None
    try:
        chunks = YouTubeTranscriptApi.get_transcript(vid, languages=list(languages))
    except Exception as exc:  # TranscriptsDisabled/NoTranscriptFound/etc.
        logger.warning("No transcript for %s: %s", vid, exc)
        return None
    raw = " ".join(c.get("text", "") for c in chunks)
    return clean_transcript(raw) or None


_VS_PATTERNS = (
    r"([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+)+)\s+(?:vs\.?|VS|versus|x)\s+"
    r"([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+)+)",
    r"([A-Z][\w.'-]+)\s+(?:vs\.?|VS|versus)\s+([A-Z][\w.'-]+)",
)


def parse_fighters(title: str | None) -> tuple[str | None, str | None]:
    """Best-effort '<A> vs <B>' split from a video title (the LLM confirms later)."""
    if not title:
        return None, None
    for pat in _VS_PATTERNS:
        m = re.search(pat, title)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None


def guess_year(title: str | None) -> int | None:
    """First 19xx/20xx in the title, else None."""
    if not title:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", title)
    return int(m.group(0)) if m else None


_SEARCH = "https://www.youtube.com/results"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)


def search_video_id(query: str) -> str | None:
    """Top video id for a search query by scraping the results page (no API key)."""
    if not query:
        return None
    try:
        r = requests.get(
            _SEARCH, params={"search_query": query, "hl": "en", "gl": "US"},
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en"},
            cookies={"CONSENT": "YES+1"}, timeout=20,
        )
        if not r.ok:
            logger.warning("Search failed (%s) for %r", r.status_code, query)
            return None
        m = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', r.text)
        return m.group(1) if m else None
    except Exception as exc:
        logger.warning("Search error for %r: %s", query, exc)
        return None


def resolve_to_video_id(text: str) -> tuple[str | None, str | None]:
    """Turn any input → (video_id, query_used).

    Accepts a video URL/id (returns it directly), a ``/results?search_query=`` URL, or a
    bare ``"Name vs Name …"`` query (resolves to the top search hit). ``query_used`` is the
    search text when a search was run (handy as a title fallback for name parsing)."""
    text = (text or "").strip()
    vid = extract_video_id(text)
    if vid:
        return vid, None
    query = text
    if "search_query=" in text:
        qs = parse_qs(urlparse(text).query).get("search_query")
        if qs:
            query = qs[0]
    return search_video_id(query), query


def _playlist_id(url: str) -> str | None:
    qs = parse_qs(urlparse(url).query)
    return qs.get("list", [None])[0]


def playlist_video_urls(playlist_url: str) -> list[str]:
    """Video watch-URLs in a public playlist via its RSS feed (no API key)."""
    pid = _playlist_id(playlist_url)
    if not pid:
        logger.warning("No playlist id (list=...) in %s", playlist_url)
        return []
    try:
        import feedparser

        feed = feedparser.parse(f"{_PLAYLIST_FEED}?playlist_id={pid}")
    except Exception as exc:
        logger.warning("Playlist feed fetch failed for %s: %s", pid, exc)
        return []
    urls: list[str] = []
    for entry in getattr(feed, "entries", []):
        vid = getattr(entry, "yt_videoid", None) or extract_video_id(getattr(entry, "link", ""))
        if vid:
            urls.append(f"https://www.youtube.com/watch?v={vid}")
    return urls
