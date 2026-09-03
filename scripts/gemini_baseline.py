#!/usr/bin/env python
"""Zero-shot Gemini baseline vs concordance-audited ground truth.

    uv run python -m scripts.gemini_baseline --n 10 [--dry-run] [--thinking high]

Ground truth is ``data/frame_pdf/trials_2023_24/answers/<slug>.events.json`` -- concordance
audited (``docs/gemini_concordance_audit.md``): every kept event was independently re-read off
the frames by a human auditor. That substitutes the human-review gate by explicit owner
decision (same doc, §4's provenance note), which is why this baseline treats it as ground
truth rather than requiring ``frame_registrar``'s own stamp.

ponytail: the classic ``data/frame_pdf/out/<slug>/events.json`` + ``sheets/`` pipeline the
ticket named has NO bout today carrying both a rendered sheet and a human/reviewed source
(checked 2026-09; all `out/processed/*.events.json` are still `frame_answer_import (... not
yet human-reviewed)`) -- so there is nothing to baseline against there yet. This script reads
the trials_2023_24 corpus instead, which does have 41 bouts with both a PDF sheet (library
pages embedded, same as any frame_pdf.py sheet) and a concordance-audited answer. Extend
``select_candidates`` to also scan ``data/frame_pdf/out/*/`` the day that pipeline has a
reviewed bout to offer.

Match rule: a human event and a model event match when ``type`` is equal, the canonicalised
label is equal (``scripts.enrich_from_audit.node_key`` -- the same ``clean_label ->
_normalize_name -> canonicalize`` chain every graph/map consumer in this repo already uses),
and ``|ts_human - ts_model| <= --ts-tolerance`` (default 10s, per the concordance audit's own
±10s rule). Matching is greedy nearest-ts, one-to-one.

Output: ``<--out-dir>/<slug>/gemini_baseline.json`` (raw model reading, mirrors
``gemini_read_frames.py``'s own on-disk shape) + ``gemini_raw.json`` (usage/cost) per bout,
never touching the ground-truth ``events.json``. Aggregate CSV at ``--csv``
(``data/processed/gemini_baseline.csv`` by default -- ``data/frame_pdf/*.csv`` is not
gitignored, ``data/processed/*`` is, and this is a re-derivable report).

No ``GEMINI_API_KEY`` (or ``--dry-run``) -> every bout gets ``gemini_read_frames``'s own
empty-shaped dry-run answer, so matching/reporting runs unchanged with 0 model events.

``--guidance`` swaps the one-call whole-sheet read (``gemini_read_frames.read_frames``) for
the page-by-page guided read (``gemini_read_frames.read_frames_guided``) -- same 10 bouts, same
split/seed, same matcher, so the A (default) vs B (``--guidance``) run is the ONLY variable
between two calls of this script. The Markov guidance model (``fit_markov_model``, fitted on
the full ``matches`` corpus) is fit ONCE per run and reused across bouts, not once per bout.

Privacy class: public competition footage (trials_2023_24), same as the sheets themselves.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.next_moves import MarkovNextMoves  # noqa: E402
from scripts.enrich_from_audit import node_key  # noqa: E402
from scripts.frame_pdf import Entry  # noqa: E402
from scripts.gemini_normalize import slugify  # noqa: E402
from scripts.gemini_read_frames import (  # noqa: E402
    DEFAULT_MODEL,
    empty_answer,
    fit_markov_model,
    load_prompt,
    read_frames,
    read_frames_guided,
    usage_totals,
)

logger = logging.getLogger("gemini_baseline")

TRIALS_DIR = REPO / "data" / "frame_pdf" / "trials_2023_24"
BOUTS_PATH = REPO / "data" / "frame_pdf" / "trials_2023_24_bouts.json"
OUT_DIR = REPO / "data" / "frame_pdf" / "gemini_baseline"
# data/frame_pdf/*.csv is NOT gitignored (only out/ and trials_2023_24/*.pdf are) while
# data/processed/* is -- this is a re-derivable report, not a durable artefact, so it goes
# where the repo already expects generated data to live.
CSV_PATH = REPO / "data" / "processed" / "gemini_baseline.csv"
# Cost guard named in the ticket -- a batch this size is a rounding error per bout
# (docs/video_frames_gemini.md "Cost"), but the cap is enforced here, not just documented.
MAX_N = 10

_HUMAN_MARKERS = ("concordance-audited", "frame_registrar (human")


def is_human_reviewed(source: str) -> bool:
    """True for a concordance-audited answer or a ``frame_registrar`` human save. False for a
    raw model reading, including one whose provenance string happens to contain the substring
    "human" only as part of "not yet human-reviewed"."""
    s = source or ""
    return any(marker in s for marker in _HUMAN_MARKERS)


def label_key(label: str, type_hint: str) -> str:
    """The same canonicalised node key every graph/map consumer compares by."""
    return node_key(str(label or ""), str(type_hint or ""))


@dataclass(frozen=True)
class Candidate:
    slug: str
    sheet_path: Path
    human_path: Path
    bout_label: str


def select_candidates(n: int, *, trials_dir: Path = TRIALS_DIR,
                      bouts_path: Path = BOUTS_PATH) -> list[Candidate]:
    """Up to ``n`` bouts (curated index order) that have BOTH a rendered sheet PDF and a
    concordance-audited ground-truth answer. Anything else is skipped, not guessed at."""
    if n <= 0 or not bouts_path.exists():
        return []
    data = json.loads(bouts_path.read_text(encoding="utf-8"))
    url = str(data.get("url") or "")
    answers_dir = trials_dir / "answers"
    out: list[Candidate] = []
    for b in data.get("bouts", []):
        if len(out) >= n:
            break
        label = str(b.get("label") or "")
        if not label:
            continue
        # Entry.slug is the exact construction frame_pdf.py used to name the rendered PDF.
        entry = Entry(url=url, start=b.get("start"), label=label)
        sheet_path = trials_dir / f"{entry.slug}.pdf"
        if not sheet_path.exists():
            continue
        slug = slugify(label)
        human_path = answers_dir / f"{slug}.events.json"
        if not human_path.exists():
            continue
        try:
            source = json.loads(human_path.read_text(encoding="utf-8")).get("source", "")
        except json.JSONDecodeError:
            continue
        if not is_human_reviewed(source):
            continue
        out.append(Candidate(slug=slug, sheet_path=sheet_path, human_path=human_path,
                             bout_label=label))
    return out


@dataclass(frozen=True)
class Match:
    human_idx: int
    model_idx: int
    ts_diff: int
    actor_match: bool


def match_bout(human: list[dict[str, Any]], model: list[dict[str, Any]],
               ts_tolerance: int = 10) -> list[Match]:
    """Greedy nearest-ts one-to-one matching. A pair is even a CANDIDATE only when type and
    canonical label agree and the timestamps sit within tolerance; among candidates, the
    closest-in-time pair wins first so a human event never gets paired with a distant
    same-label duplicate while a near one goes unmatched."""
    candidates: list[tuple[int, int, int]] = []
    for hi, h in enumerate(human):
        h_key = label_key(str(h.get("label", "")), str(h.get("type", "")))
        for mi, m in enumerate(model):
            if h.get("type") != m.get("type"):
                continue
            if h_key != label_key(str(m.get("label", "")), str(m.get("type", ""))):
                continue
            try:
                diff = abs(int(h.get("ts", 0)) - int(m.get("ts", 0)))
            except (TypeError, ValueError):
                continue
            if diff > ts_tolerance:
                continue
            candidates.append((diff, hi, mi))
    candidates.sort(key=lambda c: c[0])

    used_h: set[int] = set()
    used_m: set[int] = set()
    matches: list[Match] = []
    for diff, hi, mi in candidates:
        if hi in used_h or mi in used_m:
            continue
        used_h.add(hi)
        used_m.add(mi)
        actor_match = (str(human[hi].get("actor", "")).strip().casefold() ==
                      str(model[mi].get("actor", "")).strip().casefold())
        matches.append(Match(human_idx=hi, model_idx=mi, ts_diff=diff, actor_match=actor_match))
    matches.sort(key=lambda m: m.human_idx)
    return matches


def _mean(xs: list[float]) -> float | None:
    return statistics.mean(xs) if xs else None


def bout_metrics(human: list[dict[str, Any]], model: list[dict[str, Any]],
                 matches: list[Match]) -> dict[str, Any]:
    """Per-type TP/support/predicted counts, actor accuracy and mean |ts| error among
    matches, and the model's own high-confidence rate. Precision/recall/F1 are derived from
    the counts by ``precision_recall_f1`` -- kept separate so aggregation can sum raw counts
    across bouts before dividing (micro-average, not an average of ratios)."""
    by_type: dict[str, dict[str, int]] = {}

    def bump(t: str, key: str) -> None:
        by_type.setdefault(t, {"tp": 0, "support": 0, "predicted": 0})[key] += 1

    for h in human:
        bump(str(h.get("type", "")), "support")
    for m in model:
        bump(str(m.get("type", "")), "predicted")
    for match in matches:
        bump(str(human[match.human_idx].get("type", "")), "tp")

    ts_errors = [m.ts_diff for m in matches]
    actor_flags = [m.actor_match for m in matches]
    conf_flags = [e.get("confidence") == "high" for e in model]

    return {
        "n_human": len(human),
        "n_model": len(model),
        "n_matched": len(matches),
        "by_type": by_type,
        "mean_ts_error": _mean(ts_errors),
        "actor_accuracy": _mean(actor_flags),
        "confidence_high_rate": _mean(conf_flags),
    }


def precision_recall_f1(tp: int, support: int, predicted: int) -> tuple[float | None, float | None, float | None]:
    precision = tp / predicted if predicted else None
    recall = tp / support if support else None
    f1 = (2 * precision * recall / (precision + recall)
         if precision and recall and (precision + recall) > 0 else None)
    return precision, recall, f1


def _sum_page_usage(pages: list[dict[str, Any]]) -> dict[str, int]:
    """Guided read's per-page usage dicts (``gemini_read_frames.usage_totals`` shape) -> one
    bout-level total, same 4 fields the whole-sheet path reports."""
    out = {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}
    for p in pages:
        for k in out:
            out[k] += p["usage"][k]
    return out


def process_bout(cand: Candidate, *, model: str, thinking: str, dry_run: bool,
                 out_dir: Path, guidance: bool = False,
                 markov_model: MarkovNextMoves | None = None) -> dict[str, Any]:
    """One zero-shot Gemini read of ``cand.sheet_path``, saved under its own directory. Never
    opens ``cand.human_path`` for writing. ``guidance=True`` reads page-by-page
    (``gemini_read_frames.read_frames_guided``) instead of the whole sheet in one call."""
    bout_dir = out_dir / cand.slug
    bout_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    usage: dict[str, int] | None = None
    if dry_run or not api_key:
        answer = empty_answer("no GEMINI_API_KEY" if not api_key else "--dry-run")
    elif guidance:
        prompt = load_prompt()
        answer, pages = read_frames_guided(cand.sheet_path, prompt, model, thinking,
                                           markov_model=markov_model)
        usage = _sum_page_usage(pages)
        (bout_dir / "gemini_raw.json").write_text(json.dumps({
            "model": model, "thinking": thinking, "guidance": True, "pages": pages,
        }, indent=2), encoding="utf-8")
    else:
        prompt = load_prompt()
        answer, resp = read_frames([cand.sheet_path], prompt, model, thinking)
        usage = usage_totals(resp.usage_metadata)
        (bout_dir / "gemini_raw.json").write_text(json.dumps({
            "model": model, "thinking": thinking, "text": resp.text,
            "usage_metadata": (resp.usage_metadata.model_dump(mode="json")
                              if resp.usage_metadata else None),
        }, indent=2), encoding="utf-8")
    (bout_dir / "gemini_baseline.json").write_text(json.dumps(answer, indent=2), encoding="utf-8")
    return {"answer": answer, "usage": usage}


def write_csv(csv_path: Path, rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["slug", "n_human", "n_model", "n_matched", "mean_ts_error", "actor_accuracy",
             "confidence_high_rate"]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})
        w.writerow({"slug": "AGGREGATE", "n_human": sum(r["n_human"] for r in rows),
                   "n_model": sum(r["n_model"] for r in rows),
                   "n_matched": sum(r["n_matched"] for r in rows),
                   "mean_ts_error": aggregate["mean_ts_error"],
                   "actor_accuracy": aggregate["actor_accuracy"],
                   "confidence_high_rate": aggregate["confidence_high_rate"]})


def run(n: int, *, model: str = DEFAULT_MODEL, thinking: str = "high", ts_tolerance: int = 10,
       dry_run: bool = False, guidance: bool = False, out_dir: Path = OUT_DIR,
       csv_path: Path = CSV_PATH, trials_dir: Path = TRIALS_DIR,
       bouts_path: Path = BOUTS_PATH) -> dict[str, Any]:
    capped = min(n, MAX_N)
    if n > MAX_N:
        logger.warning("--n %d capped to %d (cost guard)", n, MAX_N)
    candidates = select_candidates(capped, trials_dir=trials_dir, bouts_path=bouts_path)
    if not candidates:
        logger.warning("no eligible bouts (sheet + concordance-audited answer) under %s",
                       trials_dir)
    # Fit ONCE, reused for every bout -- refitting per bout would be the same corpus 10x over.
    markov_model = fit_markov_model() if guidance and not dry_run else None

    rows: list[dict[str, Any]] = []
    agg_by_type: dict[str, dict[str, int]] = {}
    agg_ts_errors: list[float] = []
    agg_actor_flags: list[bool] = []
    agg_conf_flags: list[bool] = []
    usage_sum = {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0}

    for cand in candidates:
        human = json.loads(cand.human_path.read_text(encoding="utf-8")).get("events") or []
        result = process_bout(cand, model=model, thinking=thinking, dry_run=dry_run,
                              out_dir=out_dir, guidance=guidance, markov_model=markov_model)
        model_events = result["answer"].get("events") or []
        matches = match_bout(human, model_events, ts_tolerance=ts_tolerance)
        metrics = bout_metrics(human, model_events, matches)
        rows.append({"slug": cand.slug, **metrics})

        for t, c in metrics["by_type"].items():
            a = agg_by_type.setdefault(t, {"tp": 0, "support": 0, "predicted": 0})
            for k in c:
                a[k] += c[k]
        agg_ts_errors += [m.ts_diff for m in matches]
        agg_actor_flags += [m.actor_match for m in matches]
        agg_conf_flags += [e.get("confidence") == "high" for e in model_events]

        usage = result["usage"]
        if usage:
            for k in usage_sum:
                usage_sum[k] += usage[k]

    by_type_report = {}
    for t, c in agg_by_type.items():
        p, r, f1 = precision_recall_f1(c["tp"], c["support"], c["predicted"])
        by_type_report[t] = {**c, "precision": p, "recall": r, "f1": f1}

    aggregate = {
        "n_bouts": len(candidates),
        "guidance": guidance,
        "by_type": by_type_report,
        "mean_ts_error": _mean(agg_ts_errors),
        "actor_accuracy": _mean(agg_actor_flags),
        "confidence_high_rate": _mean(agg_conf_flags),
        "total_usage": usage_sum,
    }
    write_csv(csv_path, rows, aggregate)
    return {"rows": rows, "aggregate": aggregate}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=MAX_N, help=f"bouts to read (capped at {MAX_N})")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--thinking", choices=("low", "medium", "high"), default="high")
    ap.add_argument("--ts-tolerance", type=int, default=10, help="seconds, per event match")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--guidance", action="store_true",
                    help="page-by-page read with a Markov next-move prompt hint "
                         "(gemini_read_frames.read_frames_guided) instead of one whole-sheet "
                         "call -- pass distinct --out-dir/--csv from the A run so B doesn't "
                         "overwrite it")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--csv", type=Path, default=CSV_PATH)
    a = ap.parse_args()

    result = run(a.n, model=a.model, thinking=a.thinking, ts_tolerance=a.ts_tolerance,
                dry_run=a.dry_run, guidance=a.guidance, out_dir=a.out_dir, csv_path=a.csv)
    print(json.dumps(result["aggregate"], indent=2))
    logger.info("wrote %s (%d bout rows)", a.csv, len(result["rows"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
