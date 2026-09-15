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
   (`--cookies-from-browser firefox`), seek the centre + `±2s` neighbour frames with ffmpeg,
   delete the video. Resume-safe (skips a candidate whose centre frame already exists).
3. **ask** — one Gemini (`gemini-pro-latest`, thinking high, temperature 0) call per candidate:
   the two athletes' names, the closed curated vocabulary, the three frames, no hint of the
   corpus label. Writes `seed.jsonl` — every candidate, agreement graded, never dropped.
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

    ``ts_origin='video_absolute'`` -> ``ts`` is already the answer. ``'bout_relative'`` ->
    add the bout's own start. Anything else (``None``/unrecognised) -> ``None``, never a
    guessed default -- a bout-relative timestamp misread as absolute silently mislocates
    every frame in a DIFFERENT fight (AA-010, see ``scripts/frame_pdf.py`` module docstring).
    """
    if ts is None:
        return None
    if ts_origin == "video_absolute":
        return float(ts)
    if ts_origin == "bout_relative":
        return float(video_start_seconds or 0) + float(ts)
    return None


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


def build_plan(matches: list[dict[str, Any]], curated: list[dict[str, Any]],
               per_technique: int = 6, min_events: int = 0,
               ) -> tuple[list[PlanRow], dict[str, int]]:
    """Pure: prod match rows (as ``_load_matches`` shapes them) + the curated library ->
    sampled plan rows + per-technique candidate counts BEFORE sampling (what ``report`` calls
    "video-backed events" -- how many existed, not how many were picked).
    """
    by_en: dict[str, list[dict[str, Any]]] = {t["en"]: [] for t in curated if t.get("en")}
    for m in matches:
        bout = _bout_slug(m["a_name"], m["b_name"], m["year"])
        for ev in m.get("sequence") or []:
            if not isinstance(ev, dict) or not ev.get("label"):
                continue
            cleaned = clean_label(str(ev["label"]), str(ev.get("type", "")))
            if cleaned not in by_en:
                continue
            ts = absolute_ts(m.get("video_start_seconds"), m.get("ts_origin"), ev.get("ts"))
            if ts is None:
                continue
            by_en[cleaned].append({
                "match_id": m["match_id"], "video_url": m["video_url"], "ts": ts,
                "type": str(ev.get("type", "")), "actor": ev.get("actor"), "bout": bout,
                "a_name": m["a_name"], "b_name": m["b_name"],
                "year": m.get("year"), "event": m.get("event"),
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


# ── extract ──────────────────────────────────────────────────────────────────────
def frame_paths(node_key: str, bout: str, ts_ms: int) -> dict[str, Path]:
    d = OUT_DIR / node_key
    base = f"{bout}__{ts_ms}"
    return {"center": d / f"{base}.jpg", "m2": d / f"{base}_m2.jpg", "p2": d / f"{base}_p2.jpg"}


def _ffmpeg_frame(video: Path, local_ts: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{max(local_ts, 0.0):.3f}",
         "-i", str(video), "-frames:v", "1", "-vf", "scale=1280:720", "-q:v", "3", str(out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning("ffmpeg failed at t=%.2f for %s: %s", local_ts, out,
                       (r.stderr or r.stdout).strip()[-300:])
        return False
    return True


def extract_match(rows: list[dict[str, Any]], tmp: Path) -> int:
    """One bout's own candidates -> frames on disk. Downloads the video ONCE (only the span
    the candidates need, padded), seeks per candidate, deletes the video after -- frames are
    the durable artefact here, the video is not (same archive policy as
    ``docs/frame_pdf_reading.md``). Returns how many centre frames exist on disk after this
    call (already-there ones count, for resumability)."""
    from scripts.frame_pdf import QUALITY, fetch

    video_url = rows[0]["video_url"]
    ts_list = [float(r["ts"]) for r in rows]
    start = max(0.0, min(ts_list) - 5)
    end = max(ts_list) + 5
    video = fetch(video_url, tmp, start, end, fmt=QUALITY["full_match"][1])
    written = 0
    try:
        for r in rows:
            paths = frame_paths(str(r["node_key"]), str(r["bout"]), int(r["ts_ms"]))
            if paths["center"].exists():
                written += 1
                continue
            local = float(r["ts"]) - start
            if _ffmpeg_frame(video, local, paths["center"]):
                written += 1
            _ffmpeg_frame(video, max(local - 2, 0.0), paths["m2"])
            _ffmpeg_frame(video, local + 2, paths["p2"])
    finally:
        video.unlink(missing_ok=True)
    return written


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


def ask_gemini(row: dict[str, Any], vocab_text: str, client: Any,
              model: str = MODEL) -> dict[str, Any]:
    from google.genai import errors, types

    from scripts.gemini_read_frames import build_generate_config, usage_totals

    paths = frame_paths(str(row["node_key"]), str(row["bout"]), int(row["ts_ms"]))
    parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg")
            for p in (paths["m2"], paths["center"], paths["p2"]) if p.exists()]
    parts.append(types.Part.from_text(text=build_prompt(row, vocab_text)))

    try:
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config("high", temperature=0))
    except errors.ClientError:
        resp = client.models.generate_content(
            model=model, contents=parts, config=build_generate_config(None, temperature=0))

    try:
        parsed = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    # response_mime_type=application/json guarantees valid JSON, not an OBJECT -- measured:
    # the model occasionally wraps its answer in a one-element array. First dict element
    # counts, anything else is "no answer" rather than an AttributeError mid-batch.
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    if not isinstance(parsed, dict):
        parsed = {}
    return {"raw": resp.text, "parsed": parsed, "usage": usage_totals(resp.usage_metadata)}


def run_ask(plan: list[dict[str, Any]], *, dry_run: bool = False) -> list[dict[str, Any]]:
    curated = load_curated()
    vocab = vocabulary_text(curated)
    client = None
    if not dry_run:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    seed: list[dict[str, Any]] = []
    for i, row in enumerate(plan, 1):
        logger.info("[%d/%d] %s (%s)", i, len(plan), row["node_key"], row["bout"])
        if client is None:
            answer: dict[str, Any] = {
                "parsed": {}, "usage": {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0},
            }
        else:
            answer = ask_gemini(row, vocab, client)
        agree, conf = score_agreement(str(row["node_key"]), answer["parsed"])
        seed.append({
            "node_key": row["node_key"], "bout": row["bout"], "ts_ms": row["ts_ms"],
            "frame": str(frame_paths(str(row["node_key"]), str(row["bout"]), int(row["ts_ms"]))
                        ["center"].relative_to(REPO)),
            "corpus_label": row["label"], "model_label": answer["parsed"].get("label"),
            "model_type": answer["parsed"].get("type"),
            "actor_role": answer["parsed"].get("actor_role"),
            "model_confidence": answer["parsed"].get("confidence"),
            "reason": answer["parsed"].get("reason"),
            "agree": agree, "review_confidence": conf, "usage": answer["usage"],
        })
    return seed


# ── report ───────────────────────────────────────────────────────────────────────
def build_report(seed: list[dict[str, Any]], counts: dict[str, int],
                 curated: list[dict[str, Any]]) -> str:
    per_tech: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in seed:
        per_tech[str(s["corpus_label"])].append(s)

    total = len(seed)
    full = sum(1 for s in seed if s["agree"] == "full")
    partial = sum(1 for s in seed if s["agree"] == "partial")
    no = sum(1 for s in seed if s["agree"] == "no")

    lines = ["# Gemini dictionary-seed report", "",
             f"Total candidates asked: {total}",
             f"Agreement: full {full}, partial {partial}, no {no}"
             + (f" ({full / total:.0%} full)" if total else ""), ""]

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
        matches = _load_matches()
        curated = prioritize_curated(load_curated(), load_coverage())
        plan, counts = build_plan(matches, curated, per_technique=a.per_technique,
                                  min_events=a.min_events)
        plan = cap_plan(plan, a.max_calls)
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
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(build_report(seed, counts, curated), encoding="utf-8")
        logger.info("wrote %s", REPORT_PATH)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
