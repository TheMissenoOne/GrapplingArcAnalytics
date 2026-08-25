"""ADCC Trials 2023-24 concordance-audited frame answers -> six importable dumps, one per
event tag (repo convention: one dump module per event, see ``scripts/reprocess_all.py``).

Reuses ``scripts.frame_answer_to_dump`` (``--from-answers``/``--allow-audited``/``--exclude``,
added for this batch) to convert the 29 flat ``data/frame_pdf/trials_2023_24/answers/*.events.json``
files whose ``source`` carries "concordance-audited" and are NOT already in prod. The 12
slugs in ``answers/enrich_targets_exclude.json`` already exist as matches in prod and go
through a separate enrichment script, not this one.

On top of the generic converter, this script applies things specific to this batch, none of
which the generic converter can know:

- **Athlete-name map** -- these people already exist in prod under a different spelling; a
  key mismatch would mint a phantom athlete (failure-archaeology scar #2). Applied to
  athlete_a/athlete_b/opponent/winner/every event actor, globally (not just the slugs the
  name happens to originate from -- the same person appears as a winner in one bout and an
  opponent in another).
- **Event-name map** -- raw bout labels ("EU/Africa/ME Trials 2024 (Zagreb)") -> DB
  convention ("ADCC Trials 2024 European") -- and the mapped name is also the SPLIT KEY: each
  bout is written into the dump module for its own event, not one shared dump.
- **weight_class** (from each bout's own ``answers/raw/<slug>.json`` ``audit.division``) and
  **stage** ("Final" for all bouts except the one semifinal in this set) and a
  ``video_start_seconds`` fallback to ``audit.curated_start`` when the answer itself has no
  ``bout.bout_start_seconds`` (both from https://www.youtube.com/watch?v=pwGbW5GZgfc).

No database. Never opens a connection -- only reads ``answers/*.events.json`` +
``answers/raw/*.json`` and (with ``--write``) writes six plain ``.py`` dump literals.

    uv run python -m scripts.adcc_trials2324_frames_to_dump              # report only
    uv run python -m scripts.adcc_trials2324_frames_to_dump --write      # also write the dumps
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.frame_answer_to_dump import (  # noqa: E402
    DumpBlock,
    convert_all,
    print_report,
    render_dump,
)

ANSWERS = REPO / "data" / "frame_pdf" / "trials_2023_24" / "answers"
RAW_DIR = ANSWERS / "raw"
EXCLUDE_FILE = ANSWERS / "enrich_targets_exclude.json"
DUMPS_DIR = REPO / "scripts" / "dumps"
VIDEO_URL = "https://www.youtube.com/watch?v=pwGbW5GZgfc"

NAME_MAP = {
    "Dominic Mejia": "Dominic Mahia",
    "Amanda Leve": "Amanda Levy",
    "Nicky Ryan": "Nikki Ryan",
    "Daniel Manasoiu": "Dan Manasoiu",
    "Elionai Braz": "Eli Braz",
    "Jozef Chen": "Jozeph Chen",
    "Salla Simola": "Salli Simola",
    "Luiz Paulo": "Luis Paulo",
}

# (raw bout.event label) -> (DB-convention event tag). The mapped tag is also the split key:
# one dump module per event, per scripts/reprocess_all.py's DATASETS convention.
EVENT_MAP = {
    "EU/ME/Africa Trials 2023 (Warsaw)": "ADCC Trials 2023 European",
    "Asia & Oceania Trials 2023 (Singapore)": "ADCC Trials 2023 Asia & Oceania",
    "South American Trials 1 2024 (Belo Horizonte)": "ADCC Trials 2024 South American 1",
    "South American Trials 2 2024 (Sao Paulo)": "ADCC Trials 2024 South American 2",
    "EU/Africa/ME Trials 2024 (Zagreb)": "ADCC Trials 2024 European",
    "Asia & Oceania Trials 2 2024 (Bangkok)": "ADCC Trials 2024 Asia & Oceania",
}

# (event tag) -> dump module filename.
EVENT_MODULE = {
    "ADCC Trials 2023 European": "adcc_trials2023_european_frames_data.py",
    "ADCC Trials 2023 Asia & Oceania": "adcc_trials2023_asia_oceania_frames_data.py",
    "ADCC Trials 2024 South American 1": "adcc_trials2024_sa1_frames_data.py",
    "ADCC Trials 2024 South American 2": "adcc_trials2024_sa2_frames_data.py",
    "ADCC Trials 2024 European": "adcc_trials2024_european_frames_data.py",
    "ADCC Trials 2024 Asia & Oceania": "adcc_trials2024_asia_oceania_frames_data.py",
}

# The raw bout notes for this one call it a semifinal; every other bout in this set is a
# division final.
SEMIFINAL_SLUGS = {"aurelie-le-vern-vs-nadia-frankland"}


def _map_name(name: str) -> str:
    return NAME_MAP.get(name, name)


def _fixup(slug: str, block: DumpBlock) -> DumpBlock:
    """Batch-specific post-processing over one converted block: name map, event map,
    weight_class from the raw audit file, stage, video_start_seconds fallback."""
    audit_path = RAW_DIR / f"{slug}.json"
    audit: dict[str, Any] = {}
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8")).get("audit") or {}

    out: DumpBlock = {}
    for (a_name, year), m in block.items():
        m = dict(m)
        m["opponent"] = _map_name(str(m.get("opponent") or ""))
        if m.get("winner"):
            m["winner"] = _map_name(str(m["winner"]))
        m["event"] = EVENT_MAP.get(str(m.get("event") or ""), m.get("event"))
        m["weight_class"] = audit.get("division", "")
        m["stage"] = "Semifinal" if slug in SEMIFINAL_SLUGS else "Final"
        if m.get("video_start_seconds") is None:
            m["video_start_seconds"] = audit.get("curated_start")
        m["events"] = [{**e, "actor": _map_name(str(e["actor"]))} for e in m["events"]]
        out[(_map_name(a_name), year)] = m
    return out


def build() -> tuple[list[Any], dict[str, list[DumpBlock]]]:
    """Convert + fix up every non-excluded audited answer, then bucket the resulting blocks
    by their (mapped) event tag -- one bucket per dump module."""
    exclude = set(json.loads(EXCLUDE_FILE.read_text(encoding="utf-8")))
    reports, blocks = convert_all(
        from_answers=ANSWERS, allow_audited=True, exclude=exclude
    )
    # blocks is parallel to the converted-only subset of reports, in visit order.
    converted_slugs = [r.slug for r in reports if r.status == "converted"]
    fixed = [_fixup(slug, block) for slug, block in zip(converted_slugs, blocks, strict=True)]

    by_event: dict[str, list[DumpBlock]] = {event: [] for event in EVENT_MODULE}
    for block in fixed:
        event = str(next(iter(block.values()))["event"])
        by_event.setdefault(event, []).append(block)
    return reports, by_event


def _header(event: str) -> str:
    return (
        f'"""ADCC Trials frame-read match dump -- {event} (concordance-audited).\n\n'
        "Generated from concordance-audited frame readings under "
        "data/frame_pdf/trials_2023_24/answers/ (procedure: docs/gemini_concordance_audit.md) "
        "by scripts/adcc_trials2324_frames_to_dump.py, via "
        "scripts/frame_answer_to_dump.py's convert_all/render_dump; keyed by "
        f"(athlete_a_name, year). Event tag: {event!r}.\n"
        'Do not edit by hand."""\n'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                     help="write the six dump modules to scripts/dumps/")
    a = ap.parse_args()

    reports, by_event = build()
    print_report(reports)

    for event, blocks in by_event.items():
        module = EVENT_MODULE[event]
        total_events = sum(len(next(iter(b.values()))["events"]) for b in blocks)
        print(f"\n{event}: {len(blocks)} bout(s), {total_events} event(s) -> {module}")
        if a.write and blocks:
            (DUMPS_DIR / module).write_text(
                render_dump(blocks, header=_header(event)), encoding="utf-8"
            )

    total_bouts = sum(len(b) for b in by_event.values())
    total_events = sum(
        len(next(iter(block.values()))["events"]) for blocks in by_event.values()
        for block in blocks
    )
    print(f"\n{total_bouts} bout(s), {total_events} event(s) total across "
          f"{len(EVENT_MODULE)} modules")
    print("wrote all modules" if a.write else "dry run -- pass --write to produce the dumps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
