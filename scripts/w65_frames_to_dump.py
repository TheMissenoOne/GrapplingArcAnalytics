"""Women-65 concordance-audited frame answers -> seven importable dumps, one per event tag
(repo convention: one dump module per event, see ``scripts/reprocess_all.py``).

Reuses ``scripts.frame_answer_to_dump`` (``--from-answers``/``--allow-audited``/``--exclude``)
to convert the 21 flat ``data/frame_pdf/out/processed/audit/*.events.json`` files whose
``source`` carries "concordance-audited" (procedure: docs/gemini_concordance_audit.md). The 4
slugs in ``EXCLUDE_SLUGS`` already exist as matches in prod and go through a separate
enrichment script, not this one.

On top of the generic converter, this script applies things specific to this batch, none of
which the generic converter can know:

- **Athlete-name map** -- these people already exist in prod under a different spelling; a
  key mismatch would mint a phantom athlete (failure-archaeology scar #2). Applied to
  athlete_a/athlete_b/opponent/winner/every event actor, globally (not just the slugs the
  name happens to originate from -- the same person can appear as a winner in one bout and an
  opponent in another).
- **Per-slug event map** -- raw ``bout.event`` text is inconsistent across sheets for the same
  DB event ("World Championship No-Gi" / "IBJJF World Championship No-Gi" / "Pan Championship
  No-Gi" all mean the DB's "World/Pan No-Gi <year>"), so the split key is an explicit
  slug -> event-tag table, not the raw field. The mapped tag is also the SPLIT KEY: each bout
  is written into the dump module for its own event, not one shared dump. Two of the seven
  event tags ("Pan No-Gi 2025", "Polaris 36") already exist in prod from other batches -- this
  batch's modules get a distinct filename (``..._frames_data.py`` / ``..._frames2_data.py``) so
  they don't collide with the existing dump.
- **video_start_seconds** fallback to each bout's own ``raw/<slug>.json`` ``audit.curated_start``
  when the answer itself has no ``bout.bout_start_seconds``. Unlike the ADCC Trials 2023-24
  frame batch (one shared recording, one ``VIDEO_URL``), every bout here is its own single-bout
  YouTube upload -- its id is the slug's trailing 11 characters (``SLUG_VIDEO_URL``, used only
  for the per-event report below; the dump format itself carries no ``video_url`` field --
  ``scripts/dump_import.py:video_index`` resolves that from ``url_mapping.json`` at import
  time, not from the dump).

weight_class/stage are left as the generic converter's own default (``""``) -- these are not
divisioned bouts and this batch's raw files carry no stage/division field to fix up with,
unlike the ADCC Trials batch's ``audit.division``.

No database. Never opens a connection -- only reads
``data/frame_pdf/out/processed/audit/*.events.json`` + ``.../audit/raw/*.json`` and (with
``--write``) writes seven plain ``.py`` dump literals.

    uv run python -m scripts.w65_frames_to_dump              # report only
    uv run python -m scripts.w65_frames_to_dump --write       # also write the dumps
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

ANSWERS = REPO / "data" / "frame_pdf" / "out" / "processed" / "audit"
RAW_DIR = ANSWERS / "raw"
DUMPS_DIR = REPO / "scripts" / "dumps"

# Cross-referenced against prod (women-65 campaign audit); these 4 already exist as matches
# and go through scripts/w65 enrichment, not a fresh dump.
EXCLUDE_SLUGS = {
    "helena-crevar-vs-amanda-nicole--polaris-37-2026-NO62LWlUUNQ",
    "helena-crevar-vs-leilani-bernales--wno-25-2024-Vo7ymhLxDxo",
    "sarah-galvao-vs-libby-genge--polaris-36-2026-bhtda0EZYpU",
    "sarah-galvao-vs-zara-tofano--polaris-38-2026-IPgbC27xBQQ",
}

NAME_MAP = {
    "Amanda Nicole": "Amanda Pamela Nicole",
    "Salla Simola": "Salli Simola",
}

# slug -> DB-convention event tag. See module docstring: raw bout.event text disagrees with
# itself across sheets for the same DB event, so this explicit table is the split key.
SLUG_EVENT: dict[str, str] = {
    "anabel-lopez-vs-aurelie-le-vern--european-no-gi-2024-8xvq3lM6kQY": "European No-Gi 2024",
    "ane-svendsen-vs-aurelie-le-vern--european-no-gi-2024-oIMgwDldvmA": "European No-Gi 2024",
    "anabel-lopez-vs-brianna-ste-marie--world-no-gi-2024-9ThUSXSEf8w": "World No-Gi 2024",
    "anabel-lopez-vs-maria-vicentini--world-no-gi-2024-HWNPuVZYB1Q": "World No-Gi 2024",
    "anabel-lopez-vs-paige-ivette--world-no-gi-2024-GHFT3RO4v_k": "World No-Gi 2024",
    "anabel-lopez-vs-salla-simola--world-no-gi-2024-U7ovaUg3y4U": "World No-Gi 2024",
    "nadia-frankland-vs-elisabeth-clay--world-no-gi-2024-V0TNJTVVuwA": "World No-Gi 2024",
    "nadia-frankland-vs-gabrieli-pessanha--world-no-gi-2024-7H1PhSukqZg": "World No-Gi 2024",
    "nadia-frankland-vs-paige-ivette--world-no-gi-2024-ImqWcrhUF8w": "World No-Gi 2024",
    "morgan-black-vs-brianna-ste-marie--world-no-gi-2024-BzwFZSrxJkM": "World No-Gi 2024",
    "nadia-frankland-vs-gabriele-schuck--pan-no-gi-2024-wFqamBHsK3g": "Pan No-Gi 2024",
    "helena-crevar-vs-gabriela-miranda--world-no-gi-2025-IGaL1pyNtuU": "World No-Gi 2025",
    "injana-goodman-vs-elisabeth-clay--world-no-gi-2025-C4ZeMLqnTDs": "World No-Gi 2025",
    "nadia-frankland-vs-gabrieli-pessanha--world-no-gi-2025-LQUors-3gZM": "World No-Gi 2025",
    "injana-goodman-vs-julia-maele--european-no-gi-2025-vnYJGBVM78E": "European No-Gi 2025",
    "anabel-lopez-vs-erin-harpe--pan-no-gi-2025-YIJvzBtP3q4": "Pan No-Gi 2025",
    "anabel-lopez-vs-kendall-reusing--polaris-36-2026-2nfCwbxdvTY": "Polaris 36",
}

# (event tag) -> dump module filename.
EVENT_MODULE = {
    "European No-Gi 2024": "european_nogi2024_frames_data.py",
    "World No-Gi 2024": "world_nogi2024_frames_data.py",
    "Pan No-Gi 2024": "pan_nogi2024_frames_data.py",
    "World No-Gi 2025": "world_nogi2025_frames_data.py",
    "European No-Gi 2025": "european_nogi2025_frames_data.py",
    # "Pan No-Gi 2025" and "Polaris 36" already have a dump module in prod (pan_nogi2025_data.py,
    # polaris36_data.py/polaris36_women_data.py) -- distinct filenames avoid collision.
    "Pan No-Gi 2025": "pan_nogi2025_frames2_data.py",
    "Polaris 36": "polaris36_frames_data.py",
}


def _map_name(name: str) -> str:
    return NAME_MAP.get(name, name)


def _video_url(slug: str) -> str:
    """Every bout in this batch is its own single-bout YouTube upload; the video id is the
    slug's trailing 11 characters (YouTube ids are always 11 chars, and can themselves
    contain a hyphen -- e.g. ...-LQUors-3gZM -- so this cannot split on the last "-")."""
    return f"https://www.youtube.com/watch?v={slug[-11:]}"


def _fixup(slug: str, block: DumpBlock) -> DumpBlock:
    """Batch-specific post-processing over one converted block: name map, event tag,
    video_start_seconds fallback, and the block key.

    ``render_dump`` merges every block into ONE dict keyed by (a_name, year) (see its
    docstring); several athletes in this batch (Anabel Lopez, Nadia Frankland) fight more
    than once in the same event+year, so a plain ``(a_name, year)`` key collides and
    ``dict.update`` silently drops all but the last bout under that name -- measured: World
    No-Gi 2024 fell from 8 bouts to 3 before this fix. ``build_matches`` has a built-in escape
    hatch for exactly this (``" vs " in a_name`` -> split into both participants), so the key
    here is always ``"{a_name} vs {opponent}"``, which is unique per (pair, year, event)."""
    audit_path = RAW_DIR / f"{slug}.json"
    audit: dict[str, Any] = {}
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8")).get("audit") or {}

    out: DumpBlock = {}
    for (a_name, year), m in block.items():
        m = dict(m)
        a_name = _map_name(a_name)
        m["opponent"] = _map_name(str(m.get("opponent") or ""))
        if m.get("winner"):
            m["winner"] = _map_name(str(m["winner"]))
        m["event"] = SLUG_EVENT[slug]
        if m.get("video_start_seconds") is None:
            m["video_start_seconds"] = audit.get("curated_start")
        m["events"] = [{**e, "actor": _map_name(str(e["actor"]))} for e in m["events"]]
        out[(f"{a_name} vs {m['opponent']}", year)] = m
    return out


def build() -> tuple[list[Any], list[tuple[str, DumpBlock]], dict[str, list[DumpBlock]]]:
    """Convert + fix up every non-excluded audited answer, then bucket the resulting blocks
    by their (mapped) event tag -- one bucket per dump module. Also returns the flat
    (slug, block) pairs in visit order, for a per-bout report line that needs the slug."""
    reports, blocks = convert_all(
        from_answers=ANSWERS, allow_audited=True, exclude=EXCLUDE_SLUGS
    )
    # blocks is parallel to the converted-only subset of reports, in visit order.
    converted_slugs = [r.slug for r in reports if r.status == "converted"]
    fixed = [_fixup(slug, block) for slug, block in zip(converted_slugs, blocks, strict=True)]
    slug_blocks = list(zip(converted_slugs, fixed, strict=True))

    by_event: dict[str, list[DumpBlock]] = {event: [] for event in EVENT_MODULE}
    for slug, block in slug_blocks:
        by_event[SLUG_EVENT[slug]].append(block)
    return reports, slug_blocks, by_event


def _header(event: str) -> str:
    return (
        f'"""Women-65 frame-read match dump -- {event} (concordance-audited).\n\n'
        "Generated from concordance-audited frame readings under "
        "data/frame_pdf/out/processed/audit/ (procedure: docs/gemini_concordance_audit.md) "
        "by scripts/w65_frames_to_dump.py, via scripts/frame_answer_to_dump.py's "
        f"convert_all/render_dump; keyed by (athlete_a_name, year). Event tag: {event!r}.\n"
        'Do not edit by hand."""\n'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                     help="write the seven dump modules to scripts/dumps/")
    a = ap.parse_args()

    reports, slug_blocks, by_event = build()
    print_report(reports)

    for slug, block in slug_blocks:
        (matchup, year), m = next(iter(block.items()))  # matchup is already "A vs B"
        start = m.get("video_start_seconds")
        print(f"    {matchup} ({year}) -- {_video_url(slug)}"
              f"{f'&t={start}s' if start else ''}")

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
