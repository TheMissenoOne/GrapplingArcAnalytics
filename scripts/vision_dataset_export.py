#!/usr/bin/env python
"""Exporters over a `data/finetune/` split — every consumer is a FUNCTION OF the dataset.

    uv run python -m scripts.vision_dataset_export vertex-sft --gcs-prefix gs://bucket/ga-v1
    uv run python -m scripts.vision_dataset_export frame-classification

Nothing here is the storage format. The dataset (``scripts/vision_dataset.py``) is frames +
labels + a frozen split; an exporter reshapes ONE split for ONE consumer and writes under
``data/finetune/exports/<name>/``. Adding a consumer must never change the dataset.

Three consumers, two of them real:

``vertex-sft``
    Vertex AI supervised fine-tuning, multimodal JSONL. One example = one BOUT: the whole
    sheet PDF plus the canonical reading prompt in, the audited answer JSON out. That shape
    is chosen by the INFERENCE target, not by convenience -- ``scripts/gemini_read_frames.py``
    sends the whole sheet in one call, so a model tuned on single pages would be asked at
    serving time for something it never saw. Only ``source == "human"`` (or human-accepted)
    labels are admissible; model readings are training data for nothing.

``frame-classification``
    frame -> (state, action, actor, successful) rows for our OWN vision model. One row per
    labelled frame, absolute image path, split column. CSV plus the same content as JSONL.

``coco``
    Stub, deliberately. COCO's payload is boxes/segmentation and this corpus has none: the
    sheets are whole 1280x720 broadcast frames with no athlete boxes anywhere in the
    pipeline. Named here so nobody re-derives that it is missing; it becomes real the day a
    detector or a human puts boxes on frames.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.gemini_read_frames import load_prompt  # noqa: E402
from scripts.vision_dataset import DATASET  # noqa: E402

logger = logging.getLogger("vision_dataset_export")

PDF_MIME = "application/pdf"
# Vertex tuning, document modality (docs.cloud.google.com, "Document tuning"): max 300 PDF
# pages and 4 PDF files per example, 20 MB per file. Only the size cap is enforced -- our
# sheets are 7-39 pages against a 300-page ceiling, so a page check would be dead code, while
# the largest sheet (19.3 MiB) sits 3% under the size cap and one re-render could cross it.
MAX_SHEET_BYTES = 20 * 1024 * 1024


def load_split(dataset: Path, version: str) -> dict[str, Any]:
    split: dict[str, Any] = json.loads(
        (dataset / "splits" / f"{version}.json").read_text(encoding="utf-8"))
    return split


def load_labels(dataset: Path, slug: str) -> list[dict[str, Any]]:
    path = dataset / "labels" / f"{slug}.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln]


def admissible(line: dict[str, Any]) -> bool:
    """A label a model may learn from: human-originated, or model-originated and accepted.

    ``source`` is never rewritten on review (that laundering is the ``frame_registrar`` bug
    of 2026-08-24), so admissibility is the disjunction, not a string test on origin alone.
    """
    return line.get("source") == "human" or line.get("review") == "accepted"


# ------------------------------------------------------------------------ vertex sft

# Bout-header keys the target keeps. `notes`, `identity_verified_by` and the audited
# `identity_discriminator` are AUDIT ARTEFACTS -- the auditor rewrote them citing internal
# page filenames and verdicts ("no winner-swap flag", "verified frames: ...-05.jpg"). Teaching
# a model to emit that register is teaching it to fabricate a provenance it cannot have, so
# `identity_discriminator` is taken back from the model's own pre-audit reading (which the
# audit verified) and the other two are dropped.
TARGET_BOUT_KEYS = ("athlete_a", "athlete_b", "event", "year", "winner", "win_type",
                    "bout_start_seconds", "bout_end_seconds", "identity_discriminator",
                    "final_score", "advantages", "penalties")


def _answer_from_labels(slug: str, lines: list[dict[str, Any]],
                        source_answer: dict[str, Any],
                        raw_answer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild the target JSON from the DATASET, not from the answer file.

    Round-tripping through the labels is the point: the target a tuned model learns to emit
    is exactly what survived review, in the canonical vocabulary, with provenance stripped
    (a model must never learn to emit a ``source`` stamp claiming it was human-reviewed).
    """
    header = dict(source_answer.get("bout", {}))
    # ponytail: pre-audit discriminator or none at all — never the audited one. Measured
    # 2026-09-02: 34 of 62 bouts have a pre-audit discriminator, so 28 targets carry no
    # discriminator. That risks teaching "omit this field"; carrying the audited text
    # instead teaches a register the model provably cannot produce (it cites page filenames
    # and `audit.flags=[]`), which is worse. Upgrade path: have the audit record its
    # verified discriminator in the model's own register, then take it from there.
    header["identity_discriminator"] = \
        (raw_answer or {}).get("bout", {}).get("identity_discriminator")
    bout = {k: header[k] for k in TARGET_BOUT_KEYS
            if header.get(k) not in (None, "", {}, [])}
    events = []
    for ln in sorted(lines, key=lambda r: (r["event_ts"], r["node_key"], r["actor_key"])):
        ev = {"ts": ln["event_ts"], "label": ln["label"], "actor": ln["actor"],
              "successful": ln["successful"], "type": ln["type"]}
        if "points" in ln:
            ev["points"] = ln["points"]
        events.append(ev)
    return {"bout": bout, "events": events}


