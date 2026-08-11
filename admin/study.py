"""Study backend — local port of the site Study page's data path.

The public site's Study (``site/study.html`` + study.js) talks to the Supabase edge
function ``youtube-transcript`` and renders whatever it returns. The edge function
returns metadata + grouped segments + snippets but NO concept nodes, so the public
concept map is always empty. This module rebuilds the same pipeline in Python so the
personal admin dashboard gets:

  - metadata via oEmbed + timed captions via ``youtube-transcript-api`` (keyless),
  - segment grouping ported from ``supabase/functions/youtube-transcript/grouping.ts``,
  - TF-IDF grounding over the local technique library (port of ``site/study-rag.js``)
    → neighbour concept nodes/relationships/quality so the study renders a real map.

Personal use only — binds to the local dashboard, no cloud, no API keys.
"""

from __future__ import annotations

import math
import re
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


class StudyError(Exception):
    """Friendly user-facing failure; study.js shows ``error.message`` verbatim."""


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

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        chunks = YouTubeTranscriptApi.get_transcript(vid, languages=list(languages))
    except ImportError as exc:
        raise StudyError("youtube-transcript-api not installed (run uv sync)") from exc
    except Exception as exc:
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

    metadata = {
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": str(meta.get("title") or "YouTube video"),
        "channel": str(meta.get("author_name") or "YouTube"),
        "thumbnail": str(meta.get("thumbnail_url") or ""),
        "duration": None,
    }
    return metadata, captions


# ── segment grouping (port of grouping.ts) ────────────────────────────────────

_TARGET_CHARS = 1100
_MAX_SECONDS = 120


def group_captions(captions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coalesce caption lines into ~1-min study segments (``seg-N``).

    Behavioral port of ``supabase/functions/youtube-transcript/grouping.ts``
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
