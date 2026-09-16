#!/usr/bin/env python
"""Owner's "library with video + timestamp" audit: pull corpus events that resolve to a
curated technique, grab the frame at their own timestamp, ask Gemini which curated label it
sees, and keep the disagreements front and centre for a human.

    uv run python -m scripts.dictionary_seed plan --per-technique 3 --min-events 3 --max-calls 150
    uv run python -m scripts.dictionary_seed extract
    uv run python -m scripts.dictionary_seed preverify
    uv run python -m scripts.dictionary_seed ask
    uv run python -m scripts.dictionary_seed report

Five steps, each reading the previous step's file so a batch can be re-run from wherever it
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
3. **preverify** — local, free, no Gemini. Five cheap screens gate `verdict: skip` (centre
   frame missing/undecodable or fewer than 5/9 strip frames present, a near-blank centre
   frame, a static ±30s window, an out-of-range or intro-card timestamp, a duplicate centre
   frame already used by another candidate); a sixth (no/one person detected on the centre
   frame via an optional YOLOv8n person detector) is ADVISORY ONLY — measured 2026-09-16 that
   a general-purpose detector isn't reliable enough on entangled grappling to gate anything,
   so it lands in `warnings`, never `reasons`. Writes `preverify.jsonl` (verdict `ok`/`skip` +
   reasons + warnings + metrics per candidate), `preverify_summary.json` (counts per reason
   AND per warning) and `preverify_sheet_<n>.png` contact sheets (≤24 candidates/page,
   captioned `skip: <reasons>` / `warn: <warnings>`) for a human to spot-check before any
   Gemini call happens.
4. **ask** — ONE Gemini (`gemini-pro-latest`, temperature 0) call per candidate by default: the
   already-preverified centre frame (offset 0) is read BLIND (two athletes' names, closed
   curated vocabulary, three frames, no hint of the corpus label) and that read becomes
   `candidate_label` — the label a human reviews. `--align` opts back into a second, first
   call: a NOT-blind stage A that shows the 9-frame window and asks which offset actually
   shows the corpus label (a non-default choice gets its full-res frame re-extracted on
   demand) before the same blind read runs on whichever frame it picked. A candidate
   `preverify` marked `skip` is written straight to `seed.jsonl` with `visible: false`,
   `review_confidence: low`, reason `preverify:<reasons>` and zero usage — never sent to
   Gemini, no stage A or B either way. `visible: no` at stage A (`--align` only) skips the
   blind read too (`review_confidence: low`, reason `not_visible_in_window`). Resume-safe: a
   candidate already in `seed.jsonl` (node_key+bout+ts_ms) is skipped and the new answers are
   appended, not overwritten.
5. **report** — re-grades an existing `seed.jsonl` IN PLACE, no Gemini calls: adds/refreshes
   `agree_near`, `candidate_label`/`candidate_type`, `second_opinion` and `review_confidence`
   on every row (so a seed written before these fields existed is upgraded, not stuck). Writes
   `REPORT.md`: per-technique agreement (by corpus label, what was searched for, AND by
   candidate label, what the model actually saw), zero-video-backed techniques, cost, top
   confusions (the dictionary's ambiguous pairs).

**Decisão 2026-09-16** (owner): the Gemini blind read is the CANDIDATE label for human
review; the corpus label the plan searched for is only a SECOND OPINION shown alongside it.
Measured why: batch 2 (91 rows) — 6 disagreements eyeballed by hand, 5 of 6 showed the model
naming the position actually VISIBLE in the frame while the corpus label simply was not in it
(corpus-timestamp misalignment, not a wrong model read). Trusting the corpus label as ground
truth was therefore the wrong prior even after the stage-A alignment window. Consequences:
stage A is now OPTIONAL, off by default (`ask --align` turns it back on) — with the model's
own read as the label, the frame no longer has to show the corpus label at all, so the centre
frame at offset 0 (already screened by `preverify`) is read blind directly; `review_confidence`
is redefined around the candidate (see `compute_review_confidence`) instead of "did the model
match the corpus"; and the human review queue (`scripts/dictionary_audit.py queue`) now shows
the candidate first, the corpus label as a second opinion beside it.

**Decisão 2026-09-16, item 32** (owner): a frame can show an ACTION and a STATE at once. When
the candidate and the corpus label form a plausible action/state pair (Guard Pass + Side
Control, Triangle Choke + Closed Guard, Heel Hook + Leg Entanglement, ...), that is NOT a
disagreement — it is a double label with HIGH confidence, and the frame keeps BOTH labels.
`score_agreement` gains a `pair` tier, checked BEFORE `near`: one side resolves to an action,
the other to a state (`analysis.taxonomy_kind.kind_of_entry`), and the pair is backed by
evidence already in the repo — real corpus adjacency (`build_pair_support`, cached to
`data/finetune/audit/gemini_seed/pair_support.json` by `plan`, ≥ 2 distinct bouts), the
existing exit-orientation rule (`_arrived_at_state`), or a shared curated sub-family
(`_pair_family`, reusing `style_profile_core._sub_family`) — never a hand list of pairs.
`pair_rule` on the seed row says which one fired. A `pair` row's `labels` field carries BOTH
technique claims (`{node_key, kind, source}`, corpus + candidate); every other tier keeps only
the candidate (decision 2026-09-16, item 31, above).

Privacy: public corpus only (`matches`/`athletes`), same class as `scripts/frame_pdf.py`.
Prod is read-only; this script writes nothing back to the DB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.names import _normalize_name, canonicalize  # noqa: E402
from analysis.style_profile_core import _bout_slug, _sub_family  # noqa: E402
from analysis.taxonomy_kind import (  # noqa: E402
    exit_orientation,
    kind_of_entry,
    load_inference_table,
    orientation_for_inference,
)
from analysis.technique_match import clean_label  # noqa: E402

logger = logging.getLogger("dictionary_seed")

TECH_LIB_PATH = REPO / "analysis" / "data" / "technique_library.json"
OUT_DIR = REPO / "data" / "finetune" / "audit" / "gemini_seed"
PLAN_PATH = OUT_DIR / "plan.jsonl"
PLAN_COUNTS_PATH = OUT_DIR / "plan_counts.json"
SEED_PATH = OUT_DIR / "seed.jsonl"
# `build_pair_support`'s own cache, written once by `plan` (needs `matches.sequence`, which
# only `plan` reads) and consumed read-only by `report`/`score_agreement` -- see the "pair"
# tier docstring on `score_agreement` below (owner decision 2026-09-16, item 32).
PAIR_SUPPORT_PATH = OUT_DIR / "pair_support.json"
PREVERIFY_PATH = OUT_DIR / "preverify.jsonl"
PREVERIFY_SUMMARY_PATH = OUT_DIR / "preverify_summary.json"
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


# Owner decision 2026-09-16 (item 32): a frame can show an ACTION and a STATE at once --
# corpus adjacency ("does this action/state pair actually sit next to each other in a real
# sequence?") is rule (a) of the "pair" tier below. `MIN_PAIR_SUPPORT_BOUTS` mirrors the
# `select_candidates`/`min_events` convention of counting DISTINCT bouts, not raw mentions --
# two logged occurrences inside the same one-off bout is one data point, not two.
MIN_PAIR_SUPPORT_BOUTS = 2


def build_pair_support(matches: list[dict[str, Any]]) -> dict[str, int]:
    """``matches`` (as ``_load_matches`` shapes them) -> ``{"<action_key>|<state_key>":
    distinct_bout_count}`` over every adjacent (action, state) pair in a match's own
    ``sequence`` order, where both sides resolve to a CURATED entry
    (``analysis.taxonomy_kind.kind_of_entry``) and the two kinds differ. Written once by
    `plan` (the only step that reads ``matches.sequence``) to :data:`PAIR_SUPPORT_PATH`;
    read by `report`/:func:`score_agreement` as evidence for the "pair" tier's rule (a) --
    never a hand list of plausible pairs, the corpus IS the list.

    Key order is always ``action|state`` regardless of which one came first in the sequence
    (a submission attempted FROM a state and a state reached BY an action are the same
    adjacency evidence either way) -- so a caller checking one specific (action, state)
    candidate builds the exact same key it looks up.
    """
    curated = _curated_by_node_key()
    counts: Counter[str] = Counter()
    seen_bouts: dict[str, set[str]] = defaultdict(set)
    for m in matches:
        raw_events = [ev for ev in (m.get("sequence") or []) if isinstance(ev, dict)]
        if len(raw_events) < 2:
            continue
        match_id = str(m.get("match_id") or "")
        resolved: list[tuple[str, str] | None] = []
        for ev in raw_events:
            label = str(ev.get("label") or "")
            if not label:
                resolved.append(None)
                continue
            key = node_key_of(clean_label(label, str(ev.get("type") or "")))
            entry = curated.get(key)
            if entry is None:
                resolved.append(None)
                continue
            kind = kind_of_entry(str(entry["en"]), str(entry.get("type") or ""))
            resolved.append((key, kind) if kind in ("action", "state") else None)
        for a, b in zip(resolved, resolved[1:], strict=False):
            if a is None or b is None or a[1] == b[1]:
                continue
            action_key, state_key = (a[0], b[0]) if a[1] == "action" else (b[0], a[0])
            pair_key = f"{action_key}|{state_key}"
            if match_id not in seen_bouts[pair_key]:
                seen_bouts[pair_key].add(match_id)
                counts[pair_key] += 1
    return dict(counts)


def load_pair_support(path: Path = PAIR_SUPPORT_PATH) -> dict[str, int]:
    """:data:`PAIR_SUPPORT_PATH`, or ``{}`` when `plan` hasn't written one yet -- same
    "sibling artefact may not exist" tolerance as :func:`load_coverage`. No caching, same
    convention as that function -- a few KB, read once per `report`/scoring call."""
    if not path.exists():
        return {}
    data: dict[str, int] = json.loads(path.read_text(encoding="utf-8"))
    return data


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


# ── preverify ────────────────────────────────────────────────────────────────────
# Local, free, no Gemini -- screens every planned candidate's already-extracted frames so an
# obvious mistake (blank feed, paused/static window, too few frames on disk, corpus ts
# landing in an intro card or past the video's own end, the same frame reused for two
# candidates) never spends a Gemini call. `no_people` is advisory-only, see PERSON_MIN_COUNT
# below -- it never gates. Runs on `plan.jsonl` + whatever `extract` already put on disk;
# never downloads or deletes anything itself.
BLANK_STD_MAX = 8.0     # centre frame near-uniform (paused feed, solid graphic)
BLANK_MEAN_MAX = 12.0   # centre frame near-black (dropped feed, black card)
STATIC_DOWNSAMPLE = (64, 36)   # cheap enough to diff 9 frames without reading them for real
STATIC_DIFF_MAX = 2.0   # mean abs pixel diff between consecutive strip frames, 0-255 scale
INTRO_TS_S = 20.0       # below this, ts is almost certainly a tale-of-the-tape/intro card
# Stage A (`ask_alignment`) only sends whichever strip frames exist -- a full 9/9 window is
# nice, not required. Below this many present there isn't enough of the window left to trust
# an alignment choice, so `missing_frames` fires; at/above it a gap is tolerated.
STRIPS_MIN_PRESENT = 5
PERSON_MIN_AREA_FRAC = 0.01   # a detected box smaller than 1% of the frame is noise, not a person
# ponytail: MIN_COUNT=1 (zero persons is still a real miss; one is not) is now moot for the
# verdict -- measured on sheets 1-2 (2026-09-16) that this general-purpose YOLO detector isn't
# reliable enough on entangled grappling to GATE anything (it merges two grapplers into one
# box, or misses a dark-arena frame that's perfectly usable) -- kept only to decide the
# `no_people` WARNING (never `reasons`, see `preverify_candidate`). Upgrade path: a
# grappling-tuned detector, if one is ever trained, could move this back to gating.
PERSON_MIN_COUNT = 1
SHEET_PAGE_SIZE = 24
_SHEET_CENTER = (160, 90)
_SHEET_STRIP = (64, 36)
_SHEET_CAPTION_H = 34
_SHEET_PAD = 6


def _load_gray_array(path: Path) -> np.ndarray | None:
    """Decode one frame to a grayscale float array, or ``None`` when the file is missing or
    fails to decode -- the ``missing_frames`` check's own definition of "bad"."""
    if not path.exists():
        return None
    try:
        with Image.open(path) as img:
            img.load()
            return np.asarray(img.convert("L"), dtype=np.float64)
    except Exception:
        return None


