"""Admin audit tools — the whole data path for both audit modes, same shape as
``admin/study.py`` owning the Study page.

Mode A (rounds) — ``data/video/owner/out/<slug>/{read.json,analysis.json,frames/,video.mp4?,
AUDIT_VISUAL.md}``. **PRIVATE** (root ``CLAUDE.md`` + this repo's ``CLAUDE.md``, "Public vs
Private Data") — the owner's own training footage. Human verdicts land in
``verdicts.json`` beside the round; nothing here ever copies a frame/clip/verdict out of
``data/video/``.

Mode B (dictionary) — ``data/finetune/audit/gemini_seed/seed.jsonl``. **PUBLIC** corpus
frames. Verdicts flow through ``scripts.dictionary_audit.apply``, the sole writer of
``data/finetune/audit/verdicts.jsonl`` (owned by ``scripts.vision_dataset``); this module
never writes that file directly.
"""

from __future__ import annotations

import html
import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analysis.names import _normalize_name, canonicalize

REPO = Path(__file__).resolve().parent.parent
ROUND_ROOT = REPO / "data" / "video" / "owner" / "out"
DICTIONARY_LIBRARY = REPO / "analysis" / "data" / "technique_library.json"
NODE_LIBRARY = REPO / "data" / "frame_pdf" / "node_library.json"
DICT_AUDIT_ROOT = REPO / "data" / "finetune" / "audit"
DICT_SEED_ROOT = DICT_AUDIT_ROOT / "gemini_seed"

_FRAME_RE = re.compile(r"^t(\d{5})(?:b(\d+))?$")


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Mode A: rounds ──────────────────────────────────────────────────────────────────────────


def _round_dirs(root: Path | None = None) -> list[Path]:
    # ponytail: `root` defaults through `None`, not `root: Path = ROUND_ROOT` -- a default
    # *argument value* is bound once at def time, so admin/server.py's routes (which never
    # pass `root`) would keep calling the ORIGINAL ROUND_ROOT forever, even after a test (or
    # a future caller) monkeypatches the module attribute. Reading it inside the body keeps
    # every call late-bound to whatever ROUND_ROOT currently is, same convention
    # scripts/round_audit.py's OUT_ROOT already relies on for its own resume tests.
    root = ROUND_ROOT if root is None else root
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "read.json").is_file())


def list_round_slugs(root: Path | None = None) -> list[dict[str, Any]]:
    """One summary row per round — counts + difficulty, for the rounds list page."""
    out = []
    for d in _round_dirs(root):
        read_doc = _read_json(d / "read.json") or {}
        events = read_doc.get("events") or []
        analysis = _read_json(d / "analysis.json") or {}
        verdicts_doc = _read_json(d / "verdicts.json") or {}
        reviewed = sum(1 for v in verdicts_doc.get("events") or [] if v.get("verdict"))
        out.append({
            "slug": d.name,
            "n_events": len(events),
            "n_reviewed": reviewed,
            "difficulty": analysis.get("difficulty"),
            "has_video": (d / "video.mp4").is_file(),
            "has_visual_audit": (d / "AUDIT_VISUAL.md").is_file(),
        })
    return out


def _frame_index(frames_dir: Path) -> list[dict[str, Any]]:
    """Frame filenames -> ``{ts, file}``, sorted -- filenames carry the timestamp
    (``t%05d[b%d].jpg``, ``scripts/video_frames.py:extract_frames``)."""
    frames: list[dict[str, Any]] = []
    if frames_dir.is_dir():
        for p in frames_dir.glob("t*.jpg"):
            m = _FRAME_RE.match(p.stem)
            if not m:
                continue
            frames.append({
                "ts": int(m.group(1)), "suffix": int(m.group(2) or 0), "file": p.name,
            })
    frames.sort(key=lambda f: (f["ts"], f["suffix"]))
    return frames


