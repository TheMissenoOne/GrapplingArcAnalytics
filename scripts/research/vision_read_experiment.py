"""Gemini vision-read configuration sweep — harness only. NO calls until the owner says the
reference reads exist (``data/frame_pdf/out/experiments/<bout>/reference_fable.json``, written
by independent Fable subagents, same schema this repo's readers already use).

    uv run python -m scripts.research.vision_read_experiment --dry-run
    uv run python -m scripts.research.vision_read_experiment --smoke
    uv run python -m scripts.research.vision_read_experiment [grid flags...]
    uv run python -m scripts.research.vision_read_experiment --report [--pairwise]

## Bouts

Four adaptive-sampled sheets under ``data/frame_pdf/lorenzo_bernardi/<slug>_adaptive/`` (each
with ``motion.json``/``decision.json`` beside ``sheets/<slug>_adaptive.pdf``):
``flo_raphelson`` (Lorenzo Bernardi vs Raphelson Tarcisio Felipetto Alves Pereira, ADCC Brazil
Open 2023, 7:48, FloGrappling), ``lima`` (Lorenzo Bernardi vs Lucas Lima, ADCC São Paulo Open
2024, 2:18, FloGrappling), ``balil2025`` (Lorenzo Bernardi vs Gustavo Balil Mendonça Rodrigues,
ADCC São Paulo Open 2025, 7:24, FloGrappling, scoreboard visible), ``durans`` (Lorenzo Bernardi
vs Henrique Durans, Thunder Fight GP / Arnold Sports 2025, 5:08, YouTube, moving camera) --
owner-supplied 2026-09-14, in :data:`BOUTS`. These are the two CANDIDATE names per bout, not a
pre-established kit binding: the sheet's own page 1 (confirmed by reading it) carries only the
source filename + sampling stats, no identity, so :func:`bout_context_note` states the names
alongside an explicit instruction that the model must still work out which kit is which itself
(pages 2-4 are the "Allowed labels" vocabulary, 376 techniques; frame grids start at page 5).

## Models

Verified against the installed SDK (``client.models.list()``, 2026-09-14): ``gemini-3.6-flash``
is live (matches ``scripts/gemini_read_frames.DEFAULT_MODEL``, the existing repo convention).
There is no ``gemini-3.6-pro`` — no dated "pro" id exists at all; the closest is the rolling
alias ``gemini-pro-latest`` or a dated preview (``gemini-3.1-pro-preview``). Default here is
``gemini-pro-latest`` (stable alias, not a preview) — pass ``--models`` to try
``gemini-3.1-pro-preview`` or a flash-lite variant (``gemini-3.1-flash-lite``) instead.

## Input arms

``--input pdf`` (default) sends the sheet PDF as one file part, same as
``gemini_read_frames.read_frames``. ``--input jpeg`` rasterises every page to JPEG first —
PyMuPDF (``fitz``) if installed, else ``pdftoppm`` (poppler-utils, verified present on this
machine, no new dependency) — and sends one image part per page instead.
:func:`jpeg_arm_available` reports which path (if any) is usable.

## Matcher

:func:`match_events` — greedy nearest-``ts`` one-to-one, a pair is a candidate when ``type`` is
equal, the canonical label (``scripts.enrich_from_audit.node_key``, i.e. ``clean_label ->
_normalize_name -> canonicalize`` — the exact chain ``scripts/gemini_baseline.py`` already
scores against and every graph/map consumer in this repo shares) is equal, ``|Δts| <= tol``
(default 10s, the concordance audit's own window), and — unless ``ignore_actor`` — the actor
matches via ``analysis.names.athlete_key``. Same shape as ``gemini_baseline.match_bout``, not
reimplemented from scratch; this version adds the actor toggle (for the relaxed variant) and
normalises actors through ``athlete_key`` instead of a bare casefold, since Fable's reference
and Gemini's own reading will not always spell a name identically.

Privacy class: public competition footage (same corpus class as ``trials_2023_24``).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from analysis.names import athlete_key  # noqa: E402
from scripts.enrich_from_audit import node_key  # noqa: E402
from scripts.frame_answer import load_labels  # noqa: E402
from scripts.frame_answer import validate as validate_answer
from scripts.gemini_baseline import precision_recall_f1  # noqa: E402
from scripts.gemini_read_frames import (  # noqa: E402
    build_generate_config,
    load_prompt,
    usage_totals,
)

logger = logging.getLogger("vision_read_experiment")

DATA_ROOT = REPO / "data" / "frame_pdf" / "lorenzo_bernardi"
OUT_ROOT = REPO / "data" / "frame_pdf" / "out" / "experiments"
REPORT_OUT = REPO / "docs" / "research" / "2026-09-14_vision_read_experiment.md"
REFERENCE_NAME = "reference_fable.json"

DEFAULT_MODELS = ["gemini-3.6-flash", "gemini-pro-latest"]
DEFAULT_TEMPERATURES = [0.0, 0.5, 1.0]
DEFAULT_INPUTS = ["pdf"]
SMOKE_BOUT = "lima"  # cheapest sheet (28 frames, 2:18) -- proves the wiring, not the model


@dataclass(frozen=True)
class BoutSpec:
    slug: str
    duration_label: str
    source: str
    camera: str
    athlete_a: str = ""
    athlete_b: str = ""
    event: str = ""
    year: int | None = None
    scoreboard: bool = False

    @property
    def dir(self) -> Path:
        return DATA_ROOT / f"{self.slug}_adaptive"

    @property
    def pdf(self) -> Path:
        return self.dir / "sheets" / f"{self.slug}_adaptive.pdf"

    @property
    def decision(self) -> Path:
        return self.dir / "decision.json"


#: Owner-supplied 2026-09-14 (see docs/research/2026-09-14_vision_read_experiment.md) -- the
#: two CANDIDATE names per bout, not a pre-established kit binding. Lorenzo Bernardi is the
#: constant across all four.
BOUTS: dict[str, BoutSpec] = {
    "flo_raphelson": BoutSpec(
        "flo_raphelson", "7:48", "FloGrappling", "fixed",
        athlete_a="Lorenzo Bernardi",
        athlete_b="Raphelson Tarcisio Felipetto Alves Pereira",
        event="ADCC Brazil Open", year=2023),
    "lima": BoutSpec(
        "lima", "2:18", "FloGrappling", "fixed",
        athlete_a="Lorenzo Bernardi", athlete_b="Lucas Lima",
        event="ADCC São Paulo Open", year=2024),
    "balil2025": BoutSpec(
        "balil2025", "7:24", "FloGrappling", "fixed", scoreboard=True,
        athlete_a="Lorenzo Bernardi", athlete_b="Gustavo Balil Mendonça Rodrigues",
        event="ADCC São Paulo Open", year=2025),
    "durans": BoutSpec(
        "durans", "5:08", "YouTube", "moving",
        athlete_a="Lorenzo Bernardi", athlete_b="Henrique Durans",
        event="Thunder Fight GP (Arnold Sports)", year=2025),
}


def bout_context_note(spec: BoutSpec) -> str:
    """The "full context" fed to every call -- exactly what is actually known about this
    bout (see module docstring). Names/event/year are the two CANDIDATE athletes, never a
    pre-established kit binding -- the model still has to work out which body is which from
    frames/narration, same as the prompt's own ``identity_discriminator`` instruction."""
    bits = [f"Video source: {spec.source}.", f"Reported duration: ~{spec.duration_label}.",
           f"Camera: {spec.camera}."]
    if spec.scoreboard:
        bits.append("A broadcast scoreboard is visible in some frames.")
    if spec.athlete_a and spec.athlete_b:
        when = f", {spec.event} {spec.year}" if spec.event else ""
        bits.append(f"The two athletes competing in this bout are {spec.athlete_a} and "
                    f"{spec.athlete_b}{when}. These are the two CANDIDATE names -- which "
                    "kit belongs to which name is NOT given here and must be established "
                    "from the frames/narration yourself, exactly as identity_discriminator "
                    "asks; do not assume a left-to-right, walkout-order or overlay-order "
                    "binding.")
    else:
        bits.append("Athlete identities are NOT pre-supplied -- derive athlete_a, "
                    "athlete_b and identity_discriminator entirely from the frames and "
                    "narration, per the prompt's own rules.")
    return " ".join(bits)


