"""Study backend — the whole Study data path, in Python.

This began as a local port of the public site's Study page, which called a Supabase edge
function (``youtube-transcript``) for metadata and grouped segments. That page and that
function are both gone: the site no longer has a Study, nothing called the function, and it
was deleted rather than left deployed. This module is now the only implementation, and it
does more than the edge function ever did — the edge function returned no concept nodes, so
the public map it fed was always empty.

  - metadata via oEmbed + timed captions via ``youtube-transcript-api`` (keyless),
  - segment grouping (originally ported from the edge function's ``grouping.ts``),
  - TF-IDF grounding over the local technique library (port of ``site/study-rag.js``)
    → neighbour concept nodes/relationships/quality so the study renders a real map.

Personal use only — binds to the local dashboard, no cloud, no API keys.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import time
from html import escape
from pathlib import Path
from typing import Any

import requests

from export.study_knowledge import (
    load_technique_library,
    reshape_to_study_knowledge,
)
from harvest.transcripts import extract_video_id

TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9_'-]*")
_OEMBED = "https://www.youtube.com/oembed"

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
TECHNIQUE_LIBRARY_PATH = _PROJECT_ROOT / "data" / "processed" / "technique_library.json"
REPORTS_DIR = _PROJECT_ROOT / "data" / "study" / "reports"


class StudyError(Exception):
    """Friendly user-facing failure; study.js shows ``error.message`` verbatim."""


def _report_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-_")
    return slug[:80] or "video"


def _report_html(payload: dict[str, Any]) -> str:
    video = payload["video"]
    title = escape(str(video.get("title") or "Study report"))
    channel = escape(str(video.get("channel") or "YouTube"))
    overview = escape(str((payload.get("summary") or {}).get("overview") or ""))
    segments = payload.get("segments") or []
    snippets = payload.get("snippets") or []
    nodes = payload.get("nodes") or []
    relationships = payload.get("relationships") or []
    segment_html = "".join(
        f"<article><h2>{escape(str(s.get('id') or 'Segment'))}</h2>"
        f"<p class='time'>{float(s.get('start') or 0):.1f}s–{float(s.get('end') or 0):.1f}s</p>"
        f"<p>{escape(str(s.get('text') or ''))}</p></article>"
        for s in segments
    )
    transcript_html = "".join(
        f"<li><a href='{escape(str(video.get('url') or ''), quote=True)}"
        f"&t={int(float(s.get('start') or 0))}s'>"
        f"{float(s.get('start') or 0):.1f}s</a> {escape(str(s.get('text') or ''))}</li>"
        for s in snippets
    )
    node_html = "".join(
        f"<li><strong>{escape(str(n.get('label') or n.get('id') or ''))}</strong>"
        f" <span>{escape(str(n.get('kind') or n.get('type') or 'concept'))}</span></li>"
        for n in nodes
    )
    data = escape(json.dumps(payload, ensure_ascii=False), quote=False)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — GrapplingArc Study</title><style>
:root{{color-scheme:dark;--bg:#0b0b0e;--panel:#15151a;--line:#2d2d35;
--ink:#f4f4f7;--muted:#a2a2ad;--blue:#7ea8ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.55 system-ui,sans-serif}}
main{{max-width:900px;margin:0 auto;padding:48px 24px}}
h1{{font-size:clamp(30px,6vw,56px);line-height:1.05;margin:0 0 12px}}
h2{{font-size:20px;margin:0 0 8px}}
h3{{font-size:15px;margin:28px 0 10px;color:var(--blue)}}
p{{margin:8px 0;color:var(--muted)}}
.meta{{font-family:monospace;font-size:12px}}
article{{border-top:1px solid var(--line);padding:20px 0}}
.time,a{{color:var(--blue)}}
ul{{padding-left:22px;color:var(--muted)}}li{{margin:7px 0}}
section{{margin-top:42px}}.raw{{display:none}}
</style></head><body><main><p class="meta">GRAPPLINGARC / LOCAL STUDY REPORT</p><h1>{title}</h1>
<p>{channel} · {len(segments)} segments · {len(snippets)} captions · {len(nodes)} concepts</p>
<p>{overview}</p>
<section><h3>Segments</h3>{segment_html or '<p>No segments.</p>'}</section>
<section><h3>Concepts</h3><ul>{node_html or '<li>No concepts resolved.</li>'}</ul></section>
<section><h3>Transcript</h3><ul>{transcript_html or '<li>No transcript.</li>'}</ul></section>
<section><h3>Relationships</h3><p>{len(relationships)} concept relationships</p></section>
<script type="application/json" class="raw">{data}</script></main></body></html>"""


