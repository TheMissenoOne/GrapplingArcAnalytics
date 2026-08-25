"""Convert reviewed frame-read answers into the dump shape ``scripts/dump_import`` expects.

Step 7 of the frame-reading pipeline (``docs/frame_pdf_reading.md`` §1, §4.7) -- the missing
link between a human-reviewed ``data/frame_pdf/out/<slug>/events.json`` and an importable dump.

**Review gate.** Only files whose ``source`` field carries "human review" convert -- the
stamp ``frame_registrar (human review over model reading)`` written by
``scripts/frame_registrar.py:stamp_source``. A raw model reading
(``frame_answer_import (returned reading, not yet human-reviewed)``) is skipped, not
converted; the report says why. As of 2026-08-24 every file under ``out/`` carries the
unreviewed stamp, so a run today converts zero files -- that is the gate working, not a bug.

**Never guesses an identity.** Every event's ``actor``, and the bout's ``winner``, are
resolved via ``analysis.names.athlete_key`` against ONLY the bout's own two competitors
(``bout.athlete_a``/``athlete_b`` in the same file). A name that matches neither is REFUSED
-- excluded from the output, counted, and listed in the report -- never guessed onto one side.

**Never mangles a slash label.** ``analysis.names._normalize_name`` collapses
"Reset / Stalemate" and "Reset/Stalemate" to different keys depending on whitespace around
the slash -- a known defect in the shared node-key contract this converter does not attempt
to route around. A label containing "/" is refused (reason ``slash_label``), not passed
through to mint a wrong node.

**Never writes 0.** Output events keep exactly the six keys ``dump_import``'s importer
consumes -- ``label``/``type``/``actor``/``ts``/``successful``/``points`` -- and drop
whatever else the frame-reading schema allowed (``confidence``/``note``/``new_label``); each
file's report counts how many keys were dropped.

**No database.** This module never imports ``db.*`` and never opens a connection. It only
reads ``events.json`` files and (with ``--write``) writes a plain ``.py`` dump literal.

    uv run python -m scripts.frame_answer_to_dump              # report only (default)
    uv run python -m scripts.frame_answer_to_dump --dry-run    # same, explicit
    uv run python -m scripts.frame_answer_to_dump --write      # also write the dump file

``--from-answers <dir>`` reads flat ``<slug>.events.json`` files (e.g. concordance-audited
batches) instead of the default ``out/<slug>/events.json`` folders. ``--allow-audited``
additionally accepts a ``source`` containing "concordance-audited" -- WITHOUT it, audited
files are still refused (the default review gate is unchanged). ``--exclude <path>`` skips
the slugs listed in that JSON file (e.g. bouts already imported through another path).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.names import athlete_key  # noqa: E402

OUT = REPO / "data" / "frame_pdf" / "out"
DUMP_PATH = REPO / "scripts" / "dumps" / "frame_pdf_data.py"

# The exact whitelist scripts/dump_import + scripts/insert_ufc_matches._clean_events keep
# per event (mirrors scripts/insert_ufc_matches.py-style import). "actor" is resolved
# separately (see _resolve), everything else not in this set is dropped.
EVENT_KEEP = ("label", "type", "actor", "ts", "successful", "points")

REVIEWED_MARK = "human review"
# --allow-audited additionally accepts this stamp (frame_answer.py's concordance-audit
# provenance, e.g. "gemini reading, concordance-audited (kept N/M) 2026-08-25"). Without the
# flag these files are still refused -- the default gate is unchanged.
AUDITED_MARK = "concordance-audited"


@dataclass
class FileReport:
    slug: str
    status: str  # converted | skipped_unreviewed | invalid_json | no_bout
    reason: str = ""
    events_kept: int = 0
    refused: list[str] = field(default_factory=list)
    dropped_keys: int = 0


DumpBlock = dict[tuple[str, int | None], dict[str, Any]]


def _resolve(name: str, a_key: str, b_key: str, a_name: str, b_name: str) -> str | None:
    """The bout's own two competitors are the only valid resolution targets -- never a guess."""
    key = athlete_key(name)
    if key == a_key:
        return a_name
    if key == b_key:
        return b_name
    return None