def is_blank(gray: np.ndarray) -> bool:
    """Near-uniform (paused feed / solid graphic) or near-black (dropped feed) centre frame."""
    return float(gray.std()) < BLANK_STD_MAX or float(gray.mean()) < BLANK_MEAN_MAX


def _downsample(gray: np.ndarray, size: tuple[int, int] = STATIC_DOWNSAMPLE) -> np.ndarray:
    img = Image.fromarray(np.clip(gray, 0, 255).astype("uint8")).resize(size)
    return np.asarray(img, dtype=np.float64)


def is_static_window(grays: list[np.ndarray]) -> bool:
    """True when every consecutive pair of the 9 strip frames (downsampled 64x36) is
    near-identical -- a paused feed, a graphic held on screen, or a replay card sitting still
    across the whole +/-30s alignment window. Fewer than 2 frames has nothing to compare, so
    it reads as NOT static -- ``missing_frames`` is the check that flags that case."""
    if len(grays) < 2:
        return False
    small = [_downsample(g) for g in grays]
    return all(float(np.abs(small[i] - small[i - 1]).mean()) < STATIC_DIFF_MAX
              for i in range(1, len(small)))


def ts_in_intro(ts_ms: int) -> bool:
    return (ts_ms / 1000.0) < INTRO_TS_S


def ts_out_of_range(ts_ms: int, duration: float | None) -> bool:
    if duration is None:
        return False
    return (ts_ms / 1000.0) > duration


def count_persons(boxes: list[tuple[float, float, float, float]],
                  frame_w: int, frame_h: int) -> tuple[int, list[float]]:
    """``boxes`` (pixel xyxy person boxes) -> (# boxes covering >= 1% of the frame, every
    box's area fraction, largest first). The count is what ``is_no_people`` gates on; the full
    list is kept in ``metrics`` for a human skimming the sheet."""
    frame_area = float(frame_w * frame_h) or 1.0
    areas = sorted(((x2 - x1) * (y2 - y1)) / frame_area for x1, y1, x2, y2 in boxes)[::-1]
    persons = sum(1 for a in areas if a >= PERSON_MIN_AREA_FRAC)
    return persons, areas


