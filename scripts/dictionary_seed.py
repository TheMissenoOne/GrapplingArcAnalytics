#!/usr/bin/env python
"""Owner's "library with video + timestamp" audit: pull corpus events that resolve to a
curated technique, grab the frame at their own timestamp, ask Gemini which curated label it
sees, and keep the disagreements front and centre for a human.

    uv run python -m scripts.dictionary_seed plan --per-technique 3 --min-events 3 --max-calls 150
    uv run python -m scripts.dictionary_seed extract
    uv run python -m scripts.dictionary_seed ask
    uv run python -m scripts.dictionary_seed report

Four steps, each reading the previous step's file so a batch can be re-run from wherever it
stopped:

1. **plan** — read-only prod (`matches.status='final'`, `video_url is not null`). For every
   curated technique in `analysis/data/technique_library.json` (210 entries), find every
   sequence event whose `clean_label` resolves to that technique's canonical `en`, resolve its
   video-absolute timestamp via `ts_origin`, and sample up to `--per-technique` spread across
   distinct bouts (`select_candidates`). Writes `plan.jsonl` + `plan_counts.json` (every
   candidate count, before sampling — what `report` calls "video-backed events").
2. **extract** — group `plan.jsonl` by bout, download each video ONCE with yt-dlp
   (`--cookies-from-browser firefox`), seek a 9-frame `±30s` alignment window (640×360) plus
   the full-res (`1280×720`) centre + `±2s` neighbours with ffmpeg, delete the video.
   Resume-safe (skips a candidate whose frames already exist). Corpus `ts` is measured
   unreliable even after `plan`'s own reclassification (2026-09-15) — the window exists so
   stage A below can correct a still-wrong second instead of reading the wrong frame blind.
3. **ask** — TWO Gemini (`gemini-pro-latest`, temperature 0) calls per candidate. Stage A
   (NOT blind) shows the 9-frame window and asks which offset actually shows the corpus
   label; a non-default choice gets its full-res frame re-extracted on demand. Stage B is the
   original blind read (two athletes' names, closed curated vocabulary, three frames, no hint
   of the corpus label) on whichever frame stage A picked. `visible: no` at stage A skips
   stage B (`review_confidence: low`, reason `not_visible_in_window`). Writes `seed.jsonl` —
   every candidate, agreement graded, never dropped.
4. **report** — `REPORT.md`: per-technique agreement, zero-video-backed techniques, cost, top
   confusions (the dictionary's ambiguous pairs).

Privacy: public corpus only (`matches`/`athletes`), same class as `scripts/frame_pdf.py`.
Prod is read-only; this script writes nothing back to the DB.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.names import _normalize_name, canonicalize  # noqa: E402
from analysis.style_profile_core import _bout_slug  # noqa: E402
from analysis.technique_match import clean_label  # noqa: E402

logger = logging.getLogger("dictionary_seed")

TECH_LIB_PATH = REPO / "analysis" / "data" / "technique_library.json"
OUT_DIR = REPO / "data" / "finetune" / "audit" / "gemini_seed"
PLAN_PATH = OUT_DIR / "plan.jsonl"
PLAN_COUNTS_PATH = OUT_DIR / "plan_counts.json"
SEED_PATH = OUT_DIR / "seed.jsonl"
REPORT_PATH = OUT_DIR / "REPORT.md"
# One-time archive of the pre-realignment batch's own graded answers (single frame, corpus
# ts trusted as-is) + the misalignment diagnostic `plan` computes against it, both written
# ONLY the first time `plan` finds a previous seed.jsonl -- so `report` can show a real
# before/after even after several re-plans. Never overwritten once it exists.
BEFORE_SEED_PATH = OUT_DIR / "seed_before_alignment.jsonl"
ALIGNMENT_PATH = OUT_DIR / "ts_realignment.json"
# Sibling audit's own output (scripts/dictionary_audit.py), read-only -- its `entries[]`
# say which curated technique already has zero/thin HUMAN frames, so a capped batch here
# spends its calls on exactly the gap that audit found, not on whatever curated.json
# happens to list first. Never written by this module.
COVERAGE_PATH = REPO / "data" / "finetune" / "audit" / "coverage.json"

# "positions" (owner's word) -- a frame is IN these, the other five event types are things
# that HAPPEN (same state/action split vision_dataset.md derives from node_type). Sampled
# first within a bout because a held position is a cleaner single-frame read than an action
# that may straddle two samples.
STATE_TYPES = {"guard", "control"}

MODEL = "gemini-pro-latest"
# US$/M tokens, pro-tier, labelled as an estimate in the report (task brief's own figures).
GEMINI_PRICE_IN_PER_M = 1.25
GEMINI_PRICE_OUT_PER_M = 10.0


def load_curated() -> list[dict[str, Any]]:
    return list(json.loads(TECH_LIB_PATH.read_text(encoding="utf-8")))


def node_key_of(en: str) -> str:
    return canonicalize(_normalize_name(en))


def load_coverage(path: Path = COVERAGE_PATH) -> dict[str, dict[str, Any]] | None:
    """``en`` -> the dictionary audit's own coverage entry, or ``None`` when that audit
    hasn't run yet (a fresh checkout, or this module running first) -- callers fall back to
    curated order rather than crashing on a sibling artefact that may not exist."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(e["en"]): e for e in data.get("entries", []) if e.get("en")}


# zero: the audit found NO human-reviewed frame at all. thin: some, not enough. Both rank
# ahead of everything else; "corpus_events" (recorded by the audit itself) breaks the tie
# toward whichever technique actually has the most footage to spend a call on.
_BUCKET_RANK = {"zero": 0, "thin": 1}