# --------------------------------------------------------------------------- grid

@dataclass(frozen=True)
class CallSpec:
    bout: str
    model: str
    temperature: float
    thinking: str
    input: str
    repeat: int

    @property
    def out_name(self) -> str:
        return (f"gemini__{self.model}__t{self.temperature:.1f}__{self.thinking}__"
               f"{self.input}__r{self.repeat}.json")

    def out_path(self, out_root: Path = OUT_ROOT) -> Path:
        return out_root / self.bout / self.out_name


def thinking_levels_for(model: str, requested: list[str] | None) -> list[str]:
    """``--thinking`` unset -> "high" everywhere, plus "low" as a second arm for a flash
    model specifically (cheap enough to always compare; pro/preview ids are not assumed to
    support the field at all, so they only get what the caller asked for). Explicit
    ``--thinking`` overrides this per-model default entirely."""
    if requested:
        return list(requested)
    return ["high", "low"] if "flash" in model else ["high"]


def build_grid(bouts: list[str], models: list[str], temperatures: list[float],
               thinking: list[str] | None, inputs: list[str], repeats: int) -> list[CallSpec]:
    calls: list[CallSpec] = []
    for bout in bouts:
        for model in models:
            levels = thinking_levels_for(model, thinking)
            for temp in temperatures:
                n_repeats = repeats if temp > 0 else 1
                for level in levels:
                    for input_ in inputs:
                        for r in range(1, n_repeats + 1):
                            calls.append(CallSpec(bout, model, temp, level, input_, r))
    return calls


# --------------------------------------------------------------------------- rasterisation

def _has_fitz() -> bool:
    try:
        import fitz  # noqa: F401

        return True
    except ImportError:
        return False


def jpeg_arm_available() -> tuple[bool, str]:
    """(usable, how) -- checked once, not assumed."""
    if _has_fitz():
        return True, "pymupdf"
    if which("pdftoppm"):
        return True, "pdftoppm"
    return False, "neither pymupdf nor pdftoppm found"


def rasterize_pdf(pdf_path: Path, *, dpi: int = 150) -> list[bytes]:
    """Every page of ``pdf_path`` -> one JPEG each, in page order."""
    if _has_fitz():
        import fitz

        doc = fitz.open(pdf_path)
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        return [page.get_pixmap(matrix=matrix).tobytes("jpeg") for page in doc]

    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        prefix = Path(td) / "page"
        subprocess.run(["pdftoppm", "-jpeg", "-r", str(dpi), str(pdf_path), str(prefix)],
                       check=True, capture_output=True)
        return [p.read_bytes() for p in sorted(Path(td).glob("page*.jpg"))]


# --------------------------------------------------------------------------- calling gemini

def build_parts(spec: CallSpec, prompt_text: str, context_note: str,
                rasterize_cache: dict[Path, list[bytes]]) -> list[Any]:
    from google.genai import types

    pdf_path = BOUTS[spec.bout].pdf
    parts: list[Any] = []
    if spec.input == "pdf":
        parts.append(types.Part.from_bytes(data=pdf_path.read_bytes(),
                                           mime_type="application/pdf"))
    else:
        if pdf_path not in rasterize_cache:
            rasterize_cache[pdf_path] = rasterize_pdf(pdf_path)
        for jpg in rasterize_cache[pdf_path]:
            parts.append(types.Part.from_bytes(data=jpg, mime_type="image/jpeg"))
    parts.append(types.Part.from_text(text=context_note + "\n\n" + prompt_text))
    return parts