def export_vertex_sft(dataset: Path, version: str, gcs_prefix: str,
                      out: Path | None = None) -> dict[str, Any]:
    """Write ``train.jsonl``/``val.jsonl`` + the upload manifest for Vertex SFT."""
    split = load_split(dataset, version)
    manifest = json.loads(
        (dataset / "manifests" / f"{version}.json").read_text(encoding="utf-8"))
    sheets = {b["slug"]: b for b in manifest["bouts"]}
    prompt = load_prompt()
    out = out or dataset / "exports" / "vertex_sft"
    out.mkdir(parents=True, exist_ok=True)
    prefix = gcs_prefix.rstrip("/")

    uploads: dict[str, Path] = {}
    stats: dict[str, Any] = {"prompt_chars": len(prompt), "gcs_prefix": prefix,
                             "skipped": {}, "splits": {}}
    for side in ("train", "val"):
        rows = []
        for slug in split[side]:
            lines = [ln for ln in load_labels(dataset, slug) if admissible(ln)]
            if not lines:
                stats["skipped"][slug] = "no admissible (human/accepted) labels"
                continue
            meta = sheets.get(slug)
            sheet = (dataset / "sheets" / f"{slug}.pdf")
            if meta is None or not sheet.exists():
                stats["skipped"][slug] = "no sheet"
                continue
            if meta["sheet_bytes"] > MAX_SHEET_BYTES:
                stats["skipped"][slug] = (
                    f"sheet {meta['sheet_bytes'] / 1e6:.1f} MB over Vertex's 20 MB per-file "
                    "limit — re-render at a lower JPEG quality or split it (4 PDFs/example "
                    "are allowed)")
                continue
            src_answer, raw_answer = _source_answer(slug)
            if not raw_answer.get("bout", {}).get("identity_discriminator"):
                stats["no_pre_audit_discriminator"] = \
                    stats.get("no_pre_audit_discriminator", 0) + 1
            target = _answer_from_labels(slug, lines, src_answer, raw_answer)
            uri = f"{prefix}/sheets/{slug}.pdf"
            uploads[uri] = sheet.resolve()
            rows.append({"contents": [
                {"role": "user", "parts": [
                    {"fileData": {"mimeType": PDF_MIME, "fileUri": uri}},
                    {"text": prompt}]},
                {"role": "model", "parts": [
                    {"text": json.dumps(target, ensure_ascii=False)}]}]})
        path = out / f"{side}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                        encoding="utf-8")
        stats["splits"][side] = {"examples": len(rows), "file": str(path),
                                 "bytes": path.stat().st_size}

    with (out / "upload_manifest.tsv").open("w", encoding="utf-8") as fh:
        fh.write("local_path\tgcs_uri\tbytes\n")
        for uri, local in sorted(uploads.items()):
            fh.write(f"{local}\t{uri}\t{local.stat().st_size}\n")
    stats["uploads"] = len(uploads)
    stats["upload_bytes"] = sum(p.stat().st_size for p in uploads.values())
    (out / "stats.json").write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n",
                                    encoding="utf-8")
    return stats