def prioritize_curated(curated: list[dict[str, Any]],
                       coverage: dict[str, dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Curated techniques reordered so the audit's zero-human-frame bucket is planned FIRST
    (by corpus frequency), then thin, then the rest -- unchanged relative order inside each
    tier. A `--max-calls` cap downstream then spends its budget on the coverage gap instead
    of whatever position ``technique_library.json`` happens to list first. Identity when no
    ``coverage`` snapshot is given."""
    if not coverage:
        return curated

    def key(t: dict[str, Any]) -> tuple[int, int]:
        cov = coverage.get(str(t.get("en", "")))
        rank = _BUCKET_RANK.get(str(cov.get("bucket")), 2) if cov else 2
        events = int(cov.get("corpus_events") or 0) if cov else 0
        return (rank, -events)

    return sorted(curated, key=key)


# ── ts arithmetic ────────────────────────────────────────────────────────────────
def absolute_ts(video_start_seconds: float | None, ts_origin: str | None,
                ts: float | None) -> float | None:
    """Video-absolute second for one event, or ``None`` when it cannot be located.

    ``ts_origin`` is an evidence-based CLASS (see :func:`classify_ts_origin`), not the raw
    DB flag -- ``'video_absolute'``/``'absolute_from_zero'`` -> ``ts`` is already the answer
    (the latter is the zero-start case, arithmetically identical, kept as its own class only
    so callers can tell the two apart). ``'bout_relative'`` -> add the bout's own start.
    Anything else (``'unknown'``/``None``) -> ``None``, never a guessed default -- a
    bout-relative timestamp misread as absolute silently mislocates every frame in a
    DIFFERENT fight (AA-010, see ``scripts/frame_pdf.py`` module docstring -- and measured
    live in this corpus 2026-09-15: `matches.ts_origin='video_absolute'` on a bout whose
    events were actually bout-relative, landing every frame in the WNO tale-of-the-tape
    intro card instead of the bout).
    """
    if ts is None:
        return None
    if ts_origin in ("video_absolute", "absolute_from_zero"):
        return float(ts)
    if ts_origin == "bout_relative":
        return float(video_start_seconds or 0) + float(ts)
    return None


# Floor below which a `video_start_seconds` is treated as noise rather than a real offset --
# below it, "all events are less than start" is not distinguishing evidence (a 12s intro and
# a 12s event both round to "small"), so classification falls through to the zero-start branch
# instead of misreading a near-zero start as proof of bout-relative ts.
_START_EVIDENCE_FLOOR = 60.0


def classify_ts_origin(video_start_seconds: float | None, event_ts: list[float],
                       video_duration: float | None) -> str:
    """A match's ts semantics, decided from the EVENT VALUES rather than trusted from the
    DB's own `ts_origin` flag -- that flag is measured wrong on live data (see
    :func:`absolute_ts`'s docstring). Returns one of:

    - ``'bout_relative'``  -- every event ts is below `video_start_seconds` (which is itself
      a real offset, > 60s): the events count from the BOUT's own start, so the absolute
      second is `video_start_seconds + ts`.
    - ``'video_absolute'`` -- every event ts is at/after `video_start_seconds`: already
      counted from the FILE's start.
    - ``'absolute_from_zero'`` -- no usable `video_start_seconds` (NULL or 0, the segment
      IS the whole file), and every event ts fits inside the video's own duration: ts is
      already the file-absolute second.
    - ``'unknown'`` -- no events, a mixed match (some events above start, some below -- not
      explainable by either convention), or a zero-start match whose max ts exceeds the
      video's duration (the ts axis doesn't fit this video at all). Callers skip these and
      count them, rather than guess.
    """
    if not event_ts:
        return "unknown"
    if video_start_seconds is not None and video_start_seconds > _START_EVIDENCE_FLOOR:
        if all(t < video_start_seconds for t in event_ts):
            return "bout_relative"
        if all(t >= video_start_seconds for t in event_ts):
            return "video_absolute"
        return "unknown"
    if not video_start_seconds:  # None or 0 -- the download IS the whole file
        if video_duration is not None and max(event_ts) < video_duration:
            return "absolute_from_zero"
    return "unknown"


def ts_class_matches_flag(ts_origin_flag: str | None, ts_class: str) -> bool:
    """Did the DB's own (unreliable) `ts_origin` flag happen to agree with the
    evidence-based reclassification? Used only to COUNT how many previously-planned
    candidates were silently misaligned -- never to decide placement itself."""
    if ts_origin_flag == "video_absolute":
        return ts_class in ("video_absolute", "absolute_from_zero")
    if ts_origin_flag == "bout_relative":
        return ts_class == "bout_relative"
    return False  # NULL/unrecognised flag was never usable before this fix


# ── plan sampling ────────────────────────────────────────────────────────────────
def _is_state(type_: str) -> bool:
    return type_ in STATE_TYPES


def select_candidates(candidates: list[dict[str, Any]], per_technique: int) -> list[dict[str, Any]]:
    """Up to ``per_technique`` candidates spread across distinct bouts (one per bout per
    round-robin pass, in the order bouts first appear), state-type (guard/control) ahead of
    action types within each bout, ties broken by earliest ``ts``. Deterministic.
    """
    if per_technique <= 0:
        return []
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for c in candidates:
        mid = c["match_id"]
        if mid not in groups:
            groups[mid] = []
            order.append(mid)
        groups[mid].append(c)
    for g in groups.values():
        g.sort(key=lambda c: (not _is_state(str(c.get("type", ""))), c.get("ts") or 0.0))

    chosen: list[dict[str, Any]] = []
    idx = dict.fromkeys(order, 0)
    while len(chosen) < per_technique:
        progressed = False
        for mid in order:
            if len(chosen) >= per_technique:
                break
            i = idx[mid]
            if i < len(groups[mid]):
                chosen.append(groups[mid][i])
                idx[mid] = i + 1
                progressed = True
        if not progressed:
            break
    return chosen


@dataclass
class PlanRow:
    node_key: str
    label: str                 # curated canonical english label
    type: str
    match_id: str
    video_url: str
    ts: float                  # video-absolute seconds
    ts_ms: int
    actor: str | None
    bout: str                  # slug, matches export/match_breakdown.py's own
    a_name: str
    b_name: str
    year: int | None
    event: str | None
    ts_class: str               # classify_ts_origin's verdict for this candidate's match


def build_plan(matches: list[dict[str, Any]], curated: list[dict[str, Any]],
               per_technique: int = 6, min_events: int = 0, *,
               probe_duration_fn: Any = None,
               ) -> tuple[list[PlanRow], dict[str, int]]:
    """Prod match rows (as ``_load_matches`` shapes them) + the curated library -> sampled
    plan rows + per-technique candidate counts BEFORE sampling (what ``report`` calls
    "video-backed events" -- how many existed, not how many were picked).

    ts semantics are decided PER MATCH by :func:`classify_ts_origin` off the match's own raw
    event timestamps -- the DB's `ts_origin` flag is read nowhere here, because it is
    measured wrong on live rows (see `absolute_ts`'s docstring). ``probe_duration_fn``
    (``video_url -> seconds|None``) is only called, and only once per url (memoised), for a
    match whose `video_start_seconds` is NULL/0 AND that has at least one event resolving to
    a curated technique -- classification needs a duration there, and nothing else does, so
    every other match costs no network call. ``None`` (the default, and every existing
    caller/test before this) disables probing entirely; such matches simply classify
    ``'unknown'`` and contribute no candidate.
    """
    by_en: dict[str, list[dict[str, Any]]] = {t["en"]: [] for t in curated if t.get("en")}
    duration_cache: dict[str, float | None] = {}

    def _duration(url: str) -> float | None:
        if url not in duration_cache:
            duration_cache[url] = probe_duration_fn(url) if probe_duration_fn else None
        return duration_cache[url]

    for m in matches:
        raw_events = [ev for ev in (m.get("sequence") or []) if isinstance(ev, dict)]
        hits = [ev for ev in raw_events if ev.get("label") and
               clean_label(str(ev["label"]), str(ev.get("type", ""))) in by_en]
        if not hits:
            continue
        ts_list = [float(ev["ts"]) for ev in raw_events if ev.get("ts") is not None]
        video_start = m.get("video_start_seconds")
        duration = _duration(m["video_url"]) if not video_start else None
        klass = classify_ts_origin(video_start, ts_list, duration)
        if klass == "unknown":
            continue
        bout = _bout_slug(m["a_name"], m["b_name"], m["year"])
        for ev in hits:
            cleaned = clean_label(str(ev["label"]), str(ev.get("type", "")))
            ts = absolute_ts(video_start, klass, ev.get("ts"))
            if ts is None:
                continue
            by_en[cleaned].append({
                "match_id": m["match_id"], "video_url": m["video_url"], "ts": ts,
                "type": str(ev.get("type", "")), "actor": ev.get("actor"), "bout": bout,
                "a_name": m["a_name"], "b_name": m["b_name"],
                "year": m.get("year"), "event": m.get("event"), "ts_class": klass,
            })

    counts = {en: len(v) for en, v in by_en.items()}
    plan: list[PlanRow] = []
    for tech in curated:
        en = tech.get("en", "")
        if not en or counts.get(en, 0) < min_events:
            continue
        node_key = node_key_of(en)
        for c in select_candidates(by_en[en], per_technique):
            plan.append(PlanRow(
                node_key=node_key, label=en, type=str(tech.get("type", "")),
                match_id=c["match_id"], video_url=c["video_url"], ts=c["ts"],
                ts_ms=int(round(c["ts"] * 1000)), actor=c.get("actor"), bout=c["bout"],
                a_name=c["a_name"], b_name=c["b_name"], year=c.get("year"), event=c.get("event"),
                ts_class=c["ts_class"],
            ))
    return plan, counts


def cap_plan(plan: list[PlanRow], max_calls: int | None) -> list[PlanRow]:
    """Cap the TOTAL candidate count at ``max_calls`` by dropping whole techniques from the
    tail rather than truncating one mid-technique -- a partial technique block would make
    ``report``'s per-technique numbers look like a real sample when it is an artefact of
    where the cap landed."""
    if max_calls is None:
        return plan
    order: list[str] = []
    by_key: dict[str, list[PlanRow]] = {}
    for row in plan:
        if row.node_key not in by_key:
            by_key[row.node_key] = []
            order.append(row.node_key)
        by_key[row.node_key].append(row)
    out: list[PlanRow] = []
    budget = max_calls
    for key in order:
        group = by_key[key]
        if len(group) > budget:
            break
        out.extend(group)
        budget -= len(group)
    return out


def _load_matches() -> list[dict[str, Any]]:
    """Read-only prod: every final bout carrying a video link and a sequence."""
    from sqlalchemy import text
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    from db.base import get_engine

    with get_engine().connect() as c:
        rows = c.execute(text("""
            select m.id::text, m.video_url, m.video_start_seconds, m.ts_origin,
                   m.event, m.year, a.name, b.name, m.sequence
              from matches m
              join athletes a on a.id = m.athlete_a_id
              join athletes b on b.id = m.athlete_b_id
             where m.status = 'final' and m.video_url is not null and m.sequence is not null
        """)).fetchall()
    return [
        {"match_id": r[0], "video_url": r[1], "video_start_seconds": r[2], "ts_origin": r[3],
         "event": r[4], "year": r[5], "a_name": r[6], "b_name": r[7], "sequence": r[8]}
        for r in rows
    ]


def probe_duration(video_url: str) -> float | None:
    """yt-dlp `--dump-json --skip-download` -- no download, just the file's own duration, for
    :func:`classify_ts_origin`'s zero-start branch. Best-effort: a probe failure (geo-block,
    removed video) returns ``None`` and that match classifies ``'unknown'`` rather than
    crashing a `plan` run over one bad url."""
    from scripts.frame_pdf import probe

    try:
        dur = probe(video_url).get("duration")
        return float(dur) if dur else None
    except Exception as exc:
        logger.warning("duration probe failed for %s: %s", video_url, exc)
        return None


# ── extract ──────────────────────────────────────────────────────────────────────
# Corpus ts is unreliable even after `classify_ts_origin` (AA-010-style: the classification
# is per-MATCH evidence, a single mis-logged event ts inside an otherwise-good match still
# slips through) -- so extraction never trusts one frame. It samples a WINDOW around the
# planned second and lets stage A (ask_alignment) pick which offset actually shows the
# label. Asymmetric-looking but not: 5/10/20/30 doubles each step, covering a wide net
# (+/-30s) without linearly multiplying the frame count the way a fixed step would.
WINDOW_OFFSETS: tuple[int, ...] = (-30, -20, -10, -5, 0, 5, 10, 20, 30)
STRIP_W, STRIP_H = 640, 360     # cheap -- stage A only has to tell WHICH frame, not read it
HIRES_W, HIRES_H = 1280, 720    # stage B's actual blind read


def _offset_tag(offset: int) -> str:
    return f"p{offset}" if offset >= 0 else f"m{-offset}"


def strip_frame_path(node_key: str, bout: str, ts_ms: int, offset: int) -> Path:
    return OUT_DIR / node_key / f"{bout}__{ts_ms}__strip_{_offset_tag(offset)}.jpg"


def chosen_frame_paths(node_key: str, bout: str, ts_ms: int, offset: int) -> dict[str, Path]:
    """Full-res centre + its OWN ±2s neighbours, named with the offset stage A picked --
    resume-safe by construction (a re-run that picks the same offset again finds the same
    files; a different offset gets its own, never overwriting the first)."""
    d = OUT_DIR / node_key
    base = f"{bout}__{ts_ms}__chosen_{_offset_tag(offset)}"
    return {"center": d / f"{base}.jpg", "m2": d / f"{base}_m2.jpg", "p2": d / f"{base}_p2.jpg"}


def _ffmpeg_frame(video: Path, local_ts: float, out: Path,
                  width: int = HIRES_W, height: int = HIRES_H) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{max(local_ts, 0.0):.3f}",
         "-i", str(video), "-frames:v", "1", "-vf", f"scale={width}:{height}",
         "-q:v", "3", str(out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning("ffmpeg failed at t=%.2f for %s: %s", local_ts, out,
                       (r.stderr or r.stdout).strip()[-300:])
        return False
    return True


def extract_match(rows: list[dict[str, Any]], tmp: Path) -> int:
    """One bout's own candidates -> frames on disk. Downloads the video ONCE (spanning every
    candidate's full +/-30s window, padded), seeks per candidate/offset, deletes the video
    after -- frames are the durable artefact here, the video is not (same archive policy as
    ``docs/frame_pdf_reading.md``). Per candidate: the 9-frame low-res alignment strip, plus
    the full-res centre (offset 0) + its ±2s neighbours -- the common case where the corpus
    ts is already right needs no second download later. Returns how many candidates have
    their offset-0 full-res centre frame on disk after this call (already-there ones count,
    for resumability)."""
    from scripts.frame_pdf import QUALITY, fetch

    video_url = rows[0]["video_url"]
    ts_list = [float(r["ts"]) for r in rows]
    pad = max(WINDOW_OFFSETS) + 5.0   # covers every window offset plus a seek safety margin
    start = max(0.0, min(ts_list) - pad)
    end = max(ts_list) + pad
    video = fetch(video_url, tmp, start, end, fmt=QUALITY["full_match"][1])
    written = 0
    try:
        for r in rows:
            node_key, bout, ts_ms = str(r["node_key"]), str(r["bout"]), int(r["ts_ms"])
            local_center = float(r["ts"]) - start
            for offset in WINDOW_OFFSETS:
                strip = strip_frame_path(node_key, bout, ts_ms, offset)
                if not strip.exists():
                    _ffmpeg_frame(video, max(local_center + offset, 0.0), strip,
                                 STRIP_W, STRIP_H)
            chosen = chosen_frame_paths(node_key, bout, ts_ms, 0)
            if chosen["center"].exists():
                written += 1
                continue
            if _ffmpeg_frame(video, local_center, chosen["center"]):
                written += 1
            _ffmpeg_frame(video, max(local_center - 2, 0.0), chosen["m2"])
            _ffmpeg_frame(video, local_center + 2, chosen["p2"])
    finally:
        video.unlink(missing_ok=True)
    return written


def ensure_chosen_frames(rows_needing: list[dict[str, Any]]) -> None:
    """Stage A picked a NON-default offset for these candidates -- the full-res centre +
    ±2s neighbours at that offset never got extracted during `extract` (only offset 0 did),
    so they're "re-extracted at that offset" here: one short re-download per match (the
    handful of chosen offsets are always within the original +/-30s window, so a tight span
    suffices), then the usual ffmpeg seeks. Best-effort per match -- a failed re-download
    leaves those candidates' chosen frames missing, and stage B treats a missing frame the
    same as "no answer" rather than crashing the batch."""
    from scripts.frame_pdf import QUALITY, fetch

    by_match: dict[str, list[dict[str, Any]]] = {}
    for r in rows_needing:
        by_match.setdefault(str(r["match_id"]), []).append(r)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, (mid, rows) in enumerate(by_match.items(), 1):
            video_url = rows[0]["video_url"]
            targets = [float(r["ts"]) + int(r["chosen_offset"]) for r in rows]
            start = max(0.0, min(targets) - 5.0)
            end = max(targets) + 5.0
            logger.info("[stage B re-extract %d/%d] match %s (%d frames)",
                       i, len(by_match), mid, len(rows))
            try:
                video = fetch(video_url, tmp, start, end, fmt=QUALITY["full_match"][1])
            except Exception as exc:
                logger.warning("stage B re-download failed for match %s: %s", mid, exc)
                continue
            for r in rows:
                target = float(r["ts"]) + int(r["chosen_offset"])
                local = target - start
                paths = chosen_frame_paths(str(r["node_key"]), str(r["bout"]), int(r["ts_ms"]),
                                           int(r["chosen_offset"]))
                _ffmpeg_frame(video, local, paths["center"])
                _ffmpeg_frame(video, max(local - 2, 0.0), paths["m2"])
                _ffmpeg_frame(video, local + 2, paths["p2"])
            video.unlink(missing_ok=True)
            for p in tmp.iterdir():
                if p.is_file():
                    p.unlink()


def run_extract(plan: list[dict[str, Any]]) -> dict[str, int]:
    by_match: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in plan:
        mid = str(row["match_id"])
        if mid not in by_match:
            by_match[mid] = []
            order.append(mid)
        by_match[mid].append(row)

    stats = {"matches": 0, "matches_failed": 0, "frames": 0}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, mid in enumerate(order, 1):
            rows = by_match[mid]
            logger.info("[%d/%d] match %s (%d candidates)", i, len(order), mid, len(rows))
            try:
                stats["frames"] += extract_match(rows, tmp)
                stats["matches"] += 1
            except Exception as exc:  # yt-dlp 404 / geo-block / no format -- skip, record, move on
                logger.warning("skip match %s: %s", mid, exc)
                stats["matches_failed"] += 1
            for p in tmp.iterdir():
                if p.is_file():
                    p.unlink()
    return stats


# ── ask ──────────────────────────────────────────────────────────────────────────
def vocabulary_text(curated: list[dict[str, Any]]) -> str:
    """Closed vocabulary block for the Gemini prompt -- same "Allowed labels" framing
    ``scripts/frame_pdf.py:draw_library_pages`` prints on a sheet PDF, built here from the
    CURATED 210 (``analysis/data/technique_library.json``) since that is the set this audit
    is reviewing, not the DB's larger ``technique_nodes`` dump."""
    groups: dict[str, list[str]] = {}
    for t in curated:
        en = t.get("en")
        if not en:
            continue
        groups.setdefault(str(t.get("type") or "other"), []).append(str(en))
    lines = ["# Allowed labels — use one of these, verbatim", ""]
    for g in sorted(groups):
        lines.append(f"## {g}")
        lines += [f"- {n}" for n in sorted(groups[g])]
        lines.append("")
    return "\n".join(lines)


