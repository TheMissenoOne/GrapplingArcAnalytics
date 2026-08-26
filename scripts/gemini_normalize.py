"""Normalize raw Gemini frame-reading dumps into one structured answer file per bout.

Step 1 of the documented procedure in ``docs/gemini_concordance_audit.md``. The input is a
folder of files saved straight out of AI Studio, which arrive in three shapes -- a bare
``[{event}, ...]`` array with no bout header, a single ``{"bout": ..., "events": ...}``
object, or a ``{"bouts": [...]}`` wrapper holding several bouts -- and often carry literal
control characters inside JSON strings (AI Studio line-wraps its output). This script is
the "basic conversion": tolerant parse, match each reading to its bout in the curated index
by timestamp range, drop exact duplicates, apply the deterministic fixes no human needs to
look at, and write ``<slug>.json`` per bout in the ``scripts/frame_answer.py`` shape.

Deterministic fixes applied here (everything else is the visual audit's job):
- ``type: "penalty"`` events move to ``bout.penalties`` free text -- penalties are
  scoreboard facts, not techniques, per the frame_answer schema.
- ``bout.winner`` is ALWAYS the published winner (the curated label is "Winner vs.
  Opponent"); a disagreeing Gemini winner is preserved as an audit flag, never as data.
- Off-library labels and off-schema fields are flagged for the audit, not silently fixed.

    uv run python scripts/gemini_normalize.py <input_dir> [--out <dir>]

Default --out: data/frame_pdf/trials_2023_24/answers/raw
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

from scripts.frame_answer import EVENT_TYPES, load_labels  # noqa: E402

BOUTS_INDEX = REPO / "data" / "frame_pdf" / "trials_2023_24_bouts.json"
DEFAULT_OUT = REPO / "data" / "frame_pdf" / "trials_2023_24" / "answers" / "raw"


def tolerant_json(text: str) -> Any:
    """Parse JSON that may carry literal control characters inside strings."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(re.sub(r"[\x00-\x1f]", " ", text))


def label_fold(label: str) -> str:
    """Casefold + collapse every unicode dash to '-'. The library carries non-breaking
    hyphens (U+2011) in labels like 'Head‑Arm Control (Top)'; Gemini types ASCII hyphens,
    which is a spelling difference no reader can see. Fold both sides before comparing."""
    return re.sub(r"[‐‑‒–—−]", "-", label.strip().casefold())


def canonical_labels() -> dict[str, str]:
    """fold -> the library's exact label (original casing), for snapping a spelling that
    differs only in ways no reader can see (case, dash variant)."""
    lib_path = REPO / "data" / "frame_pdf" / "node_library.json"
    if not lib_path.exists():
        return {}
    lib = json.loads(lib_path.read_text(encoding="utf-8"))
    return {label_fold(str(n["label"])): str(n["label"]) for n in lib["nodes"]}