def convert_file(
    path: Path, *, slug: str | None = None, allow_audited: bool = False
) -> tuple[FileReport, DumpBlock | None]:
    """One ``events.json`` -> (report, dump block) or (report, None) if not convertible.

    ``slug`` defaults to ``path.parent.name`` (the ``out/<slug>/events.json`` folder shape);
    pass it explicitly for a flat ``<slug>.events.json`` file (``--from-answers``)."""
    slug = slug if slug is not None else path.parent.name
    try:
        answer = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return FileReport(slug, "invalid_json", reason=str(exc)), None

    source = str(answer.get("source") or "")
    accepted = REVIEWED_MARK in source or (allow_audited and AUDITED_MARK in source)
    if not accepted:
        return FileReport(slug, "skipped_unreviewed", reason=source), None

    bout = answer.get("bout") or {}
    a_name = str(bout.get("athlete_a") or "").strip()
    b_name = str(bout.get("athlete_b") or "").strip()
    if not a_name or not b_name:
        return FileReport(slug, "no_bout", reason="bout.athlete_a/athlete_b missing"), None
    a_key, b_key = athlete_key(a_name), athlete_key(b_name)
    year = bout.get("year") if isinstance(bout.get("year"), int) else None

    events_out: list[dict[str, Any]] = []
    refused: list[str] = []
    dropped_keys = 0
    for i, e in enumerate(answer.get("events") or []):
        label = str(e.get("label") or "")
        if "/" in label:
            refused.append(f"events[{i}] {label!r}: slash_label")
            continue
        actor = _resolve(str(e.get("actor") or ""), a_key, b_key, a_name, b_name)
        if actor is None:
            refused.append(f"events[{i}] {label!r}: unresolved actor {e.get('actor')!r}")
            continue
        dropped_keys += sum(1 for k in e if k not in EVENT_KEEP)
        kept = {k: e[k] for k in EVENT_KEEP if k in e and k != "actor"}
        kept["actor"] = actor
        events_out.append(kept)

    winner_raw = str(bout.get("winner") or "").strip()
    winner = None
    if winner_raw:
        winner = _resolve(winner_raw, a_key, b_key, a_name, b_name)
        if winner is None:
            refused.append(f"bout.winner: unresolved winner {winner_raw!r}")

    m: dict[str, Any] = {
        "winner": winner,
        "method": bout.get("win_type") or "",
        "opponent": b_name,
        "event": bout.get("event") or "",
        "weight_class": "",
        "stage": "",
        "events": events_out,
        # The seam this converter exists to keep true: frame answers are read straight off
        # the video clock, so ``ts`` is already video-absolute, not bout-relative.
        "ts_origin": "video_absolute",
        "video_start_seconds": bout.get("bout_start_seconds"),
    }
    report = FileReport(slug, "converted", events_kept=len(events_out),
                         refused=refused, dropped_keys=dropped_keys)
    return report, {(a_name, year): m}


def convert_all(
    out_dir: Path = OUT,
    *,
    from_answers: Path | None = None,
    allow_audited: bool = False,
    exclude: set[str] | None = None,
) -> tuple[list[FileReport], list[DumpBlock]]:
    """Default: scan ``out_dir/<slug>/events.json`` folders. ``from_answers``: scan flat
    ``<slug>.events.json`` files in that directory instead (no per-folder frame lookups
    exist to degrade -- the only per-folder thing this module ever read was the slug
    itself, now passed explicitly). ``exclude`` skips slugs by name either way."""
    exclude = exclude or set()
    reports: list[FileReport] = []
    blocks: list[DumpBlock] = []

    if from_answers is not None:
        if not from_answers.exists():
            return reports, blocks
        for f in sorted(from_answers.glob("*.events.json")):
            slug = f.name[: -len(".events.json")]
            if slug in exclude:
                continue
            report, block = convert_file(f, slug=slug, allow_audited=allow_audited)
            reports.append(report)
            if block is not None:
                blocks.append(block)
        return reports, blocks

    if not out_dir.exists():
        return reports, blocks
    for d in sorted(p for p in out_dir.iterdir() if p.is_dir()):
        if d.name in exclude:
            continue
        f = d / "events.json"
        if not f.exists():
            continue
        report, block = convert_file(f, allow_audited=allow_audited)
        reports.append(report)
        if block is not None:
            blocks.append(block)
    return reports, blocks