def build_prompt(row: dict[str, Any], vocab_text: str) -> str:
    return (
        f"Two athletes are grappling: {row['a_name']} and {row['b_name']}. Kit is unknown "
        "(gi or no-gi -- do not guess it). You are shown three frames from one bout: the "
        "CENTRE frame (the moment in question), one frame ~2s BEFORE it and one ~2s AFTER "
        "it, given only for context -- answer about the CENTRE frame only.\n\n"
        f"{vocab_text}\n"
        "Which curated position/technique above best describes what is happening at the "
        "CENTRE frame? Answer ONLY this JSON object, no other text:\n"
        '{"label": "<one label from the list above, verbatim>", '
        '"type": "<its type>", '
        '"actor_role": "top|bottom|neutral|unclear", '
        '"confidence": "high|medium|low", '
        '"alternative": "<another label from the list, or null>", '
        '"reason": "<one sentence>"}'
    )


def build_alignment_prompt(row: dict[str, Any]) -> str:
    offsets = ", ".join(str(o) for o in WINDOW_OFFSETS)
    return (
        f"Two athletes are grappling: {row['a_name']} and {row['b_name']}. You are shown "
        f"{len(WINDOW_OFFSETS)} frames from one bout, in order, sampled at these offsets "
        f"(seconds, relative to a reference timestamp a database logged): {offsets}.\n\n"
        f"The database says \"{row['label']}\" happens near this reference timestamp, but "
        "the exact second may be off (a data alignment bug, not your judgement to fix by "
        "guessing) -- your only job is to say which of the shown frames actually shows it.\n\n"
        f"Which frame (by its offset) BEST shows \"{row['label']}\"? If none of them do, say "
        "so. Answer ONLY this JSON object, no other text:\n"
        '{"offset": <one of the offsets above, as a number>, '
        '"visible": "yes"|"no", "reason": "<one sentence>"}'
    )


