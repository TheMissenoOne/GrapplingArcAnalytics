"""Load returned frame-read answers into the registrar's own format, with divergences attached.

This is the seam that was missing: an answer produced by reading a sheet was a dead end until
someone retyped it. It is deliberately NOT a silent converter -- every bout it writes carries
whatever it could not reconcile, in `bout.notes`, so the divergence opens in the registrar
next to the frames rather than sitting in a report nobody reads twice.

**Nothing is auto-corrected.** Where the footage contradicts the answer, the contradiction is
recorded and the answer is left as written. A converter that quietly fixes what it thinks is
wrong destroys the only signal there is about how reliable the reading was.

    uv run python scripts/frame_answer_import.py answers.json --check
    uv run python scripts/frame_answer_import.py answers.json --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OUT = REPO / "data" / "frame_pdf" / "out"
LIBRARY = REPO / "data" / "frame_pdf" / "node_library.json"
BJJH = REPO / "data" / "frame_pdf" / "bjjh_results.json"
TYPES = {"control", "submission", "guard", "takedown", "pass", "transition", "sweep", "escape"}


def fold(s: str) -> str:
    d = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in d if not unicodedata.combining(c)).casefold().strip()


def toks(s: str) -> set[str]:
    return {t for t in re.sub(r"[^a-z ]", " ", fold(s)).split() if len(t) > 2}


def find_folder(bout: dict[str, Any]) -> Path | None:
    want = set().union(*[toks(c) for c in bout["competitors"]])
    best, score = None, 0
    for d in OUT.iterdir():
        if not (d / "frames.jsonl").exists():
            continue
        have = {t for t in re.sub(r"[^a-z]", " ", d.name).split() if len(t) > 2}
        s = len(want & have)
        if s >= 3 and str(bout.get("year", "")) in d.name and s > score:
            best, score = d, s
    return best


def check(bout: dict[str, Any], folder: Path | None, labels: dict[str, str]) -> list[str]:
    """Everything about this answer that does not add up. Mechanical only -- what a frame
    would settle is left to a person, because this cannot look at one."""
    out: list[str] = []
    people = {fold(c) for c in bout["competitors"]}
    ts_range = None
    if folder:
        rows = [json.loads(x) for x in
                (folder / "frames.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        if rows:
            ts_range = (rows[0]["ts"], rows[-1]["ts"])

    for i, e in enumerate(bout.get("events", [])):
        at = f"events[{i}] {e.get('label')!r}"
        from analysis.names import _normalize_name as _nn
        from analysis.names import canonicalize as _cn
        raw = _nn(e.get("label", ""))
        key = _cn(raw)
        if key not in labels:
            out.append(f"{at}: normalises to {key!r}, which is not a library node")
        elif key != raw:
            out.append(f"{at}: folds to {labels[key]!r} via the synonym map (not an error, "
                       "recorded so the rewrite is visible)")
        if e.get("type") not in TYPES:
            out.append(f"{at}: type {e.get('type')!r} is not one of the eight")
        if fold(e.get("actor", "")) not in people:
            out.append(f"{at}: actor {e.get('actor')!r} is neither competitor")
        if ts_range and not (ts_range[0] <= e.get("ts", -1) <= ts_range[1]):
            out.append(f"{at}: ts {e.get('ts')} outside the sampled range {ts_range}")
        if e.get("points") == 0:
            out.append(f"{at}: points 0 — omit the field instead")

    # Points against the final score. Under-count is normal (not every score is attributable);
    # OVER-count is not, and neither is a scoreline nobody's events can reach.
    fs = bout.get("final_score")
    if fs and isinstance(fs, str) and "-" in fs:
        tally: dict[str, int] = {}
        for e in bout.get("events", []):
            if e.get("points"):
                tally[fold(e["actor"])] = tally.get(fold(e["actor"]), 0) + int(e["points"])
        claimed = sorted(int(x) for x in fs.split("-") if x.strip().isdigit())
        counted = sorted(list(tally.values()) + [0] * (2 - len(tally)))
        if counted and claimed and counted[-1] > claimed[-1]:
            out.append(f"events award {counted[-1]} points but final_score tops out at "
                       f"{claimed[-1]}")
        elif counted != claimed:
            out.append(f"final_score {fs} is not fully accounted for by events {counted} "
                       "(under-count, not a contradiction)")
        # Orientation is undeclared: "9-5" against competitors [A, B] could mean either side.
        if claimed[0] != claimed[1] and bout.get("winner"):
            first = bout["competitors"][0]
            positional_winner = first if int(fs.split("-")[0]) > int(fs.split("-")[1]) \
                else bout["competitors"][1]
            if not (toks(positional_winner) & toks(bout["winner"])):
                out.append(f"final_score {fs} read against competitors order gives "
                           f"{positional_winner} the higher score, but winner is "
                           f"{bout['winner']} — the field has no declared orientation")
    return out


def to_registrar(bout: dict[str, Any], problems: list[str], extra: list[str]) -> dict[str, Any]:
    keep = ("ts", "label", "actor", "successful", "type", "points", "note", "confidence")
    notes = [n for n in (bout.get("identification_note"), bout.get("score_note"),
                         bout.get("method_note"), bout.get("replay_range")) if n]
    if bout.get("identity_verified_by"):
        notes.append(f"identity: {bout['identity_verified_by']}")
    for p in problems:
        notes.append(f"UNRECONCILED — {p}")
    for p in extra:
        notes.append(f"CONTRADICTED BY FRAMES — {p}")
    return {
        "bout": {k: v for k, v in {
            "athlete_a": bout["competitors"][0], "athlete_b": bout["competitors"][1],
            "event": bout.get("event"), "year": bout.get("year"),
            "winner": bout.get("winner"), "win_type": bout.get("method"),
            "bout_start_seconds": bout.get("bout_start_seconds"),
            "bout_end_seconds": bout.get("bout_end_seconds"),
            "final_score": bout.get("final_score"),
            "identity_verified_by": bout.get("identity_verified_by"),
            "notes": " · ".join(notes) or None,
        }.items() if v is not None},
        "events": [{k: e[k] for k in keep if k in e} for e in bout.get("events", [])],
        "source": "frame_answer_import (returned reading, not yet human-reviewed)",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("answers", type=Path)
    ap.add_argument("--frame-findings", type=Path,
                    help="JSON of {bout_key: [contradictions]} established by looking at frames")
    ap.add_argument("--write", action="store_true", help="write events.json into each folder")
    a = ap.parse_args()

    data = json.loads(a.answers.read_text(encoding="utf-8"))
    bouts = data if isinstance(data, list) else [data]
    # Keyed by node_key, not by display string. The identity contract is the KEY -- comparing
    # rendered labels rejected 'Reset/Stalemate' against a library holding 'Reset / Stalemate',
    # which is the same node with different whitespace around a slash.
    from analysis.names import canonicalize
    nodes = json.loads(LIBRARY.read_text(encoding="utf-8"))["nodes"]
    # Keyed by the node's OWN key. Folding the alias node's canonicalized key in here would
    # overwrite the canonical entry with the alias's display label — "guard pull" would read
    # back as "Pull Guard", the exact thing the synonym exists to stop.
    labels = {n["key"]: n["label"] for n in nodes}
    for n in nodes:
        labels.setdefault(canonicalize(n["key"]), n["label"])
    findings = json.loads(a.frame_findings.read_text(encoding="utf-8")) \
        if a.frame_findings else {}

    total_problems = 0
    for b in bouts:
        folder = find_folder(b)
        name = " vs ".join(c.split()[-1] for c in b["competitors"]) + f" {b.get('year', '')}"
        probs = check(b, folder, labels)
        extra = findings.get(folder.name if folder else "", [])
        total_problems += len(probs) + len(extra)
        flag = "" if not (probs or extra) else f"  [{len(probs) + len(extra)}]"
        print(f"{'ok ' if not (probs or extra) else 'CHECK'} {name[:40]:42s} "
              f"{folder.name[:30] if folder else 'NO FOLDER':32s}{flag}")
        for p in probs:
            print(f"        · {p}")
        for p in extra:
            print(f"        ! {p}")
        if a.write and folder:
            (folder / "events.json").write_text(
                json.dumps(to_registrar(b, probs, extra), indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8")
    print(f"\n{len(bouts)} bouts, {total_problems} unreconciled points"
          + ("  — written" if a.write else "  — dry run, use --write"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