def is_no_people(persons: int) -> bool:
    return persons < PERSON_MIN_COUNT


_PERSON_MODEL: Any = None
_PERSON_MODEL_TRIED = False


def person_detector() -> Any | None:
    """Lazily load an Ultralytics YOLOv8n general detector (class 0 = person) -- installed via
    the ``cv`` extra, not a hard dependency (same guarded-import shape as
    ``cv.pose_estimate.PoseEstimator._ultralytics_runtime``). Missing package or a failed
    weight download both cache to ``None`` once rather than retrying per candidate; callers
    record ``detector: unavailable`` in ``metrics`` and skip the ``no_people`` check rather
    than fail it -- an owner without the extra installed still gets the other five checks."""
    global _PERSON_MODEL, _PERSON_MODEL_TRIED
    if _PERSON_MODEL_TRIED:
        return _PERSON_MODEL
    _PERSON_MODEL_TRIED = True
    try:
        from ultralytics import YOLO  # type: ignore[attr-defined, unused-ignore]

        _PERSON_MODEL = YOLO("yolov8n.pt")
    except Exception as exc:
        logger.warning("person detector unavailable: %s", exc)
        _PERSON_MODEL = None
    return _PERSON_MODEL


def detect_person_boxes(path: Path, model: Any) -> list[tuple[float, float, float, float]]:
    """One frame -> its person-class boxes (pixel xyxy), via an already-loaded model."""
    results = model.predict(str(path), verbose=False, classes=[0])
    if not results or results[0].boxes is None:
        return []
    xyxy = results[0].boxes.xyxy.cpu().numpy()
    return [(float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in xyxy]


def preverify_candidate(row: dict[str, Any], *, duration: float | None,
                        detector: Any | None) -> dict[str, Any]:
    """One plan row -> ``{node_key, bout, ts_ms, verdict, reasons, warnings, metrics,
    center_path, strip_paths}``. ``verdict`` is ``ok`` iff ``reasons`` is empty --
    ``warnings`` are informational only and NEVER gate the verdict (``no_people``: the
    general-purpose YOLO detector merges entangled grapplers into one box or misses a dark
    arena often enough on this domain, measured on sheets 1-2 2026-09-16, that it can only be
    advisory). Never touches the network or deletes anything -- reads whatever ``extract``
    already put on disk. The ``duplicate_frame`` check is NOT here (it needs every OTHER
    candidate's centre frame too) -- :func:`run_preverify` adds it in a second pass."""
    node_key, bout, ts_ms = str(row["node_key"]), str(row["bout"]), int(row["ts_ms"])
    center_path = chosen_frame_paths(node_key, bout, ts_ms, 0)["center"]
    strip_paths = [strip_frame_path(node_key, bout, ts_ms, o) for o in WINDOW_OFFSETS]

    reasons: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    center_gray = _load_gray_array(center_path)
    strip_grays = [_load_gray_array(p) for p in strip_paths]
    strips_missing = sum(1 for g in strip_grays if g is None)
    strips_present = len(WINDOW_OFFSETS) - strips_missing
    if center_gray is None or strips_present < STRIPS_MIN_PRESENT:
        reasons.append("missing_frames")
        metrics["missing_frames"] = {"center": center_gray is None, "strips_missing": strips_missing}

    if center_gray is not None:
        metrics["blank"] = {"std": round(float(center_gray.std()), 2),
                            "mean": round(float(center_gray.mean()), 2)}
        if is_blank(center_gray):
            reasons.append("blank_frame")

    present_strips = [g for g in strip_grays if g is not None]
    if len(present_strips) == len(WINDOW_OFFSETS) and is_static_window(present_strips):
        reasons.append("static_window")

    metrics["duration"] = duration
    if ts_out_of_range(ts_ms, duration):
        reasons.append("ts_out_of_range")
    if ts_in_intro(ts_ms):
        reasons.append("ts_in_intro")

    if center_gray is not None:
        if detector is None:
            metrics["detector"] = "unavailable"
        else:
            boxes = detect_person_boxes(center_path, detector)
            h, w = center_gray.shape
            persons, areas = count_persons(boxes, w, h)
            metrics["persons"] = persons
            metrics["person_areas"] = [round(a, 4) for a in areas[:3]]
            if is_no_people(persons):
                warnings.append("no_people")

    return {
        "node_key": node_key, "bout": bout, "ts_ms": ts_ms,
        "verdict": "skip" if reasons else "ok", "reasons": reasons, "warnings": warnings,
        "metrics": metrics,
        "center_path": str(center_path), "strip_paths": [str(p) for p in strip_paths],
    }


def _add_duplicate_frame_reason(results: list[dict[str, Any]]) -> None:
    """Second pass over an already-scored batch: an identical centre frame (md5 of the file's
    own bytes) already used by an EARLIER candidate marks every later one ``duplicate_frame``
    -- the first occurrence stays whatever it already was. Mutates ``results`` in place."""
    seen: dict[str, int] = {}
    for i, r in enumerate(results):
        p = Path(r["center_path"])
        if not p.exists():
            continue   # missing_frames already covers this candidate
        digest = hashlib.md5(p.read_bytes()).hexdigest()
        if digest in seen:
            other = results[seen[digest]]
            r["reasons"].append("duplicate_frame")
            r["metrics"]["duplicate_of"] = f"{other['node_key']}/{other['bout']}/{other['ts_ms']}"
            r["verdict"] = "skip"
        else:
            seen[digest] = i


def run_preverify(plan_rows: list[dict[str, Any]], *,
                  probe_duration_fn: Any = probe_duration,
                  detector: Any | None = "auto") -> list[dict[str, Any]]:
    """``plan.jsonl`` rows -> graded preverify rows. ``duration`` per candidate is probed once
    per distinct ``video_url`` (memoised) and only for a row that carries one; a row with no
    url just records ``duration: null`` and skips the range check rather than guessing.
    ``detector="auto"`` loads (and caches) the real YOLO person detector; pass ``None`` or an
    injected callable-model to skip/replace it in tests."""
    if detector == "auto":
        detector = person_detector()
    duration_cache: dict[str, float | None] = {}

    def _duration(row: dict[str, Any]) -> float | None:
        url = row.get("video_url")
        if not url:
            return None
        if url not in duration_cache:
            duration_cache[url] = probe_duration_fn(url) if probe_duration_fn else None
        return duration_cache[url]

    results = [preverify_candidate(row, duration=_duration(row), detector=detector)
              for row in plan_rows]
    _add_duplicate_frame_reason(results)
    return results


def build_preverify_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(results),
        "verdicts": dict(Counter(r["verdict"] for r in results)),
        "reasons": dict(Counter(reason for r in results for reason in r["reasons"])),
        "warnings": dict(Counter(w for r in results for w in r.get("warnings", []))),
    }


def _sheet_thumb(path: Path, size: tuple[int, int]) -> Image.Image:
    """A frame -> a fixed-size RGB thumbnail, letterboxed on a dark background -- a broken or
    missing frame becomes a flat red-ish tile instead of crashing the sheet build."""
    try:
        with Image.open(path) as src:
            img = src.convert("RGB")
            img.thumbnail(size)
            canvas = Image.new("RGB", size, (40, 40, 40))
            canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
            return canvas
    except Exception:
        return Image.new("RGB", size, (90, 30, 30))


