"""Bruno Rocha concordance-audited frame answers -> three importable dumps, one per event tag
(repo convention: one dump module per event, see ``scripts/reprocess_all.py``).

Reuses ``scripts.frame_answer_to_dump`` (``--from-answers``/``--allow-audited``) to convert the
four flat ``data/frame_pdf/bruno_rocha/answers/*.events.json`` files whose ``source`` carries
"concordance-audited" (procedure: docs/gemini_concordance_audit.md; curated index
``data/frame_pdf/bruno_rocha_bouts.json``; manifest ``data/frame_pdf/bruno_rocha.json``).

Deliberately thinner than its two siblings (``w65_frames_to_dump.py``,
``adcc_trials2324_frames_to_dump.py``), and each omission is a measured fact about this batch,
not a shortcut:

- **No exclude list.** All four bouts are new. Probed read-only 2026-09-01: no ``matches`` row
  carries any of the four Flo video ids, and Bruno's nine existing bouts are against different
  opponents.
- **No athlete-name map.** The usual phantom-duplicate guard (failure-archaeology scar #2) has
  nothing to map here: the same probe found NO athlete row for Rafael Silverio da Fonseca,
  Keven Julio, Bryan Silva or Joao Cleber Araujo Borges under any spelling (fuzzy pass over
  rocha/silverio/fonseca/keven/julio/bryan/silva/cleber/borges/araujo), so all four are new
  rows created at import; and the one athlete who DOES exist is already written with the DB's
  own spelling, "Bruno Fernandes Rocha" (id 7558d1f1), in the curated index and in every kept
  event.
- **No per-slug event table.** ``bout.event`` already holds the curated event tag (the curated
  index is what set it), so the split key is read from the answer rather than re-declared.
- **No ``video_start_seconds`` fallback.** The curated index sets ``bout_start`` per bout from
  its own clock read, so the value in each answer is already the audited one. The CBJJ
  Brasileiro bout has none ON PURPOSE: its video begins after the bout has already started
  (clock 6:40, score 0x0, at the first sampled frames), so every non-negative value would be
  a lie and the field stays null.

weight_class/stage are the generic converter's default (``""``). Only the FPJJ bout has a
readable division banner ("Faixa Branca / Master 2 / Meio-Pesado", quartas-final); it is kept
in the curated index and the audit notes rather than split across two loosely-typed columns
for a single bout.

No database. Never opens a connection -- only reads
``data/frame_pdf/bruno_rocha/answers/*.events.json`` and (with ``--write``) writes three plain
``.py`` dump literals.

    uv run python -m scripts.bruno_rocha_frames_to_dump            # report only
    uv run python -m scripts.bruno_rocha_frames_to_dump --write    # also write the dumps
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

ANSWERS = REPO / "data" / "frame_pdf" / "bruno_rocha" / "answers"
DUMPS_DIR = REPO / "scripts" / "dumps"
MANIFEST = REPO / "data" / "frame_pdf" / "bruno_rocha.json"

# One module per event tag. None of these three tags exists in prod yet (probed 2026-09-01:
# the only near miss among existing tags is "ACBJJ 13"), so the filenames cannot collide with
# an earlier batch's dump the way two of the women-65 modules had to.
EVENT_MODULE = {
    # FPJJ Circuito Paulista Gi Etapa 3 2026 (16132708): DISCARDED 2026-09-01 — the card reads
    # FAIXA BRANCA / MASTER 2 while this athlete is a purple belt; owner ruled it a namesake.
    "CBJJE BJJ Paulista 2026": "cbjje_bjj_paulista_2026_frames_data.py",
    "CBJJ Brasileiro No-Gi 2026": "cbjj_brasileiro_nogi_2026_frames_data.py",
}


def _video_url(slug: str) -> str:
    """Flo page URL for a slug, from the manifest -- the slug's trailing digits are the video
    id (the number after ``/video/``). Report only: the dump format carries no ``video_url``,
    ``scripts/dump_import.py:video_index`` resolves that from ``url_mapping.json`` at import."""
    vid = slug.rsplit("-", 1)[-1]
    videos = json.loads(MANIFEST.read_text(encoding="utf-8"))["videos"]
    return next((v["url"] for v in videos if f"/video/{vid}-" in v["url"]), f"(no manifest entry for {vid})")


def build() -> tuple[list[Any], list[tuple[str, DumpBlock]], dict[str, list[DumpBlock]]]:
    """Convert every audited answer, then bucket the blocks by their event tag -- one bucket
    per dump module. Also returns the (slug, block) pairs in visit order, for a per-bout
    report line that needs the slug."""
    reports, blocks = convert_all(from_answers=ANSWERS, allow_audited=True)
    converted_slugs = [r.slug for r in reports if r.status == "converted"]
    slug_blocks = list(zip(converted_slugs, blocks, strict=True))

    by_event: dict[str, list[DumpBlock]] = {event: [] for event in EVENT_MODULE}
    for slug, block in slug_blocks:
        event = str(next(iter(block.values()))["event"])
        if event not in by_event:
            raise SystemExit(f"{slug}: event {event!r} has no module in EVENT_MODULE")
        by_event[event].append(block)
    return reports, slug_blocks, by_event


def _header(event: str) -> str:
    return (
        f'"""Bruno Rocha frame-read match dump -- {event} (concordance-audited).\n\n'
        "Generated from concordance-audited frame readings under "
        "data/frame_pdf/bruno_rocha/answers/ (procedure: docs/gemini_concordance_audit.md) "
        "by scripts/bruno_rocha_frames_to_dump.py, via scripts/frame_answer_to_dump.py's "
        f"convert_all/render_dump; keyed by (athlete_a_name, year). Event tag: {event!r}.\n"
        'Do not edit by hand."""\n'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                     help="write the three dump modules to scripts/dumps/")
    a = ap.parse_args()

    reports, slug_blocks, by_event = build()
    print_report(reports)

    for slug, block in slug_blocks:
        (a_name, year), m = next(iter(block.items()))
        start = m.get("video_start_seconds")
        print(f"    {a_name} vs {m['opponent']} ({year}) -- {_video_url(slug)}"
              f"{f'&t={start}s' if start else '  (no bout start: video begins mid-bout)'}")

    for event, blocks in by_event.items():
        module = EVENT_MODULE[event]
        total_events = sum(len(next(iter(b.values()))["events"]) for b in blocks)
        print(f"\n{event}: {len(blocks)} bout(s), {total_events} event(s) -> {module}")
        if a.write and blocks:
            (DUMPS_DIR / module).write_text(
                render_dump(blocks, header=_header(event)), encoding="utf-8"
            )

    total_bouts = sum(len(b) for b in by_event.values())
    total_events = sum(len(next(iter(block.values()))["events"])
                       for blocks in by_event.values() for block in blocks)
    print(f"\n{total_bouts} bout(s), {total_events} event(s) across "
          f"{sum(1 for b in by_event.values() if b)} module(s)")
    if not a.write:
        print("dry run -- pass --write to produce the dump modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
