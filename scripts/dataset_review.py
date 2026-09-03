#!/usr/bin/env python
"""Human review loop over model-origin labels in ``data/finetune/`` (active learning).

    uv run python -m scripts.dataset_review list                     # what needs a human
    uv run python -m scripts.dataset_review list --bout <slug> -n 40
    uv run python -m scripts.dataset_review accept <label_id> [...]
    uv run python -m scripts.dataset_review reject <label_id> [...] --note "actor flipped"

The loop this closes: a zero-shot or tuned Gemini reading is ingested by
``scripts/vision_dataset.py`` as labels with ``source=gemini`` / ``gemini_ft:<job>``. Those
labels are NOT training data. A human accepts or rejects each one here, and only then does
``scripts/vision_dataset_export.py:admissible`` let it into a training export — so every
round of tuning is trained on human-confirmed ground truth and the model's own output can
never bootstrap itself.

**``source`` is never rewritten.** Accepting a model reading records
``review: "accepted"`` + reviewer + timestamp alongside the original ``source``. Laundering
a model reading into ``source: "human"`` is the exact defect ``frame_registrar.py`` was
fixed for on 2026-08-24, and it is what makes a corpus unauditable a year later. For the
same reason a ``source == "human"`` line is refused here: it has nothing to review.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.vision_dataset import DATASET  # noqa: E402

logger = logging.getLogger("dataset_review")


def iter_labels(dataset: Path) -> list[tuple[Path, list[dict[str, Any]]]]:
    out = []
    for path in sorted((dataset / "labels").glob("*.jsonl")):
        rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln]
        out.append((path, rows))
    return out


def write_labels(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8")


def pending(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Model-origin labels a human has not ruled on yet."""
    return [r for r in rows if r.get("source") != "human" and r.get("review") is None]


def cmd_list(dataset: Path, bout: str | None, limit: int, disputed_only: bool,
             show_covered: bool = False) -> int:
    """Print the review queue, hardest first.

    Order is the active-learning bit: a model label that CONTRADICTS a human label on the
    same frame is where the model and the ground truth actually disagree, so it teaches more
    per minute of attention than one that merely adds an unseen event.
    """
    queue: list[tuple[int, dict[str, Any]]] = []
    totals: Counter[str] = Counter()
    for _path, rows in iter_labels(dataset):
        human = {(r["ts_ms"], r["node_key"]) for r in rows if r.get("source") == "human"}
        human_frames = {r["ts_ms"] for r in rows if r.get("source") == "human"}
        for r in pending(rows):
            if bout and r["bout"] != bout:
                continue
            key = (r["ts_ms"], r["node_key"])
            if key in human:
                # An identical human label on the same frame already carries this claim into
                # every export. Reviewing it changes nothing, so it is not in the queue.
                rank, kind = 0, "covered by an identical human label (no review needed)"
            elif r["ts_ms"] in human_frames:
                rank, kind = 1, "DISPUTED (human read this frame differently)"
            else:
                rank, kind = 2, "unseen (no human label on this frame)"
            totals[kind] += 1
            if rank == 0 and not show_covered:
                continue
            if disputed_only and rank != 1:
                continue
            queue.append((rank, {**r, "_kind": kind}))
    queue.sort(key=lambda t: (-t[0] if t[0] == 1 else t[0], t[1]["bout"], t[1]["ts_ms"]))
    queue.sort(key=lambda t: ({1: 0, 2: 1, 0: 2}[t[0]], t[1]["bout"], t[1]["ts_ms"]))

    for _rank, r in queue[:limit]:
        print(f"{r['label_id']}  {r['bout'][:38]:38s} t={r['ts']:6d}  "
              f"{r['node_key'][:24]:24s} {str(r['actor_key'])[:20]:20s} "
              f"ok={str(r['successful'])[:5]:5s} src={r['source']:10s} {r['_kind']}")
    shown = min(limit, len(queue))
    print(f"\n{shown} shown / {len(queue)} needing review / "
          f"{sum(totals.values())} model labels with no verdict")
    for k, v in totals.most_common():
        print(f"  {v:5d}  {k}")
    return 0


def cmd_rule(dataset: Path, verdict: str, ids: list[str], note: str, reviewer: str) -> int:
    wanted = set(ids)
    stamped = datetime.now(UTC).date().isoformat()
    hit = 0
    refused: list[str] = []
    for path, rows in iter_labels(dataset):
        touched = False
        for r in rows:
            if r.get("label_id") not in wanted:
                continue
            if r.get("source") == "human":
                refused.append(r["label_id"])
                continue
            r["review"] = verdict
            r["reviewer"] = reviewer
            r["reviewed_at"] = stamped
            if note:
                r["review_note"] = note
            touched, hit = True, hit + 1
        if touched:
            write_labels(path, rows)
    missing = wanted - {i for i in wanted if hit}
    print(f"{verdict}: {hit} label(s) by {reviewer} on {stamped}")
    if refused:
        print(f"refused {len(refused)} human-origin label(s) — nothing to review there: "
              f"{refused[:5]}")
    if hit == 0 and not refused:
        print(f"no label matched {sorted(missing)[:5]}")
        return 1
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["list", "accept", "reject"])
    ap.add_argument("ids", nargs="*", help="label_id values (accept/reject)")
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--bout", help="list: restrict to one bout slug")
    ap.add_argument("-n", "--limit", type=int, default=30)
    ap.add_argument("--show-covered", action="store_true",
                    help="list: also show model labels an identical human label covers")
    ap.add_argument("--disputed-only", action="store_true",
                    help="list: only labels contradicting a human label on the same frame")
    ap.add_argument("--note", default="")
    ap.add_argument("--reviewer", default="")
    a = ap.parse_args()

    if a.command == "list":
        return cmd_list(a.dataset, a.bout, a.limit, a.disputed_only, a.show_covered)
    if not a.ids:
        ap.error(f"{a.command} needs at least one label_id (see `list`)")
    reviewer = a.reviewer or getpass.getuser()
    return cmd_rule(a.dataset, "accepted" if a.command == "accept" else "rejected",
                    a.ids, a.note, reviewer)


if __name__ == "__main__":
    raise SystemExit(main())