def _merge_events(
    events: list[dict[str, Any]], verdicts_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Original ``read.json`` events (id = their index) + any reviewer-added events (id
    ``new-*``), each carrying its saved verdict/correction if one exists. This IS the reload
    state — no separate "restore" path, the merged view always reflects the last save."""
    merged = []
    for i, ev in enumerate(events):
        vid = str(i)
        v = verdicts_by_id.get(vid) or {}
        merged.append({
            "id": vid, "original": ev,
            "verdict": v.get("verdict"), "corrected": v.get("corrected"),
            "note": v.get("note") or "",
        })
    added = [(vid, v) for vid, v in verdicts_by_id.items() if vid.startswith("new-")]
    for vid, v in sorted(added, key=lambda pair: (pair[1].get("corrected") or {}).get("ts", 0)):
        merged.append({
            "id": vid, "original": None,
            "verdict": v.get("verdict") or "added", "corrected": v.get("corrected"),
            "note": v.get("note") or "",
        })
    return merged


def load_round(slug: str, root: Path | None = None) -> dict[str, Any] | None:
    root = ROUND_ROOT if root is None else root
    d = root / slug
    read_doc = _read_json(d / "read.json")
    if read_doc is None:
        return None
    verdicts_doc = _read_json(d / "verdicts.json") or {}
    verdicts_by_id = {str(v.get("id")): v for v in verdicts_doc.get("events") or []}
    analysis = _read_json(d / "analysis.json") or {}
    return {
        "slug": slug,
        "events": _merge_events(read_doc.get("events") or [], verdicts_by_id),
        "resets": read_doc.get("resets") or [],
        "bout": read_doc.get("bout") or {},
        "frames": _frame_index(d / "frames"),
        "has_video": (d / "video.mp4").is_file(),
        # Reuses analysis.json's own `highlights` (start/end/label/score, already derived by
        # `analysis.round_analysis.build_highlights`) instead of re-parsing HIGHLIGHTS.md --
        # same 5 clips, structured. The round page seeks the ONE main video to `start` rather
        # than playing the separately-cut clip files under highlights/.
        "highlights": analysis.get("highlights") or [],
        "visual_html": (
            render_markdown((d / "AUDIT_VISUAL.md").read_text(encoding="utf-8"))
            if (d / "AUDIT_VISUAL.md").is_file() else ""
        ),
    }


def save_round_verdicts(
    slug: str, payload: dict[str, Any], root: Path | None = None
) -> dict[str, Any]:
    """Autosave target — the whole verdicts document is overwritten every call (idempotent,
    no merge logic needed client- or server-side). Atomic write: a crash mid-save never
    corrupts the last good ``verdicts.json``."""
    root = ROUND_ROOT if root is None else root
    d = root / slug
    if not (d / "read.json").is_file():
        raise FileNotFoundError(slug)
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("payload.events must be a list")
    doc = {
        "slug": slug,
        "reviewed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "events": events,
    }
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ".verdicts.json.tmp"
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(d / "verdicts.json")
    return doc


def build_corrected_timeline(slug: str, root: Path | None = None) -> list[dict[str, Any]]:
    """The human-truth event list: original events with a saved correction applied, dropping
    anything marked ``not_visible``, plus every reviewer-added event. Written beside the
    round as ``events_corrected.json`` and returned for an immediate download."""
    root = ROUND_ROOT if root is None else root
    round_data = load_round(slug, root)
    if round_data is None:
        raise FileNotFoundError(slug)
    out: list[dict[str, Any]] = []
    for ev in round_data["events"]:
        if ev["verdict"] == "not_visible":
            continue
        corrected = ev.get("corrected") or {}
        if ev["original"] is not None:
            base = dict(ev["original"])
            for k in ("ts", "actor", "label", "type", "successful"):
                if corrected.get(k) is not None:
                    base[k] = corrected[k]
        else:
            base = {k: corrected.get(k) for k in ("ts", "actor", "label", "type", "successful")}
        base["verdict"] = ev["verdict"] or "unreviewed"
        out.append(base)
    out.sort(key=lambda e: float(e.get("ts") or 0.0))
    (root / slug / "events_corrected.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out


def round_frame_path(slug: str, filename: str, root: Path | None = None) -> Path | None:
    root = ROUND_ROOT if root is None else root
    base = (root / slug / "frames").resolve()
    p = (base / filename).resolve()
    if base not in p.parents:
        return None
    return p if p.is_file() else None


def round_video_path(slug: str, root: Path | None = None) -> Path | None:
    root = ROUND_ROOT if root is None else root
    p = root / slug / "video.mp4"
    return p if p.is_file() else None


def audit_label_vocab() -> list[dict[str, str]]:
    """The Mode A label picker's vocabulary: the curated dictionary (en/pt/type, 211
    entries) first, then the corpus vocabulary (``data/frame_pdf/node_library.json``) for
    anything not already covered — first-writer-wins on the label, same convention
    ``sync_app_artifacts`` uses for node identity."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    rows = _read_json(DICTIONARY_LIBRARY) or []
    for row in rows:
        en = str(row.get("en") or "").strip()
        if not en or en.lower() in seen:
            continue
        seen.add(en.lower())
        out.append({
            "label": en, "type": str(row.get("type") or ""), "pt": str(row.get("pt") or ""),
            "node_key": canonicalize(_normalize_name(en)),
        })
    doc = _read_json(NODE_LIBRARY) or {}
    for node in doc.get("nodes") or []:
        label = str(node.get("label") or "").strip()
        if not label or label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append({
            "label": label, "type": str(node.get("node_type") or ""), "pt": "",
            "node_key": str(node.get("key") or canonicalize(_normalize_name(label))),
        })
    return out


