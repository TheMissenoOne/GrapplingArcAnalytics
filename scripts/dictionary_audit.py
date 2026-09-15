#!/usr/bin/env python
"""Dictionary audit — is the curated technique dictionary COMPLETE enough to fine-tune on?

    uv run python -m scripts.dictionary_audit measure [--min-human 8] [--no-db]
    uv run python -m scripts.dictionary_audit queue   [--cap 12] [--limit 40]
    uv run python -m scripts.dictionary_audit apply verdicts.jsonl [--write]

"Complete" is defined in ``docs/dictionary_audit.md`` and measured here: every curated entry
the vision model must recognise has (a) a canonical label + type, (b) at least ``--min-human``
human-VERIFIED frame examples, (c) a written visual definition; and every label that appears
in the corpus or in a model/human read with no curated entry has a verdict (add / alias /
reject). Three commands, in that order, and none of them edits the curated library:

``measure``
    Counts coverage per curated entry from the four places a technique can show up — the
    dataset's own labels, the public corpus (``matches.sequence`` on prod, read-only), the 4
    Fable reference reads and the 64 Gemini reads under ``data/frame_pdf/out/experiments/``.
    Writes ``data/finetune/audit/coverage.json`` and refreshes the generated block of
    ``docs/research/2026-09-15_dictionary_audit.md``.

``queue``
    Turns the thin/zero buckets and the candidate labels into something a human can actually
    rule on: one contact sheet per technique (``data/finetune/audit/sheets/<node_key>.pdf``),
    2x2 landscape like ``scripts/frame_pdf.py``, header carrying the canonical label, pt/en,
    type, curated variants and the visual definition (or "definição a escrever"), every frame
    captioned with bout + ts + who proposed it. Index in ``audit/queue.md``.

``apply``
    Ingests the owner's verdicts, one line per frame::

        {"node_key": "kimura", "bout": "<slug>", "ts_ms": 145000, "verdict": "accept",
         "note": "grip visible, figure-four closed"}

    ``verdict`` is ``accept`` | ``reject`` | ``relabel:<node_key>`` | ``alias:<node_key>``.

Two provenance rules this command does NOT bend, both of them scars:

* **``source`` is never rewritten.** Accepting a frame a model proposed records
  ``review: accepted`` beside the model's own ``source`` — the laundering
  ``frame_registrar.py`` was fixed for on 2026-08-24. A line is MINTED with
  ``source: "human"`` only for ``relabel:``, where the claim's origin genuinely is the human
  who typed it, and for a frame no read ever labelled.
* **The curated library is never edited by a script.** ``alias:`` writes a proposal into
  ``analysis/data/dictionary_proposals.json``; adding, aliasing or rejecting an entry in
  ``analysis/data/technique_library.json`` stays a reviewed change, same gate as a schema
  change (root ``CLAUDE.md``).

Every verdict also goes to the durable store ``data/finetune/audit/verdicts.jsonl``, because
``vision_dataset --build`` rewrites every ``labels/*.jsonl`` from the answer files and would
otherwise erase the human's work on the next build (``vision_dataset.apply_verdicts`` replays
the store at the end of each build).

Public corpus only. Nothing here touches a user-fed row: the labels come from published
competition footage and the corpus read is ``matches``/``athletes`` (root ``CLAUDE.md``,
"Public vs Private Data").
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.names import _normalize_name, athlete_key, canonicalize  # noqa: E402
from analysis.technique_match import clean_label  # noqa: E402
from scripts.vision_dataset import (  # noqa: E402
    DATASET,
    build,
    extract_frames,
    label_id,
    load_library,
    load_verdicts,
    record_verdicts,
    taxonomy_version,
)

logger = logging.getLogger("dictionary_audit")

DICTIONARY = REPO / "analysis" / "data" / "technique_library.json"
DEFINITIONS = REPO / "analysis" / "data" / "technique_definitions.json"
PROPOSALS = REPO / "analysis" / "data" / "dictionary_proposals.json"
EXPERIMENTS = REPO / "data" / "frame_pdf" / "out" / "experiments"
BERNARDI = REPO / "data" / "frame_pdf" / "lorenzo_bernardi"

AUDIT = DATASET / "audit"
COVERAGE = AUDIT / "coverage.json"
SEED = AUDIT / "gemini_seed" / "seed.jsonl"
SHEETS = AUDIT / "sheets"
QUEUE_JSON = AUDIT / "queue.json"
QUEUE_MD = AUDIT / "queue.md"
REPORT = REPO / "docs" / "research" / "2026-09-15_dictionary_audit.md"

BEGIN = "<!-- BEGIN GENERATED: dictionary_audit measure -->"
END = "<!-- END GENERATED -->"

DEFAULT_MIN_HUMAN = 8
DEFAULT_CAP = 12
#: Types a frame cannot carry as a discrete occurrence. "Posture", "Pressure", "Connection"
#: are qualities of a position, not events a reader logs — they are in the dictionary for the
#: App's teaching vocabulary, and counting them as uncovered vision classes would put 11
#: permanent failures in the denominator. Excluded with the reason recorded, not dropped.
OUT_OF_SCOPE_TYPES = frozenset({"concept"})

NO_DEFINITION = "definição a escrever"


def node_key_of(label: str) -> str:
    """The one chain every graph/map/label consumer in this repo uses."""
    return canonicalize(_normalize_name(str(label or "")))


# ----------------------------------------------------------------------- dictionary

@dataclass
class DictEntry:
    en: str
    pt: str
    type: str
    variants: list[str]
    node_key: str
    in_node_library: bool
    definition: str | None = None
    verified_frames: set[tuple[str, int]] = field(default_factory=set)
    human_frames: set[tuple[str, int]] = field(default_factory=set)
    model_unreviewed: int = 0
    corpus_events: int = 0
    fable_events: int = 0
    experiment_events: int = 0

    @property
    def in_scope(self) -> bool:
        return self.type not in OUT_OF_SCOPE_TYPES

    def bucket(self, min_human: int) -> str:
        if not self.in_scope:
            return "out-of-scope"
        if not self.in_node_library:
            # The reader is only ever offered node_library.json's labels, and
            # vision_dataset.build_labels DROPS anything else as `off_library_label`. So this
            # entry cannot receive a frame at all until the two vocabularies are reconciled.
            return "unreachable"
        n = len(self.verified_frames)
        return "well-covered" if n >= min_human else ("thin" if n else "zero")


def load_definitions() -> dict[str, str]:
    """node_key -> 1-2 line visual definition. Absent file = nothing written yet."""
    if not DEFINITIONS.is_file():
        return {}
    raw = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    return {node_key_of(k): str(v).strip() for k, v in raw.items() if str(v).strip()}


def dictionary_collisions() -> dict[str, list[str]]:
    """Curated entries that collapse onto ONE node_key — a duplicate in the dictionary.

    The key is the project's own ``canonicalize(_normalize_name(...))``, so a collision is
    not a naming quibble: both spellings already ARE the same node everywhere downstream,
    and the dictionary is the only place still claiming they are two techniques.
    """
    seen: dict[str, list[str]] = defaultdict(list)
    for row in json.loads(DICTIONARY.read_text(encoding="utf-8")):
        en = str(row.get("en") or "").strip()
        if en:
            seen[node_key_of(en)].append(en)
    return {k: sorted(v) for k, v in sorted(seen.items()) if len(v) > 1}


def load_dictionary() -> dict[str, DictEntry]:
    """The curated dictionary, keyed by node_key, with its reachability already resolved."""
    library = load_library()
    definitions = load_definitions()
    out: dict[str, DictEntry] = {}
    for row in json.loads(DICTIONARY.read_text(encoding="utf-8")):
        en = str(row.get("en") or "").strip()
        if not en:
            continue
        key = node_key_of(en)
        out[key] = DictEntry(
            en=en, pt=str(row.get("pt") or ""), type=str(row.get("type") or ""),
            variants=[str(v) for v in row.get("variants") or []],
            node_key=key, in_node_library=key in library,
            definition=definitions.get(key))
    return out


def resolver(entries: dict[str, DictEntry]) -> Any:
    """``(label, type) -> (DictEntry|None, kind)`` through the shared ``clean_label``.

    ``clean_label`` refuses to canonicalise across technique types, which is correct for the
    corpus and wrong for this audit: a label the dictionary HAS under another type is not a
    missing technique, it is a type conflict (``audit_ontology``'s ``dual_identity`` family)
    and it needs a different verdict. So both lookups run, and the second one is reported as
    what it is instead of being counted as a hole in the dictionary.
    """
    by_en = {node_key_of(e.en): e for e in entries.values()}

    def resolve(label: str, type_hint: str = "") -> tuple[DictEntry | None, str]:
        hit = by_en.get(node_key_of(clean_label(label, type_hint)))
        if hit is not None:
            return hit, "match"
        loose = by_en.get(node_key_of(clean_label(label)))
        if loose is not None:
            return None, f"type_conflict:{loose.node_key}"
        return None, "missing"

    return resolve


# --------------------------------------------------------------------------- sources

@dataclass
class Candidate:
    """A label seen in the wild with no curated entry — needs add / alias / reject."""

    node_key: str
    labels: Counter[str] = field(default_factory=Counter)
    types: Counter[str] = field(default_factory=Counter)
    sources: Counter[str] = field(default_factory=Counter)
    kinds: Counter[str] = field(default_factory=Counter)
    in_node_library: bool = False

    @property
    def total(self) -> int:
        return sum(self.sources.values())


def _dataset_label_lines(dataset: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((dataset / "labels").glob("*.jsonl")):
        rows.extend(json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.strip())
    return rows


def _experiment_reads() -> list[dict[str, Any]]:
    """The 4 Fable reference reads + the 64 Gemini reads, flattened to one shape.

    Labels only — this audit counts vocabulary, it does not re-score the experiment (that is
    ``docs/research/2026-09-14_vision_read_experiment.md``).
    """
    reads: list[dict[str, Any]] = []
    for path in sorted(EXPERIMENTS.glob("*/*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if path.name == "reference_fable.json":
            events, origin = raw.get("events") or [], "fable"
            reader = str(raw.get("reader") or "fable")
            sheet = None
        else:
            events = ((raw.get("parsed") or {}).get("events")) or []
            origin = "gemini"
            req = raw.get("request") or {}
            reader = f"{req.get('model', 'gemini')} t{req.get('temperature')}"
            sheet = req.get("sheet")
        reads.append({"bout_dir": path.parent.name, "origin": origin, "reader": reader,
                      "sheet": sheet, "events": events, "file": str(path)})
    return reads


def experiment_sheet(bout_dir: str, reads: list[dict[str, Any]]) -> Path | None:
    """The sheet PDF an experiment's reads were taken off, from the reads themselves."""
    for r in reads:
        if r["bout_dir"] == bout_dir and r.get("sheet"):
            path = Path(str(r["sheet"]))
            path = path if path.is_absolute() else REPO / path
            if path.is_file():
                return path
    hits = sorted(BERNARDI.glob(f"{bout_dir}_adaptive/sheets/*.pdf"))
    return hits[0] if hits else None


def corpus_events() -> list[tuple[str, str, int]]:
    """``(label, type, count)`` over every public bout's sequence. Read-only prod."""
    from sqlalchemy import select

    from db.base import db_session
    from db.models import Match

    counter: Counter[tuple[str, str]] = Counter()
    with db_session() as session:
        for (seq,) in session.execute(select(Match.sequence)):
            for ev in seq or []:
                label = str((ev or {}).get("label") or "").strip()
                if label:
                    counter[(label, str(ev.get("type") or ""))] += 1
    return [(lab, typ, n) for (lab, typ), n in counter.most_common()]


# --------------------------------------------------------------------------- measure

def measure(dataset: Path = DATASET, min_human: int = DEFAULT_MIN_HUMAN,
            with_db: bool = True) -> dict[str, Any]:
    entries = load_dictionary()
    collisions = dictionary_collisions()
    resolve = resolver(entries)
    candidates: dict[str, Candidate] = {}

    def candidate(label: str, typ: str, kind: str, source: str, n: int) -> None:
        key = node_key_of(label)
        if not key:
            return
        cand = candidates.setdefault(key, Candidate(node_key=key))
        cand.labels[str(label).strip()] += n
        cand.types[typ] += n
        cand.sources[source] += n
        cand.kinds[kind] += n

    library = load_library()

    for ln in _dataset_label_lines(dataset):
        hit, kind = resolve(str(ln.get("label") or ""), str(ln.get("type") or ""))
        verified = ln.get("source") == "human" or ln.get("review") == "accepted"
        frame = (str(ln.get("bout")), int(ln.get("ts_ms") or 0))
        if hit is None:
            candidate(str(ln.get("label") or ""), str(ln.get("type") or ""), kind,
                      "dataset_verified" if verified else "dataset_model", 1)
            continue
        if verified:
            hit.verified_frames.add(frame)
            if ln.get("source") == "human":
                hit.human_frames.add(frame)
        elif ln.get("review") is None:
            hit.model_unreviewed += 1

    reads = _experiment_reads()
    for read in reads:
        for ev in read["events"]:
            label = str(ev.get("label") or "")
            hit, kind = resolve(label, str(ev.get("type") or ""))
            if hit is None:
                candidate(label, str(ev.get("type") or ""), kind, read["origin"], 1)
            elif read["origin"] == "fable":
                hit.fable_events += 1
            else:
                hit.experiment_events += 1

    corpus_read: str | None = None
    if with_db:
        for label, typ, n in corpus_events():
            hit, kind = resolve(label, typ)
            if hit is None:
                candidate(label, typ, kind, "corpus", n)
            else:
                hit.corpus_events += n
        corpus_read = datetime.now(UTC).date().isoformat()

    for cand in candidates.values():
        cand.in_node_library = cand.node_key in library

    rows: list[dict[str, Any]] = []
    for entry in sorted(entries.values(), key=lambda e: e.en):
        rows.append({
            "en": entry.en, "pt": entry.pt, "type": entry.type, "node_key": entry.node_key,
            "variants": entry.variants,
            "in_node_library": entry.in_node_library,
            "definition": entry.definition,
            "has_definition": bool(entry.definition),
            "verified_frames": len(entry.verified_frames),
            "human_frames": len(entry.human_frames),
            "model_unreviewed": entry.model_unreviewed,
            "corpus_events": entry.corpus_events,
            "fable_events": entry.fable_events,
            "experiment_events": entry.experiment_events,
            "bucket": entry.bucket(min_human),
        })

    cand_rows: list[dict[str, Any]] = []
    for cand in sorted(candidates.values(), key=lambda c: (-c.total, c.node_key)):
        kind = cand.kinds.most_common(1)[0][0]
        cand_rows.append({
            "node_key": cand.node_key,
            "label": cand.labels.most_common(1)[0][0],
            "type": cand.types.most_common(1)[0][0],
            "kind": "type_conflict" if kind.startswith("type_conflict") else "missing",
            "conflicts_with": kind.split(":", 1)[1] if kind.startswith("type_conflict") else None,
            "in_node_library": cand.in_node_library,
            "total": cand.total,
            "by_source": dict(sorted(cand.sources.items())),
            "spellings": dict(cand.labels.most_common()),
        })

    buckets = Counter(r["bucket"] for r in rows)
    coverage = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "min_human": min_human,
        "taxonomy_version": taxonomy_version(),
        "dictionary": str(DICTIONARY.relative_to(REPO)),
        "corpus_read": corpus_read,
        "totals": {
            "entries": len(rows),
            **{k: buckets.get(k, 0) for k in
               ("well-covered", "thin", "zero", "unreachable", "out-of-scope")},
            "with_definition": sum(1 for r in rows if r["has_definition"]),
            "candidates": len(cand_rows),
            "candidate_mentions": sum(c["total"] for c in cand_rows),
            "candidates_in_node_library": sum(1 for c in cand_rows if c["in_node_library"]),
            "dictionary_collisions": len(collisions),
        },
        "dictionary_collisions": collisions,
        "entries": rows,
        "candidates": cand_rows,
    }
    COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE.write_text(json.dumps(coverage, ensure_ascii=False, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    write_report(coverage)
    return coverage


def _table(head: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def report_block(coverage: dict[str, Any]) -> str:
    t = coverage["totals"]
    entries = coverage["entries"]
    zero = [e for e in entries if e["bucket"] == "zero"]
    thin = [e for e in entries if e["bucket"] == "thin"]
    unreachable = [e for e in entries if e["bucket"] == "unreachable"]
    zero.sort(key=lambda e: (-e["corpus_events"], e["en"]))
    thin.sort(key=lambda e: (e["verified_frames"], -e["corpus_events"], e["en"]))
    lines = [
        BEGIN,
        f"_Generated {coverage['generated']} by `scripts/dictionary_audit.py measure`; "
        f"corpus read {coverage['corpus_read'] or '(skipped, --no-db)'}; "
        f"taxonomy `{coverage['taxonomy_version']}`. Do not hand-edit this block._",
        "",
        "### Coverage of the curated dictionary",
        "",
        _table(["bucket", "entries", "meaning"], [
            ["`well-covered`", str(t["well-covered"]),
             f"≥ {coverage['min_human']} human-verified frames"],
            ["`thin`", str(t["thin"]), f"1–{coverage['min_human'] - 1} verified frames"],
            ["`zero`", str(t["zero"]), "no verified frame at all"],
            ["`unreachable`", str(t["unreachable"]),
             "no node in `data/frame_pdf/node_library.json` — the reader is never offered "
             "this label, so `vision_dataset.build_labels` would drop it as "
             "`off_library_label`"],
            ["`out-of-scope`", str(t["out-of-scope"]),
             "`concept` type — a quality of a position, not a discrete occurrence a reader logs"],
            ["**total**", str(t["entries"]),
             f"**{t['with_definition']} carry a written visual definition**"],
        ]),
        "",
        f"Candidates (a label in the corpus or a read with no curated entry): "
        f"**{t['candidates']}** distinct, {t['candidate_mentions']} mentions, "
        f"{t['candidates_in_node_library']} of them already a node in `node_library.json`.",
        "",
        "### Top zero-coverage techniques by corpus frequency",
        "",
        _table(["#", "technique", "type", "corpus events", "model labels awaiting review",
                "reads (fable/experiments)"],
               [[str(i + 1), f"`{e['en']}`", e["type"], str(e["corpus_events"]),
                 str(e["model_unreviewed"]),
                 f"{e['fable_events']}/{e['experiment_events']}"]
                for i, e in enumerate(zero[:10])]),
        "",
        "### Thinnest covered techniques",
        "",
        _table(["technique", "verified frames", "corpus events"],
               [[f"`{e['en']}`", str(e["verified_frames"]), str(e["corpus_events"])]
                for e in thin[:10]]),
        "",
        "### Candidate labels — need a verdict (add / alias / reject)",
        "",
        _table(["label", "type", "kind", "mentions", "by source"],
               [[f"`{c['label']}`", c["type"],
                 c["kind"] + (f" → `{c['conflicts_with']}`" if c["conflicts_with"] else ""),
                 str(c["total"]),
                 ", ".join(f"{k} {v}" for k, v in c["by_source"].items())]
                for c in coverage["candidates"][:20]]),
        "",
        "### Unreachable curated entries (no node in the reader's vocabulary)",
        "",
        ", ".join(f"`{e['en']}`" for e in unreachable) or "(none)",
        "",
        "### Curated entries that collide on one `node_key`",
        "",
        "\n".join(f"- `{k}` ← " + ", ".join(f"`{v}`" for v in vs)
                  for k, vs in coverage.get("dictionary_collisions", {}).items())
        or "(none)",
        END,
    ]
    return "\n".join(lines)


def write_report(coverage: dict[str, Any]) -> Path:
    block = report_block(coverage)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    if REPORT.is_file():
        text = REPORT.read_text(encoding="utf-8")
        if BEGIN in text and END in text:
            head, rest = text.split(BEGIN, 1)
            _old, tail = rest.split(END, 1)
            REPORT.write_text(head + block + tail, encoding="utf-8")
            return REPORT
        REPORT.write_text(text.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
        return REPORT
    REPORT.write_text(REPORT_HEAD + block + "\n", encoding="utf-8")
    return REPORT


REPORT_HEAD = """# Dictionary audit — completing the technique dictionary for the vision dataset

Runner: `scripts/dictionary_audit.py` (procedure: `docs/dictionary_audit.md`). The block
below is GENERATED by `measure`; prose goes above or below it, never inside.

Privacy class **A, public competition data**: every input is a published-footage label —
`data/finetune/labels/*.jsonl`, `matches.sequence`, the frame-reading experiments. No
user-fed row is read here (root `CLAUDE.md`, "Public vs Private Data").

"""


# ----------------------------------------------------------------------------- queue

@dataclass(frozen=True)
class Proposal:
    node_key: str
    bout: str
    ts_ms: int
    ts: int
    rank: int
    offset: int
    caption: str
    frame: str            # path relative to the dataset root
    origin: str           # provenance of the CLAIM, for a minted label line
    label: str
    type: str
    actor: str | None
    successful: bool | None

    @property
    def sort_key(self) -> tuple[int, int, str, int]:
        return self.rank, abs(self.offset), self.bout, self.ts_ms


def _frames_of(dataset: Path, slug: str) -> list[dict[str, Any]]:
    record = dataset / "frames" / slug / "frames.json"
    if not record.is_file():
        return []
    rows: list[dict[str, Any]] = json.loads(record.read_text(encoding="utf-8"))
    return sorted(rows, key=lambda r: r["ts_ms"])


def near_frames(ts: int, frames: list[dict[str, Any]], radius: int = 1
                ) -> list[tuple[int, dict[str, Any]]]:
    """The sampled frame nearest ``ts`` plus ``radius`` neighbours, as ``(offset, frame)``.

    Neighbours matter because a technique named at one sample is routinely VISIBLE at the
    one before it (the reading prompt logs the result, not the entry), and because the
    Bernardi sheets sample adaptively — consecutive stamps are 1–10 s apart, so "the exact
    second" is not a thing to match on.
    """
    if not frames:
        return []
    best = min(range(len(frames)), key=lambda i: (abs(frames[i]["ts"] - ts), i))
    out = []
    for off in range(-radius, radius + 1):
        i = best + off
        if 0 <= i < len(frames):
            out.append((off, frames[i]))
    return out


def load_seed(path: Path | None = None) -> list[dict[str, Any]]:
    """``scripts/dictionary_seed.py``'s corpus-event seed, when it exists.

    One JSON object per line, written by ``dictionary_seed.run_ask``: ``node_key``,
    ``bout``, ``ts_ms``, ``frame`` (repo-relative), ``corpus_label``, the blind model's
    ``model_label``/``model_type``, ``agree`` and ``review_confidence`` — ``high`` when the
    blind read agreed with the corpus label, ``low`` when it did not. Nothing is dropped
    there, so the disagreements arrive here as the frames most worth a human's eyes.

    It carries NO actor name (``actor_role`` is top/bottom, not an identity), which is why
    an accepted seed frame mints a label line with a null actor — see ``mint_line``.
    """
    path = path or SEED
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def seed_frame(node_key: str, bout: str, ts_ms: int) -> str:
    """Where ``dictionary_seed.frame_paths`` puts a candidate's CENTRE frame.

    Its ``_m2``/``_p2`` neighbours are context for the blind model, not separate claims, so
    they are not queued: a slot on the sheet is capped per technique and three views of one
    claim would crowd out three other techniques.
    """
    return f"audit/gemini_seed/{node_key}/{bout}__{ts_ms}.jpg"


def seed_frame_rel(row: dict[str, Any], node_key: str, dataset: Path) -> str:
    """The seed's ``frame`` (repo-relative) as a DATASET-relative path, or the derived one."""
    raw = str(row.get("frame") or "")
    if raw:
        path = Path(raw)
        path = path if path.is_absolute() else REPO / path
        try:
            return str(path.resolve().relative_to(dataset.resolve()))
        except ValueError:
            pass
    return seed_frame(node_key, str(row.get("bout") or ""), int(row.get("ts_ms") or 0))


def gather_proposals(dataset: Path, wanted: set[str]) -> dict[str, list[Proposal]]:
    """Candidate frames per node_key, best evidence first.

    Order is the whole ergonomics of the audit: a frame the corpus AND a blind Gemini read
    agree on is the cheapest "yes" in the queue, a disagreement is the most informative
    "look closely", and a read-only proposal is the long tail.
    """
    out: dict[str, list[Proposal]] = defaultdict(list)

    for row in load_seed():
        key = node_key_of(str(row.get("node_key") or ""))
        if key not in wanted:
            continue
        high = str(row.get("review_confidence") or "").lower() == "high"
        ts_ms = int(row.get("ts_ms") or 0)
        bout = str(row.get("bout") or "")
        model = str(row.get("model_label") or "?")
        out[key].append(Proposal(
            node_key=key, bout=bout, ts_ms=ts_ms,
            ts=int(row.get("ts") or ts_ms // 1000), rank=0 if high else 1, offset=0,
            caption="corpus+gemini ✓" if high else f"corpus ✗ gemini: {model}",
            frame=seed_frame_rel(row, key, dataset), origin="gemini",
            label=str(row.get("corpus_label") or row.get("label") or ""),
            type=str(row.get("type") or ""), actor=row.get("actor"),
            successful=row.get("successful")))

    for path in sorted((dataset / "labels").glob("*.jsonl")):
        slug = path.stem
        frames = _frames_of(dataset, slug)
        for line in (json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
                     if ln.strip()):
            key = str(line.get("node_key") or "")
            if key not in wanted or line.get("source") == "human" or line.get("review"):
                continue
            for off, frame in near_frames(int(line["ts"]), frames):
                out[key].append(Proposal(
                    node_key=key, bout=slug, ts_ms=int(frame["ts_ms"]), ts=int(frame["ts"]),
                    rank=2, offset=off,
                    caption=f"{line.get('source')} read" + (f" ({off:+d} frame)" if off else ""),
                    frame=f"frames/{slug}/{frame['file']}", origin=str(line.get("source")),
                    label=str(line.get("label") or ""), type=str(line.get("type") or ""),
                    actor=line.get("actor"), successful=line.get("successful")))

    reads = _experiment_reads()
    library = load_library()
    for bout_dir in sorted({r["bout_dir"] for r in reads}):
        sheet = experiment_sheet(bout_dir, reads)
        if sheet is None:
            logger.warning("no sheet for experiment %s", bout_dir)
            continue
        slug = sheet.stem
        relevant = [r for r in reads if r["bout_dir"] == bout_dir
                    and any(node_key_of(str(ev.get("label") or "")) in wanted
                            for ev in r["events"])]
        if not relevant:
            continue
        frames = _frames_of(dataset, slug) or extract_frames(sheet, dataset / "frames" / slug)
        frames = sorted(frames, key=lambda r: r["ts_ms"])
        for read in relevant:
            for ev in read["events"]:
                key = node_key_of(str(ev.get("label") or ""))
                if key not in wanted or not isinstance(ev.get("ts"), int):
                    continue
                for off, frame in near_frames(int(ev["ts"]), frames):
                    out[key].append(Proposal(
                        node_key=key, bout=slug, ts_ms=int(frame["ts_ms"]),
                        ts=int(frame["ts"]), rank=3, offset=off,
                        caption=f"{read['reader']}" + (f" ({off:+d} frame)" if off else ""),
                        frame=f"frames/{slug}/{frame['file']}",
                        origin="human" if read["origin"] == "fable" else "gemini",
                        label=str(library.get(key, {}).get("label")
                                  or ev.get("label") or ""),
                        type=str(ev.get("type") or ""), actor=ev.get("actor"),
                        successful=ev.get("successful")))
    return out


def dedupe(proposals: list[Proposal], cap: int, seen: set[tuple[str, int]]) -> list[Proposal]:
    """Best proposal per (bout, ts_ms), capped, skipping frames already ruled on."""
    best: dict[tuple[str, int], Proposal] = {}
    for p in sorted(proposals, key=lambda p: p.sort_key):
        k = (p.bout, p.ts_ms)
        if k in seen or k in best:
            continue
        best[k] = p
    return sorted(best.values(), key=lambda p: p.sort_key)[:cap]


def queue(dataset: Path = DATASET, cap: int = DEFAULT_CAP, limit: int | None = None,
          min_human: int = DEFAULT_MIN_HUMAN, coverage: dict[str, Any] | None = None,
          render: bool = True) -> dict[str, Any]:
    coverage = coverage or json.loads(COVERAGE.read_text(encoding="utf-8"))
    entries = {e["node_key"]: e for e in coverage["entries"]}
    targets: list[dict[str, Any]] = []
    for e in coverage["entries"]:
        if e["bucket"] in ("zero", "thin"):
            targets.append({**e, "priority": 0 if e["bucket"] == "zero" else 1,
                            "kind": "curated", "label": e["en"]})
    for c in coverage["candidates"]:
        targets.append({**c, "priority": 2, "kind": "candidate", "bucket": "candidate",
                        "en": c["label"], "corpus_events": c["by_source"].get("corpus", 0),
                        "verified_frames": 0})
    targets.sort(key=lambda t: (t["priority"], -int(t.get("corpus_events") or 0),
                                -int(t.get("total") or 0), t["node_key"]))

    ruled = {(str(v["bout"]), int(v["ts_ms"])) for v in load_verdicts(AUDIT / "verdicts.jsonl")}

    proposals = gather_proposals(dataset, {t["node_key"] for t in targets})
    rows: list[dict[str, Any]] = []
    for t in targets:
        picked = dedupe(proposals.get(t["node_key"], []), cap, ruled)
        if not picked:
            rows.append({**_target_row(t, entries), "frames": 0, "sheet": None,
                         "skipped": "no candidate frame proposed by any read"})
            continue
        if limit is not None and sum(1 for r in rows if r.get("sheet")) >= limit:
            rows.append({**_target_row(t, entries), "frames": len(picked), "sheet": None,
                         "skipped": f"--limit {limit} reached"})
            continue
        sheet = SHEETS / f"{_safe(t['node_key'])}.pdf"
        if render:
            missing = render_sheet(dataset, t, picked, sheet)
        else:
            missing = 0
        rows.append({**_target_row(t, entries), "frames": len(picked) - missing,
                     "sheet": str(sheet.relative_to(dataset)) if render else None,
                     "proposals": [p.__dict__ for p in picked]})

    out = {"generated": datetime.now(UTC).isoformat(timespec="seconds"),
           "cap": cap, "min_human": min_human,
           "seed_rows": len(load_seed()),
           "targets": rows}
    QUEUE_JSON.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    write_queue_md(out)
    return out


def _target_row(t: dict[str, Any], entries: dict[str, Any]) -> dict[str, Any]:
    return {"node_key": t["node_key"], "label": t.get("en") or t.get("label"),
            "kind": t["kind"], "bucket": t.get("bucket", "candidate"),
            "type": t.get("type", ""), "corpus_events": int(t.get("corpus_events") or 0),
            "verified_frames": int(t.get("verified_frames") or 0),
            "has_definition": bool(entries.get(t["node_key"], {}).get("has_definition"))}


def _safe(node_key: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in node_key).strip("-") or "unnamed"


def write_queue_md(queue_out: dict[str, Any]) -> Path:
    head = ["priority", "technique", "bucket", "corpus", "verified", "frames queued",
            "definition", "sheet"]
    rows = []
    for t in queue_out["targets"]:
        rows.append([
            {"curated": "", "candidate": "candidate"}.get(t["kind"], ""),
            f"`{t['label']}`", t["bucket"], str(t["corpus_events"]),
            str(t["verified_frames"]), str(t["frames"]),
            "ok" if t["has_definition"] else "**escrever**",
            f"`{t['sheet']}`" if t.get("sheet") else f"— ({t.get('skipped', '')})"])
    text = "\n".join([
        "# Audit queue — one contact sheet per technique",
        "",
        f"Generated {queue_out['generated']} by `scripts/dictionary_audit.py queue` "
        f"(cap {queue_out['cap']} frames/technique, "
        f"{queue_out['seed_rows']} seed rows). **Generated file — do not hand-edit.**",
        "",
        "Order: zero-coverage techniques by corpus frequency first (those are the classes a "
        "tuning run cannot learn today), then thin ones, then candidate labels with no "
        "curated entry. Open a sheet, rule on each frame, and write one verdict line per "
        "frame into `verdicts.jsonl`:",
        "",
        "```json",
        '{"node_key": "kimura", "bout": "<slug>", "ts_ms": 145000, "verdict": "accept", '
        '"note": "figure-four closed, visible"}',
        "```",
        "",
        "`verdict`: `accept` | `reject` | `relabel:<node_key>` | `alias:<node_key>`.",
        "",
        _table(head, rows),
        ""])
    QUEUE_MD.write_text(text, encoding="utf-8")
    return QUEUE_MD


# ---------------------------------------------------------------------- contact sheet

def render_sheet(dataset: Path, target: dict[str, Any], picked: list[Proposal],
                 out_path: Path) -> int:
    """One 2x2 landscape contact sheet for one technique. Returns frames that were missing.

    Reuses ``scripts/frame_pdf.py``'s canvas helpers (font registration, glyph folding,
    capped text block, page size) rather than re-deriving them — same look as the reading
    sheets the auditor already knows, and reportlab still passes each JPEG through untouched.
    """
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    from scripts import frame_pdf as fp

    fp.set_page_size("landscape")
    fp._register_fonts()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = Canvas(str(out_path), pagesize=fp.PAGE)
    title = str(target.get("label") or target.get("en") or target["node_key"])
    c.setTitle(fp._txt(f"{title} — dictionary audit"))

    y = fp.PH - fp.PAD
    c.setFont(fp.FONT_B, 20)
    c.drawString(fp.PAD, y, fp._txt(title))
    y -= 24
    c.setFont(fp.FONT, 11)
    meta = (f"{target.get('pt') or ''}    ·    type: {target.get('type') or '?'}"
            f"    ·    node_key: {target['node_key']}"
            f"    ·    {target.get('bucket', 'candidate')}")
    c.drawString(fp.PAD, y, fp._txt(meta))
    y -= 22
    counts = (f"verified frames {target.get('verified_frames', 0)}   ·   "
              f"corpus events {target.get('corpus_events', 0)}   ·   "
              f"frames queued here {len(picked)}")
    y = fp._text_block(c, counts, fp.PAD, y, fp.PW - 2 * fp.PAD, 10, 14)
    y -= 8
    c.setFont(fp.FONT_B, 11)
    c.drawString(fp.PAD, y, "definição visual")
    y -= 15
    y = fp._text_block(c, target.get("definition") or NO_DEFINITION + " — 1-2 linhas: o que "
                       "tem de estar visível no frame para isto contar",
                       fp.PAD, y, fp.PW - 2 * fp.PAD, 10, 14)
    y -= 8
    if target.get("variants"):
        y = fp._text_block(c, "variantes: " + ", ".join(target["variants"]),
                           fp.PAD, y, fp.PW - 2 * fp.PAD, 9, 12)
    y -= 12
    y = fp._text_block(
        c, 'um veredito por frame em verdicts.jsonl: {"node_key": "'
           + target["node_key"] + '", "bout": "<slug>", "ts_ms": <ts_ms>, "verdict": '
           '"accept|reject|relabel:<node_key>|alias:<node_key>", "note": "..."}',
        fp.PAD, y, fp.PW - 2 * fp.PAD, 9, 12, font=fp.FONT_M)
    c.showPage()

    cols, rows = 2, 2
    per = cols * rows
    cell_w = (fp.PW - 2 * fp.PAD) / cols
    cell_h = (fp.PH - 2 * fp.PAD) / rows
    cap_h = cell_h * 0.20
    drawn = [p for p in picked if (dataset / p.frame).is_file()]
    missing = len(picked) - len(drawn)
    for start in range(0, len(drawn), per):
        for j, p in enumerate(drawn[start:start + per]):
            x = fp.PAD + (j % cols) * cell_w
            y_top = fp.PH - fp.PAD - (j // cols) * cell_h
            c.setFont(fp.FONT_B, 8)
            c.drawString(x, y_top - 9, fp._txt(f"{fp.hhmmss(p.ts)}   ({p.ts}s)   {p.bout}"))
            c.drawImage(ImageReader(str(dataset / p.frame)), x,
                        y_top - cell_h + 6 + cap_h, width=cell_w - 8,
                        height=cell_h - 22 - cap_h, preserveAspectRatio=True, anchor="n")
            fp._text_block_capped(c, f"ts_ms {p.ts_ms} · {p.caption}", x,
                                  y_top - cell_h + cap_h - 4, cell_w - 8, 7.5, 9, cap_h - 4)
        c.showPage()
    c.save()
    return missing


# ----------------------------------------------------------------------------- apply

SIMPLE_VERDICTS = ("accept", "reject")


def parse_verdict(raw: str) -> tuple[str, str]:
    """``accept`` | ``reject`` | ``relabel:<key>`` | ``alias:<key>`` -> ``(kind, target)``."""
    value = str(raw or "").strip()
    kind, _, target = value.partition(":")
    kind = kind.strip().lower()
    if kind in SIMPLE_VERDICTS and not target:
        return kind, ""
    if kind in ("relabel", "alias") and target.strip():
        return kind, node_key_of(target)
    raise ValueError(f"unknown verdict {raw!r} — accept | reject | relabel:<node_key> | "
                     "alias:<node_key>")


def _proposal_index(queue_out: dict[str, Any],
                    dataset: Path = DATASET) -> dict[tuple[str, str, int], dict[str, Any]]:
    """What proposed each queued frame, so a verdict can keep the claim's real origin.

    The seed is read as a FALLBACK, not only through ``queue.json``: a verdict on a seed
    frame that a later ``queue`` run did not re-emit must still resolve to "a model proposed
    this", otherwise accepting it would mint ``source: "human"`` — the laundering this file
    exists not to do.
    """
    idx: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in load_seed():
        key = node_key_of(str(row.get("node_key") or ""))
        ts_ms = int(row.get("ts_ms") or 0)
        idx[(key, str(row.get("bout") or ""), ts_ms)] = {
            "node_key": key, "bout": row.get("bout"), "ts_ms": ts_ms, "ts": ts_ms // 1000,
            "frame": seed_frame_rel(row, key, dataset), "origin": "gemini",
            "label": row.get("corpus_label") or "", "type": "", "actor": None,
            "successful": None}
    for t in queue_out.get("targets", []):
        for p in t.get("proposals") or []:
            idx[(str(p["node_key"]), str(p["bout"]), int(p["ts_ms"]))] = p
    return idx


def mint_line(node_key: str, bout: str, ts_ms: int, proposal: dict[str, Any] | None,
              source: str, reviewer: str, stamped: str, note: str,
              library: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A label line for a claim no existing line carries. Same schema as the builder's.

    ``actor`` is left null when the proposing read named none: this line asserts that the
    technique is VISIBLE in the frame, not who performed it, and inventing an actor to fill
    the field is the exact failure ``frame_answer.py`` refuses on the way in. The Vertex SFT
    exporter skips actorless lines for that reason; frame classification keeps them.
    """
    node = library.get(node_key, {})
    positional = node.get("node_type") in {"control", "guard"}
    actor = (proposal or {}).get("actor")
    frame = (proposal or {}).get("frame") or f"frames/{bout}/{ts_ms:09d}.jpg"
    ts = int((proposal or {}).get("ts") or ts_ms // 1000)
    actor_key = athlete_key(str(actor)) if actor else ""
    line = {
        "bout": bout, "frame": frame, "ts": ts, "ts_ms": ts_ms, "event_ts": ts,
        "node_key": node_key,
        "label": node.get("label") or (proposal or {}).get("label") or node_key,
        "type": (proposal or {}).get("type") or node.get("node_type") or "",
        "state": node_key if positional else None,
        "action": None if positional else node_key,
        "actor": actor, "actor_key": actor_key,
        "successful": (proposal or {}).get("successful"),
        "taxonomy_version": taxonomy_version(),
        "source": source,
        "reviewer": reviewer, "reviewed_at": stamped,
        "review": "accepted", "confidence": "high",
        "claim": "technique_presence",
    }
    if note:
        line["review_note"] = note
    line["label_id"] = label_id(bout, ts_ms, node_key, actor_key, source)
    return line


def record_proposal(kind: str, node_key: str, target: str, note: str, reviewer: str,
                    stamped: str, path: Path | None = None) -> None:
    """Append a dictionary change PROPOSAL. Never edits the curated library itself."""
    path = path or PROPOSALS
    data: dict[str, Any] = {"_comment": (
        "Proposals for analysis/data/technique_library.json, written by "
        "scripts/dictionary_audit.py apply. Applying one is a reviewed, hand-made change — "
        "a script must never edit the curated dictionary (root CLAUDE.md)."), "proposals": []}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("proposals", [])
    entry = {"kind": kind, "node_key": node_key, "target": target or None, "note": note,
             "by": reviewer, "at": stamped}
    existing = [p for p in data["proposals"]
                if (p.get("kind"), p.get("node_key"), p.get("target"))
                == (entry["kind"], entry["node_key"], entry["target"])]
    if not existing:
        data["proposals"].append(entry)
        data["proposals"].sort(key=lambda p: (p["kind"], p["node_key"], p.get("target") or ""))
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")


def apply(verdict_file: Path, dataset: Path = DATASET, write: bool = False,
          reviewer: str = "", rebuild: bool = True) -> dict[str, Any]:
    reviewer = reviewer or f"{getpass.getuser()} (dictionary audit)"
    stamped = datetime.now(UTC).date().isoformat()
    library = load_library()
    queue_out = json.loads(QUEUE_JSON.read_text(encoding="utf-8")) if QUEUE_JSON.is_file() \
        else {}
    proposals = _proposal_index(queue_out, dataset)
    entries = load_dictionary()

    records: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    problems: list[str] = []
    for raw in verdict_file.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        node_key = node_key_of(str(row.get("node_key") or ""))
        bout, ts_ms = str(row.get("bout") or ""), int(row.get("ts_ms") or 0)
        note = str(row.get("note") or "")
        try:
            kind, target = parse_verdict(str(row.get("verdict") or ""))
        except ValueError as exc:
            problems.append(str(exc))
            continue
        proposal = proposals.get((node_key, bout, ts_ms))
        if kind == "alias":
            if write:
                record_proposal("alias", node_key, target, note, reviewer, stamped)
            stats["alias_proposed"] += 1
            continue
        if kind == "reject":
            records.append({"bout": bout, "ts_ms": ts_ms, "node_key": node_key,
                            "verdict": "rejected", "reviewer": reviewer,
                            "reviewed_at": stamped, "note": note or None, "line": None})
            stats["rejected"] += 1
            continue
        # accept / relabel -> the frame carries a technique, and a label line must exist
        key = target if kind == "relabel" else node_key
        # A human naming a technique nobody proposed IS the origin of that claim; accepting
        # what a model proposed is not (docs/vision_dataset.md, "Provenance").
        source = "human" if kind == "relabel" or proposal is None \
            else str(proposal.get("origin") or "gemini")
        line = mint_line(key, bout, ts_ms, proposal if kind == "accept" else None,
                         source, reviewer, stamped, note, library)
        records.append({"bout": bout, "ts_ms": ts_ms, "node_key": key,
                        "verdict": "accepted", "reviewer": reviewer, "reviewed_at": stamped,
                        "note": note or None, "line": line})
        stats["accepted" if kind == "accept" else "relabelled"] += 1
        if kind == "relabel" and node_key != key:
            records.append({"bout": bout, "ts_ms": ts_ms, "node_key": node_key,
                            "verdict": "rejected", "reviewer": reviewer,
                            "reviewed_at": stamped,
                            "note": f"relabelled to {key}", "line": None})
        if key not in entries:
            stats["accepted_off_dictionary"] += 1
            if write:
                record_proposal("add", key, "", note or "accepted from an audit sheet",
                                reviewer, stamped)

    out = {"verdicts": sum(stats.values()), "stats": dict(sorted(stats.items())),
           "problems": problems, "records": len(records), "written": False}
    if problems:
        out["ok"] = False
        return out
    if not write:
        return out

    new = record_verdicts(records, AUDIT / "verdicts.jsonl")
    out["written"], out["new_store_keys"] = True, new
    if rebuild:
        manifest = build(dataset=dataset)
        out["applied_verdicts"] = manifest["applied_verdicts"]
        out["labels"] = manifest["counts"]["labels"]
    return out


# ------------------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    m = sub.add_parser("measure", help="coverage per curated entry + candidate labels")
    m.add_argument("--min-human", type=int, default=DEFAULT_MIN_HUMAN)
    m.add_argument("--no-db", action="store_true", help="skip the prod corpus read")
    m.add_argument("--dataset", type=Path, default=DATASET)

    q = sub.add_parser("queue", help="contact sheets for thin/zero techniques + candidates")
    q.add_argument("--cap", type=int, default=DEFAULT_CAP, help="frames per technique")
    q.add_argument("--limit", type=int, help="render at most N sheets")
    q.add_argument("--dataset", type=Path, default=DATASET)

    a = sub.add_parser("apply", help="ingest the owner's verdicts")
    a.add_argument("verdicts", type=Path)
    a.add_argument("--write", action="store_true", help="default is a dry run")
    a.add_argument("--dry-run", action="store_true", help="explicit no-op (the default)")
    a.add_argument("--no-rebuild", action="store_true",
                   help="skip the vision_dataset rebuild after writing")
    a.add_argument("--reviewer", default="")
    a.add_argument("--dataset", type=Path, default=DATASET)

    args = ap.parse_args(argv)

    if args.command == "measure":
        cov = measure(args.dataset, args.min_human, with_db=not args.no_db)
        t = cov["totals"]
        print(json.dumps(t, indent=1))
        print(f"\nreport: {REPORT}\ncoverage: {COVERAGE}")
        return 0
    if args.command == "queue":
        out = queue(args.dataset, args.cap, args.limit)
        rendered = [t for t in out["targets"] if t.get("sheet")]
        print(f"{len(rendered)} sheet(s) under {SHEETS}, "
              f"{sum(t['frames'] for t in rendered)} frames queued; "
              f"{len(out['targets']) - len(rendered)} technique(s) had no candidate frame")
        print(f"index: {QUEUE_MD}")
        return 0
    out = apply(args.verdicts, args.dataset, write=args.write and not args.dry_run,
                reviewer=args.reviewer, rebuild=not args.no_rebuild)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 1 if out.get("problems") else 0


if __name__ == "__main__":
    raise SystemExit(main())