def _call_gemini(client: Any, parts: list[Any], model: str, thinking: str,
                 temperature: float) -> tuple[Any, str | None]:
    """One ``generate_content`` call. A model that rejects ``thinking_level`` (400) is retried
    once without it -- same fallback ``gemini_read_frames._generate_with_retry`` uses, kept
    local here rather than imported (that helper is module-private)."""
    from google.genai import errors

    try:
        resp = client.models.generate_content(
            model=model, contents=parts,
            config=build_generate_config(thinking, temperature))
        return resp, thinking
    except errors.ClientError:
        logger.warning("model %s rejected thinking_level=%s, retrying without thinking",
                       model, thinking)
        resp = client.models.generate_content(
            model=model, contents=parts,
            config=build_generate_config(None, temperature))
        return resp, None


def _request_dict(spec: CallSpec, thinking_used: str | None) -> dict[str, Any]:
    return {"bout": spec.bout, "model": spec.model, "temperature": spec.temperature,
           "thinking": spec.thinking, "thinking_used": thinking_used,
           "input": spec.input, "repeat": spec.repeat,
           "sheet": str(BOUTS[spec.bout].pdf.relative_to(REPO))}


def run_call(client: Any, spec: CallSpec, prompt: str, rasterize_cache: dict[Path, list[bytes]],
            *, out_root: Path = OUT_ROOT) -> dict[str, Any]:
    """One call -> one saved file, always. An API failure (quota, 404, transient 5xx -- the
    grid deliberately mixes in a preview/alias model id that may not be provisioned) is
    caught and recorded as an ``error: true`` payload with ``parsed: null`` rather than
    aborting the run -- ``collect_rows`` already treats an unparsed response as a schema
    problem, so no downstream code needs to know the difference between "bad JSON" and
    "the request never came back"."""
    context = bout_context_note(BOUTS[spec.bout])
    parts = build_parts(spec, prompt, context, rasterize_cache)
    t0 = time.monotonic()
    out_path = spec.out_path(out_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        resp, used_thinking = _call_gemini(client, parts, spec.model, spec.thinking,
                                           spec.temperature)
    except Exception as exc:  # noqa: BLE001 -- any transport/API failure, recorded not fatal
        elapsed = time.monotonic() - t0
        payload: dict[str, Any] = {
            "request": _request_dict(spec, None), "raw_response": None, "parsed": None,
            "parse_error": f"API error: {exc}",
            "usage": {"prompt": 0, "candidates": 0, "thoughts": 0, "total": 0},
            "latency_seconds": elapsed, "error": True,
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.warning("call failed (%s), recorded error file: %s", exc, out_path)
        return payload

    elapsed = time.monotonic() - t0
    text = resp.text
    parse_error = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        parsed, parse_error = None, str(exc)

    payload = {
        "request": _request_dict(spec, used_thinking),
        "raw_response": text,
        "parsed": parsed,
        "parse_error": parse_error,
        "usage": usage_totals(resp.usage_metadata),
        "latency_seconds": elapsed,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("wrote %s (%.1fs, %d tokens)", out_path, elapsed, payload["usage"]["total"])
    return payload


def build_batch_grid(bouts: list[str] | None = None) -> list[CallSpec]:
    """The 2026-09-14 owner-approved batch: 36 base calls (``gemini-3.6-flash``
    high+low + ``gemini-pro-latest`` high, x 3 temperatures, pdf) + a JPEG arm for
    ``gemini-3.6-flash`` thinking=high at all 3 temperatures (12) + one extra repeat of
    ``gemini-3.6-flash`` at t in {0.5, 1.0}, both thinking levels, pdf, for self-consistency
    (16) -- 64 calls across the 4 bouts."""
    bouts = bouts or list(BOUTS)
    base = build_grid(bouts, DEFAULT_MODELS, DEFAULT_TEMPERATURES, None, ["pdf"], repeats=1)
    jpeg_arm = build_grid(bouts, ["gemini-3.6-flash"], DEFAULT_TEMPERATURES, ["high"],
                          ["jpeg"], repeats=1)
    self_consistency_extra = [
        CallSpec(bout, "gemini-3.6-flash", temp, thinking, "pdf", 2)
        for bout in bouts for temp in (0.5, 1.0) for thinking in ("high", "low")
    ]
    return base + jpeg_arm + self_consistency_extra


def run_grid(calls: list[CallSpec], *, out_root: Path = OUT_ROOT) -> list[dict[str, Any]]:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set -- use --dry-run")
    client = genai.Client(api_key=api_key)
    prompt = load_prompt()
    rasterize_cache: dict[Path, list[bytes]] = {}

    results = []
    done = 0
    total = len(calls)
    for spec in calls:
        out_path = spec.out_path(out_root)
        if out_path.exists():
            logger.info("skip (exists): %s", out_path)
            continue
        results.append(run_call(client, spec, prompt, rasterize_cache, out_root=out_root))
        done += 1
        if done % 5 == 0 or done == total:
            logger.info("progress: %d/%d calls made this run (%d total in grid)",
                       done, total, total)
    return results


def run_smoke() -> int:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY not set")
    client = genai.Client(api_key=api_key)
    prompt = load_prompt()
    spec = CallSpec(bout=SMOKE_BOUT, model="gemini-3.6-flash", temperature=0.0,
                    thinking="high", input="pdf", repeat=1)
    payload = run_call(client, spec, prompt, {})
    print(json.dumps({"usage": payload["usage"], "latency_seconds": payload["latency_seconds"],
                      "out": str(spec.out_path())}, indent=2))
    return 0


# --------------------------------------------------------------------------- matcher

@dataclass(frozen=True)
class EventMatch:
    ref_idx: int
    cand_idx: int
    ts_diff: int
    actor_match: bool


def _event_key(ev: dict[str, Any]) -> str:
    return node_key(str(ev.get("label", "")), str(ev.get("type", "")))


def _surname_initial_match(actor: str, full_name: str) -> bool:
    """``actor`` folds onto ``full_name`` when its last token is the same surname and its
    first token is a prefix of ``full_name``'s first name -- "L. Bernardi" / "L Bernardi" /
    "Lorenzo Bernardi" all fold onto "Lorenzo Bernardi"; "R. Pereira" does NOT fold onto
    "Raphelson Tarcisio Felipetto Alves Pereira" unless the token compared is the one the
    model actually wrote as first (this is a first-vs-first-token rule, not "any token")."""
    a_tokens = str(actor or "").strip().split()
    n_tokens = str(full_name or "").strip().split()
    if not a_tokens or not n_tokens:
        return False
    if athlete_key(a_tokens[-1]) != athlete_key(n_tokens[-1]):
        return False
    first = a_tokens[0].strip(".").casefold()
    return bool(first) and n_tokens[0].casefold().startswith(first)


def fold_actor(actor: str, bout: dict[str, Any] | None) -> tuple[str, bool]:
    """``(resolved_name, resolved)`` -- folds an abbreviated actor onto whichever of
    ``bout``'s two athletes it matches by surname + initial (see
    :func:`_surname_initial_match`). Unresolvable (no bout, or neither athlete matches)
    returns ``(actor, False)`` unchanged -- the caller counts that as a mismatch, never a
    guess. A full, already-correct name resolves trivially (its own initial prefixes
    itself)."""
    a = str((bout or {}).get("athlete_a") or "")
    b = str((bout or {}).get("athlete_b") or "")
    for name in (a, b):
        if name and _surname_initial_match(actor, name):
            return name, True
    return actor, False


def count_unresolved_actors(events: list[dict[str, Any]],
                            bout: dict[str, Any] | None) -> int:
    """How many of ``events`` carry an ``actor`` that :func:`fold_actor` could not bind to
    either of ``bout``'s two athletes -- an empty actor doesn't count (that's a separate,
    already-flagged schema problem, not a name-folding failure)."""
    return sum(1 for e in events
              if str(e.get("actor", "")).strip() and not fold_actor(str(e.get("actor", "")), bout)[1])


def match_events(reference: list[dict[str, Any]], candidate: list[dict[str, Any]], *,
                 tol: int = 10, ignore_actor: bool = False,
                 reference_bout: dict[str, Any] | None = None,
                 candidate_bout: dict[str, Any] | None = None) -> list[EventMatch]:
    """Greedy nearest-``ts`` one-to-one. See module docstring "Matcher" section. Actors are
    folded onto their own bout's two athletes first (:func:`fold_actor`) before comparison,
    so "L. Bernardi" and "Lorenzo Bernardi" compare equal; an unresolvable actor is compared
    as written (counts as a mismatch, per :func:`count_unresolved_actors`)."""
    triples: list[tuple[int, int, int]] = []
    for ri, r in enumerate(reference):
        r_key = _event_key(r)
        r_actor = athlete_key(fold_actor(str(r.get("actor", "")), reference_bout)[0])
        for ci, c in enumerate(candidate):
            if r.get("type") != c.get("type"):
                continue
            if r_key != _event_key(c):
                continue
            if not ignore_actor:
                c_actor = athlete_key(fold_actor(str(c.get("actor", "")), candidate_bout)[0])
                if r_actor != c_actor:
                    continue
            try:
                diff = abs(int(r.get("ts", 0)) - int(c.get("ts", 0)))
            except (TypeError, ValueError):
                continue
            if diff > tol:
                continue
            triples.append((diff, ri, ci))
    triples.sort(key=lambda t: t[0])

    used_r: set[int] = set()
    used_c: set[int] = set()
    matches: list[EventMatch] = []
    for diff, ri, ci in triples:
        if ri in used_r or ci in used_c:
            continue
        used_r.add(ri)
        used_c.add(ci)
        r_resolved = fold_actor(str(reference[ri].get("actor", "")), reference_bout)[0]
        c_resolved = fold_actor(str(candidate[ci].get("actor", "")), candidate_bout)[0]
        actor_match = athlete_key(r_resolved) == athlete_key(c_resolved)
        matches.append(EventMatch(ri, ci, diff, actor_match))
    matches.sort(key=lambda m: m.ref_idx)
    return matches


def swap_bout_actors(bout: dict[str, Any],
                     events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``events`` with every ``actor`` flipped between ``bout``'s two athletes. Two of the
    Fable references (``durans``, ``lima``) flag the kit->name binding as UNVERIFIED (static
    lower-third, no narration) -- a whole-bout swap is the one systematic error that recurs
    in this pipeline (docs/gemini_concordance_audit.md), so it needs its own matcher mode
    rather than reading as ordinary noise. An actor :func:`fold_actor` cannot bind to either
    athlete (a typo, a kit description used as the actor by mistake) passes through
    unchanged."""
    a, b = str(bout.get("athlete_a") or ""), str(bout.get("athlete_b") or "")
    a_key, b_key = athlete_key(a), athlete_key(b)
    if not a_key or not b_key or a_key == b_key:
        return list(events)

    def flip(actor: Any) -> Any:
        resolved, ok = fold_actor(str(actor), bout)
        if not ok:
            return actor
        k = athlete_key(resolved)
        if k == a_key:
            return b
        if k == b_key:
            return a
        return actor

    return [{**e, "actor": flip(e.get("actor", ""))} for e in events]


def score_events_swap_invariant(reference: list[dict[str, Any]], candidate_bout: dict[str, Any],
                                candidate: list[dict[str, Any]], *,
                                tol: int = 10,
                                reference_bout: dict[str, Any] | None = None) -> dict[str, Any]:
    """``max(strict F1 as-is, strict F1 with the candidate's actors swapped)`` -- whichever
    orientation scores higher wins, ``flipped`` says which one. Relaxed F1 is untouched by a
    swap (it ignores actor entirely), so this mode only ever affects strict-style scoring."""
    as_is = match_events(reference, candidate, tol=tol, ignore_actor=False,
                        reference_bout=reference_bout, candidate_bout=candidate_bout)
    swapped_events = swap_bout_actors(candidate_bout, candidate)
    swapped = match_events(reference, swapped_events, tol=tol, ignore_actor=False,
                          reference_bout=reference_bout, candidate_bout=candidate_bout)
    p0, r0, f0 = precision_recall_f1(len(as_is), len(reference), len(candidate))
    p1, r1, f1v = precision_recall_f1(len(swapped), len(reference), len(candidate))
    flipped = (f1v or -1.0) > (f0 or -1.0)
    tp, p, r, f1 = ((len(swapped), p1, r1, f1v) if flipped else (len(as_is), p0, r0, f0))
    return {"tp": tp, "support": len(reference), "predicted": len(candidate),
           "precision": p, "recall": r, "f1": f1, "flipped": flipped}


def score_events(reference: list[dict[str, Any]], candidate: list[dict[str, Any]], *,
                 tol: int = 10,
                 reference_bout: dict[str, Any] | None = None,
                 candidate_bout: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Strict (actor required) + relaxed (actor ignored) precision/recall/F1, plus
    ``swap_invariant`` (see :func:`score_events_swap_invariant`) when ``candidate_bout`` is
    given -- omitted otherwise, since a swap needs to know the candidate's own athlete_a/b."""
    out: dict[str, dict[str, Any]] = {}
    for name, ignore_actor in (("strict", False), ("relaxed", True)):
        matches = match_events(reference, candidate, tol=tol, ignore_actor=ignore_actor,
                              reference_bout=reference_bout, candidate_bout=candidate_bout)
        p, r, f1 = precision_recall_f1(len(matches), len(reference), len(candidate))
        out[name] = {"tp": len(matches), "support": len(reference), "predicted": len(candidate),
                     "precision": p, "recall": r, "f1": f1}
    if candidate_bout is not None:
        out["swap_invariant"] = score_events_swap_invariant(
            reference, candidate_bout, candidate, tol=tol, reference_bout=reference_bout)
    return out


def _norm_score_map(score: Any) -> dict[str, int] | None:
    if not isinstance(score, dict):
        return None
    return {athlete_key(str(k)): v for k, v in score.items()}


#: Kit-colour words this repo's Fable references actually use, in no particular reading
#: order -- :func:`_find_color` picks whichever occurs EARLIEST in the text, since a
#: discriminator clause names the primary kit colour first and any trim/accent after.
_COLOR_WORDS = ("black", "white", "blue", "red", "yellow", "green", "teal", "maroon", "grey",
               "gray", "orange", "purple", "pink", "navy", "brown")


def _find_color(text: str) -> str | None:
    t = text.casefold()
    hits = [(t.index(c), c) for c in _COLOR_WORDS if c in t]
    return min(hits)[1] if hits else None


def extract_kit_colors(discriminator: str, athlete_a: str,
                       athlete_b: str) -> dict[str, str] | None:
    """Best-effort ``athlete_key(name) -> kit colour word`` from a free-text
    ``identity_discriminator``. Only the two clause shapes actually observed in this batch's
    references are attempted:

    1. ``"athlete_a = <clause>; athlete_b = <clause>"`` (``durans``)
    2. ``"... <COLOR> = <name>, <COLOR> = <name> ..."`` (``lima``'s "Assumed TEAL = Lorenzo
       Bernardi, MAROON = Lucas Lima")

    Anything else -- and any ambiguous match (both clauses reduce to the same colour) --
    returns ``None``: not cheaply parseable, not guessed.
    """
    text = discriminator or ""
    m_a = re.search(r"athlete_a\s*=\s*([^;]+)", text, re.IGNORECASE)
    m_b = re.search(r"athlete_b\s*=\s*([^;]+)", text, re.IGNORECASE)
    if m_a and m_b:
        ca, cb = _find_color(m_a.group(1)), _find_color(m_b.group(1))
        if ca and cb and ca != cb:
            return {athlete_key(athlete_a): ca, athlete_key(athlete_b): cb}

    color_by_name: dict[str, str] = {}
    for word, name in re.findall(r"\b([A-Za-z]+)\s*=\s*([A-Z][A-Za-z.'\- ]+?)(?:[,.;]|$)", text):
        if word.casefold() in _COLOR_WORDS:
            color_by_name[athlete_key(name)] = word.casefold()
    a_key, b_key = athlete_key(athlete_a), athlete_key(athlete_b)
    if (a_key in color_by_name and b_key in color_by_name and
            color_by_name[a_key] != color_by_name[b_key]):
        return color_by_name
    return None


def kit_winner_agreement(reference: dict[str, Any],
                         candidate: dict[str, Any]) -> tuple[bool | None, str]:
    """Do the two reads agree on WHICH KIT (not which name) won -- sidesteps a whole-bout
    name swap entirely. ``None`` + a reason when either side's ``identity_discriminator``
    doesn't parse via :func:`extract_kit_colors`, or the declared winner isn't one of the
    two names the parse found -- never a guess."""
    ref_colors = extract_kit_colors(str(reference.get("identity_discriminator") or ""),
                                    str(reference.get("athlete_a") or ""),
                                    str(reference.get("athlete_b") or ""))
    cand_colors = extract_kit_colors(str(candidate.get("identity_discriminator") or ""),
                                     str(candidate.get("athlete_a") or ""),
                                     str(candidate.get("athlete_b") or ""))
    if ref_colors is None or cand_colors is None:
        return None, "identity_discriminator not cheaply parseable on one or both sides"
    ref_color = ref_colors.get(athlete_key(str(reference.get("winner") or "")))
    cand_color = cand_colors.get(athlete_key(str(candidate.get("winner") or "")))
    if ref_color is None or cand_color is None:
        return None, "declared winner not one of the two parsed names"
    return ref_color == cand_color, "compared kit colour of the declared winner"


def metadata_agreement(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """``None`` where either side lacks the field to compare -- not a disagreement, unknown."""
    out: dict[str, Any] = {}

    rw, cw = reference.get("winner"), candidate.get("winner")
    out["winner_match"] = (athlete_key(str(rw)) == athlete_key(str(cw))) if rw and cw else None

    rt, ct = reference.get("win_type"), candidate.get("win_type")
    out["win_type_match"] = (str(rt).strip().casefold() == str(ct).strip().casefold()
                             if rt and ct else None)

    rs, cs = _norm_score_map(reference.get("final_score")), _norm_score_map(candidate.get("final_score"))
    out["final_score_match"] = (rs == cs) if rs is not None and cs is not None else None

    r0, r1 = reference.get("bout_start_seconds"), reference.get("bout_end_seconds")
    c0, c1 = candidate.get("bout_start_seconds"), candidate.get("bout_end_seconds")
    if isinstance(r0, int) and isinstance(r1, int) and isinstance(c0, int) and isinstance(c1, int):
        out["bout_window_match"] = abs(r0 - c0) <= 5 and abs(r1 - c1) <= 5
    else:
        out["bout_window_match"] = None

    ref_disc_present = bool(str(reference.get("identity_discriminator") or "").strip())
    cand_disc_present = bool(str(candidate.get("identity_discriminator") or "").strip())
    out["identity_discriminator_present"] = cand_disc_present  # kept: pre-existing field name
    out["identity_discriminator_ref_present"] = ref_disc_present
    out["identity_discriminator_both_present"] = ref_disc_present and cand_disc_present

    kit_match, kit_reason = kit_winner_agreement(reference, candidate)
    out["kit_winner_match"] = kit_match
    out["kit_winner_reason"] = kit_reason
    return out


def frame_times_from_decision(decision_path: Path) -> list[int]:
    if not decision_path.exists():
        return []
    data = json.loads(decision_path.read_text(encoding="utf-8"))
    return [int(round(t)) for t in data.get("frame_timestamps", [])]


# --------------------------------------------------------------------------- report

def _mean(xs: list[float]) -> float | None:
    vals = [x for x in xs if x is not None]
    return statistics.mean(vals) if vals else None


def _mean_bool(xs: list[bool | None]) -> float | None:
    vals = [1.0 if x else 0.0 for x in xs if x is not None]
    return statistics.mean(vals) if vals else None


CONFIG_FIELDS = ("model", "temperature", "thinking", "input")


def _roster_for(bout: str) -> dict[str, Any] | None:
    """``BOUTS[bout]``'s two owner-supplied athlete names as a fold/swap target, or ``None``
    for a bout this module doesn't know (or one with no names set yet)."""
    spec = BOUTS.get(bout)
    if spec is None or not spec.athlete_a:
        return None
    return {"athlete_a": spec.athlete_a, "athlete_b": spec.athlete_b}


def collect_rows(out_root: Path, bouts: list[str], *, tol: int = 10) -> list[dict[str, Any]]:
    """One row per saved call result that has a reference to score against. Bouts with no
    ``reference_fable.json`` yet are silently skipped -- nothing to score, not an error."""
    labels = load_labels()
    rows: list[dict[str, Any]] = []
    for bout in bouts:
        bdir = out_root / bout
        ref_path = bdir / REFERENCE_NAME
        if not ref_path.exists():
            continue
        reference = json.loads(ref_path.read_text(encoding="utf-8"))
        ref_bout = reference.get("bout") or {}
        ref_events = reference.get("events") or []
        times = frame_times_from_decision(BOUTS[bout].decision) if bout in BOUTS else []
        # The FIXED two-athlete roster (owner-supplied, BOUTS[bout]) is the fold/swap target
        # for actor matching -- not each side's own self-reported bout dict, which may name
        # the pair in a different order or spelling (or, if parsing failed, not at all).
        # metadata_agreement below still compares the self-reported ref_bout/cand_bout, since
        # THAT disagreement (did the model even get the winner/score right) is a different
        # question from "can this actor string be bound to a known name".
        roster = _roster_for(bout)

        for f in sorted(bdir.glob("gemini__*.json")):
            payload = json.loads(f.read_text(encoding="utf-8"))
            req = payload.get("request", {})
            parsed = payload.get("parsed")
            cand_bout = (parsed or {}).get("bout") or {}
            cand_events = (parsed or {}).get("events") or []
            problems = (validate_answer(parsed, labels, times) if isinstance(parsed, dict)
                       else ["response was not valid JSON"])
            rows.append({
                "bout": bout, "file": f.name, **{k: req.get(k) for k in CONFIG_FIELDS},
                "repeat": req.get("repeat", 1),
                "scores": score_events(ref_events, cand_events, tol=tol,
                                       reference_bout=roster, candidate_bout=roster),
                "metadata": metadata_agreement(ref_bout, cand_bout),
                "n_problems": len(problems),
                "n_unresolved_actors": count_unresolved_actors(cand_events, roster),
                "usage": payload.get("usage"),
                "latency_seconds": payload.get("latency_seconds"),
                "events": cand_events,
            })
    return rows


def aggregate_by_config(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Micro-average per config (sum raw tp/support/predicted across bouts+repeats, then
    divide) -- same convention as ``scripts.gemini_finetune.evaluate``, not an average of
    per-row ratios."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in CONFIG_FIELDS)].append(r)

    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for key, rs in groups.items():
        agg: dict[str, Any] = {"n": len(rs)}
        for variant in ("strict", "relaxed"):
            tp = sum(r["scores"][variant]["tp"] for r in rs)
            support = sum(r["scores"][variant]["support"] for r in rs)
            predicted = sum(r["scores"][variant]["predicted"] for r in rs)
            p, rec, f1 = precision_recall_f1(tp, support, predicted)
            agg[variant] = {"tp": tp, "support": support, "predicted": predicted,
                            "precision": p, "recall": rec, "f1": f1}
        if all("swap_invariant" in r["scores"] for r in rs):
            tp = sum(r["scores"]["swap_invariant"]["tp"] for r in rs)
            support = sum(r["scores"]["swap_invariant"]["support"] for r in rs)
            predicted = sum(r["scores"]["swap_invariant"]["predicted"] for r in rs)
            p, rec, f1 = precision_recall_f1(tp, support, predicted)
            agg["swap_invariant"] = {
                "tp": tp, "support": support, "predicted": predicted,
                "precision": p, "recall": rec, "f1": f1,
                "flipped_rate": _mean_bool([r["scores"]["swap_invariant"]["flipped"]
                                            for r in rs])}
        agg["metadata"] = {mk: _mean_bool([r["metadata"][mk] for r in rs])
                           for mk in ("winner_match", "win_type_match", "final_score_match",
                                     "bout_window_match", "identity_discriminator_present",
                                     "identity_discriminator_ref_present",
                                     "identity_discriminator_both_present", "kit_winner_match")}
        parseable = [r["metadata"]["kit_winner_match"] for r in rs
                    if r["metadata"]["kit_winner_match"] is not None]
        agg["kit_winner_parseable_n"] = len(parseable)
        agg["total_unresolved_actors"] = sum(r.get("n_unresolved_actors", 0) for r in rs)
        agg["mean_problems"] = _mean([float(r["n_problems"]) for r in rs])
        agg["mean_tokens"] = _mean([float(r["usage"]["total"]) for r in rs if r.get("usage")])
        agg["mean_latency"] = _mean([r["latency_seconds"] for r in rs
                                     if r.get("latency_seconds") is not None])
        out[key] = agg
    return out


def self_consistency(rows: list[dict[str, Any]], *, tol: int = 10) -> dict[tuple[Any, ...], float | None]:
    """For configs with >1 repeat of the SAME (bout, config): mean strict-F1 treating each
    repeat as the "reference" for every other repeat of the same call -- how much the model
    agrees with itself, not with the ground truth."""
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(r["bout"], *(r[k] for k in CONFIG_FIELDS))].append(r)

    by_config: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    for key, rs in groups.items():
        if len(rs) < 2:
            continue
        config_key = key[1:]
        roster = _roster_for(str(key[0]))
        for i in range(len(rs)):
            for j in range(len(rs)):
                if i == j:
                    continue
                matches = match_events(rs[i]["events"], rs[j]["events"], tol=tol,
                                      reference_bout=roster, candidate_bout=roster)
                _, _, f1 = precision_recall_f1(len(matches), len(rs[i]["events"]),
                                               len(rs[j]["events"]))
                if f1 is not None:
                    by_config[config_key].append(f1)
    return {k: _mean(v) for k, v in by_config.items()}


def pairwise_config_agreement(rows: list[dict[str, Any]], *,
                              tol: int = 10) -> dict[tuple[Any, ...], float | None]:
    """Gemini-vs-Gemini: for every bout, every pair of DISTINCT configs' own event lists
    (repeat 1 only -- self-consistency already covers repeats), strict-F1 agreement between
    them. Keyed by the pair of config tuples, order-independent."""
    by_bout: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["repeat"] == 1:
            by_bout[r["bout"]].append(r)

    scores: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    for bout, rs in by_bout.items():
        roster = _roster_for(bout)
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                ka = tuple(rs[i][k] for k in CONFIG_FIELDS)
                kb = tuple(rs[j][k] for k in CONFIG_FIELDS)
                if ka == kb:
                    continue
                pair = tuple(sorted((ka, kb), key=repr))
                matches = match_events(rs[i]["events"], rs[j]["events"], tol=tol,
                                      reference_bout=roster, candidate_bout=roster)
                _, _, f1 = precision_recall_f1(len(matches), len(rs[i]["events"]),
                                               len(rs[j]["events"]))
                if f1 is not None:
                    scores[pair].append(f1)
    return {k: _mean(v) for k, v in scores.items()}


def _fmt(x: float | None, pct: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x * 100:.0f}%" if pct else f"{x:.2f}"


def _config_label(key: tuple[Any, ...]) -> str:
    model, temp, thinking, input_ = key
    return f"{model} t={temp:g} {thinking} {input_}"


def render_report(rows: list[dict[str, Any]], *, bouts: list[str],
                  pairwise: bool = False, tol: int = 10) -> str:
    lines = ["# Vision-read configuration experiment", "",
             "Auto-generated by `scripts/research/vision_read_experiment.py --report`. "
             "Do not hand-edit this file below this line -- rerun the report instead.", ""]

    if not rows:
        lines.append("Nothing to score yet under "
                     f"`{OUT_ROOT.relative_to(REPO)}/<bout>/` -- either `{REFERENCE_NAME}` "
                     "is missing for these bouts, or no `gemini__*.json` call result is "
                     "saved yet. Run the grid, confirm Fable's reference reads are in "
                     "place, then `--report` again.")
        return "\n".join(lines) + "\n"

    agg = aggregate_by_config(rows)
    ranked = sorted(agg.items(), key=lambda kv: kv[1]["strict"]["f1"] or -1.0, reverse=True)

    lines += ["## Per-config (averaged over bouts)", "",
             "| config | n | strict P/R/F1 | relaxed P/R/F1 | swap-inv F1 (flip%) | winner | "
             "kit winner (n parseable) | win_type | score | window | id.disc. (ref/cand) | "
             "unresolved actors | mean schema problems | mean tokens | mean latency (s) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, a in ranked:
        s, r = a["strict"], a["relaxed"]
        m = a["metadata"]
        swap = a.get("swap_invariant")
        swap_cell = (f"{_fmt(swap['f1'])} ({_fmt(swap['flipped_rate'], pct=True)})"
                    if swap else "—")
        kit_cell = f"{_fmt(m['kit_winner_match'], pct=True)} (n={a['kit_winner_parseable_n']})"
        lines.append(
            f"| {_config_label(key)} | {a['n']} | "
            f"{_fmt(s['precision'])}/{_fmt(s['recall'])}/{_fmt(s['f1'])} | "
            f"{_fmt(r['precision'])}/{_fmt(r['recall'])}/{_fmt(r['f1'])} | "
            f"{swap_cell} | "
            f"{_fmt(m['winner_match'], pct=True)} | {kit_cell} | "
            f"{_fmt(m['win_type_match'], pct=True)} | "
            f"{_fmt(m['final_score_match'], pct=True)} | {_fmt(m['bout_window_match'], pct=True)} | "
            f"{_fmt(m['identity_discriminator_ref_present'], pct=True)}/"
            f"{_fmt(m['identity_discriminator_present'], pct=True)} | "
            f"{a['total_unresolved_actors']} | "
            f"{_fmt(a['mean_problems'])} | {_fmt(a['mean_tokens'])} | {_fmt(a['mean_latency'])} |")

    lines += ["", "## Ranked (best strict F1 first)", ""]
    for i, (key, a) in enumerate(ranked, 1):
        lines.append(f"{i}. **{_config_label(key)}** — F1 {_fmt(a['strict']['f1'])} "
                     f"(relaxed {_fmt(a['relaxed']['f1'])})")

    lines += ["", "## Per-bout", ""]
    for bout in bouts:
        bout_rows = [r for r in rows if r["bout"] == bout]
        if not bout_rows:
            continue
        lines += [f"### {bout}", "",
                 "| config | repeat | strict P/R/F1 | swap-inv F1 | flipped | kit winner | "
                 "unresolved actors | schema problems |",
                 "|---|---|---|---|---|---|---|---|"]
        for r in bout_rows:
            key = tuple(r[k] for k in CONFIG_FIELDS)
            s = r["scores"]["strict"]
            swap = r["scores"].get("swap_invariant")
            swap_f1 = _fmt(swap["f1"]) if swap else "—"
            flipped = ("yes" if swap and swap["flipped"] else
                      "no" if swap else "—")
            kit = r["metadata"]["kit_winner_match"]
            kit_cell = "agree" if kit is True else "disagree" if kit is False else "n/a"
            lines.append(f"| {_config_label(key)} | {r['repeat']} | "
                        f"{_fmt(s['precision'])}/{_fmt(s['recall'])}/{_fmt(s['f1'])} | "
                        f"{swap_f1} | {flipped} | {kit_cell} | "
                        f"{r.get('n_unresolved_actors', 0)} | {r['n_problems']} |")
        lines.append("")

    sc = self_consistency(rows, tol=tol)
    if sc:
        lines += ["## Self-consistency (repeated temp>0 configs)", "",
                 "| config | mean self-agreement F1 |", "|---|---|"]
        for key, val in sorted(sc.items(), key=lambda kv: kv[1] or -1.0, reverse=True):
            lines.append(f"| {_config_label(key)} | {_fmt(val)} |")
        lines.append("")

    if pairwise:
        pw = pairwise_config_agreement(rows, tol=tol)
        lines += ["## Pairwise Gemini-vs-Gemini agreement", "",
                 "| config A | config B | agreement F1 |", "|---|---|---|"]
        for (ka, kb), val in sorted(pw.items(), key=lambda kv: kv[1] or -1.0, reverse=True):
            lines.append(f"| {_config_label(ka)} | {_config_label(kb)} | {_fmt(val)} |")
        lines.append("")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- cli

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bouts", nargs="+", default=list(BOUTS), choices=list(BOUTS))
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--temperatures", type=float, nargs="+", default=DEFAULT_TEMPERATURES)
    ap.add_argument("--thinking", nargs="+", choices=("low", "medium", "high"), default=None,
                    help="unset -> high everywhere, plus low for flash models")
    ap.add_argument("--input", dest="inputs", nargs="+", choices=("pdf", "jpeg"),
                    default=DEFAULT_INPUTS)
    ap.add_argument("--repeats", type=int, default=1,
                    help="repeats per config at temperature>0 (temp==0 always runs once)")
    ap.add_argument("--batch", action="store_true",
                    help="the owner-approved 2026-09-14 grid (see build_batch_grid) instead "
                         "of --models/--temperatures/--thinking/--input/--repeats")
    ap.add_argument("--tol", type=int, default=10, help="match tolerance, seconds")
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--smoke", action="store_true",
                    help="one tiny call (lima, gemini-3.6-flash, t=0) to prove the wiring")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--pairwise", action="store_true", help="with --report: add the "
                    "Gemini-vs-Gemini agreement table")
    ap.add_argument("--report-out", type=Path, default=REPORT_OUT)
    a = ap.parse_args()

    if a.smoke:
        return run_smoke()

    jpeg_ok, jpeg_how = jpeg_arm_available()
    if not jpeg_ok:
        logger.warning("jpeg arm unavailable (%s) -- dropping from the grid", jpeg_how)
    elif a.batch or "jpeg" in a.inputs:
        logger.info("jpeg arm via %s", jpeg_how)

    if a.report:
        rows = collect_rows(a.out_root, a.bouts, tol=a.tol)
        md = render_report(rows, bouts=a.bouts, pairwise=a.pairwise, tol=a.tol)
        a.report_out.parent.mkdir(parents=True, exist_ok=True)
        a.report_out.write_text(md, encoding="utf-8")
        print(f"wrote {a.report_out} ({len(rows)} scored rows)")
        return 0

    if a.batch:
        calls = build_batch_grid(a.bouts)
        if not jpeg_ok:
            calls = [c for c in calls if c.input != "jpeg"]
    else:
        inputs = [i for i in a.inputs if jpeg_ok or i != "jpeg"]
        calls = build_grid(a.bouts, a.models, a.temperatures, a.thinking, inputs, a.repeats)

    if a.dry_run:
        print(f"{len(calls)} call(s):")
        for c in calls:
            print(f"  {c.out_path(a.out_root).relative_to(REPO)}")
        return 0

    run_grid(calls, out_root=a.out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