def _sheet_caption(result: dict[str, Any]) -> str:
    ts_s = result["ts_ms"] / 1000.0
    tag = result["verdict"] if result["verdict"] == "ok" else f"skip: {'+'.join(result['reasons'])}"
    if result.get("warnings"):
        tag += f" · warn: {'+'.join(result['warnings'])}"
    return f"{result['node_key']} · {result['bout']} · {ts_s:.1f}s · {tag}"


def build_sheet_page(results: list[dict[str, Any]]) -> Image.Image:
    """One contact-sheet page (<= ``SHEET_PAGE_SIZE`` rows): centre-frame thumb + the 9 strip
    thumbs in a row, captioned with node_key/bout/ts/verdict -- what a human skims before
    trusting `ask` to spend Gemini calls."""
    row_w = _SHEET_CENTER[0] + _SHEET_PAD + len(WINDOW_OFFSETS) * (_SHEET_STRIP[0] + 2)
    row_h = max(_SHEET_CENTER[1], _SHEET_STRIP[1]) + _SHEET_CAPTION_H
    page = Image.new("RGB", (row_w + 2 * _SHEET_PAD, row_h * max(len(results), 1) + _SHEET_PAD),
                     (20, 20, 20))
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default()
    for i, r in enumerate(results):
        y = i * row_h + _SHEET_PAD
        x = _SHEET_PAD
        page.paste(_sheet_thumb(Path(r["center_path"]), _SHEET_CENTER), (x, y))
        x += _SHEET_CENTER[0] + _SHEET_PAD
        for sp in r["strip_paths"]:
            page.paste(_sheet_thumb(Path(sp), _SHEET_STRIP), (x, y))
            x += _SHEET_STRIP[0] + 2
        draw.text((_SHEET_PAD, y + max(_SHEET_CENTER[1], _SHEET_STRIP[1]) + 2),
                  _sheet_caption(r), fill=(230, 230, 230), font=font)
    return page


def write_sheets(results: list[dict[str, Any]], out_dir: Path,
                 page_size: int = SHEET_PAGE_SIZE) -> list[Path]:
    paths = []
    for n, i in enumerate(range(0, max(len(results), 1), page_size), 1):
        page = build_sheet_page(results[i:i + page_size])
        p = out_dir / f"preverify_sheet_{n}.png"
        page.save(p)
        paths.append(p)
    return paths


