#!/usr/bin/env python
"""Build ``data/finetune/`` — the durable, versioned vision dataset for BJJ frame reading.

    uv run python -m scripts.vision_dataset --build
    uv run python -m scripts.vision_dataset --build --version v1 --seed 0

This is a DATASET, not an export. Frames are immutable files with hashes; labels are one
JSONL line per labelled frame carrying its own provenance; splits are cut once per version
and never reshuffled. Whatever consumes it (Vertex SFT today, our own vision model later)
lives in ``scripts/vision_dataset_export.py`` as a function OF a split, never as the
storage format.

Layout::

    data/finetune/
      frames/<bout_slug>/<ts_ms>.jpg   immutable, one frame per file
      frames/<bout_slug>/frames.json   [{file, ts, ts_ms, sha256, bytes, page, index}]
      labels/<bout_slug>.jsonl         one line per labelled frame (schema below)
      sheets/<bout_slug>.pdf           SYMLINK to the rendered sheet -- a derived view
      splits/<version>.json            group-wise split, cut once, never reshuffled
      manifests/<version>.json         counts per class/split, hashes, taxonomy version
      DATASET_CARD.md                  origin, licence/privacy, known defects

Where the pixels come from
--------------------------
``scripts/frame_pdf.py`` renders each bout's frames into a sheet PDF through reportlab,
which passes a JPEG through UNTOUCHED (that is why a sheet is ~6 MB and not ~40 MB). The
storage policy of ``docs/frame_pdf_reading.md`` §1 then drops the ``frames/`` folders and
keeps the PDF as the durable artefact. So the original 1280x720 JPEGs still exist -- inside
the PDFs -- and ``pdfimages -j`` gets them back byte-for-byte (verified: the extracted files
still carry ffmpeg's ``Lavc`` JPEG comment). Their video-absolute timestamps come from the
sheet's own TEXT layer ("H:MM:SS (NNNNs)" above every cell), in the same document order as
the images, so nothing is re-derived by arithmetic that could drift from what a human read.

No network, no video download, no re-render.

Label line
----------
::

    {"bout": "<slug>", "frame": "frames/<slug>/<ts_ms>.jpg", "ts": 17665, "ts_ms": 17665000,
     "node_key": "armbar", "label": "Armbar", "type": "submission",
     "state": null, "action": "armbar",
     "actor": "Nadia Frankland", "actor_key": "nadia-frankland", "successful": true,
     "taxonomy_version": "node_library@<sha12>",
     "source": "human" | "gemini" | "gemini_ft:<job>",
     "reviewer": "...", "reviewed_at": "2026-08-25", "review": "accepted"|"rejected"|null,
     "confidence": "high"|"low", "label_id": "<sha12>"}

``state`` vs ``action`` is DERIVED, not invented: the technique library
(``data/frame_pdf/node_library.json``) already carries a ``node_type`` per node, and
``control``/``guard`` are positions a frame is IN while the other six types are things that
HAPPEN. Both are emitted so the raw ``type`` is never lost.

There is deliberately NO per-frame state for unlabelled frames. The reading prompt says
"log DISCRETE occurrences, not per-frame states", so a dense state track does not exist in
the source data and would have to be invented here. The manifest counts unlabelled frames
instead; filling them in is a labelling job, not a build step.

Provenance
----------
``source`` is the ORIGIN of the claim and is never rewritten -- promoting a model reading to
"human" on review is exactly the laundering ``frame_registrar.py`` was fixed for (2026-08-24).
A human pass sets ``review: accepted|rejected`` alongside; admissible = ``source == "human"``
OR ``review == "accepted"``. ``scripts/dataset_review.py`` is the only writer of ``review``,
and it refuses to touch a ``source == "human"`` line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from analysis.names import _normalize_name, athlete_key, canonicalize  # noqa: E402

logger = logging.getLogger("vision_dataset")

DATA = REPO / "data"
FRAME_PDF = DATA / "frame_pdf"
DATASET = DATA / "finetune"
NODE_LIBRARY = FRAME_PDF / "node_library.json"

# Positional node types -- a frame is IN one of these. Everything else HAPPENS in a frame.
STATE_TYPES = frozenset({"control", "guard"})

DEFAULT_VAL_FRACTION = 0.2
DEFAULT_SEED = 0


# --------------------------------------------------------------------------- sources

@dataclass(frozen=True)
class Source:
    """One directory of answer JSON, with the provenance every file in it carries."""

    answers: Path
    origin: str            # "human" | "gemini"
    sheets: Path | None    # directory holding <slug>.pdf, or None -> resolved per batch
    batch: str
    pattern: str = "*.events.json"


def default_sources() -> list[Source]:
    """The audited (human) and raw (gemini) answer sets that exist today.

    Order matters only for reporting; a bout appearing in several sources keeps one label
    line per (frame, node_key, actor, source), so a human line and a gemini line coexist.
    """
    trials = FRAME_PDF / "trials_2023_24"
    processed = FRAME_PDF / "out" / "processed"
    bruno = FRAME_PDF / "bruno_rocha"
    return [
        Source(trials / "answers", "human", trials, "trials_2023_24"),
        Source(processed / "audit", "human", processed, "women_65"),
        Source(bruno / "answers", "human", None, "bruno_rocha"),
        Source(trials / "answers" / "raw", "gemini", trials, "trials_2023_24", "*.json"),
        Source(processed / "audit" / "raw", "gemini", processed, "women_65", "*.json"),
        Source(bruno / "answers" / "raw", "gemini", None, "bruno_rocha", "*.json"),
    ]


def taxonomy_version() -> str:
    """Pin the label vocabulary a build was made against, by content hash of the library."""
    digest = hashlib.sha256(NODE_LIBRARY.read_bytes()).hexdigest()[:12]
    return f"node_library@{digest}"


def load_library() -> dict[str, dict[str, Any]]:
    """node_key -> node row, from the same 376-node library the sheets print."""
    lib = json.loads(NODE_LIBRARY.read_text(encoding="utf-8"))
    return {n["key"]: n for n in lib["nodes"]}


# ------------------------------------------------------------------ sheet resolution

def _fold(s: str) -> str:
    """Same fold ``frame_pdf.Entry.slug`` applies before slugifying a bout label."""
    return re.sub(r"[^a-z0-9-]+", "-", s.lower().replace(" ", "-")).strip("-")


def sheet_index(sheets_dir: Path) -> dict[str, Path]:
    """Fold every sheet PDF's stem so an answer slug can be matched against it.

    The two names are slugified by different functions in different scripts
    (``frame_pdf.Entry.slug`` vs ``gemini_normalize.slugify``) and disagree on punctuation:
    ``heikki-jussila-vs-daniel-manasoiu`` (answer) vs
    ``heikki-jussila-vs--daniel-manasoiu-pwGbW5GZgfc`` (sheet). Folding runs of ``-`` and
    matching on prefix is what bridges them without either script changing.
    """
    out: dict[str, Path] = {}
    if not sheets_dir.is_dir():
        return out
    for p in sorted(sheets_dir.glob("*.pdf")):
        out[re.sub(r"-+", "-", p.stem.lower())] = p
    return out


def resolve_sheet(slug: str, index: dict[str, Path]) -> Path | None:
    key = re.sub(r"-+", "-", slug.lower())
    if key in index:
        return index[key]
    # Sheet names append "-<start>-<video_id>"; the answer slug is the bare bout label.
    hits = [p for k, p in sorted(index.items()) if k.startswith(key + "-")]
    return hits[0] if len(hits) == 1 else None


# ------------------------------------------------------------------ frame extraction

_TS_RE = re.compile(r"\((\d+)s\)")


def sheet_timestamps(pdf: Path) -> list[int]:
    """Video-absolute seconds printed above each frame cell, in document order."""
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True,
                          check=True).stdout
    return [int(m) for m in _TS_RE.findall(text)]


def _image_pages(pdf: Path) -> list[int]:
    """Page number of every embedded image, in the order ``pdfimages`` emits them."""
    out = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True,
                         check=True).stdout.splitlines()
    pages = []
    for line in out[2:]:
        parts = line.split()
        if len(parts) > 2 and parts[2] == "image":
            pages.append(int(parts[0]))
    return pages


def extract_frames(pdf: Path, dest: Path) -> list[dict[str, Any]]:
    """Pull the original JPEGs out of a sheet PDF into ``dest/<ts_ms>.jpg``.

    Idempotent: an existing ``frames.json`` whose files are all present is returned as-is.
    Frames are IMMUTABLE -- a rebuild never rewrites a byte that is already on disk, which
    is what makes the sha256 in the manifest worth recording.
    """
    record = dest / "frames.json"
    if record.exists():
        cached: list[dict[str, Any]] = json.loads(record.read_text(encoding="utf-8"))
        if all((dest / r["file"]).exists() for r in cached):
            return cached

    stamps = sheet_timestamps(pdf)
    pages = _image_pages(pdf)
    if len(stamps) != len(pages):
        raise ValueError(f"{pdf.name}: {len(stamps)} printed timestamps vs {len(pages)} "
                         f"embedded images — refusing to guess the pairing")

    dest.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-j", str(pdf), f"{tmp}/f"], check=True)
        got = sorted(Path(tmp).iterdir())
        if len(got) != len(stamps):
            raise ValueError(f"{pdf.name}: extracted {len(got)} images for {len(stamps)} "
                             "timestamps")
        for i, (src, ts, page) in enumerate(zip(got, stamps, pages, strict=True)):
            data = src.read_bytes()
            name = f"{ts * 1000:09d}.jpg"
            target = dest / name
            if not target.exists():
                target.write_bytes(data)
            rows.append({"file": name, "ts": ts, "ts_ms": ts * 1000,
                         "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                         "page": page, "index": i})
    record.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    return rows


# ------------------------------------------------------------------------- labelling

def classify_origin(answer: dict[str, Any], declared: str) -> str:
    """Provenance from the answer's own ``source`` string, falling back to the directory.

    Both stamps in circulation are recognised: ``concordance-audited`` (the batch QA of
    ``docs/gemini_concordance_audit.md``, where a human formed an independent read of every
    event) and ``human review`` (``frame_registrar.py``). Anything else is a model reading.
    """
    src = str(answer.get("source") or "")
    if "concordance-audited" in src or "human review" in src:
        return "human"
    if src:
        return "gemini"
    return declared


def snap_to_frame(ts: int, frame_ts: list[int]) -> int | None:
    """Nearest sampled frame to an event's second, or None if none is within half a step.

    Events are read off the sheet, so an event's ts is normally a printed stamp exactly.
    A few sit between samples (the audit is allowed to move a ts onto the scoreboard change);
    those attach to the nearest frame, and anything further than half the sampling interval
    away has no frame to attach to and is dropped rather than pinned to a frame that does
    not show it.
    """
    if not frame_ts:
        return None
    best = min(frame_ts, key=lambda f: (abs(f - ts), f))
    step = min((b - a for a, b in zip(frame_ts, frame_ts[1:], strict=False)), default=5)
    return best if abs(best - ts) <= max(step, 1) / 2 else None


def label_id(bout: str, ts_ms: int, node_key: str, actor_key: str, source: str) -> str:
    raw = f"{bout}|{ts_ms}|{node_key}|{actor_key}|{source}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def build_labels(slug: str, answer: dict[str, Any], frames: list[dict[str, Any]],
                 origin: str, library: dict[str, dict[str, Any]],
                 taxonomy: str) -> tuple[list[dict[str, Any]], Counter[str]]:
    """One label line per (event snapped to its frame). Returns (lines, drop reasons)."""
    frame_ts = [f["ts"] for f in frames]
    by_ts = {f["ts"]: f for f in frames}
    bout = answer.get("bout", {})
    reviewer = str(bout.get("identity_verified_by") or "") if origin == "human" else ""
    reviewed_at = ""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(answer.get("source") or ""))
    if m:
        reviewed_at = m.group(1)

    lines: list[dict[str, Any]] = []
    drops: Counter[str] = Counter()
    for ev in answer.get("events", []):
        raw_label = str(ev.get("label") or "").strip()
        if not raw_label:
            drops["no_label"] += 1
            continue
        node_key = canonicalize(_normalize_name(raw_label))
        node = library.get(node_key)
        # The vocabulary is the contract; an off-library label mints a second node and
        # splits one technique in two silently (docs/frame_pdf_reading.md §2.5).
        if node is None:
            drops["off_library_label"] += 1
            continue
        ts = ev.get("ts")
        if not isinstance(ts, int):
            drops["no_ts"] += 1
            continue
        snapped = snap_to_frame(ts, frame_ts)
        if snapped is None:
            drops["no_frame_within_half_step"] += 1
            continue
        actor = str(ev.get("actor") or "").strip()
        if not actor:
            drops["no_actor"] += 1
            continue
        ntype = str(ev.get("type") or node.get("node_type") or "")
        positional = (node.get("node_type") in STATE_TYPES)
        frame = by_ts[snapped]
        source = origin
        line = {
            "bout": slug,
            "frame": f"frames/{slug}/{frame['file']}",
            "ts": snapped,
            "ts_ms": frame["ts_ms"],
            "event_ts": ts,
            "node_key": node_key,
            "label": node.get("label", raw_label),
            "type": ntype,
            "state": node_key if positional else None,
            "action": None if positional else node_key,
            "actor": actor,
            "actor_key": athlete_key(actor),
            "successful": ev.get("successful"),
            "taxonomy_version": taxonomy,
            "source": source,
            "reviewer": reviewer or None,
            "reviewed_at": reviewed_at or None,
            "review": "accepted" if origin == "human" else None,
            "confidence": ev.get("confidence") or ("high" if origin == "human" else "low"),
        }
        if isinstance(ev.get("points"), int):
            line["points"] = ev["points"]
        line["label_id"] = label_id(slug, frame["ts_ms"], node_key, line["actor_key"], source)
        lines.append(line)
    lines.sort(key=lambda r: (r["ts_ms"], r["node_key"], r["actor_key"], r["source"]))
    return lines, drops


# ----------------------------------------------------------------------------- split

@dataclass
class Bout:
    slug: str
    batch: str
    sheet: Path
    athletes: tuple[str, ...]
    n_frames: int = 0
    n_labels: int = 0
    origins: set[str] = field(default_factory=set)


def athlete_groups(bouts: list[Bout]) -> dict[str, str]:
    """bout slug -> group id, where a group is a connected component over shared athletes.

    Grouping BY BOUT is the project's golden rule (93% vs 21% leakage, measured on the BJJ
    visual dataset), and one example == one bout already satisfies it. This goes one level
    further because it has to: the hardest half of the task is deciding WHICH BODY is which,
    and an athlete who appears in both train and val leaks exactly that. Kit, tape and
    tattoos are memorisable across bouts -- that property is what made the Bruno Rocha
    identity call decidable by hand, so it is equally available to a model.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for b in bouts:
        node = f"bout:{b.slug}"
        find(node)
        for a in b.athletes:
            if a:
                union(node, f"ath:{a}")
    return {b.slug: find(f"bout:{b.slug}") for b in bouts}


