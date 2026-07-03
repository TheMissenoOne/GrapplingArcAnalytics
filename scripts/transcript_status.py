#!/usr/bin/env python
"""Reconcile the transcripts folder and report per-stem status.

Shows, for every event stem, whether it has: a transcript (.txt), a dump (.py), a queued
transcript, a mapped video, a slot in ``reprocess_all.DATASETS`` (pipeline), and a DB match.
Orphans = transcripts with no .py dump — the Deepseek queue.

Usage:
    uv run python -m scripts.transcript_status                  # report only
    uv run python -m scripts.transcript_status --sync           # move orphans → queue/
    uv run python -m scripts.transcript_status --emit-prompts   # write deepseek/<stem>.md
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRANSCRIPTS_DIR = Path(__file__).resolve().parents[1] / "transcripts"
QUEUE_DIR = TRANSCRIPTS_DIR / "queue"


def load_url_mapping() -> dict[str, Any]:
    """Load url_mapping.json for video_url and seconds data."""
    mapping_path = Path(__file__).resolve().parents[1] / "url_mapping.json"
    if mapping_path.exists():
        try:
            return json.loads(mapping_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not load url_mapping.json: %s", e)
    return {}


def pipeline_labels() -> set[str]:
    """Event labels wired into ``reprocess_all.DATASETS`` (i.e. imported into the DB).

    A transcript stem present here is fully in the pipeline: dump → *_data.py → DB.
    """
    try:
        from scripts.reprocess_all import DATASETS

        return {label for _mod, _event, label in DATASETS}
    except Exception as e:  # importer edited / renamed — degrade, don't crash the report
        logger.warning("Could not load reprocess_all.DATASETS: %s", e)
        return set()


def db_event_tags() -> set[str]:
    """Distinct ``Match.event`` tags present in the DB (empty if the DB is unreachable).

    A stem is "in DB" when its event tag (via DATASETS / url_mapping) appears here — so the
    column reflects real imported matches, not a per-stem key that could never match.
    """
    import os

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    if not os.environ.get("DATABASE_URL"):
        logger.warning("DATABASE_URL not set — DB column will be blank")
        return set()
    try:
        from sqlalchemy import select

        from db.base import db_session
        from db.models import Match

        with db_session() as session:
            rows = session.execute(
                select(Match.event).where(Match.status == "final", Match.event.isnot(None))
            ).scalars()
            return {str(e) for e in rows}
    except Exception as e:
        logger.warning("Could not query DB: %s", e)
        return set()


def _dataset_events() -> dict[str, str]:
    """stem/label → event tag from ``reprocess_all.DATASETS`` (for the in-DB cross-ref)."""
    try:
        from scripts.reprocess_all import DATASETS

        return {label: event for _mod, event, label in DATASETS if event}
    except Exception:
        return {}


def reconcile() -> dict[str, Any]:
    """Scan transcripts/ and generate manifest status."""
    if not TRANSCRIPTS_DIR.exists():
        logger.warning("transcripts/ directory not found; creating structure...")
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    url_mapping = load_url_mapping()
    db_events = db_event_tags()
    dataset_events = _dataset_events()
    pipeline = pipeline_labels()

    # Scan for .txt and .py files
    txt_files = {f.stem: f for f in TRANSCRIPTS_DIR.glob("*.txt")}
    py_files = {f.stem: f for f in TRANSCRIPTS_DIR.glob("*.py")}
    queue_files = {f.stem: f for f in QUEUE_DIR.glob("*.txt")}

    manifest: dict[str, dict[str, Any]] = {}
    orphans: list[str] = []

    # Process all stems (union of txt, py, queue, url_mapping)
    all_stems = (
        set(txt_files.keys()) | set(py_files.keys()) | set(queue_files.keys())
        | set(url_mapping.keys())
    )

    for stem in sorted(all_stems):
        entry = {
            "txt": stem in txt_files,
            "py": stem in py_files,
            "queue": stem in queue_files,
        }

        if stem in url_mapping:
            mapping = url_mapping[stem]
            entry["video_url"] = mapping.get("video_url")
            entry["event"] = mapping.get("event_title")
            # Count matches in the mapping
            entry["match_count"] = len(mapping.get("matches", []))

        # In DB when this stem's event tag (from DATASETS or url_mapping) has final matches.
        event_tag = dataset_events.get(stem) or (url_mapping.get(stem) or {}).get("event_title")
        entry["in_db"] = bool(event_tag and event_tag in db_events)
        entry["in_pipeline"] = stem in pipeline  # wired into reprocess_all.DATASETS

        manifest[stem] = entry

        # Orphan = a transcript (main dir or queue/) with no .py dump → needs Deepseek.
        if (entry["txt"] or entry["queue"]) and not entry["py"]:
            orphans.append(stem)

    return {"manifest": manifest, "orphans": orphans}


def sync_queue(data: dict[str, Any]) -> list[str]:
    """Move orphan transcripts sitting in the main dir into ``queue/`` (plan: queue/ holds
    exactly the un-dumped transcripts). Returns the stems relocated."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for stem in data["orphans"]:
        src = TRANSCRIPTS_DIR / f"{stem}.txt"
        dst = QUEUE_DIR / f"{stem}.txt"
        if src.exists() and not dst.exists():
            src.rename(dst)
            moved.append(stem)
    return moved