def resolve_offset(parsed: dict[str, Any]) -> int:
    """Stage A's chosen offset, snapped to the nearest member of ``WINDOW_OFFSETS`` -- a
    model answering "12" for a 10s slot is a rounding slip, not a different frame. An
    unparsable/missing answer defaults to 0 (the frame the corpus itself pointed at)."""
    val = parsed.get("offset")
    if val is None:
        return 0
    try:
        raw = float(val)
    except (TypeError, ValueError):
        return 0
    return min(WINDOW_OFFSETS, key=lambda o: abs(o - raw))


def score_agreement(node_key: str, model_answer: dict[str, Any]) -> tuple[str, str]:
    """``(agree, review_confidence)``. ``agree`` in ``full|partial|no``; ``review_confidence``
    is ``high`` only on full agreement -- disagreement is exactly what the human review
    queue exists to see, so it is never dropped, only marked ``low``."""
    model_label = str(model_answer.get("label") or "")
    if model_label and node_key_of(clean_label(model_label)) == node_key:
        return "full", "high"
    alt = str(model_answer.get("alternative") or "")
    if alt and node_key_of(clean_label(alt)) == node_key:
        return "partial", "low"
    return "no", "low"


_EMPTY_USAGE = {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}


def _sum_usage(*usages: dict[str, Any]) -> dict[str, int]:
    out = dict(_EMPTY_USAGE)
    for u in usages:
        for k in out:
            out[k] += int((u or {}).get(k, 0))
    return out