def cut_split(bouts: list[Bout], val_fraction: float, seed: int) -> dict[str, Any]:
    """Deterministic group-wise split: largest group first, always to the emptier side.

    Not ``GroupShuffleSplit``: one component here holds 23 of 70 bouts, and a random group
    permutation can land that single component in val and hand back a 33% test set. Filling
    the side with the larger deficit, largest group first, is deterministic and cannot
    overshoot by more than one group.
    """
    groups = athlete_groups(bouts)
    by_group: dict[str, list[Bout]] = defaultdict(list)
    for b in bouts:
        by_group[groups[b.slug]].append(b)
    # seed only breaks ties between equal-sized groups -- the ordering is otherwise total,
    # so the same corpus + same seed is the same split, byte for byte.
    order = sorted(by_group.items(),
                   key=lambda kv: (-len(kv[1]),
                                   hashlib.sha256(f"{seed}|{kv[0]}".encode()).hexdigest()))
    target_val = round(len(bouts) * val_fraction)
    train: list[str] = []
    val: list[str] = []
    for gid, members in order:
        slugs = sorted(b.slug for b in members)
        take_val = (target_val - len(val)) >= (len(bouts) - target_val - len(train))
        (val if (take_val and len(val) < target_val) else train).extend(slugs)
    return {
        "version": None,
        "seed": seed,
        "val_fraction": val_fraction,
        "group_key": "athlete_connected_component",
        "groups": {gid: sorted(b.slug for b in ms) for gid, ms in sorted(by_group.items())},
        "train": sorted(train),
        "val": sorted(val),
        "excluded": {},
    }