def _source_answer(slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(audited answer, pre-audit model answer) for a bout's non-per-frame header fields.

    The dataset stores per-frame labels; ``bout`` metadata (names, winner, clock bounds,
    identity discriminator) is not per frame and stays in the answer file. Looked up rather
    than duplicated so there is one copy of it.
    """
    from scripts.vision_dataset import default_sources

    found: dict[str, dict[str, Any]] = {}
    for src in default_sources():
        for cand in (src.answers / f"{slug}.events.json", src.answers / f"{slug}.json"):
            if cand.exists() and src.origin not in found:
                found[src.origin] = json.loads(cand.read_text(encoding="utf-8"))
    return found.get("human", {}), found.get("gemini", {})


# ---------------------------------------------------------------- frame classification

FRAME_FIELDS = ("frame", "bout", "split", "ts", "ts_ms", "state", "action", "node_key",
                "type", "actor_key", "successful", "source", "review", "confidence")


def export_frame_classification(dataset: Path, version: str,
                                out: Path | None = None,
                                include_model_labels: bool = False) -> dict[str, Any]:
    """frame -> label rows for our own classifier. CSV + JSONL, absolute image paths."""
    split = load_split(dataset, version)
    out = out or dataset / "exports" / "frame_classification"
    out.mkdir(parents=True, exist_ok=True)
    side = {s: "train" for s in split["train"]} | {s: "val" for s in split["val"]}

    rows: list[dict[str, Any]] = []
    per_split: dict[str, int] = defaultdict(int)
    for slug, sp in sorted(side.items()):
        for ln in load_labels(dataset, slug):
            if not include_model_labels and not admissible(ln):
                continue
            rows.append({
                "frame": str((dataset / ln["frame"]).resolve()), "bout": slug, "split": sp,
                "ts": ln["ts"], "ts_ms": ln["ts_ms"], "state": ln["state"],
                "action": ln["action"], "node_key": ln["node_key"], "type": ln["type"],
                "actor_key": ln["actor_key"], "successful": ln["successful"],
                "source": ln["source"], "review": ln["review"],
                "confidence": ln["confidence"]})
            per_split[sp] += 1
    rows.sort(key=lambda r: (r["split"], r["bout"], r["ts_ms"], r["node_key"]))

    with (out / "frames.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(FRAME_FIELDS))
        w.writeheader()
        w.writerows(rows)
    (out / "frames.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8")
    stats = {"rows": len(rows), "by_split": dict(sorted(per_split.items())),
             "include_model_labels": include_model_labels,
             "classes": len({r["node_key"] for r in rows})}
    (out / "stats.json").write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n",
                                    encoding="utf-8")
    return stats


def export_coco(dataset: Path, version: str) -> None:
    raise NotImplementedError(
        "no COCO export: this corpus carries no bounding boxes or masks. Frames are whole "
        "1280x720 broadcast stills and nothing upstream (frame_pdf.py, the reading prompt, "
        "the concordance audit) ever localises an athlete in one. Wire this up only once a "
        "detector or a human annotator puts boxes on frames — until then a COCO file would "
        "be one image-level class per whole image, which frame_classification already is.")


# ----------------------------------------------------------------------------- cli

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("consumer", choices=["vertex-sft", "frame-classification", "coco"])
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--gcs-prefix", default="gs://REPLACE-ME-bucket/grapplingarc/v1",
                    help="gs:// prefix the sheets will be uploaded under (vertex-sft)")
    ap.add_argument("--include-model-labels", action="store_true",
                    help="frame-classification: also emit unreviewed gemini labels")
    a = ap.parse_args()

    if a.consumer == "vertex-sft":
        if a.gcs_prefix.startswith("gs://REPLACE-ME"):
            logger.warning("--gcs-prefix is the placeholder; fileUri values are not real yet")
        print(json.dumps(export_vertex_sft(a.dataset, a.version, a.gcs_prefix), indent=1))
    elif a.consumer == "frame-classification":
        print(json.dumps(export_frame_classification(
            a.dataset, a.version, include_model_labels=a.include_model_labels), indent=1))
    else:
        export_coco(a.dataset, a.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
