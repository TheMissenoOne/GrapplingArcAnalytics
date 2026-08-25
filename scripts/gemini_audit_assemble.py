"""Assemble concordance-audited frame readings into final answer files.

Step 5 of ``docs/gemini_concordance_audit.md``. Inputs are the normalized raw answers
(``answers/raw/<slug>.json``, from ``scripts/gemini_normalize.py``) and the auditors'
verdict files (``<slug>.audited.json``: kept_events + per-event log + verified identity).
Output is one schema-valid answer per bout at ``answers/<slug>.events.json`` -- kept
events only, published winner, audited identity -- plus the audit log preserved at
``answers/audit_log/<slug>.json`` so every kept/dropped decision stays reviewable.

    uv run python scripts/gemini_audit_assemble.py --audited <dir> [--answers <dir>]

Validates every assembled file with ``scripts.frame_answer.validate`` (labels + schema)
and its own bout-range check (events must sit inside the curated bout window); exits 1
on any problem.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.frame_answer import load_labels, validate  # noqa: E402
from scripts.gemini_normalize import canonical_labels, label_fold  # noqa: E402

DEFAULT_ANSWERS = REPO / "data" / "frame_pdf" / "trials_2023_24" / "answers"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audited", type=Path, required=True)
    ap.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS)
    a = ap.parse_args()

    labels = load_labels()
    log_dir = a.answers / "audit_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    problems_total = 0
    rows = []
    for raw_path in sorted((a.answers / "raw").glob("*.json")):
        slug = raw_path.stem
        audited_path = a.audited / f"{slug}.audited.json"
        if not audited_path.exists():
            print(f"SKIP {slug}: no audited file")
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        audited = json.loads(audited_path.read_text(encoding="utf-8"))
        canon = canonical_labels()
        kept = sorted(({**e, "label": canon.get(label_fold(str(e["label"])), e["label"])}
                       for e in audited["kept_events"]), key=lambda e: e["ts"])
        n_read = len(raw["events"])

        bout = dict(raw["bout"])
        if audited.get("identity_check"):
            bout["identity_discriminator"] = audited["identity_check"]
        bout["identity_verified_by"] = "concordance audit (frames + narration + published result)"
        notes = [n for n in (bout.get("notes"), audited.get("notes")) if n]
        if notes:
            bout["notes"] = " | ".join(str(n) for n in notes)

        answer = {"bout": bout, "events": kept,
                  "source": f"gemini reading, concordance-audited (kept {len(kept)}/{n_read}) "
                            "2026-08-25"}
        probs = validate(answer, labels, [])
        lo, hi = raw["audit"]["curated_start"] - 5, raw["audit"]["curated_end"] + 30
        probs += [f"events[{i}].ts {e['ts']} outside curated bout {lo}..{hi}"
                  for i, e in enumerate(kept) if not (lo <= e["ts"] <= hi)]
        if probs:
            problems_total += len(probs)
            for p in probs:
                print(f"PROBLEM {slug}: {p}")
        (a.answers / f"{slug}.events.json").write_text(
            json.dumps(answer, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        (log_dir / f"{slug}.json").write_text(
            json.dumps({"log": audited.get("log", []), "identity_check": audited.get("identity_check"),
                        "notes": audited.get("notes")}, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
        rows.append((slug, n_read, len(kept)))

    total_read = sum(r[1] for r in rows)
    total_kept = sum(r[2] for r in rows)
    for slug, nr, nk in rows:
        mark = "" if nr == nk else f"  (dropped {nr - nk})"
        print(f"  {slug:44s} {nk:2d}/{nr:2d}{mark}")
    print(f"{len(rows)} bouts assembled: kept {total_kept}/{total_read} events; "
          f"{problems_total} validation problems")
    return 1 if problems_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