def slugify(label: str) -> str:
    s = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def readings(payload: Any) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Every (bout_meta, events) pair a parsed file contains, whatever its wrapper shape."""
    if isinstance(payload, list):
        # Batch-3 variant: an array of BOUT OBJECTS (each carrying its own 'events' and a
        # 'competitors' pair) rather than a bare events array. Detect by the 'events' key.
        if payload and all(isinstance(b, dict) and "events" in b for b in payload):
            out = []
            for b in payload:
                meta = {k: v for k, v in b.items() if k != "events"}
                comp = meta.pop("competitors", None)
                if isinstance(comp, list) and len(comp) == 2:
                    meta.setdefault("athlete_a", comp[0])
                    meta.setdefault("athlete_b", comp[1])
                if "win_method" in meta:
                    meta.setdefault("method", meta.pop("win_method"))
                out.append((meta, b.get("events") or []))
            return out
        return [({}, payload)]
    if isinstance(payload, dict) and "bouts" in payload:
        return [(b.get("bout") or {}, b.get("events") or []) for b in payload["bouts"]]
    if isinstance(payload, dict):
        return [(payload.get("bout") or {}, payload.get("events") or [])]
    return []


def match_bout(events: list[dict[str, Any]], index: list[dict[str, Any]]) -> dict[str, Any] | None:
    tss = [e["ts"] for e in events if isinstance(e.get("ts"), int)]
    if not tss:
        return None
    lo, hi = min(tss), max(tss)
    for b in index:
        if b["start"] <= lo and hi <= b["end"] + 30:
            return b
    return None


def year_of(event: str) -> int | None:
    m = re.search(r"20\d\d", event)
    return int(m.group(0)) if m else None


def normalize_one(bout_meta: dict[str, Any], events: list[dict[str, Any]],
                  curated: dict[str, Any], labels: set[str], source_file: str) -> dict[str, Any]:
    athlete_a, athlete_b = (x.strip() for x in curated["label"].split(" vs. "))
    flags: list[str] = []
    penalties: list[str] = []
    kept: list[dict[str, Any]] = []
    canon = canonical_labels()
    for e in sorted(events, key=lambda e: e.get("ts", 0)):
        if e.get("type") == "penalty":
            penalties.append(f"{e.get('actor', '?')} ~t{e.get('ts', '?')} ({e.get('label', 'penalty')})")
            continue
        lab = str(e.get("label", "")).strip()
        if canon and label_fold(lab) in canon:
            e = {**e, "label": canon[label_fold(lab)]}
        elif labels:
            flags.append(f"ts {e.get('ts')}: label {lab!r} off-library -> audit must snap or drop")
        if e.get("type") not in EVENT_TYPES:
            flags.append(f"ts {e.get('ts')}: type {e.get('type')!r} invalid -> audit must fix or drop")
        if str(e.get("actor", "")).strip() not in (athlete_a, athlete_b):
            flags.append(f"ts {e.get('ts')}: actor {e.get('actor')!r} is neither competitor")
        kept.append(e)

    gemini_winner = str(bout_meta.get("winner") or "").strip()
    if gemini_winner and gemini_winner != athlete_a:
        flags.append(f"gemini winner {gemini_winner!r} CONTRADICTS published winner {athlete_a!r}"
                     " -- identity of every actor in this bout is suspect")

    bout: dict[str, Any] = {"athlete_a": athlete_a, "athlete_b": athlete_b,
                            "event": curated["event"], "winner": athlete_a}
    if (y := year_of(curated["event"])) is not None:
        bout["year"] = y
    for k in ("win_type", "bout_start_seconds", "bout_end_seconds",
              "identity_discriminator", "final_score", "advantages", "notes"):
        if bout_meta.get(k):
            # final_score/advantages must be name->points maps; a string ("0-3") has no
            # declared orientation (the frame_answer scar) -- drop it rather than carry it.
            if k in ("final_score", "advantages") and not isinstance(bout_meta[k], dict):
                flags.append(f"{k} was a string ({bout_meta[k]!r}) -- dropped, orientation undeclared")
                continue
            bout[k] = bout_meta[k]
    if penalties:
        bout["penalties"] = "; ".join(penalties)

    return {"bout": bout, "events": kept,
            "audit": {"division": curated["division"], "source_file": source_file,
                      "curated_start": curated["start"], "curated_end": curated["end"],
                      "gemini_winner": gemini_winner or None, "flags": flags,
                      "status": "normalized, awaiting concordance audit"}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input_dir", type=Path)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args()

    index = json.loads(BOUTS_INDEX.read_text(encoding="utf-8"))["bouts"]
    labels = load_labels()
    a.out.mkdir(parents=True, exist_ok=True)

    seen: dict[str, str] = {}     # slug -> signature, to drop exact duplicates
    written, dup, problems = 0, 0, 0
    for f in sorted(a.input_dir.iterdir()):
        if not f.is_file():
            continue
        try:
            payload = tolerant_json(f.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            print(f"UNPARSEABLE {f.name}: {exc}")
            problems += 1
            continue
        for bout_meta, events in readings(payload):
            curated = match_bout(events, index)
            if curated is None:
                print(f"NO BOUT MATCH {f.name}: {len(events)} events do not fit one curated bout")
                problems += 1
                continue
            slug = slugify(curated["label"])
            # ts can be absent on a raw reading; sort None-safe (batch 3 crashed here).
            sig = json.dumps(sorted((e.get("ts") if isinstance(e.get("ts"), int) else -1,
                                     str(e.get("label"))) for e in events))
            if slug in seen:
                if seen[slug] == sig:
                    dup += 1
                    continue
                print(f"CONFLICTING DUPLICATE for {curated['label']} in {f.name} -- keeping first, "
                      "flagging")
                problems += 1
                continue
            seen[slug] = sig
            out = normalize_one(bout_meta, events, curated, labels, f.name)
            (a.out / f"{slug}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
            written += 1
            if out["audit"]["flags"]:
                for fl in out["audit"]["flags"]:
                    print(f"  flag {slug}: {fl}")
    print(f"{written} bouts written to {a.out} ({dup} duplicate readings dropped, "
          f"{problems} problems)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