def build_preverify_skip_seed_row(row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    """A preverify ``skip`` verdict -> the exact shape ``run_ask`` writes to ``seed.jsonl``, at
    zero Gemini cost -- so `report` never has to special-case where a row came from, and the
    candidate is never silently dropped. Never "read" (no Gemini call happened), so
    ``candidate_label`` is null and ``review_confidence`` is ``low`` -- see
    :func:`compute_review_confidence`."""
    tag = f"preverify:{'+'.join(reasons)}"
    frame = chosen_frame_paths(str(row["node_key"]), str(row["bout"]), int(row["ts_ms"]), 0)["center"]
    return {
        "node_key": row["node_key"], "bout": row["bout"], "ts_ms": row["ts_ms"],
        "ts_class": row.get("ts_class"),
        "chosen_offset": 0, "visible": False, "align_reason": tag,
        "frame": str(frame.relative_to(REPO)) if str(frame).startswith(str(REPO)) else str(frame),
        "corpus_label": row.get("label"), "model_label": None, "model_type": None,
        "candidate_label": None, "candidate_type": None,
        "second_opinion": {"corpus_label": row.get("label"), "agree": "no", "agree_near": "no"},
        "actor_role": None, "model_confidence": None, "reason": tag,
        "agree": "no", "review_confidence": "low", "usage": dict(_EMPTY_USAGE),
    }


def partition_for_ask(plan_rows: list[dict[str, Any]], existing_seed: list[dict[str, Any]],
                      preverify_rows: list[dict[str, Any]],
                      ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Plan rows -> ``(rows still needing a Gemini ask, seed rows for preverify-skipped
    candidates)``. A row already present in ``existing_seed`` (matched on
    node_key+bout+ts_ms) is dropped from both -- what makes `ask` resume-safe. A row
    ``preverify`` marked ``skip`` never reaches the Gemini list at all."""
    done = {(s["node_key"], s["bout"], s["ts_ms"]) for s in existing_seed}
    preverify_by_key = {(p["node_key"], p["bout"], p["ts_ms"]): p for p in preverify_rows}
    to_ask: list[dict[str, Any]] = []
    skip_seed: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for row in plan_rows:
        key = (row["node_key"], row["bout"], row["ts_ms"])
        if key in done or key in seen:
            continue
        seen.add(key)
        pv = preverify_by_key.get(key)
        if pv and pv.get("verdict") == "skip":
            skip_seed.append(build_preverify_skip_seed_row(row, list(pv.get("reasons") or [])))
        else:
            to_ask.append(row)
    return to_ask, skip_seed


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


_CURATED_BY_NODE_KEY: dict[str, dict[str, Any]] | None = None


def _curated_by_node_key() -> dict[str, dict[str, Any]]:
    """``node_key -> curated entry``, built once from the same 210-entry
    ``technique_library.json`` everything else in this module reads -- no second table."""
    global _CURATED_BY_NODE_KEY
    if _CURATED_BY_NODE_KEY is None:
        _CURATED_BY_NODE_KEY = {node_key_of(t["en"]): t for t in load_curated() if t.get("en")}
    return _CURATED_BY_NODE_KEY


def _same_family(node_key: str, model_key: str) -> bool:
    """Near rule (1): corpus and model resolve to two DIFFERENT curated entries that share the
    same curated ``type`` -- a parent/child or generic-vs-specific pair within one family
    (Lasso Guard/De la Riva -> Open Guard, Turtle Control -> Turtle Position). Reuses the
    library's own ``type`` field -- no hand list of pairs, no new table.

    ``type == "submission"`` on both sides narrows further, through
    ``style_profile_core._sub_family`` (the curated strangle/leglock/armlock keyword table
    the style profile already uses) -- ``type`` alone is too coarse there: Heel Hook and
    Kimura are BOTH ``submission`` but a leg lock is not a shoulder lock. Every other type
    (guard/control/...) has no such curated sub-split in the repo today, so it stays a plain
    type match."""
    corpus = _curated_by_node_key().get(node_key)
    model = _curated_by_node_key().get(model_key)
    if not corpus or not model or node_key == model_key:
        return False
    if not corpus["type"] or corpus["type"] != model["type"]:
        return False
    if corpus["type"] == "submission":
        cf, mf = _sub_family(corpus["en"]), _sub_family(model["en"])
        return cf is not None and cf == mf
    return True


# Stance (5-way, `orientation_for_inference`) -> Orientation (3-way, `action_exit_orientation`)
# -- `controlling`/`controlled` are the SAME physical dominance as `top`/`bottom` (D1's own
# 5-vs-3 split, see `taxonomy_kind._POSITIONAL_ROLES`), collapsed here only for this one
# comparison; nothing upstream is touched.
_STANCE_TO_ORIENTATION = {"top": "top", "controlling": "top",
                          "bottom": "bottom", "controlled": "bottom"}


def _arrived_at_state(node_key: str, model_answer: Mapping[str, Any]) -> bool:
    """Near rule (2): the corpus label is an ACTION (``kind_of_entry``) whose curated ``type``
    carries a non-neutral declared landing orientation (``action_exit_orientation`` in
    ``data/taxonomy/inference_table.json`` -- the same R0 exit table
    ``analysis.outcome_inference`` uses), and the model's state reads that SAME orientation
    (``orientation_for_inference``, declared table first, curated actor role second). Catches
    e.g. Guard Pass -> Side Control (pass:top, Side Control:top). Deliberately narrower than
    every "arrived-at position" example an eyeballed disagreement might suggest -- a frame
    showing the OTHER fighter's residual state (Sweep -> Open Guard, a failed/mid attempt) is
    not expressible from this table without inventing a second, opponent-relative one, so it
    stays ``no`` rather than guessing."""
    corpus = _curated_by_node_key().get(node_key)
    if not corpus:
        return False
    corpus_type = str(corpus.get("type") or "")
    if kind_of_entry(str(corpus["en"]), corpus_type) != "action":
        return False
    table = load_inference_table()
    orient = exit_orientation(table, corpus_type)
    if orient == "neutral":
        return False
    model_label = str(model_answer.get("label") or "")
    if not model_label:
        return False
    stance = orientation_for_inference(str(model_answer.get("type") or ""), model_label).value
    return _STANCE_TO_ORIENTATION.get(stance) == orient


def _pair_family(action_key: str, state_key: str) -> bool:
    """Pair rule (c): the ACTION's curated sub-family (``style_profile_core._sub_family`` --
    armlock/leglock/strangle, submissions only) also shows up in the STATE's own label or any
    of its curated variants -- e.g. Heel Hook (leglock) with Leg Entanglement, whose variant
    "leg lock entanglement" carries the same "leg lock" keyword. Reuses the curated keyword
    table already in the repo; no hand list of technique pairs. Only submissions carry a
    curated sub-family today, so a non-submission action never matches here."""
    action = _curated_by_node_key().get(action_key)
    state = _curated_by_node_key().get(state_key)
    if not action or not state or str(action.get("type") or "") != "submission":
        return False
    fam = _sub_family(str(action["en"]))
    if fam is None:
        return False
    texts = [state.get("en"), *(state.get("variants") or [])]
    return any(_sub_family(str(t)) == fam for t in texts if t)


def _pair_rule(node_key: str, model_key: str, pair_support: Mapping[str, int] | None,
               ) -> str | None:
    """Owner decision 2026-09-16 (item 32): is ``node_key`` (the corpus label) and
    ``model_key`` (the candidate) a genuine double label on one frame -- one side an ACTION,
    the other a STATE (``kind_of_entry``), backed by evidence already in the repo? Returns
    which rule fired (``adjacency``/``exit_orientation``/``family``), or ``None`` when the two
    aren't one action + one state, or no rule backs the pair -- the caller falls through to
    the existing ``near``/``no`` tiers in either case.

    Checked in the same priority order a human would trust: real corpus adjacency first (a),
    then the declared exit-orientation table (b, :func:`_arrived_at_state`), then a shared
    curated family (c, :func:`_pair_family`) -- the first one that fires wins, `pair_rule`
    only ever names one."""
    corpus = _curated_by_node_key().get(node_key)
    model = _curated_by_node_key().get(model_key)
    if not corpus or not model or node_key == model_key:
        return None
    corpus_kind = kind_of_entry(str(corpus["en"]), str(corpus.get("type") or ""))
    model_kind = kind_of_entry(str(model["en"]), str(model.get("type") or ""))
    if {corpus_kind, model_kind} != {"action", "state"}:
        return None
    action_key, state_key = (node_key, model_key) if corpus_kind == "action" \
        else (model_key, node_key)

    support = pair_support if pair_support is not None else load_pair_support()
    if int(support.get(f"{action_key}|{state_key}", 0)) >= MIN_PAIR_SUPPORT_BOUTS:
        return "adjacency"
    state_entry = _curated_by_node_key()[state_key]
    if _arrived_at_state(action_key, {"label": state_entry["en"], "type": state_entry["type"]}):
        return "exit_orientation"
    if _pair_family(action_key, state_key):
        return "family"
    return None


def score_agreement(node_key: str, model_answer: dict[str, Any],
                    pair_support: Mapping[str, int] | None = None,
                    ) -> tuple[str, str, str | None]:
    """``(agree, _legacy_conf, pair_rule)``. ``agree`` in ``full|partial|pair|near|no`` is the
    second opinion's own tier and is still used everywhere. The second element is the
    PRE-2026-09-16 confidence rule (corpus-centric: high only on full agreement) -- kept for
    callers/tests that still read it, but a seed row's own ``review_confidence`` now comes
    from :func:`compute_review_confidence` instead (candidate-centric, see its docstring).
    ``pair_rule`` names which pair rule fired (``adjacency``/``exit_orientation``/``family``,
    see :func:`_pair_rule`) and is ``None`` for every other tier. Disagreement is exactly what
    the human review queue exists to see, so it is never dropped.

    ``pair`` (added 2026-09-16, item 32) is checked BEFORE ``near``: the corpus label and the
    candidate are a genuine double label on one frame (one action, one state,
    :func:`_pair_rule`) rather than a disagreement -- see the module docstring's "Decisão
    2026-09-16 (item 32)".

    ``near`` (added 2026-09-16, `docs/dictionary_audit.md`) sits between ``partial`` and ``no``:
    the model's label is not the corpus label and not its declared ``alternative``, but is
    either the same curated family (``_same_family``) or the state that corpus ACTION declares
    it lands in (``_arrived_at_state``) -- both computed from tables already in the repo, never
    a hand list of pairs."""
    model_label = str(model_answer.get("label") or "")
    if model_label and node_key_of(clean_label(model_label)) == node_key:
        return "full", "high", None
    alt = str(model_answer.get("alternative") or "")
    if alt and node_key_of(clean_label(alt)) == node_key:
        return "partial", "low", None
    if model_label:
        model_key = node_key_of(clean_label(model_label, str(model_answer.get("type") or "")))
        rule = _pair_rule(node_key, model_key, pair_support)
        if rule:
            return "pair", "high", rule
        if _same_family(node_key, model_key) or _arrived_at_state(node_key, model_answer):
            return "near", "medium", None
    return "no", "low", None


def compute_review_confidence(agree: str, model_confidence: str | None, read: bool) -> str:
    """Human review confidence (decision 2026-09-16, `docs/dictionary_audit.md`): the
    CANDIDATE (blind Gemini read) is what a human reviews now, the corpus label is only a
    second opinion beside it -- so this is no longer "did the model match the corpus", it is
    "how much can the human trust this candidate without looking hard".

    ``high`` when the second opinion backs it up (``agree`` full or partial -- both directions
    of agreement, not just an exact match) OR the row is a ``pair`` (item 32: a plausible
    double label is not a disagreement, it's two trustworthy claims). ``medium`` on the
    ``near`` tier (same curated family / declared landing state) OR when the model itself
    reported high confidence, even with no corpus support -- either is a reason to look,
    neither is a reason to trust blindly. ``low`` otherwise, including a row that was never
    actually read (a preverify skip, `visible: no` at stage A, or a Gemini error/empty answer)
    -- those get ``read=False`` and short-circuit here regardless of ``agree``."""
    if not read:
        return "low"
    if agree in ("full", "partial", "pair"):
        return "high"
    if agree == "near" or str(model_confidence or "").strip().lower() == "high":
        return "medium"
    return "low"


def build_row_labels(node_key: str, candidate_label: str | None, candidate_type: str | None,
                     tier: str) -> list[dict[str, Any]]:
    """A seed row's own ``labels`` field (item 32): every technique this frame actually
    claims. ``[]`` when the row was never read (no candidate). One entry -- the candidate --
    for ``full``/``partial``/``near``/``no`` (decision 2026-09-16, item 31: the candidate is
    the review target, the corpus label elsewhere is only a second opinion, not a second
    claim). TWO entries for ``pair``: the candidate (``source: gemini``) AND the corpus label
    (``source: corpus``) -- a genuine double label, both trustworthy. Corpus first, to match
    ``second_opinion``'s convention of leading with the corpus half."""
    if not candidate_label:
        return []
    candidate_key = node_key_of(clean_label(str(candidate_label), str(candidate_type or "")))
    candidate_entry = _curated_by_node_key().get(candidate_key)
    candidate_kind = (kind_of_entry(str(candidate_entry["en"]), str(candidate_entry.get("type") or ""))
                      if candidate_entry else None)
    candidate_line = {"node_key": candidate_key, "kind": candidate_kind, "source": "gemini"}
    if tier != "pair" or candidate_key == node_key:
        return [candidate_line]
    corpus_entry = _curated_by_node_key().get(node_key)
    corpus_kind = (kind_of_entry(str(corpus_entry["en"]), str(corpus_entry.get("type") or ""))
                  if corpus_entry else None)
    corpus_line = {"node_key": node_key, "kind": corpus_kind, "source": "corpus"}
    return [corpus_line, candidate_line]


def regrade_seed_near(seed: list[dict[str, Any]], *,
                      pair_support: Mapping[str, int] | None = None) -> list[dict[str, Any]]:
    """Re-grade an already-asked batch with the pair/near tiers, WITHOUT another Gemini call
    and WITHOUT touching the strict ``agree`` a row already carries -- adds ``agree_near`` and
    ``pair_rule``. ``full``/``partial`` stay themselves (pair/near only ever sit between
    partial and no, ``pair`` checked first -- item 32); a ``no`` row is re-checked against
    :func:`_pair_rule`, then ``_same_family``/``_arrived_at_state``, on its own stored
    ``model_label``/``model_type``. ``alternative`` was never persisted to `seed.jsonl`
    (partial's own basis), so a `no` row's `alternative` is unrecoverable here -- fine, since
    a row already scored `partial` is left alone and never needs it."""
    out = []
    for row in seed:
        row = dict(row)
        agree = str(row.get("agree", "no"))
        if agree != "no":
            row["agree_near"] = agree
            row.setdefault("pair_rule", None)
        else:
            model_label = row.get("model_label")
            if model_label:
                model_key = node_key_of(
                    clean_label(str(model_label), str(row.get("model_type") or "")))
                answer = {"label": model_label, "type": row.get("model_type")}
                rule = _pair_rule(str(row["node_key"]), model_key, pair_support)
                if rule:
                    row["agree_near"] = "pair"
                    row["pair_rule"] = rule
                elif (_same_family(str(row["node_key"]), model_key)
                        or _arrived_at_state(str(row["node_key"]), answer)):
                    row["agree_near"] = "near"
                    row["pair_rule"] = None
                else:
                    row["agree_near"] = "no"
                    row["pair_rule"] = None
            else:
                row["agree_near"] = "no"
                row["pair_rule"] = None
        out.append(row)
    return out


def regrade_seed_review(seed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backfill/refresh ``candidate_label``/``candidate_type``/``second_opinion``/
    ``review_confidence``/``labels`` on every row, derived from what's already stored
    (``model_label``/``model_type``/``agree``/``agree_near``/``model_confidence``) -- no
    Gemini call, idempotent, and it upgrades a `seed.jsonl` written before these fields
    existed (decision 2026-09-16, `docs/dictionary_audit.md`; ``labels`` added item 32). Call
    AFTER :func:`regrade_seed_near` so ``agree_near``/``pair_rule`` are already the best tier
    available."""
    out = []
    for row in seed:
        row = dict(row)
        read = bool(row.get("model_label"))
        row["candidate_label"] = row.get("model_label") if read else None
        row["candidate_type"] = row.get("model_type") if read else None
        agree = str(row.get("agree", "no"))
        agree_near = str(row.get("agree_near") or agree)
        row["second_opinion"] = {"corpus_label": row.get("corpus_label"),
                                 "agree": agree, "agree_near": agree_near}
        # the near-aware tier decides confidence (a strict `no` regraded to `near`/`pair`
        # still earns medium/high) -- see compute_review_confidence's docstring.
        row["review_confidence"] = compute_review_confidence(
            agree_near, row.get("model_confidence"), read)
        row["labels"] = build_row_labels(str(row["node_key"]), row["candidate_label"],
                                         row["candidate_type"], agree_near)
        out.append(row)
    return out


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


def run_ask(plan: list[dict[str, Any]], *, dry_run: bool = False,
           align: bool = False) -> list[dict[str, Any]]:
    """``gemini-pro-latest`` at ``temperature=0``. Default (``align=False``, decision
    2026-09-16): ONE call per candidate -- the blind read (no corpus label given) straight on
    the already-preverified centre frame (offset 0); its answer becomes ``candidate_label``,
    the label a human reviews, with the corpus label kept only as a second opinion. A
    candidate whose centre frame never got extracted is marked ``visible: false``
    (``align_reason: extraction_failed``) and never spends a call.

    ``align=True`` opts back into the OLD two-call shape: stage A (not blind) picks which
    frame in the +/-30s window actually shows the CORPUS label first; the same blind read
    then runs on whichever frame it picked. ``visible: no`` at stage A skips the blind read
    entirely (``review_confidence: low``, reason ``not_visible_in_window``, never dropped)."""
    curated = load_curated()
    vocab = vocabulary_text(curated)
    client = None
    if not dry_run:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    stage_a: list[dict[str, Any]]
    if align:
        # stage A -- alignment (opt-in, --align)
        stage_a = []
        for i, row in enumerate(plan, 1):
            logger.info("[stage A %d/%d] %s (%s)", i, len(plan), row["node_key"], row["bout"])
            node_key, bout, ts_ms = str(row["node_key"]), str(row["bout"]), int(row["ts_ms"])
            has_frames = any(strip_frame_path(node_key, bout, ts_ms, o).exists()
                             for o in WINDOW_OFFSETS)
            if not has_frames:
                # extraction failed for this candidate's match (yt-dlp/ffmpeg) -- nothing to
                # show the model, so don't spend a call on an empty prompt.
                stage_a.append({**row, "chosen_offset": 0, "visible": False,
                                "align_reason": "extraction_failed",
                                "align_usage": dict(_EMPTY_USAGE)})
                continue
            if client is None:
                align_resp, err = {"parsed": {}, "usage": dict(_EMPTY_USAGE)}, None
            else:
                align_resp, err = _safe_call(ask_alignment, row, client)
            parsed = align_resp["parsed"]
            offset = resolve_offset(parsed)
            visible = str(parsed.get("visible", "")).strip().lower() == "yes"
            stage_a.append({**row, "chosen_offset": offset, "visible": visible,
                            "align_reason": err or parsed.get("reason"),
                            "align_usage": align_resp["usage"]})

        # any non-default choice needs its own full-res extraction (only offset 0 exists yet)
        needing = [r for r in stage_a if r["chosen_offset"] != 0 and r["visible"]
                  and not chosen_frame_paths(str(r["node_key"]), str(r["bout"]), int(r["ts_ms"]),
                                             int(r["chosen_offset"]))["center"].exists()]
        if needing and client is not None:
            ensure_chosen_frames(needing)
    else:
        # 2026-09-16: off by default -- the model's own read is the candidate, so the frame no
        # longer has to show the corpus label. Read the centre frame (offset 0, already
        # screened by `preverify`) blind directly -- zero stage-A calls, one call total below.
        stage_a = [
            {**row, "chosen_offset": 0,
             "visible": (visible := chosen_frame_paths(
                 str(row["node_key"]), str(row["bout"]), int(row["ts_ms"]), 0)["center"].exists()),
             "align_reason": None if visible else "extraction_failed",
             "align_usage": dict(_EMPTY_USAGE)}
            for row in plan
        ]

    # the blind read (stage B when --align, the only call otherwise)
    seed: list[dict[str, Any]] = []
    for i, row in enumerate(stage_a, 1):
        logger.info("[ask %d/%d] %s (%s) offset=%+d visible=%s", i, len(stage_a),
                   row["node_key"], row["bout"], row["chosen_offset"], row["visible"])
        reason: str | None
        agree: str
        pair_rule_hit: str | None
        usage: dict[str, int]
        if not row["visible"]:
            model_parsed: dict[str, Any] = {}
            agree = "no"
            pair_rule_hit = None
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
            agree, _old_conf, pair_rule_hit = score_agreement(str(row["node_key"]), model_parsed)
            reason = b_err or model_parsed.get("reason")
            usage = answer["usage"]
        read = bool(model_parsed.get("label"))
        conf = compute_review_confidence(agree, model_parsed.get("confidence"), read)
        frame = chosen_frame_paths(str(row["node_key"]), str(row["bout"]), int(row["ts_ms"]),
                                   int(row["chosen_offset"]))["center"]
        candidate_label = model_parsed.get("label") if read else None
        candidate_type = model_parsed.get("type") if read else None
        seed.append({
            "node_key": row["node_key"], "bout": row["bout"], "ts_ms": row["ts_ms"],
            "ts_class": row.get("ts_class"),
            "chosen_offset": row["chosen_offset"], "visible": row["visible"],
            "align_reason": row["align_reason"],
            "frame": str(frame.relative_to(REPO)) if str(frame).startswith(str(REPO))
                    else str(frame),
            "corpus_label": row["label"], "model_label": model_parsed.get("label"),
            "model_type": model_parsed.get("type"),
            "candidate_label": candidate_label,
            "candidate_type": candidate_type,
            "second_opinion": {"corpus_label": row["label"], "agree": agree,
                               "agree_near": agree},
            "actor_role": model_parsed.get("actor_role"),
            "model_confidence": model_parsed.get("confidence"), "reason": reason,
            "agree": agree, "review_confidence": conf, "pair_rule": pair_rule_hit,
            "labels": build_row_labels(str(row["node_key"]), candidate_label, candidate_type,
                                       agree),
            "usage": _sum_usage(row["align_usage"], usage),
        })
    return seed


def _agreement_counts(seed: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    total = len(seed)
    full = sum(1 for s in seed if s["agree"] == "full")
    partial = sum(1 for s in seed if s["agree"] == "partial")
    no = sum(1 for s in seed if s["agree"] == "no")
    return total, full, partial, no


def _near_tier(s: dict[str, Any]) -> str:
    """This row's grade under the near-aware rule -- ``agree_near`` when a re-grade wrote one
    (a previously-scored batch, graded in place with the strict ``agree`` kept untouched), else
    the row's own ``agree`` (already 4-tier for anything scored by the current
    ``score_agreement``)."""
    return str(s.get("agree_near") or s.get("agree") or "no")


def _near_counts(seed: list[dict[str, Any]]) -> tuple[int, int, int, int, int, int]:
    total = len(seed)
    full = sum(1 for s in seed if _near_tier(s) == "full")
    partial = sum(1 for s in seed if _near_tier(s) == "partial")
    pair = sum(1 for s in seed if _near_tier(s) == "pair")
    near = sum(1 for s in seed if _near_tier(s) == "near")
    no = sum(1 for s in seed if _near_tier(s) == "no")
    return total, full, partial, pair, near, no


_FRAME_CHECK_NOTE = """## Frame check (orchestrator, 2026-09-16)

6 disagreements were eyeballed by hand. In 5 the model named the position actually VISIBLE in \
the frame and the corpus label simply is not in it (Mount → Side Control ×2, RNC → front \
headlock/side, IDLR → top scramble, Wrist Lock → closed guard) -- corpus timestamp \
misalignment still dominates even after the ±30s stage-A window. Stage A itself said \
`visible: yes` on 52/64 candidates, yet the label is often not there once checked: stage A is \
lenient, not a reliable gate on its own."""


# ── report ───────────────────────────────────────────────────────────────────────
def build_report(seed: list[dict[str, Any]], counts: dict[str, int],
                 curated: list[dict[str, Any]], *,
                 alignment: dict[str, Any] | None = None,
                 before_seed: list[dict[str, Any]] | None = None,
                 preverify_summary: dict[str, Any] | None = None) -> str:
    """``alignment`` (optional): ``{"misaligned": int, "of_total": int}`` -- how many of a
    PREVIOUS batch's candidates sat on a match whose DB ``ts_origin`` flag disagreed with the
    evidence-based reclassification (see ``ts_class_matches_flag``), written once by `plan`
    right before it overwrites that previous batch. ``before_seed`` (optional): that previous
    batch's own graded answers, for an agreement before/after comparison. ``preverify_summary``
    (optional): ``build_preverify_summary``'s own output (verdicts/reasons/warnings), so the
    report can show how many candidates never reached Gemini at all."""
    per_tech: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in seed:
        per_tech[str(s["corpus_label"])].append(s)

    total, full, partial, no = _agreement_counts(seed)

    lines = ["# Gemini dictionary-seed report", "",
             f"Total candidates asked: {total}",
             f"Agreement: full {full}, partial {partial}, no {no}"
             + (f" ({full / total:.0%} full)" if total else ""), ""]

    n_total, n_full, n_partial, n_pair, n_near, n_no = _near_counts(seed)
    lines += ["## Strict vs near agreement", "",
             "`agree` is the original strict rule (full/partial/no). `agree_near` "
             "(`score_agreement`, added 2026-09-16) inserts two tiers between partial and no: "
             "`pair` (item 32, 2026-09-16 — the candidate and the corpus label are a genuine "
             "double label on one frame, one action + one state, backed by real corpus "
             "adjacency / the declared exit-orientation table / a shared curated family — "
             "`_pair_rule`) checked first, then `near` (same curated family "
             "(`technique_library.json` `type`) or the state a corpus ACTION declares it "
             "lands in (`data/taxonomy/inference_table.json` `action_exit_orientation`)) -- "
             "every rule reuses a table already in the repo, no hand list of pairs.", "",
             "| | full | partial | pair | near | no | full % |",
             "|---|---|---|---|---|---|---|",
             f"| strict (`agree`) | {full} | {partial} | – | – | {no} | "
             + (f"{full / total:.0%} |" if total else "n/a |"),
             f"| with near (`agree_near`) | {n_full} | {n_partial} | {n_pair} | {n_near} | "
             f"{n_no} | "
             + (f"{n_full / n_total:.0%} |" if n_total else "n/a |"), ""]

    if preverify_summary:
        v = preverify_summary.get("verdicts", {})
        r = preverify_summary.get("reasons", {})
        w = preverify_summary.get("warnings", {})
        lines += ["## Preverify", "",
                 "Local, no-Gemini screen run before `ask` spends any call "
                 "(`scripts/dictionary_seed.py preverify`) -- a `skip` candidate is written "
                 "straight to `seed.jsonl` at zero cost, never sent to the model.", "",
                 f"ok: {v.get('ok', 0)} · skip: {v.get('skip', 0)} "
                 f"(of {preverify_summary.get('total', 0)} planned)", "",
                 "skip reasons: " + ", ".join(f"{k} {n}" for k, n in sorted(r.items())), ""]
        if r.get("missing_frames"):
            lines.append("- `missing_frames`: two ~2h event VODs whose frame extraction never "
                         "landed enough of the 9-frame strip")
        if r.get("ts_out_of_range"):
            lines.append("- `ts_out_of_range`: corpus timestamp past the video's own duration")
        if r.get("duplicate_frame"):
            lines.append("- `duplicate_frame`: same centre frame already used by an earlier "
                         "candidate")
        if w:
            lines += ["", "warnings (advisory only, never gate a verdict): "
                     + ", ".join(f"{k} {n}" for k, n in sorted(w.items()))]
        lines.append("")

    lines.append(_FRAME_CHECK_NOTE)
    lines.append("")

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

    per_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in seed:
        label = s.get("candidate_label")
        if label:
            per_candidate[str(label)].append(s)
    lines += ["## Per technique (by candidate — what the model saw)", "",
             "The table above groups rows by the CORPUS label (what `plan` searched for). "
             "This one groups the same rows by `candidate_label` (the model's own blind read) "
             "-- decision 2026-09-16 makes that the review target, so this is what a human "
             "actually sees on the sheet.", "",
             "| candidate label | rows | corpus agree full | partial | pair | near | no | "
             "review high/medium/low |",
             "|---|---|---|---|---|---|---|---|"]
    for label in sorted(per_candidate):
        c_rows = per_candidate[label]
        f_ = sum(1 for r in c_rows if _near_tier(r) == "full")
        p_ = sum(1 for r in c_rows if _near_tier(r) == "partial")
        pa_ = sum(1 for r in c_rows if _near_tier(r) == "pair")
        ne_ = sum(1 for r in c_rows if _near_tier(r) == "near")
        n_ = sum(1 for r in c_rows if _near_tier(r) == "no")
        hi = sum(1 for r in c_rows if r.get("review_confidence") == "high")
        me = sum(1 for r in c_rows if r.get("review_confidence") == "medium")
        lo = sum(1 for r in c_rows if r.get("review_confidence") == "low")
        lines.append(f"| {label} | {len(c_rows)} | {f_} | {p_} | {pa_} | {ne_} | {n_} | "
                     f"{hi}/{me}/{lo} |")
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

    sub.add_parser("preverify")

    p_ask = sub.add_parser("ask")
    p_ask.add_argument("--dry-run", action="store_true")
    p_ask.add_argument("--align", action="store_true",
                       help="opt-in second call per candidate: pick the frame that shows the "
                            "corpus label before the blind read (off by default since "
                            "2026-09-16 -- the blind read's own answer is the candidate now)")

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
        pair_support = build_pair_support(matches)
        PAIR_SUPPORT_PATH.write_text(json.dumps(pair_support, indent=2, sort_keys=True),
                                     encoding="utf-8")
        n_techs = len({r.node_key for r in plan})
        n_zero = sum(1 for t in curated if t.get("en") and counts.get(t["en"], 0) == 0)
        logger.info("plan: %d candidates across %d techniques (of %d curated; %d with zero "
                   "video-backed events); %d action/state adjacency pairs cached",
                   len(plan), n_techs, len(curated), n_zero, len(pair_support))
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

    if a.cmd == "preverify":
        plan_rows = read_jsonl(PLAN_PATH)
        if not plan_rows:
            logger.error("no %s -- run `plan` first", PLAN_PATH)
            return 1
        results = run_preverify(plan_rows)
        write_jsonl(results, PREVERIFY_PATH)
        summary = build_preverify_summary(results)
        PREVERIFY_SUMMARY_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True),
                                          encoding="utf-8")
        sheets = write_sheets(results, OUT_DIR)
        logger.info("preverify: %d ok, %d skip %s (warnings %s) -- sheets: %s",
                   summary["verdicts"].get("ok", 0), summary["verdicts"].get("skip", 0),
                   summary["reasons"], summary["warnings"], [str(p) for p in sheets])
        return 0

    if a.cmd == "ask":
        plan_rows = read_jsonl(PLAN_PATH)
        if not plan_rows:
            logger.error("no %s -- run `plan` first", PLAN_PATH)
            return 1
        existing_seed = read_jsonl(SEED_PATH)
        preverify_rows = read_jsonl(PREVERIFY_PATH)
        to_ask, skip_seed = partition_for_ask(plan_rows, existing_seed, preverify_rows)
        dry = a.dry_run or not os.environ.get("GEMINI_API_KEY")
        asked = run_ask(to_ask, dry_run=dry, align=a.align) if to_ask else []
        seed = existing_seed + skip_seed + asked
        write_jsonl(seed, SEED_PATH)
        logger.info("ask: %d asked, %d preverify-skipped, %d already done (dry_run=%s align=%s)",
                   len(asked), len(skip_seed), len(existing_seed), dry, a.align)
        return 0

    if a.cmd == "report":
        seed = regrade_seed_near(read_jsonl(SEED_PATH))
        seed = regrade_seed_review(seed)
        write_jsonl(seed, SEED_PATH)   # persist agree_near/candidate fields -- report re-runs often, ask does not
        counts = (json.loads(PLAN_COUNTS_PATH.read_text(encoding="utf-8"))
                 if PLAN_COUNTS_PATH.exists() else {})
        curated = load_curated()
        alignment = (json.loads(ALIGNMENT_PATH.read_text(encoding="utf-8"))
                    if ALIGNMENT_PATH.exists() else None)
        before_seed = read_jsonl(BEFORE_SEED_PATH) if BEFORE_SEED_PATH.exists() else None
        preverify_summary = (json.loads(PREVERIFY_SUMMARY_PATH.read_text(encoding="utf-8"))
                             if PREVERIFY_SUMMARY_PATH.exists() else None)
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            build_report(seed, counts, curated, alignment=alignment, before_seed=before_seed,
                        preverify_summary=preverify_summary),
            encoding="utf-8")
        logger.info("wrote %s", REPORT_PATH)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
