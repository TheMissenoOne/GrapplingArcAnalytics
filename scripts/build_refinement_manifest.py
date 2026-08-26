#!/usr/bin/env python
"""Build the frame_pdf.py REFINEMENT manifest: ADCC/IBJJF POINTS-or-DECISION matches that
have video but no scoring detail in their `sequence` -- candidates to reprocess through the
hybrid frame-read flow (docs/hybrid_reprocessing_plan.md) and enrich with points/advantages.

    uv run python scripts/build_refinement_manifest.py

Selection (read-only against prod `matches`):

    family(event) in {adcc, ibjjf}          -- see `family_of()`; a minimal placeholder
                                                classifier, not the real event->family map
                                                analysis/ruleset_scoring.py is expected to own
                                                (that file did not exist on disk when this was
                                                written -- reconcile once it lands)
    win_type in ('POINTS', 'DECISION')      -- the two outcomes that imply scoring happened
    video_url is not null                   -- nothing to render without footage
    no event in `sequence` carries 'points' -- the actual "missing score info" test
    not already frame-read this cycle       -- ALREADY_FRAMED_EVENTS / ALREADY_FRAMED_SINGLES

Per selected match, one `full_match` frame_pdf.py Entry: url=video_url, start=video_start_seconds
(or 0 for a single-bout upload with no recorded start; a null-start SHARED url is excluded --
see `resolve_start()`), end=start+BOUT_WINDOW_SECONDS (no `matches` column carries a bout end),
transcript attached when exactly one file under `transcripts/` unambiguously matches the event
name (`resolve_transcript()` -- ambiguous or no match leaves it unattached, counted honestly).

Writes `data/frame_pdf/refinamento_manifest.json` and prints the selection census (per family:
total qualifying / with video / with start / with transcript / excluded-as-already-framed)
before anything is rendered -- rendering is a separate `frame_pdf.py --manifest ...` run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.names import athlete_key  # noqa: E402

OUT = REPO / "data" / "frame_pdf" / "refinamento_manifest.json"
TRANSCRIPTS_DIR = REPO / "transcripts"

# `matches` carries no bout-end column (checked: db/models.py Match has no such field), so the
# window is a flat, generous default rather than a probed value.
BOUT_WINDOW_SECONDS = 900

# Events already frame-read this cycle -- wholesale exclusion, visible constant per the ticket.
ALREADY_FRAMED_EVENTS: frozenset[str] = frozenset({
    "ADCC Trials 2023 European", "ADCC Trials 2023 Asia & Oceania",
    "ADCC Trials 2024 South American 1", "ADCC Trials 2024 South American 2",
    "ADCC Trials 2024 European", "ADCC Trials 2024 Asia & Oceania",
    "European No-Gi 2024", "World No-Gi 2024", "Pan No-Gi 2024",
    "World No-Gi 2025", "European No-Gi 2025",
})

# Individually audited bouts inside events NOT wholesale-excluded above (Pan No-Gi 2025 and
# Polaris 36 each carry exactly one concordance-audited frame reading -- see
# scripts/dumps/pan_nogi2025_frames2_data.py and scripts/dumps/polaris36_frames_data.py, both
# `docs/gemini_concordance_audit.md` output). Keyed by athlete pair (order-independent) + the
# `year` the dump literal actually carries.
ALREADY_FRAMED_SINGLES: frozenset[tuple[frozenset[str], int | None]] = frozenset({
    (frozenset({athlete_key("Anabel Lopez"), athlete_key("Erin Harpe")}), 2025),
    (frozenset({athlete_key("Anabel Lopez"), athlete_key("Kendall Reusing")}), 2026),
})


def family_of(event: str | None) -> str | None:
    """ponytail: inline placeholder classifier -- another agent is building the real
    event->family map in analysis/ruleset_scoring.py concurrently; that module did not exist
    on disk when this was written. Reconcile once it lands; this stays literal per the ticket:
    ADCC = 'adcc' or 'cji' substring, IBJJF = worlds/pan/euro/no-gi/ibjjf substring."""
    ev = (event or "").lower()
    if "adcc" in ev or "cji" in ev:
        return "adcc"
    if any(k in ev for k in ("worlds", "pan", "euro", "no-gi", "nogi", "ibjjf")):
        return "ibjjf"
    return None


def has_score_info(sequence: list[dict[str, Any]] | None) -> bool:
    """True when at least one event already carries a `points` value -- the actual test for
    "this match has scoring detail", not just a win_type guess."""
    return sequence is not None and any("points" in e for e in sequence)


def already_framed(event: str | None, athlete_a: str, athlete_b: str, year: int | None) -> bool:
    if event in ALREADY_FRAMED_EVENTS:
        return True
    key = (frozenset({athlete_key(athlete_a), athlete_key(athlete_b)}), year)
    return key in ALREADY_FRAMED_SINGLES


# ── transcript matching ──────────────────────────────────────────────────────────
# Folds both an event name ("ADCC Trials 2023 East Coast") and a transcript filename stem
# ("ADCCTrials2023EastCoastFinals") into the same token set so a spacing/casing difference
# never blocks a match; a filename is a candidate only when it carries EVERY event token.
def _tokens(s: str) -> set[str]:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    s = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", s)
    s = re.sub(r"(?<=[0-9])(?=[A-Za-z])", " ", s)
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def transcript_candidates(event: str, filenames: list[str]) -> list[str]:
    """`filenames` = transcript file stems (no dir, no extension). A filename matches when it
    carries every token the event name does; a stray 'trials' distinguishes e.g. 'ADCC 2022'
    (the base event) from 'ADCCTrials2022SouthAmericaFinals' (a different event that happens
    to be a token superset otherwise) -- guarded explicitly since it is the one collision
    measured in this corpus (2026-08-25)."""
    et = _tokens(event)
    out = []
    for f in filenames:
        ft = _tokens(f)
        if not et <= ft:
            continue
        if "trials" in ft and "trials" not in et:
            continue
        out.append(f)
    return out


def resolve_transcript(event: str, transcripts_dir: Path = TRANSCRIPTS_DIR) -> str | None:
    """None on no match OR an ambiguous one (>1 candidate) -- both are "not attached", the
    caller counts them separately for the coverage report."""
    if not transcripts_dir.is_dir():
        return None
    stems = [p.stem for p in transcripts_dir.glob("*.txt")]
    cands = transcript_candidates(event, stems)
    return str(transcripts_dir / f"{cands[0]}.txt") if len(cands) == 1 else None


# ── start/end ─────────────────────────────────────────────────────────────────────
def resolve_start(video_start_seconds: int | None, video_url: str, url_counts: Counter[str]
                   ) -> tuple[float | None, str]:
    """(start, reason). A non-null DB start always wins. A null start is only safe to default
    to 0 when nothing else in the corpus shares this exact video_url -- a shared url means a
    multi-bout upload, and rendering a full multi-hour reel from t=0 with no window is the
    thing frame_pdf.py's own DEFAULT_MAX_FRAMES ceiling exists to prevent (§ the entry's step
    would blow up trying to fit hours in DEFAULT_MAX_FRAMES frames)."""
    if video_start_seconds is not None:
        return float(video_start_seconds), "db_start"
    if url_counts[video_url] <= 1:
        return 0.0, "single_bout_url_defaulted"
    return None, "shared_url_no_start"


def build_entry(row: dict[str, Any], url_counts: Counter[str],
                 transcripts_dir: Path = TRANSCRIPTS_DIR) -> tuple[dict[str, Any] | None, str]:
    """One row (already filtered to family+win_type+video+missing-score+not-already-framed) ->
    (manifest entry or None, reason). None only for `resolve_start`'s shared-url-no-start case."""
    start, reason = resolve_start(row["video_start_seconds"], row["video_url"], url_counts)
    if start is None:
        return None, reason
    end = start + BOUT_WINDOW_SECONDS
    transcript = resolve_transcript(row["event"] or "", transcripts_dir)
    n_events = len(row["sequence"]) if row["sequence"] else 0
    note = f"{row['family']} · {row['win_type']} · {row['event']} · sequence={n_events} events"
    entry = {
        "url": row["video_url"],
        "start": start,
        "end": end,
        "label": f"{row['athlete_a']} vs {row['athlete_b']}",
        "kind": "full_match",
        "note": note,
    }
    if transcript:
        entry["transcript"] = transcript
    return entry, reason