# ── tiny stdlib-only Markdown -> HTML (AUDIT_VISUAL.md reviewer hints) ────────────────────────


def _sep_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c) for c in cells if c)


def _inline_md(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _render_table(lines: list[str]) -> str:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    if len(rows) >= 2 and _sep_row(rows[1]):
        rows.pop(1)
    if not rows:
        return ""
    head, *body = rows
    thead = "".join(f"<th>{_inline_md(c)}</th>" for c in head)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{_inline_md(c)}</td>" for c in r) + "</tr>" for r in body
    )
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"


def render_markdown(text: str) -> str:
    """Headers, bold/code/links, bullet lists, GFM pipe tables — the whole subset
    ``AUDIT_VISUAL.md`` actually uses.

    ponytail: not a general Markdown engine (no nesting, no blockquotes, no numbered
    lists) — reach for a real parser (none is a direct dep today, see
    ``docs/dictionary_audit.md``) if a report starts using more than this.
    """
    lines = text.splitlines()
    out: list[str] = []
    in_list = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            i += 1
            continue
        header = re.match(r"^(#{1,4})\s+(.*)", line)
        if header:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(header.group(1))
            out.append(f"<h{level}>{_inline_md(header.group(2))}</h{level}>")
            i += 1
            continue
        if line.strip().startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            out.append(_render_table(table_lines))
            continue
        if re.match(r"^[-*]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_md(re.sub(r'^[-*]\s+', '', line))}</li>")
            i += 1
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{_inline_md(line)}</p>")
        i += 1
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


# ── Mode B: dictionary queue ────────────────────────────────────────────────────────────────

_CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