def _parse_json_answer(text: str | None) -> dict[str, Any]:
    """``resp.text`` -> a dict, unwrapping the one-element-array shape Gemini occasionally
    returns despite ``response_mime_type=application/json`` guaranteeing valid JSON, not an
    OBJECT (measured). Anything else that isn't a dict -> "no answer", never a crash."""
    try:
        parsed = json.loads(text) if text else {}
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    return parsed if isinstance(parsed, dict) else {}


def ask_alignment(row: dict[str, Any], client: Any, model: str = MODEL) -> dict[str, Any]:
    """Stage A -- NOT blind: shows the 9-frame low-res strip with its offsets and asks which
    one shows the corpus label, to correct for a possibly wrong corpus ts before the real
    (blind) read."""
    from google.genai import errors, types

    from scripts.gemini_read_frames import build_generate_config, usage_totals

    node_key, bout, ts_ms = str(row["node_key"]), str(row["bout"]), int(row["ts_ms"])
    parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg")
            for o in WINDOW_OFFSETS
            for p in [strip_frame_path(node_key, bout, ts_ms, o)] if p.exists()]
    parts.append(types.Part.from_text(text=build_alignment_prompt(row)))

    try:
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config("high", temperature=0))
    except errors.ClientError:
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config(None, temperature=0))
    return {"parsed": _parse_json_answer(resp.text), "usage": usage_totals(resp.usage_metadata)}