# ── DB ────────────────────────────────────────────────────────────────────────────
def fetch_candidates() -> list[dict[str, Any]]:
    """Prod, read-only. Deliberately not `db_session()` (commits on clean exit) -- nothing
    here has any business writing."""
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    from sqlalchemy import text

    from db.base import get_engine

    with get_engine().connect() as c:
        rows = c.execute(text("""
            select m.event, m.year, m.win_type, m.video_url, m.video_start_seconds,
                   m.sequence, a.name, b.name
              from matches m
              join athletes a on a.id = m.athlete_a_id
              join athletes b on b.id = m.athlete_b_id
             where m.status = 'final'
               and m.win_type in ('POINTS', 'DECISION')
        """)).fetchall()

    out = []
    for r in rows:
        event, year, win_type, video_url, start, sequence, a_name, b_name = r
        fam = family_of(event)
        if fam is None:
            continue
        out.append({
            "event": event, "year": year, "win_type": win_type, "video_url": video_url,
            "video_start_seconds": start, "sequence": sequence,
            "athlete_a": a_name, "athlete_b": b_name, "family": fam,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()

    rows = fetch_candidates()
    # Qualifying = the owner's own definition: family + win_type that implies scoring, and no
    # score actually recorded yet. Everything downstream funnels from here, in the order asked
    # for: qualifying -> excluded (already framed) -> with video -> with start -> with transcript.
    qualifying = [r for r in rows if not has_score_info(r["sequence"])]

    census: dict[str, Counter[str]] = {fam: Counter() for fam in ("adcc", "ibjjf")}
    entries: list[dict[str, Any]] = []
    url_counts: Counter[str] = Counter(r["video_url"] for r in qualifying if r["video_url"])

    for r in qualifying:
        fam = r["family"]
        census[fam]["qualifying"] += 1
        if already_framed(r["event"], r["athlete_a"], r["athlete_b"], r["year"]):
            census[fam]["excluded_already_framed"] += 1
            continue
        if not r["video_url"]:
            continue
        census[fam]["with_video"] += 1
        entry, reason = build_entry(r, url_counts)
        if entry is None:
            census[fam][f"no_entry_{reason}"] += 1
            continue
        census[fam]["with_start"] += 1
        if "transcript" in entry:
            census[fam]["with_transcript"] += 1
        entries.append(entry)

    print(f"{'family':<8} {'qualifying':<12} {'with_video':<12} {'with_start':<12} "
          f"{'with_transcript':<16} {'excl_already_framed':<20}")
    for fam in ("adcc", "ibjjf"):
        c = census[fam]
        print(f"{fam:<8} {c['qualifying']:<12} {c['with_video']:<12} {c['with_start']:<12} "
              f"{c['with_transcript']:<16} {c['excluded_already_framed']:<20}")

    doc = {
        "_comment": [
            "Refinement manifest: ADCC/IBJJF POINTS-or-DECISION matches with video and no "
            "scoring detail in `sequence`, for hybrid frame-read reprocessing "
            "(docs/hybrid_reprocessing_plan.md).",
            f"{len(entries)} entries. Generated by scripts/build_refinement_manifest.py -- "
            "do not hand-edit; regenerate.",
        ],
        "videos": entries,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {a.out}: {len(entries)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
