#!/usr/bin/env python
"""Emit Deepseek recheck prompts for generic (non-specific) grappling labels.

Generic labels ("Sweep", "Takedown", "Pass", "Reversal"…) carry a type but no
technique. This finds them per event dump and writes a prompt asking Deepseek to
re-read the transcript at each event's timestamp and either SPECIFY the technique
(e.g. "Sweep" → "Scissor Sweep") or REMOVE it when the transcript can't specify —
never a blind bulk delete. Mirrors ``scripts.transcript_status --emit-prompts``.

    uv run python -m scripts.recheck_generics            # report + write prompts
    uv run python -m scripts.recheck_generics --report   # report only, no prompts
"""

from __future__ import annotations

import argparse
import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parents[1] / "transcripts" / "deepseek"

# Bare type-words + vague actions that name no specific technique.
GENERIC = {
    "sweep", "takedown", "pass", "guard pass", "guard", "control", "submission",
    "escape", "transition", "reversal", "scramble", "clinch", "pull guard",
    "takedown attempt", "submission attempt", "sweep attempt", "pass attempt",
    "counter", "combination",
}

_PROMPT = """\
# Deepseek recheck — specify or remove generic labels in `{label}`

Some events in this event's match dump carry a **generic** label (a type, not a
specific technique). For EACH event below, open the matching transcript
(`transcripts/{label}.txt` or `transcripts/queue/…`), find the moment at the given
timestamp, and either:

- **SPECIFY** — replace with the exact technique the commentary/action shows
  (e.g. `Sweep` → `Scissor Sweep`, `Takedown` → `Double Leg Takedown`), or
- **REMOVE** — if the transcript does not let you name a specific technique.

Do NOT invent techniques. Output one line per event: `<n>. <bout> @<ts> : <New Label | REMOVE>`.

## Events to recheck ({count})
{items}

The maintainer applies your answers to `transcripts/{label}.py`, re-runs
`convert_dump.py` + `reprocess_all --only {label}`.
"""


def scan() -> dict[str, list[str]]:
    """label → list of 'A vs B @ts : CurrentLabel' generic-event lines."""
    from scripts.reprocess_all import DATASETS

    found: dict[str, list[str]] = {}
    for mod, _event, label in DATASETS:
        try:
            raw = importlib.import_module(mod).RAW
        except Exception as e:  # noqa: BLE001 — a broken dump shouldn't abort the sweep
            logger.warning("skip %s: %s", mod, e)
            continue
        lines: list[str] = []
        for block in raw:
            for (a, _yr), m in block.items():
                opp = m.get("opponent", "?")
                for e in m.get("events") or []:
                    lab = str(e.get("label", "")).strip()
                    if lab.lower() in GENERIC:
                        ts = e.get("timestamp") or e.get("ts") or "?"
                        lines.append(f"{a} vs {opp} @{ts} : {lab}")
        if lines:
            found[label] = lines
    return found


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Emit Deepseek recheck prompts for generic labels")
    ap.add_argument("--report", action="store_true", help="report only, don't write prompts")
    args = ap.parse_args()

    found = scan()
    total = sum(len(v) for v in found.values())
    print(f"Generic labels: {total} events across {len(found)} events")
    for label, lines in sorted(found.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(lines):4}  {label}")

    if args.report or not found:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for label, lines in found.items():
        items = "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, 1))
        (OUT_DIR / f"recheck-{label}.md").write_text(
            _PROMPT.format(label=label, count=len(lines), items=items), encoding="utf-8"
        )
    logger.info("Wrote %d recheck prompt(s) → %s", len(found), OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