def dictionary_queue(min_confidence: str | None = None) -> list[dict[str, Any]]:
    """Cards for ``/admin/audit/dictionary`` — every ``seed.jsonl`` row Gemini actually read
    (``candidate_label`` present, same filter ``gather_proposals`` applies for seed rows),
    minus any ``(bout, ts_ms)`` already ruled on (same set ``dictionary_audit.queue`` builds
    from ``load_verdicts``).

    A ``pair`` row (item 32, 2026-09-16 — a genuine action+state double label, never a
    disagreement) additionally carries ``pair_node_key``/``pair_label``/``pair_kind``/
    ``kind`` — the OTHER half of the double label, read off the row's own ``labels`` field
    (``dictionary_seed.build_row_labels``: ``[corpus_line, candidate_line]`` for ``pair``).
    ``None`` for every other tier.

    ponytail: reads ``load_seed``/``load_verdicts`` directly rather than the full
    ``gather_proposals``/``queue`` pipeline — that pipeline also renders PDF contact sheets
    and pulls in ``labels/*.jsonl`` + experiment reads, none of which a per-frame card grid
    over the seed batch needs. Switch to ``gather_proposals`` if Mode B ever has to cover
    those other sources too.
    """
    from scripts.dictionary_audit import load_seed, node_key_of
    from scripts.vision_dataset import load_verdicts

    ruled = {(str(v["bout"]), int(v["ts_ms"])) for v in load_verdicts()}
    out: list[dict[str, Any]] = []
    for row in load_seed():
        candidate = str(row.get("candidate_label") or "").strip()
        if not candidate:
            continue
        bout, ts_ms = str(row.get("bout") or ""), int(row.get("ts_ms") or 0)
        if (bout, ts_ms) in ruled:
            continue
        second = row.get("second_opinion") or {}
        confidence = str(row.get("review_confidence") or "low")
        agree_near = str(second.get("agree_near") or row.get("agree_near")
                         or row.get("agree") or "no")
        item: dict[str, Any] = {
            "node_key": node_key_of(candidate),
            "candidate_label": candidate,
            "candidate_type": str(row.get("candidate_type") or ""),
            "confidence": confidence,
            "corpus_label": str(second.get("corpus_label") or row.get("corpus_label") or ""),
            "agree": str(second.get("agree") or row.get("agree") or "no"),
            "agree_near": agree_near,
            "bout": bout, "ts": int(row.get("ts") or ts_ms // 1000), "ts_ms": ts_ms,
            "frame": str(row.get("frame") or ""),
            "kind": None, "pair_node_key": None, "pair_label": None, "pair_kind": None,
        }
        labels = row.get("labels") or []
        if agree_near == "pair" and len(labels) == 2:
            corpus_line, candidate_line = labels[0], labels[1]
            item["kind"] = candidate_line.get("kind")
            item["pair_node_key"] = corpus_line.get("node_key")
            item["pair_label"] = item["corpus_label"] or corpus_line.get("node_key")
            item["pair_kind"] = corpus_line.get("kind")
        out.append(item)
    out.sort(key=lambda r: _CONF_ORDER.get(r["confidence"], 0))
    if min_confidence:
        floor = _CONF_ORDER.get(min_confidence, 0)
        out = [r for r in out if _CONF_ORDER.get(r["confidence"], 0) >= floor]
    return out


def dictionary_frame_path(rel: str) -> Path | None:
    base = DICT_SEED_ROOT.resolve()
    p = (REPO / rel).resolve()
    if base not in p.parents and p != base:
        return None
    return p if p.is_file() else None


def apply_dictionary_verdict(
    node_key: str, bout: str, ts_ms: int, verdict: str, note: str = "",
    pair_node_key: str | None = None,
) -> dict[str, Any]:
    """One card action -> one (or two) lines through ``scripts.dictionary_audit.apply``, the
    sole writer of ``data/finetune/audit/verdicts.jsonl``. ``rebuild=False`` (the CLI's own
    ``--no-rebuild``) — a full ``vision_dataset.build()`` per click would make every card
    slow; the owner reruns the CLI's rebuild separately when ready to fine-tune.

    ``pair_node_key`` (item 32, 2026-09-16): a ``pair`` card's OTHER half. Accept/Reject apply
    to BOTH node_keys (one ``verdicts.jsonl`` record each, same bout/ts_ms — two trustworthy
    claims on the same frame); Relabel only ever replaces the candidate's own claim
    (``node_key``), never the pair's other half.
    """
    from scripts.dictionary_audit import apply as apply_verdict_file

    rows: list[dict[str, Any]] = [
        {"node_key": node_key, "bout": bout, "ts_ms": ts_ms, "verdict": verdict}]
    if pair_node_key and verdict in ("accept", "reject"):
        rows.append({"node_key": pair_node_key, "bout": bout, "ts_ms": ts_ms, "verdict": verdict})
    if note:
        for row in rows:
            row["note"] = note
    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
        tmp_path = Path(fh.name)
    try:
        return apply_verdict_file(tmp_path, write=True, rebuild=False)
    finally:
        tmp_path.unlink(missing_ok=True)