def _safe_call(fn: Any, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
    """Run one Gemini call; on ANY exception (quota 429, network blip, malformed response)
    return an empty-but-correctly-shaped result plus the error -- never crash the batch. A
    day's quota exhausting mid-run must not throw away every candidate already graded, since
    `run_ask` only writes `seed.jsonl` once, at the very end, and a bare exception here used
    to propagate straight out of `main()` (measured live, 2026-09-15: a 429 on candidate
    31/91 lost the other 30 answers with it)."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
        return {"parsed": {}, "usage": dict(_EMPTY_USAGE)}, f"{type(exc).__name__}: {exc}"[:200]


def ask_gemini(row: dict[str, Any], vocab_text: str, client: Any,
              model: str = MODEL) -> dict[str, Any]:
    """Stage B -- the blind read, on the frame stage A chose (``row['chosen_offset']``,
    default 0 -- the corpus's own second, for a caller with no alignment stage at all)."""
    from google.genai import errors, types

    from scripts.gemini_read_frames import build_generate_config, usage_totals

    paths = chosen_frame_paths(str(row["node_key"]), str(row["bout"]), int(row["ts_ms"]),
                               int(row.get("chosen_offset", 0)))
    parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg")
            for p in (paths["m2"], paths["center"], paths["p2"]) if p.exists()]
    parts.append(types.Part.from_text(text=build_prompt(row, vocab_text)))

    try:
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config("high", temperature=0))
    except errors.ClientError:
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config(None, temperature=0))
    return {"parsed": _parse_json_answer(resp.text), "usage": usage_totals(resp.usage_metadata)}