def save_report(payload: dict[str, Any], reports_dir: Path = REPORTS_DIR) -> dict[str, str]:
    """Persist one completed local study payload as JSON + standalone HTML."""
    video = payload.get("video")
    if not isinstance(video, dict):
        raise ValueError("payload.video must be an object")
    video_id = str(video.get("id") or "").strip()
    if not video_id:
        raise ValueError("payload.video.id is required")
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_id = f"{_report_slug(video_id)}-{time.time_ns()}"
    json_name, html_name = f"{report_id}.json", f"{report_id}.html"
    json_path, html_path = reports_dir / json_name, reports_dir / html_name
    json_tmp, html_tmp = reports_dir / f".{json_name}.tmp", reports_dir / f".{html_name}.tmp"
    json_tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_tmp.write_text(_report_html(payload), encoding="utf-8")
    json_tmp.replace(json_path)
    html_tmp.replace(html_path)
    return {"id": report_id, "json_name": json_name, "html_name": html_name,
            "title": str(video.get("title") or "Study report"), "video_id": video_id}


def list_reports(reports_dir: Path = REPORTS_DIR) -> list[dict[str, str]]:
    """Return valid saved reports, newest first."""
    reports = []
    for path in reports_dir.glob("*.json") if reports_dir.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            video = payload["video"]
            report_id = path.stem
            html_path = reports_dir / f"{report_id}.html"
            if not video.get("id") or not html_path.is_file():
                continue
            reports.append({"id": report_id, "json_name": path.name, "html_name": html_path.name,
                            "title": str(video.get("title") or "Study report"),
                            "video_id": str(video["id"]), "created": str(path.stat().st_mtime_ns)})
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return sorted(reports, key=lambda r: int(r["created"]), reverse=True)


# ── transcript path ───────────────────────────────────────────────────────────