def render_dump(blocks: list[DumpBlock], *, header: str | None = None) -> str:
    """Consolidated ``.py`` literal matching the shape ``scripts/dumps/*_data.py`` already
    uses -- a single ``RAW`` list holding one dict of every converted ``(athlete_a_name,
    year)`` block, which is what ``scripts.dump_import.build_matches`` consumes.

    ``header``, if given, replaces the default module docstring verbatim (a caller-supplied
    triple-quoted string ending in ``\\n``) -- lets a batch-specific caller (e.g. a
    concordance-audited event split) document its own provenance instead of the generic
    "human-reviewed .../out/<slug>/events.json" text, which would be wrong for it."""
    merged: dict[tuple[str, int | None], dict[str, Any]] = {}
    for b in blocks:
        merged.update(b)
    doc = header if header is not None else (
        '"""Frame-read match dump -- reviewed events converted for import.\n\n'
        "Generated from human-reviewed data/frame_pdf/out/<slug>/events.json by "
        "scripts/frame_answer_to_dump.py; keyed by (athlete_a_name, year).\n"
        'Do not edit by hand."""\n'
    )
    return (
        doc
        + "# ruff: noqa: E501  (single-line serialized data literal)\n\n"
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n"
        f"RAW: list[dict[tuple[str, int | None], dict[str, Any]]] = [{merged!r}]\n"
    )


def print_report(reports: list[FileReport]) -> None:
    converted = [r for r in reports if r.status == "converted"]
    skipped = [r for r in reports if r.status == "skipped_unreviewed"]
    other = [r for r in reports if r.status not in ("converted", "skipped_unreviewed")]
    for r in reports:
        print(f"{r.status:20s} {r.slug}"
              + (f"  ({r.events_kept} events, {r.dropped_keys} dropped keys)"
                 if r.status == "converted" else ""))
        if r.reason and r.status != "converted":
            print(f"                     - {r.reason}")
        for p in r.refused:
            print(f"                     REFUSED {p}")
    print(f"\n{len(reports)} files: {len(converted)} converted, {len(skipped)} unreviewed, "
          f"{len(other)} other")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                     help="report only, no write (default)")
    ap.add_argument("--write", action="store_true",
                     help="write the consolidated dump to scripts/dumps/frame_pdf_data.py")
    ap.add_argument("--from-answers", type=Path, default=None,
                     help="read flat <slug>.events.json files from this dir instead of "
                          "out/<slug>/events.json folders")
    ap.add_argument("--allow-audited", action="store_true",
                     help="also accept source containing 'concordance-audited' "
                          "(refused by default)")
    ap.add_argument("--exclude", type=Path, default=None,
                     help="JSON file holding a list of slugs to skip")
    a = ap.parse_args()

    exclude: set[str] = set()
    if a.exclude:
        data = json.loads(a.exclude.read_text(encoding="utf-8"))
        exclude = set(data)

    reports, blocks = convert_all(
        from_answers=a.from_answers, allow_audited=a.allow_audited, exclude=exclude
    )
    print_report(reports)

    if a.write:
        if not blocks:
            print("\nnothing converted -- not writing an empty dump")
            return 0
        DUMP_PATH.write_text(render_dump(blocks), encoding="utf-8")
        print(f"\nwrote {sum(len(b) for b in blocks)} bout(s) -> {DUMP_PATH}")
    else:
        print("\ndry run -- pass --write to produce scripts/dumps/frame_pdf_data.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