_PROMPT = """\
# Deepseek task — produce the match dump for `{stem}`

You are converting a YouTube auto-caption transcript of a grappling/MMA event into a
Python match dump, following the GrapplingArc pipeline in `TRANSCRIPT_PROCESSING.md`.

## Inputs (attach when running)
- Transcript: `transcripts/queue/{stem}.txt`
- Schema + rules: `TRANSCRIPT_PROCESSING.md`
- A sample completed dump for reference: `transcripts/CJI.py`
{video_line}

## Output
A single file `transcripts/{stem}.py` — one dict literal keyed by `("Athlete Name", year)`,
each value `{{ "winner", "method", "opponent", "start", "events": [...] }}`.

## Hard requirements
1. **Keep timestamps.** Every event MUST carry a `"timestamp"` field (`M:SS` or `H:MM:SS`,
   the absolute position in the video). The pipeline turns these into `ts` seconds so the
   site can seek the player to each position — a dump without timestamps loses that.
2. Event schema: `{{"label", "type", "actor", "timestamp", "successful"?}}`.
   `type` ∈ submission|pass|control|guard|escape|sweep|transition|takedown.
   `actor` = the fighter's full name (matches one side of the bout key).
3. Ref block at the top of the transcript = source of truth for the match list + start times.
   Transcript body = source of truth for the event sequence. Only web-search for winners/methods.
4. One entry per bout. Disambiguate duplicate fighter keys with ` [start]` suffix (last resort).
5. `True`/`False` (Python booleans), not `true`/`false`.

## After producing it
The maintainer will: `uv run python scripts/convert_dump.py transcripts/{stem}.py {slug}`,
add it to `scripts/reprocess_all.DATASETS`, then `uv run python -m scripts.reprocess_all`.
Flag anything ambiguous (unnamed opponents, unclear winners, missing timestamps) inline.
"""


def emit_prompts(data: dict[str, Any]) -> list[str]:
    """Write one Deepseek prompt per orphan transcript → ``transcripts/deepseek/<stem>.md``."""
    out_dir = TRANSCRIPTS_DIR / "deepseek"
    out_dir.mkdir(parents=True, exist_ok=True)
    url_mapping = load_url_mapping()
    written: list[str] = []
    for stem in data["orphans"]:
        vid = (url_mapping.get(stem) or {}).get("video_url")
        video_line = (f"- Video: {vid}" if vid
                      else "- Video: (none mapped — add to url_mapping.json if known)")
        slug = stem.lower().replace(" ", "_").replace("-", "_")
        (out_dir / f"{stem}.md").write_text(
            _PROMPT.format(stem=stem, slug=slug, video_line=video_line), encoding="utf-8"
        )
        written.append(stem)
    return written


def print_status(data: dict[str, Any]) -> None:
    """Print a human-readable status table."""
    manifest = data["manifest"]
    orphans = data["orphans"]

    width = 74
    print("\n" + "=" * width)
    print("Transcript Status Report")
    print("=" * width)
    print(f"{'Stem':<38} {'TXT':<4}{'PY':<4}{'QUE':<4}{'VID':<4}{'PIPE':<5}{'DB':<4}")
    print("-" * width)

    def mark(v: Any) -> str:
        return "✓" if v else "·"

    for stem in sorted(manifest.keys()):
        e = manifest[stem]
        s = stem if len(stem) <= 37 else stem[:36] + "…"
        print(
            f"{s:<38} {mark(e['txt']):<4}{mark(e['py']):<4}{mark(e['queue']):<4}"
            f"{mark(e.get('video_url')):<4}{mark(e.get('in_pipeline')):<5}{mark(e.get('in_db')):<4}"
        )

    m = manifest.values()
    print("-" * width)
    print(
        f"Total {len(manifest)} stems · {sum(1 for e in m if e['txt'])} transcripts · "
        f"{sum(1 for e in m if e['py'])} dumps · {sum(1 for e in m if e.get('in_pipeline'))} "
        f"in pipeline · {len(orphans)} queued"
    )
    print("Columns: TXT dump·PY dump·QUEue orphan·VIDeo mapped·PIPEline (reprocess_all)·in DB")

    if orphans:
        print("\nQueue (orphan transcripts → transcripts/deepseek/<stem>.md prompts):")
        for stem in orphans:
            print(f"  - {stem}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Reconcile transcripts folder and generate manifest")
    ap.add_argument("--out", type=Path, default=TRANSCRIPTS_DIR / "manifest.json",
                    help="output manifest path")
    ap.add_argument("--sync", action="store_true",
                    help="move orphan transcripts from the main dir into queue/")
    ap.add_argument("--emit-prompts", action="store_true",
                    help="write a Deepseek prompt per orphan → transcripts/deepseek/<stem>.md")
    args = ap.parse_args()

    if args.sync:
        moved = sync_queue(reconcile())  # relocate first, then re-scan for an accurate report
        logger.info("Moved %d orphan transcript(s) into queue/", len(moved))

    data = reconcile()
    print_status(data)

    if args.emit_prompts:
        written = emit_prompts(data)
        logger.info("Wrote %d Deepseek prompt(s) → transcripts/deepseek/", len(written))

    # Write manifest.json
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Manifest written to %s", args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