def run_ask(plan: list[dict[str, Any]], *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Two stages, both ``gemini-pro-latest`` at ``temperature=0``: stage A (not blind) picks
    which frame in the +/-30s window actually shows the corpus label; stage B (blind, no
    corpus label given) reads that chosen frame the same way the single-frame pipeline
    always did. ``visible: no`` at stage A skips stage B entirely -- ``review_confidence:
    low``, reason ``not_visible_in_window``, never dropped."""
    curated = load_curated()
    vocab = vocabulary_text(curated)
    client = None
    if not dry_run:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    # stage A -- alignment
    stage_a: list[dict[str, Any]] = []
    for i, row in enumerate(plan, 1):
        logger.info("[stage A %d/%d] %s (%s)", i, len(plan), row["node_key"], row["bout"])
        node_key, bout, ts_ms = str(row["node_key"]), str(row["bout"]), int(row["ts_ms"])
        has_frames = any(strip_frame_path(node_key, bout, ts_ms, o).exists()
                         for o in WINDOW_OFFSETS)
        if not has_frames:
            # extraction failed for this candidate's match (yt-dlp/ffmpeg) -- nothing to
            # show the model, so don't spend a call on an empty prompt.
            stage_a.append({**row, "chosen_offset": 0, "visible": False,
                            "align_reason": "extraction_failed", "align_usage": dict(_EMPTY_USAGE)})
            continue
        if client is None:
            align, err = {"parsed": {}, "usage": dict(_EMPTY_USAGE)}, None
        else:
            align, err = _safe_call(ask_alignment, row, client)
        parsed = align["parsed"]
        offset = resolve_offset(parsed)
        visible = str(parsed.get("visible", "")).strip().lower() == "yes"
        stage_a.append({**row, "chosen_offset": offset, "visible": visible,
                        "align_reason": err or parsed.get("reason"),
                        "align_usage": align["usage"]})

    # any non-default choice needs its own full-res extraction (only offset 0 exists yet)
    needing = [r for r in stage_a if r["chosen_offset"] != 0 and r["visible"]
              and not chosen_frame_paths(str(r["node_key"]), str(r["bout"]), int(r["ts_ms"]),
                                         int(r["chosen_offset"]))["center"].exists()]
    if needing and client is not None:
        ensure_chosen_frames(needing)

    # stage B -- the blind read
    seed: list[dict[str, Any]] = []
    for i, row in enumerate(stage_a, 1):
        logger.info("[stage B %d/%d] %s (%s) offset=%+d visible=%s", i, len(stage_a),
                   row["node_key"], row["bout"], row["chosen_offset"], row["visible"])
        reason: str | None
        agree: str
        conf: str
        usage: dict[str, int]
        if not row["visible"]:
            model_parsed: dict[str, Any] = {}
            agree = "no"
            conf = "low"
            reason = "not_visible_in_window"
            usage = dict(_EMPTY_USAGE)
        else:
            answer: dict[str, Any]
            b_err: str | None
            if client is None:
                answer, b_err = {"parsed": {}, "usage": dict(_EMPTY_USAGE)}, None
            else:
                answer, b_err = _safe_call(ask_gemini, row, vocab, client)
            model_parsed = answer["parsed"]
            agree, conf = score_agreement(str(row["node_key"]), model_parsed)
            reason = b_err or model_parsed.get("reason")
            usage = answer["usage"]
        frame = chosen_frame_paths(str(row["node_key"]), str(row["bout"]), int(row["ts_ms"]),
                                   int(row["chosen_offset"]))["center"]
        seed.append({
            "node_key": row["node_key"], "bout": row["bout"], "ts_ms": row["ts_ms"],
            "ts_class": row.get("ts_class"),
            "chosen_offset": row["chosen_offset"], "visible": row["visible"],
            "align_reason": row["align_reason"],
            "frame": str(frame.relative_to(REPO)) if str(frame).startswith(str(REPO))
                    else str(frame),
            "corpus_label": row["label"], "model_label": model_parsed.get("label"),
            "model_type": model_parsed.get("type"),
            "actor_role": model_parsed.get("actor_role"),
            "model_confidence": model_parsed.get("confidence"), "reason": reason,
            "agree": agree, "review_confidence": conf,
            "usage": _sum_usage(row["align_usage"], usage),
        })
    return seed


def _agreement_counts(seed: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    total = len(seed)
    full = sum(1 for s in seed if s["agree"] == "full")
    partial = sum(1 for s in seed if s["agree"] == "partial")
    no = sum(1 for s in seed if s["agree"] == "no")
    return total, full, partial, no


# ── report ───────────────────────────────────────────────────────────────────────
def build_report(seed: list[dict[str, Any]], counts: dict[str, int],
                 curated: list[dict[str, Any]], *,
                 alignment: dict[str, Any] | None = None,
                 before_seed: list[dict[str, Any]] | None = None) -> str:
    """``alignment`` (optional): ``{"misaligned": int, "of_total": int}`` -- how many of a
    PREVIOUS batch's candidates sat on a match whose DB ``ts_origin`` flag disagreed with the
    evidence-based reclassification (see ``ts_class_matches_flag``), written once by `plan`
    right before it overwrites that previous batch. ``before_seed`` (optional): that previous
    batch's own graded answers, for an agreement before/after comparison."""
    per_tech: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in seed:
        per_tech[str(s["corpus_label"])].append(s)

    total, full, partial, no = _agreement_counts(seed)

    lines = ["# Gemini dictionary-seed report", "",
             f"Total candidates asked: {total}",
             f"Agreement: full {full}, partial {partial}, no {no}"
             + (f" ({full / total:.0%} full)" if total else ""), ""]

    lines += ["## Timestamp alignment", ""]
    if alignment:
        m, of = alignment.get("misaligned", 0), alignment.get("of_total", 0)
        pct = f" ({m / of:.0%})" if of else ""
        lines.append(f"Of the previous batch's {of} candidates, {m}{pct} sat on a match "
                     "whose DB `ts_origin` flag disagreed with the evidence-based "
                     "reclassification (`classify_ts_origin`) -- those candidates' original "
                     "frame was very likely not the labelled moment at all (the WNO "
                     "tale-of-the-tape-card defect, 2026-09-15).")
    offset_hist = Counter(int(s.get("chosen_offset", 0)) for s in seed)
    if offset_hist:
        lines.append("")
        lines.append("Stage-A chosen-offset histogram (where the label actually was, "
                     "relative to the corpus's own timestamp):")
        lines.append("")
        lines += ["| offset (s) | count |", "|---|---|"]
        lines += [f"| {o:+d} | {offset_hist.get(o, 0)} |" for o in WINDOW_OFFSETS]
    not_visible = sum(1 for s in seed if not s.get("visible", True))
    lines += ["", f"Not visible in any window frame: {not_visible} of {total}", ""]
    if before_seed:
        _, bf, bp, bn = _agreement_counts(before_seed)
        bt = len(before_seed)
        lines += ["**Before vs after realignment:**", "",
                 "| | full | partial | no | full % |", "|---|---|---|---|---|",
                 f"| before (single frame, corpus ts) | {bf} | {bp} | {bn} | "
                 f"{bf / bt:.0%} |" if bt else "| before | 0 | 0 | 0 | n/a |",
                 f"| after (stage-A-aligned frame) | {full} | {partial} | {no} | "
                 f"{full / total:.0%} |" if total else "| after | 0 | 0 | 0 | n/a |", ""]

    lines += ["## Per technique", "",
             "| technique | video-backed | asked | full | partial | no | review high/low |",
             "|---|---|---|---|---|---|---|"]
    for tech in curated:
        en = tech.get("en")
        if not en:
            continue
        rows = per_tech.get(en, [])
        if not rows and counts.get(en, 0) == 0:
            continue
        f_ = sum(1 for r in rows if r["agree"] == "full")
        p_ = sum(1 for r in rows if r["agree"] == "partial")
        n_ = sum(1 for r in rows if r["agree"] == "no")
        hi = sum(1 for r in rows if r["review_confidence"] == "high")
        lo = sum(1 for r in rows if r["review_confidence"] == "low")
        lines.append(f"| {en} | {counts.get(en, 0)} | {len(rows)} | {f_} | {p_} | {n_} | {hi}/{lo} |")
    lines.append("")

    zero = sorted(t["en"] for t in curated if t.get("en") and counts.get(t["en"], 0) == 0)
    lines += [f"## Techniques with zero video-backed events ({len(zero)})", ""]
    lines += [f"- {n}" for n in zero] + [""]

    usage_total: Counter[str] = Counter()
    for s in seed:
        for k, v in (s.get("usage") or {}).items():
            usage_total[k] += int(v)
    out_tokens = usage_total.get("candidates", 0) + usage_total.get("thoughts", 0)
    cost = (usage_total.get("prompt", 0) / 1_000_000 * GEMINI_PRICE_IN_PER_M
           + out_tokens / 1_000_000 * GEMINI_PRICE_OUT_PER_M)
    lines += ["## Cost (estimate)", "",
             f"prompt tokens: {usage_total.get('prompt', 0)}",
             f"output+thought tokens: {out_tokens}",
             f"estimated cost: ${cost:.4f} (pro ≈ $1.25/M in, $10/M out+thoughts)", ""]

    confusions = Counter(
        (s["corpus_label"], s["model_label"]) for s in seed
        if s["agree"] != "full" and s.get("model_label"))
    lines += ["## Top confusions (corpus label → model label)", "",
             "| corpus | model | count |", "|---|---|---|"]
    for (corp, mod), n in confusions.most_common(10):
        lines.append(f"| {corp} | {mod} | {n} |")
    lines.append("")

    return "\n".join(lines)


# ── I/O ──────────────────────────────────────────────────────────────────────────
def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── CLI ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--per-technique", type=int, default=6)
    p_plan.add_argument("--min-events", type=int, default=0,
                        help="only include techniques with at least this many video-backed events")
    p_plan.add_argument("--max-calls", type=int, default=None,
                        help="cap total candidates, dropping whole techniques from the tail")

    sub.add_parser("extract")

    p_ask = sub.add_parser("ask")
    p_ask.add_argument("--dry-run", action="store_true")

    sub.add_parser("report")

    a = ap.parse_args()

    if a.cmd == "plan":
        old_plan = read_jsonl(PLAN_PATH)   # -- before this run overwrites it
        matches = _load_matches()
        curated = prioritize_curated(load_curated(), load_coverage())
        plan, counts = build_plan(matches, curated, per_technique=a.per_technique,
                                  min_events=a.min_events, probe_duration_fn=probe_duration)
        plan = cap_plan(plan, a.max_calls)

        if old_plan and not BEFORE_SEED_PATH.exists():
            # First realignment run: archive the previous batch's own graded answers (if
            # `ask`/`report` already ran on it) and count how many of its candidates sat on
            # a match whose DB `ts_origin` flag disagreed with the evidence-based
            # reclassification -- ONCE, so a second `plan` doesn't overwrite the real
            # "before" with an already-realigned batch.
            if SEED_PATH.exists():
                SEED_PATH.rename(BEFORE_SEED_PATH)
            match_by_id = {m["match_id"]: m for m in matches}
            misaligned = 0
            for r in old_plan:
                m = match_by_id.get(r.get("match_id"))
                if not m:
                    continue
                ts_list = [float(ev["ts"]) for ev in (m.get("sequence") or [])
                          if isinstance(ev, dict) and ev.get("ts") is not None]
                # duration probing skipped here (diagnostic only, no network needed to COUNT
                # how many disagreed with the DB flag -- a zero-start match without a probe
                # classifies 'unknown', which already disagrees with any non-null flag).
                klass = classify_ts_origin(m.get("video_start_seconds"), ts_list, None)
                if not ts_class_matches_flag(m.get("ts_origin"), klass):
                    misaligned += 1
            ALIGNMENT_PATH.write_text(
                json.dumps({"misaligned": misaligned, "of_total": len(old_plan)}, indent=2),
                encoding="utf-8")
            logger.info("ts realignment: %d/%d of the previous batch's candidates were on a "
                       "mis-set ts_origin flag", misaligned, len(old_plan))

        write_jsonl([asdict(r) for r in plan], PLAN_PATH)
        PLAN_COUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLAN_COUNTS_PATH.write_text(json.dumps(counts, indent=2, sort_keys=True), encoding="utf-8")
        n_techs = len({r.node_key for r in plan})
        n_zero = sum(1 for t in curated if t.get("en") and counts.get(t["en"], 0) == 0)
        logger.info("plan: %d candidates across %d techniques (of %d curated; %d with zero "
                   "video-backed events)", len(plan), n_techs, len(curated), n_zero)
        return 0

    if a.cmd == "extract":
        for tool in ("yt-dlp", "ffmpeg"):
            if shutil.which(tool) is None:
                logger.error("%s not on PATH", tool)
                return 1
        plan_rows = read_jsonl(PLAN_PATH)
        if not plan_rows:
            logger.error("no %s -- run `plan` first", PLAN_PATH)
            return 1
        stats = run_extract(plan_rows)
        logger.info("extract: %d matches ok, %d failed, %d frames",
                   stats["matches"], stats["matches_failed"], stats["frames"])
        return 0

    if a.cmd == "ask":
        plan_rows = read_jsonl(PLAN_PATH)
        if not plan_rows:
            logger.error("no %s -- run `plan` first", PLAN_PATH)
            return 1
        dry = a.dry_run or not os.environ.get("GEMINI_API_KEY")
        seed = run_ask(plan_rows, dry_run=dry)
        write_jsonl(seed, SEED_PATH)
        logger.info("ask: %d answered (dry_run=%s)", len(seed), dry)
        return 0

    if a.cmd == "report":
        seed = read_jsonl(SEED_PATH)
        counts = (json.loads(PLAN_COUNTS_PATH.read_text(encoding="utf-8"))
                 if PLAN_COUNTS_PATH.exists() else {})
        curated = load_curated()
        alignment = (json.loads(ALIGNMENT_PATH.read_text(encoding="utf-8"))
                    if ALIGNMENT_PATH.exists() else None)
        before_seed = read_jsonl(BEFORE_SEED_PATH) if BEFORE_SEED_PATH.exists() else None
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            build_report(seed, counts, curated, alignment=alignment, before_seed=before_seed),
            encoding="utf-8")
        logger.info("wrote %s", REPORT_PATH)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
