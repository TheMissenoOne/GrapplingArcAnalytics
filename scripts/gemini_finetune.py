#!/usr/bin/env python
"""Run (and evaluate) a Gemini supervised-tuning job over the vision dataset's Vertex export.

    uv run python -m scripts.gemini_finetune --dry-run
    uv run python -m scripts.gemini_finetune --check-api-key      # the API-key verdict
    uv run python -m scripts.gemini_finetune --list-models
    uv run python -m scripts.gemini_finetune --upload --gcs-prefix gs://bucket/ga/v1
    uv run python -m scripts.gemini_finetune --tune  --gcs-prefix gs://bucket/ga/v1
    uv run python -m scripts.gemini_finetune --status projects/…/tuningJobs/123
    uv run python -m scripts.gemini_finetune --eval  --model tunedModels/…   [--thinking low]

Nothing here builds data. ``scripts/vision_dataset.py`` owns the dataset and
``scripts/vision_dataset_export.py vertex-sft`` owns the JSONL; this script only moves that
export to Google and measures what comes back.

**API key vs Vertex.** Multimodal tuning is Vertex-only. The Gemini Developer API (an API
key) accepts ``TuningDataset(examples=[TuningExample(text_input=…, output=…)])`` — a
TEXT-ONLY pair, no image or file part exists in that type — and the SDK refuses a
``gcs_uri`` outright. ``--check-api-key`` demonstrates it against the installed SDK rather
than asserting it. Consequence: the owner needs a GCP project, billing, the Vertex AI API,
a bucket and ADC. Checklist in ``docs/vision_dataset.md``.

**Evaluation.** ``--eval`` runs the val split's sheets through a model and scores it against
the human labels with one matcher: an event matches if ``type`` is equal, the canonical node
key is equal, and ``|Δts| ≤ 10s`` — the same tolerance the concordance audit uses to call a
reading CONCORDANT. Actor agreement is reported separately over matched pairs, because a
whole-bout identity swap is the measured recurring defect and it does not move F1 at all.
The pre-registered success criterion is in the doc: the tuned model must beat the zero-shot
baseline's F1 ON THE SAME val bouts, not on a re-cut split.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.gemini_baseline import (  # noqa: E402
    bout_metrics,
    match_bout,
    precision_recall_f1,
)
from scripts.vision_dataset import DATASET  # noqa: E402
from scripts.vision_dataset_export import (  # noqa: E402
    admissible,
    load_labels,
    load_split,
)

logger = logging.getLogger("gemini_finetune")

EXPORT = "exports/vertex_sft"
TS_TOLERANCE = 10  # seconds; the concordance audit's own CONCORDANT window
# Matches scripts/gemini_read_frames.DEFAULT_MODEL — the inference target. Measured 2026-09-02:
# `gemini-2.5-flash` is 404 for new API-key users ("no longer available to new users"), so a
# hardcoded generation of a model name goes stale. `--list-models` is the authority for what
# Vertex will actually TUNE; this default only keeps --dry-run readable.
DEFAULT_BASE_MODEL = "gemini-3.6-flash"


# ------------------------------------------------------------------- api-key verdict

def check_api_key() -> int:
    """Try multimodal tuning through the Developer API and print what actually happens."""
    from google.genai import types

    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY not set — cannot run the live attempt.")
        return 1

    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    print(f"Developer API client: vertexai={client._api_client.vertexai}")

    print("\n[1] gcs_uri (what a multimodal dataset needs) through an API key:")
    try:
        client.tunings.tune(
            base_model=DEFAULT_BASE_MODEL,
            training_dataset=types.TuningDataset(
                gcs_uri="gs://example/train.jsonl"))
        print("    UNEXPECTED: accepted.")
        return 1
    except ValueError as exc:
        print(f"    ValueError: {exc}")

    print("\n[2] the only dataset shape an API key accepts (types.TuningExample):")
    print(f"    fields = {sorted(types.TuningExample.model_fields)}")
    print("    -> text_input/output only. No Part, no fileData, no inline image: the "
          "Developer API tuning dataset has no place to put a frame sheet.")
    return 0


# ------------------------------------------------------------------------- vertex io

@dataclass
class VertexEnv:
    project: str
    location: str

    @classmethod
    def read(cls) -> VertexEnv:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        missing = [] if project else ["GOOGLE_CLOUD_PROJECT"]
        if missing:
            raise SystemExit(
                f"missing env: {', '.join(missing)}. Vertex tuning needs a project, "
                "GOOGLE_CLOUD_LOCATION (default us-central1), and Application Default "
                "Credentials (`gcloud auth application-default login`). "
                "See docs/vision_dataset.md.")
        return cls(project, location)


def vertex_client() -> Any:
    from google import genai

    env = VertexEnv.read()
    return genai.Client(vertexai=True, project=env.project, location=env.location)


def list_tunable_models() -> int:
    """Ask Vertex which base models it will tune. Listed, never guessed."""
    client = vertexai_or_die()
    rows = []
    for m in client.models.list():
        actions = set(getattr(m, "supported_actions", None) or [])
        if {"createTunedModel", "tunedModels.create", "TUNING"} & actions or "tune" in actions:
            rows.append((m.name, sorted(actions)))
    if not rows:
        print("models.list() returned no entry advertising a tuning action. Vertex does not "
              "always expose supported_actions; fall back to the tuning docs' supported-model "
              "table and pass --base-model explicitly.")
        for m in list(client.models.list())[:20]:
            print(f"  {m.name}")
        return 1
    for name, actions in rows:
        print(f"  {name}  {actions}")
    return 0


def vertexai_or_die() -> Any:
    client = vertex_client()
    if not client._api_client.vertexai:
        raise SystemExit("client is not in Vertex mode")
    return client


def upload(gcs_prefix: str, dataset: Path, dry_run: bool) -> int:
    """Push the sheets + JSONL to the bucket, or print the commands that would."""
    export = dataset / EXPORT
    manifest = export / "upload_manifest.tsv"
    if not manifest.exists():
        raise SystemExit(f"{manifest} missing — run "
                         "`python -m scripts.vision_dataset_export vertex-sft` first")
    rows = [ln.split("\t") for ln in manifest.read_text().splitlines()[1:] if ln]
    prefix = gcs_prefix.rstrip("/")
    jsonl = [(export / f"{s}.jsonl", f"{prefix}/{s}.jsonl") for s in ("train", "val")]
    total = sum(int(r[2]) for r in rows) + sum(p.stat().st_size for p, _ in jsonl)
    print(f"{len(rows)} sheet(s) + {len(jsonl)} JSONL = {total / 1e6:.0f} MB -> {prefix}")

    try:
        from google.cloud import storage  # type: ignore[import-not-found]
    except ImportError:
        print("\ngoogle-cloud-storage not installed. Either `uv pip install "
              "google-cloud-storage`, or run:\n")
        print(f"  gcloud storage cp {export}/train.jsonl {export}/val.jsonl {prefix}/")
        print(f"  gcloud storage cp {dataset}/sheets/*.pdf {prefix}/sheets/")
        return 0
    if dry_run:
        print("\n--dry-run: would upload with google-cloud-storage; nothing sent.")
        return 0
    bucket_name = prefix.removeprefix("gs://").split("/", 1)[0]
    bucket = storage.Client().bucket(bucket_name)
    for local, uri in [(Path(r[0]), r[1]) for r in rows] + jsonl:
        blob = bucket.blob(uri.removeprefix(f"gs://{bucket_name}/"))
        blob.upload_from_filename(str(local))
        logger.info("uploaded %s", uri)
    return 0


def tune(gcs_prefix: str, base_model: str, epochs: int | None, adapter: int | None,
         display_name: str, dry_run: bool) -> int:
    from google.genai import types

    prefix = gcs_prefix.rstrip("/")
    train_uri, val_uri = f"{prefix}/train.jsonl", f"{prefix}/val.jsonl"
    cfg: dict[str, Any] = {"tuned_model_display_name": display_name,
                           "validation_dataset": types.TuningValidationDataset(gcs_uri=val_uri)}
    if epochs:
        cfg["epoch_count"] = epochs
    if adapter:
        cfg["adapter_size"] = adapter
    if dry_run:
        print(json.dumps({"base_model": base_model, "training_dataset": train_uri,
                          "validation_dataset": val_uri,
                          "config": {k: str(v) for k, v in cfg.items()}}, indent=1))
        print("\n--dry-run: no job created, nothing billed.")
        return 0
    client = vertexai_or_die()
    job = client.tunings.tune(base_model=base_model,
                              training_dataset=types.TuningDataset(gcs_uri=train_uri),
                              config=types.CreateTuningJobConfig(**cfg))
    print(f"{job.name}\nstate={job.state}")
    return 0


def status(name: str) -> int:
    client = vertexai_or_die()
    job = client.tunings.get(name=name)
    print(json.dumps({"name": job.name, "state": str(job.state),
                      "tuned_model": getattr(job.tuned_model, "model", None),
                      "endpoint": getattr(job.tuned_model, "endpoint", None),
                      "error": str(job.error) if job.error else None}, indent=1))
    return 0


# ----------------------------------------------------------------------- evaluation

# The matcher is imported, not reimplemented: `scripts/gemini_baseline.py` owns it, the
# zero-shot baseline was measured with it, and a tuned-vs-baseline comparison is only a
# comparison if both sides ran the same rule (type == type, canonical label ==, |Δts| <= 10s,
# greedy nearest-ts one-to-one).

# Pre-registered success criterion. Baseline measured 2026-09-02 (gemini-3.6-flash,
# thinking=high, N=10 concordance-audited trials bouts): P 0.39 / R 0.29 / F1 0.34, ts error
# 1.1s, actor 90%. Failure is concentrated in LABEL DISCRIMINATION, not timing or identity.
BASELINE_F1 = 0.34
MIN_F1_GAIN = 0.10


def truth_events(dataset: Path, slug: str) -> list[dict[str, Any]]:
    return [{"ts": ln["event_ts"], "label": ln["label"], "type": ln["type"],
             "actor": ln["actor"], "successful": ln["successful"]}
            for ln in load_labels(dataset, slug) if admissible(ln)]


def evaluate(dataset: Path, version: str, model: str, thinking: str | None,
             out: Path | None, dry_run: bool) -> int:
    """Read every val sheet with ``model`` and score it against the human labels."""
    from scripts.gemini_read_frames import load_prompt

    split = load_split(dataset, version)
    prompt = load_prompt()
    out = out or dataset / "exports" / "eval"
    out.mkdir(parents=True, exist_ok=True)

    per_bout: list[dict[str, Any]] = []
    for slug in split["val"]:
        sheet = dataset / "sheets" / f"{slug}.pdf"
        truth = truth_events(dataset, slug)
        if dry_run:
            per_bout.append({"bout": slug, "truth_events": len(truth),
                             "sheet": str(sheet), "would_send_bytes": sheet.stat().st_size})
            continue
        pred = _read_sheet(sheet, prompt, model, thinking)
        (out / f"{slug}.pred.json").write_text(json.dumps(pred, indent=1), encoding="utf-8")
        events = pred.get("events", [])
        matches = match_bout(truth, events, TS_TOLERANCE)
        per_bout.append({"bout": slug, **bout_metrics(truth, events, matches)})

    if dry_run:
        total = sum(r["would_send_bytes"] for r in per_bout)
        print(json.dumps({"model": model, "val_bouts": len(per_bout),
                          "truth_events": sum(r["truth_events"] for r in per_bout),
                          "bytes_to_send": total, "per_bout": per_bout}, indent=1))
        print("\n--dry-run: no request made, nothing billed.")
        return 0

    # Micro-average: sum raw counts across bouts, THEN divide. An average of per-bout ratios
    # would let a 2-event bout weigh as much as a 19-event one.
    tp = sum(r["n_matched"] for r in per_bout)
    support = sum(r["n_human"] for r in per_bout)
    predicted = sum(r["n_model"] for r in per_bout)
    precision, recall, f1 = precision_recall_f1(tp, support, predicted)
    target = BASELINE_F1 + MIN_F1_GAIN
    report = {
        "model": model, "thinking": thinking, "split": version,
        "tolerance_seconds": TS_TOLERANCE,
        "micro": {"tp": tp, "support": support, "predicted": predicted,
                  "precision": precision, "recall": recall, "f1": f1},
        "pre_registered": {"baseline_f1": BASELINE_F1, "min_gain": MIN_F1_GAIN,
                           "target_f1": target,
                           "verdict": ("PASS" if (f1 or 0) >= target else "FAIL")},
        "per_bout": per_bout,
    }
    (out / f"report-{model.replace('/', '_')}.json").write_text(
        json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({**report["micro"], **report["pre_registered"]}, indent=1))
    return 0


def _read_sheet(sheet: Path, prompt: str, model: str, thinking: str | None) -> dict[str, Any]:
    """One sheet -> one answer, through whichever client the model name implies.

    A tuned Vertex model is addressed on the Vertex client; a plain model name works on
    either. Reuses ``gemini_read_frames``'s config builder so a tuned run and a zero-shot
    baseline differ only in the model string.
    """
    from google.genai import types

    from scripts.gemini_read_frames import build_generate_config

    client = vertex_client() if model.startswith("projects/") else _key_client()
    parts = [types.Part.from_bytes(data=sheet.read_bytes(), mime_type="application/pdf"),
             types.Part.from_text(text=prompt)]
    resp = client.models.generate_content(model=model, contents=parts,
                                          config=build_generate_config(thinking))
    return json.loads(resp.text)


def _key_client() -> Any:
    from google import genai

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY not set")
    return genai.Client(api_key=key)


# ----------------------------------------------------------------------------- cli

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--gcs-prefix", default="gs://REPLACE-ME-bucket/grapplingarc/v1")
    ap.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    ap.add_argument("--display-name", default="grapplingarc-frame-reader")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--adapter-size", type=int, choices=[1, 2, 4, 8, 16])
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be sent; never calls a paid endpoint")
    ap.add_argument("--check-api-key", action="store_true")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--tune", action="store_true")
    ap.add_argument("--status")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--model", help="--eval: model to score (tuned resource name or base)")
    ap.add_argument("--thinking", choices=["low", "medium", "high"])
    a = ap.parse_args()

    if a.check_api_key:
        return check_api_key()
    if a.list_models:
        return list_tunable_models()
    if a.upload:
        return upload(a.gcs_prefix, a.dataset, a.dry_run)
    if a.tune:
        return tune(a.gcs_prefix, a.base_model, a.epochs, a.adapter_size,
                    a.display_name, a.dry_run)
    if a.status:
        return status(a.status)
    if a.eval:
        if not a.model and not a.dry_run:
            ap.error("--eval needs --model (or --dry-run)")
        return evaluate(a.dataset, a.version, a.model or a.base_model, a.thinking,
                        None, a.dry_run)
    if a.dry_run:
        # Bare --dry-run: the whole plan, end to end, without touching anything.
        upload(a.gcs_prefix, a.dataset, True)
        print()
        tune(a.gcs_prefix, a.base_model, a.epochs, a.adapter_size, a.display_name, True)
        print()
        return evaluate(a.dataset, a.version, a.base_model, a.thinking, None, True)
    ap.error("pick one of --dry-run/--check-api-key/--list-models/--upload/--tune/"
             "--status/--eval")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