def fetch_transcript(
    url: str, languages: list[str] | tuple[str, ...] = ("en", "pt-BR", "pt")
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(metadata, timed_captions) for a YouTube URL — keyless oEmbed + captions.

    Raises StudyError with a message the UI can show directly for bad URLs,
    missing captions, or network failure."""
    vid = extract_video_id(url)
    if not vid:
        raise StudyError("Paste a valid YouTube URL to continue.")

    try:
        resp = requests.get(
            _OEMBED,
            params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
            timeout=15,
        )
        if not resp.ok:
            raise StudyError("Video metadata unavailable — check the link.")
        meta = resp.json()
    except requests.RequestException as exc:
        raise StudyError(f"Could not reach YouTube metadata: {exc}") from exc

    metadata = {
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": str(meta.get("title") or "YouTube video"),
        "channel": str(meta.get("author_name") or "YouTube"),
        "thumbnail": str(meta.get("thumbnail_url") or ""),
        "duration": None,
    }

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        chunks = YouTubeTranscriptApi.get_transcript(vid, languages=list(languages))
    except ImportError as exc:
        raise StudyError("youtube-transcript-api not installed (run uv sync)") from exc
    except Exception as exc:
        if "429" in str(exc) or "too many requests" in str(exc).lower():
            fallback = _fetch_transcript_with_ytdlp(vid, languages)
            if fallback:
                return metadata, fallback
            raise StudyError(
                "YouTube blocked the captions request (429). "
                "yt-dlp fallback also found no accessible captions; wait and retry."
            ) from exc
        # Include the actual error message (e.g., ParseError details) for debugging
        raise StudyError(f"Caption fetch failed: {exc}") from exc

    captions = [
        {"text": str(c.get("text", "")).strip(), "start": float(c.get("start", 0)),
         "duration": float(c.get("duration", 0))}
        for c in chunks
        if str(c.get("text", "")).strip()
    ]
    if not captions:
        raise StudyError("No captions found for this video.")

    return metadata, captions


def _fetch_transcript_with_ytdlp(
    video_id: str, languages: list[str] | tuple[str, ...]
) -> list[dict[str, Any]]:
    """Fallback caption fetch through the locally installed yt-dlp executable."""
    try:
        with tempfile.TemporaryDirectory(prefix="grapplingarc-study-") as tmp:
            output = str(Path(tmp) / "caption.%(ext)s")
            subprocess.run(
                [
                    "yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
                    "--sub-langs", ",".join(languages), "--sub-format", "vtt",
                    "--output", output, f"https://www.youtube.com/watch?v={video_id}",
                ],
                capture_output=True, text=True, timeout=45, check=False,
            )
            files = sorted(Path(tmp).glob("caption*.vtt"))
            if not files:
                return []
            return _parse_vtt(files[0].read_text(encoding="utf-8", errors="replace"))
    except (OSError, subprocess.TimeoutExpired):
        return []


def _parse_vtt(text: str) -> list[dict[str, Any]]:
    captions = []
    pattern = re.compile(
        r"(?m)^(\d{2}:\d{2}(?::\d{2})?\.\d{3})\s+-->\s+"
        r"(\d{2}:\d{2}(?::\d{2})?\.\d{3})\s*\n(.+?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )

    def seconds(value: str) -> float:
        parts = value.split(":")
        if len(parts) == 2:
            minutes, rest = parts
            return float(minutes) * 60 + float(rest)
        hours, minutes, rest = parts
        return float(hours) * 3600 + float(minutes) * 60 + float(rest)

    for start, end, raw in pattern.findall(text):
        clean = re.sub(r"<[^>]+>|\{[^}]+\}", "", raw.replace("\n", " ")).strip()
        clean = re.sub(r"\s+", " ", clean)
        if clean and clean.lower() not in {c["text"].lower() for c in captions[-1:]}:
            captions.append({"text": clean, "start": seconds(start),
                             "duration": max(0.0, seconds(end) - seconds(start))})
    return captions


# ── segment grouping (port of grouping.ts) ────────────────────────────────────

_TARGET_CHARS = 1100
_MAX_SECONDS = 120


def group_captions(captions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coalesce caption lines into ~1-min study segments (``seg-N``).

    Originally a behavioral port of the deleted ``youtube-transcript`` edge function's
    ``grouping.ts``; that function is gone, so this is the definition now
    (TARGET_CHARS=1100, MAX_SECONDS=120)."""
    if not captions:
        return []

    segments: list[dict[str, Any]] = []
    current = {"ids": [], "text": [], "start": captions[0]["start"]}

    for i, cap in enumerate(captions):
        tentative = " ".join([*current["text"], cap["text"]])
        dur = cap["start"] + cap["duration"] - current["start"]
        exceed_char = len(tentative) > _TARGET_CHARS
        exceed_time = dur > _MAX_SECONDS

        if current["ids"] and (exceed_char or exceed_time):
            segments.append({
                "id": f"seg-{len(segments)}",
                "text": " ".join(current["text"]),
                "start": current["start"],
                "end": captions[i - 1]["start"] + captions[i - 1]["duration"],
                "captionIds": list(current["ids"]),
            })
            current = {"ids": [], "text": [], "start": cap["start"]}

        current["ids"].append(i)
        current["text"].append(cap["text"])

    if current["ids"]:
        last = captions[-1]
        segments.append({
            "id": f"seg-{len(segments)}",
            "text": " ".join(current["text"]),
            "start": current["start"],
            "end": last["start"] + last["duration"],
            "captionIds": list(current["ids"]),
        })

    return segments


# ── TF-IDF grounding (port of site/study-rag.js) ──────────────────────────────


class RagIndex:
    """TF-IDF retriever over study-knowledge records.

    Score math mirrors ``site/study-rag.js`` so the local concept map matches what the
    browser retriever would score against the same corpus."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records: list[dict[str, Any]] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_tf: list[dict[str, float]] = []
        self.idf: dict[str, float] = {}
        self.doc_norms: list[float] = []

        for i, rec in enumerate(records or []):
            norm = {
                "key": str(rec.get("key") or rec.get("id") or f"node-{i}"),
                "label": str(rec.get("label") or ""),
                "type": str(rec.get("type") or "concept"),
                "aliases": [str(a) for a in (rec.get("aliases") or [])],
                "description": str(rec.get("description") or ""),
            }
            self.records.append(norm)
            self.doc_tokens.append(
                self._tokens(" ".join([
                    norm["label"], norm["type"], norm["description"],
                    " ".join(norm["aliases"])]))
            )

        n = max(1, len(self.doc_tokens))
        df: dict[str, int] = {}
        for tokens in self.doc_tokens:
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1
        for term, count in df.items():
            self.idf[term] = math.log((n + 1) / (count + 1)) + 1.0

        for tokens in self.doc_tokens:
            tf: dict[str, float] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            self.doc_tf.append(tf)
            norm = math.sqrt(sum(
                (val * self.idf.get(term, 1.0)) ** 2 for term, val in tf.items()
            )) or 1.0
            self.doc_norms.append(norm)

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return TOKEN_RE.findall((text or "").lower())

    def search(
        self, query: str, limit: int = 6, min_score: float = 0.05
    ) -> list[dict[str, Any]]:
        """Top-K concept matches (TF-IDF + alias/label boost)."""
        q_tokens = self._tokens(query)
        if not q_tokens:
            return []
        q_tf: dict[str, int] = {}
        for token in q_tokens:
            q_tf[token] = q_tf.get(token, 0) + 1
        q_norm = math.sqrt(
            sum((val * self.idf.get(term, 1.0)) ** 2 for term, val in q_tf.items())
        )
        if q_norm == 0:
            return []
        normalized = " ".join(q_tokens)

        scored: list[tuple[float, dict[str, Any]]] = []
        for idx, rec in enumerate(self.records):
            d_tf = self.doc_tf[idx]
            dot = sum(
                (q_val * self.idf.get(term, 1.0)) * (d_tf[term] * self.idf.get(term, 1.0))
                for term, q_val in q_tf.items() if term in d_tf
            )
            score = dot / (q_norm * self.doc_norms[idx])

            label_norm = " ".join(self._tokens(rec["label"]))
            if label_norm and label_norm in normalized:
                score += 0.65
            for alias in rec["aliases"]:
                alias_norm = " ".join(self._tokens(alias))
                if alias_norm and alias_norm in normalized:
                    score += 0.45
                    break

            if score >= min_score:
                scored.append((score, rec))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "nodeKey": rec["key"],
                "label": rec["label"],
                "type": rec["type"],
                "score": round(score, 4),
                "match": "tfidf+alias",
            }
            for score, rec in scored[:limit]
        ]


def load_knowledge(path: Path = TECHNIQUE_LIBRARY_PATH) -> list[dict[str, Any]]:
    """Study-knowledge records for grounding; [] if the library is missing."""
    if not path.exists():
        return []
    try:
        return reshape_to_study_knowledge(load_technique_library(path))
    except Exception:
        return []


# ── study synthesis ───────────────────────────────────────────────────────────


def build_analysis(
    url: str,
    languages: list[str] | tuple[str, ...] = ("en", "pt-BR", "pt"),
    knowledge: list[dict[str, Any]] | None = None,
    max_rels: int = 28,
) -> dict[str, Any]:
    """Full study payload matching study.js expectations — video, snippets, segments,
    concept nodes/relationships, quality, summary.

    Pass ``knowledge`` to reuse a cached index (server-side); otherwise it is loaded
    from the technique library once per call."""
    metadata, captions = fetch_transcript(url, languages)
    segments = group_captions(captions)
    knowledge = load_knowledge() if knowledge is None else knowledge
    rag = RagIndex(knowledge)

    nodes: dict[str, dict[str, Any]] = {}
    seg_hits: list[list[str]] = []

    for seg in segments:
        hits = rag.search(seg["text"], limit=6)
        seg_hits.append([h["nodeKey"] for h in hits])
        for h in hits:
            nodes.setdefault(h["nodeKey"], {
                "id": h["nodeKey"],
                "label": h["label"],
                "kind": h["type"],
                "source": "ontology",
            })

    # Concept flow: connect concepts that appear in adjacent segments, forward only.
    rels: list[tuple[str, str]] = []
    for i in range(len(seg_hits) - 1):
        for key_a in seg_hits[i][:2]:
            for key_b in seg_hits[i + 1][:2]:
                if key_a != key_b:
                    rels.append((key_a, key_b))
    pairs = {tuple(sorted(r)) for r in rels}
    relationships = [{"source": s, "target": t} for s, t in sorted(pairs)[:max_rels]]

    warns: list[str] = []
    if not knowledge:
        warns.append("No technique library loaded — run export.tech_library to ground concepts.")

    return {
        "video": metadata,
        "language": "English",
        "languageCode": "en",
        "generated": False,
        "snippets": [
            {"id": f"cap-{i}", "text": c["text"], "start": c["start"]}
            for i, c in enumerate(captions)
        ],
        "segments": [
            {"id": s["id"], "text": s["text"], "start": s["start"], "end": s["end"]}
            for s in segments
        ],
        "nodes": list(nodes.values())[:18],
        "relationships": relationships,
        "quality": {
            "segments": len(segments),
            "resolvedNodes": len(nodes),
            "relationships": len(relationships),
            "warnings": warns,
        },
        "summary": {
            "overview": (
                f"{len(segments)} segments · {len(nodes)} techniques from "
                f"{metadata['channel']} — study the concept map alongside the transcript."
            )
        },
    }