def merge_split(published: dict[str, Any], fresh: dict[str, Any]) -> dict[str, Any]:
    """Extend a published split with new bouts WITHOUT moving an existing one.

    A published split is frozen: reshuffling it invalidates every measurement taken against
    it. New bouts are appended to whichever side their group already sits on. A new bout
    whose group straddles both sides is EXCLUDED with that reason rather than quietly
    leaking an athlete across the boundary -- the fix for that is cutting a new version, not
    editing this one.
    """
    out = dict(published)
    side = {s: "train" for s in published["train"]}
    side.update({s: "val" for s in published["val"]})
    excluded = dict(published.get("excluded") or {})
    train, val = list(published["train"]), list(published["val"])
    for gid, slugs in fresh["groups"].items():
        sides = {side[s] for s in slugs if s in side}
        new = [s for s in slugs if s not in side and s not in excluded]
        if not new:
            continue
        if len(sides) > 1:
            for s in new:
                excluded[s] = "group_straddles_published_split — cut a new split version"
            continue
        dest = sides.pop() if sides else ("val" if len(val) < len(train) * 0.25 else "train")
        (val if dest == "val" else train).extend(new)
    out["train"], out["val"] = sorted(train), sorted(val)
    out["groups"] = fresh["groups"]
    out["excluded"] = excluded
    return out


# ----------------------------------------------------------------------------- build

def build(version: str = "v1", seed: int = DEFAULT_SEED,
          val_fraction: float = DEFAULT_VAL_FRACTION,
          sources: list[Source] | None = None,
          dataset: Path = DATASET,
          batches: set[str] | None = None) -> dict[str, Any]:
    library = load_library()
    taxonomy = taxonomy_version()
    sources = sources or default_sources()
    if batches:
        sources = [s for s in sources if s.batch in batches]

    bouts: dict[str, Bout] = {}
    labels: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped: dict[str, str] = {}
    drops: Counter[str] = Counter()

    for src in sources:
        if not src.answers.is_dir():
            logger.warning("source missing: %s", src.answers)
            continue
        index = sheet_index(src.sheets) if src.sheets else {}
        for path in sorted(src.answers.glob(src.pattern)):
            if path.is_dir():
                continue
            slug = path.stem.replace(".events", "")
            answer = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(answer.get("events"), list):
                continue
            sheet = resolve_sheet(slug, index)
            if sheet is None:
                skipped.setdefault(slug, f"no sheet PDF under {src.sheets or '(none)'}")
                continue
            origin = classify_origin(answer, src.origin)
            frames = extract_frames(sheet, dataset / "frames" / slug)
            lines, d = build_labels(slug, answer, frames, origin, library, taxonomy)
            drops.update(d)
            bout = bouts.get(slug)
            if bout is None:
                b = answer.get("bout", {})
                bout = bouts[slug] = Bout(
                    slug=slug, batch=src.batch, sheet=sheet,
                    athletes=tuple(sorted({athlete_key(str(b.get(k) or ""))
                                           for k in ("athlete_a", "athlete_b")} - {""})),
                    n_frames=len(frames))
            bout.origins.add(origin)
            seen = {ln["label_id"] for ln in labels[slug]}
            labels[slug].extend(ln for ln in lines if ln["label_id"] not in seen)

    # ---- write labels + sheet symlinks
    (dataset / "labels").mkdir(parents=True, exist_ok=True)
    (dataset / "sheets").mkdir(parents=True, exist_ok=True)
    for slug, lines in labels.items():
        lines.sort(key=lambda r: (r["ts_ms"], r["node_key"], r["actor_key"], r["source"]))
        bouts[slug].n_labels = len(lines)
        (dataset / "labels" / f"{slug}.jsonl").write_text(
            "".join(json.dumps(ln, ensure_ascii=False, sort_keys=True) + "\n" for ln in lines),
            encoding="utf-8")
        link = dataset / "sheets" / f"{slug}.pdf"
        if not link.exists():
            # A derived view, not a copy: 326 MB of sheets already exist one directory over.
            link.symlink_to(bouts[slug].sheet.resolve())

    # ---- split (cut once per version, never reshuffled)
    ordered = [bouts[s] for s in sorted(bouts)]
    fresh = cut_split(ordered, val_fraction, seed)
    split_path = dataset / "splits" / f"{version}.json"
    split_path.parent.mkdir(parents=True, exist_ok=True)
    if split_path.exists():
        split = merge_split(json.loads(split_path.read_text(encoding="utf-8")), fresh)
    else:
        split = fresh
    split["version"] = version
    split["excluded"] = {**skipped, **(split.get("excluded") or {})}
    split_path.write_text(json.dumps(split, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    manifest = write_manifest(dataset, version, split, ordered, labels, taxonomy, drops)
    write_card(dataset, manifest)
    return manifest


_STOP = frozenset({"the", "to", "of", "and", "a", "guard", "control", "choke", "takedown",
                   "pass", "escape", "sweep", "attempt"})


def near_miss_clusters(counts: dict[str, dict[str, int]]) -> dict[str, list[dict[str, Any]]]:
    """Labels a reader can confuse: same event ``type``, sharing a content word.

    Measured 2026-09-02, zero-shot baseline: precision/recall fail on LABEL DISCRIMINATION
    ("Snap Down" vs "Single Leg Takedown", "Choke" vs "Rear Naked Choke"; guard/control
    sub-position recall 0.09-0.13), not on timing (1.1 s mean error) or identity (90% actor).
    So the number that matters for the next labelling batch is not "how many events" but
    "how many examples separate two labels a model already mixes up". This surfaces exactly
    those groups; a group whose members total only a handful of examples cannot be learned.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for etype, labels in counts.items():
        buckets: dict[str, dict[str, int]] = defaultdict(dict)
        for key, n in labels.items():
            for word in set(key.split()) - _STOP:
                buckets[word][key] = n
        groups = [{"shared_word": w, "labels": dict(sorted(m.items(), key=lambda kv: -kv[1])),
                   "total": sum(m.values())}
                  for w, m in buckets.items() if len(m) > 1]
        if groups:
            out[etype] = sorted(groups, key=lambda g: (g["total"], g["shared_word"]))
    return out


def write_manifest(dataset: Path, version: str, split: dict[str, Any], bouts: list[Bout],
                   labels: dict[str, list[dict[str, Any]]], taxonomy: str,
                   drops: Counter[str]) -> dict[str, Any]:
    side = {s: "train" for s in split["train"]}
    side.update({s: "val" for s in split["val"]})
    per_split: dict[str, Counter[str]] = defaultdict(Counter)
    per_type: dict[str, Counter[str]] = defaultdict(Counter)
    type_label: dict[str, Counter[str]] = defaultdict(Counter)
    per_origin: Counter[str] = Counter()
    labelled_frames: dict[str, set[int]] = defaultdict(set)
    for slug, lines in labels.items():
        s = side.get(slug, "excluded")
        for ln in lines:
            per_split[s][ln["node_key"]] += 1
            per_type[s][ln["type"]] += 1
            per_origin[ln["source"]] += 1
            if s == "train" and (ln["source"] == "human" or ln.get("review") == "accepted"):
                type_label[ln["type"]][ln["node_key"]] += 1
            labelled_frames[slug].add(ln["ts_ms"])
    total_frames = sum(b.n_frames for b in bouts)
    frame_bytes = sum(
        r["bytes"] for b in bouts
        for r in json.loads((dataset / "frames" / b.slug / "frames.json").read_text()))
    manifest = {
        "version": version,
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "taxonomy_version": taxonomy,
        "split_file": f"splits/{version}.json",
        "counts": {
            "bouts": len(bouts),
            "bouts_train": len(split["train"]),
            "bouts_val": len(split["val"]),
            "bouts_excluded": len(split.get("excluded") or {}),
            "frames": total_frames,
            "frames_labelled": sum(len(v) for v in labelled_frames.values()),
            "frames_unlabelled": total_frames - sum(len(v) for v in labelled_frames.values()),
            "labels": sum(len(v) for v in labels.values()),
            "frame_bytes": frame_bytes,
        },
        "labels_by_origin": dict(sorted(per_origin.items())),
        "labels_by_type": {k: dict(sorted(v.items())) for k, v in sorted(per_type.items())},
        "classes_by_split": {k: dict(sorted(v.items(), key=lambda kv: (-kv[1], kv[0])))
                             for k, v in sorted(per_split.items())},
        "dropped_events": dict(sorted(drops.items())),
        # Train-split, admissible labels only: what a tuning run would actually learn from.
        "train_labels_by_type_and_class": {
            t: dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))
            for t, c in sorted(type_label.items())},
        "train_rare_classes": sorted(f"{ty}/{k}" for ty, c in type_label.items()
                                     for k, n in c.items() if n < 3),
        "train_near_miss_clusters": near_miss_clusters(
            {t: dict(c) for t, c in type_label.items()}),
        "bouts": [
            {"slug": b.slug, "batch": b.batch, "split": side.get(b.slug, "excluded"),
             "athletes": list(b.athletes), "frames": b.n_frames, "labels": b.n_labels,
             "origins": sorted(b.origins),
             "sheet_sha256": hashlib.sha256(b.sheet.read_bytes()).hexdigest()[:16],
             "sheet_bytes": b.sheet.stat().st_size}
            for b in bouts],
    }
    path = dataset / "manifests" / f"{version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


CARD = """# Dataset card — GrapplingArc vision dataset ({version})

Generated {generated} by `scripts/vision_dataset.py`. **Generated file — do not hand-edit**;
change the builder or the source answers and rebuild.

## What it is

Competition grappling footage sampled every 5s, one JPEG per frame, with human-audited
technique events attached to the frame they were read off. Built to be the ground truth for
(a) supervised tuning of a hosted vision model and (b) our own frame classifier later. The
frames are the originals `scripts/frame_pdf.py` extracted — recovered from the sheet PDFs
with `pdfimages -j`, byte-for-byte, no re-encode.

## Provenance and privacy

**Public corpus only.** Every bout here is competition footage already published by its
event (FloGrappling / YouTube broadcasts of ADCC Trials, IBJJF, CBJJ, Polaris). Nothing a
user fed through the App is in this dataset, and nothing from it may be joined to user-fed
rows — root `CLAUDE.md`, "Public vs Private Data". Owner-recorded gym video may only enter
with the filmed athletes' consent recorded alongside it, and user video never enters without
an explicit opt-in. Neither is in {version}.

Labels are DERIVED from broadcast footage; the footage itself is not redistributed by this
repo (`data/` is gitignored — only manifests, splits and this card are committable).

Label origin, per line, never rewritten:

| `source` | what it is | admissible for training |
|---|---|---|
| `human` | concordance audit (`docs/gemini_concordance_audit.md`) — an auditor formed an independent read of every event off the frames | yes |
| `gemini` | a raw model reading, no human verdict | only once `review: accepted` |
| `gemini_ft:<job>` | a tuned model's reading | only once `review: accepted` |

`scripts/dataset_review.py` is the only writer of `review`, and refuses `source == "human"`.

## Contents

| | |
|---|---|
| bouts | {bouts} ({bouts_train} train / {bouts_val} val / {bouts_excluded} excluded) |
| frames | {frames} ({frames_labelled} carry ≥1 label, {frames_unlabelled} unlabelled) |
| labels | {labels} |
| frame bytes | {frame_mb:.0f} MB |
| taxonomy | `{taxonomy_version}` (376-node technique library) |

Labels by origin: {by_origin}

Train-split admissible labels per event type: {by_type_totals}

## Class distribution — read this before planning the next labelling batch

The zero-shot baseline (`scripts/gemini_baseline.py`, 2026-09-02, gemini-3.6-flash,
thinking=high, N=10 audited trials bouts) measured **P 0.39 / R 0.29 / F1 0.34**, mean ts
error **1.1 s**, actor agreement **90%**, and `confidence: "high"` on 100% of events (so the
model's own confidence carries no signal). The failure is **label discrimination**, not
timing and not identity: guard/control sub-position recall was 0.09–0.13, and the confusions
were near-misses inside one event type (Snap Down vs Single Leg Takedown, Choke vs Rear
Naked Choke).

That makes class COUNT per near-miss group the number that decides whether tuning can help.

- **{n_train_classes} distinct (event type, node_key) classes in the admissible train split;
  {n_rare} of them have fewer than 3 examples.** A class seen twice cannot be separated from
  its neighbour. (The build's "train classes" line counts distinct `node_key` over ALL label
  origins, so it reads slightly lower — different question, both true.)
- Near-miss groups (same event type, sharing a content word) with their per-label counts are
  in `manifests/{version}.json` → `train_near_miss_clusters`. Worst offenders today:
{near_miss_lines}

Consequence for the next batch: labelling more bouts of the same common positions moves
nothing. What moves F1 is bouts that carry the RARE members of these groups, and audit
attention spent on separating the pairs above rather than on re-confirming `guard pull`.
Vertex SFT has no per-example weight, so coverage is the only lever available here.

## Split

`splits/{version}.json`, group key = **athlete connected component**. One example is one
bout, which already satisfies the project's grouped-by-bout rule (measured 93% vs 21%
leakage); the component grouping additionally keeps an athlete from appearing on both sides,
because kit/tape/tattoos are memorisable and identity is the hardest half of the task.

**A published split is frozen.** Rebuilding extends it with new bouts on the side their group
already sits on; a new bout whose group straddles both sides is excluded with that reason.
Reshuffling means a new version file, never an edit.

## Known limits (measured, not hedged)

- **No dense per-frame state.** The reading prompt logs discrete occurrences, not per-frame
  states, so unlabelled frames mean "nothing was logged here", NOT "nothing is happening".
  Do not train a frame-level state classifier on absence.
- **Residual leakage.** The `trials_2023_24` bouts all come from ONE 8h recording, so a val
  bout from that batch shares broadcast style, mat and overlay with train bouts. Only
  identity leakage is controlled.
- **Label quality varies by broadcast.** Concordance audit measured 93% kept on the trials
  batch and 35% on the Bruno Rocha batch; the difference tracks whether the broadcast shows
  points. See `docs/PROMPT_gemini_frame_reading.md`, "Measured performance".
- **Known defect classes** in model-origin labels: whole-bout identity swaps, actor flipped
  on guard/pass exchanges, `ts` a few frames off the scoreboard change.
- **Two source batches, one audit protocol.** `trials_2023_24` and `women_65`
  (`data/frame_pdf/out/processed/audit/`) both carry the `concordance-audited` stamp and
  both have per-event `audit_log/` verdict files. `data/frame_pdf/out/processed/*.events.json`
  — the unreviewed `frame_answer_import` sidecars one directory up — are NOT ingested.
  Rebuild with `--batches trials_2023_24` to cut a trials-only version.

## Files

```
frames/<bout>/<ts_ms>.jpg    immutable frame, sha256 in frames.json
frames/<bout>/frames.json    [{{file, ts, ts_ms, sha256, bytes, page, index}}]
labels/<bout>.jsonl          one line per labelled frame (schema in the builder docstring)
sheets/<bout>.pdf            symlink to the rendered sheet (derived view)
splits/<version>.json        frozen once cut
manifests/<version>.json     counts, hashes, taxonomy, class distribution — committable
```
"""


def write_card(dataset: Path, manifest: dict[str, Any]) -> None:
    c = manifest["counts"]
    per_type = manifest["train_labels_by_type_and_class"]
    worst = sorted(
        (g for gs in manifest["train_near_miss_clusters"].values() for g in gs),
        key=lambda g: (g["total"], g["shared_word"]))[:8]
    lines = "\n".join(
        f"  - `{g['shared_word']}` — " + ", ".join(f"`{k}` ×{v}" for k, v in g["labels"].items())
        for g in worst) or "  - (none)"
    (dataset / "DATASET_CARD.md").write_text(CARD.format(
        version=manifest["version"], generated=manifest["generated"],
        taxonomy_version=manifest["taxonomy_version"],
        by_origin=", ".join(f"`{k}` {v}" for k, v in manifest["labels_by_origin"].items()),
        by_type_totals=", ".join(f"`{t}` {sum(v.values())}"
                                 for t, v in sorted(per_type.items())),
        n_train_classes=sum(len(v) for v in per_type.values()),
        n_rare=len(manifest["train_rare_classes"]),
        near_miss_lines=lines,
        frame_mb=c["frame_bytes"] / 1e6, **c), encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", action="store_true", help="build/refresh the dataset")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--val-fraction", type=float, default=DEFAULT_VAL_FRACTION)
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--batches", nargs="*", metavar="BATCH",
                    help="restrict to these source batches (trials_2023_24, women_65, "
                         "bruno_rocha); default = all")
    a = ap.parse_args()
    if not a.build:
        ap.error("nothing to do; pass --build")
    if not shutil.which("pdfimages") or not shutil.which("pdftotext"):
        raise SystemExit("poppler-utils (pdfimages, pdftotext) not on PATH")

    m = build(a.version, a.seed, a.val_fraction, dataset=a.dataset,
              batches=set(a.batches) if a.batches else None)
    c = m["counts"]
    print(f"\n{a.dataset}  [{m['version']}]  taxonomy={m['taxonomy_version']}")
    print(f"  bouts   {c['bouts']}  ({c['bouts_train']} train / {c['bouts_val']} val / "
          f"{c['bouts_excluded']} excluded)")
    print(f"  frames  {c['frames']}  ({c['frames_labelled']} labelled, "
          f"{c['frames_unlabelled']} unlabelled, {c['frame_bytes'] / 1e6:.0f} MB)")
    print(f"  labels  {c['labels']}  by origin {m['labels_by_origin']}")
    if m["dropped_events"]:
        print(f"  dropped {m['dropped_events']}")
    for s in ("train", "val"):
        top = list(m["classes_by_split"].get(s, {}).items())[:8]
        print(f"  {s:5s} classes {len(m['classes_by_split'].get(s, {}))} — top {top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
