#!/usr/bin/env python
"""Turn competition footage into a contact-sheet PDF a vision model can read back as a
timestamped match sequence.

The corpus grows by someone watching a fight and writing down what happened and when. This
module removes the watching, not the judgement: it samples a video every N seconds, lays the
frames out in a grid with the video clock stamped on each one, and puts a context page in
front explaining which fight this is and what the answer must look like. The PDF goes to a
vision model, the model's answer comes back as events, and ``scripts/dump_import`` puts them
in the database.

    uv run python scripts/frame_pdf.py --manifest data/frame_pdf/women_65.json

**The clock is the whole point.** Every stamp printed on a frame is VIDEO-ABSOLUTE — seconds
from the start of the file at the URL, not from the start of the bout. That is the same clock
``matches.ts_origin = 'video_absolute'`` names, and ``video_start_seconds`` is where the bout
begins inside it. Getting this backwards is not a cosmetic error: a bout-relative timestamp
attached to a full-event stream puts every event in the opening minutes of a different fight,
which looks entirely plausible and is entirely wrong (docs/analytics_audit/AA-010). The
context page says so in the prompt, in both directions, so the answer cannot be ambiguous.

**Already-processed fights are skipped.** A video whose id already backs a final match with a
non-empty sequence is not re-rendered — re-deriving a sequence that a human already reviewed
is how a reviewed corpus quietly regresses. ``--force`` overrides, per video id.

Privacy class: **A, public competition data.** Published broadcasts of published bouts, the
same class as everything else in ``matches``. No user-fed data is read, written or implied.

Needs ``yt-dlp``, ``ffmpeg`` and ``ffprobe`` on PATH, and Pillow (already a dependency —
Pillow writes the PDF itself, so there is no reportlab here).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen.canvas import Canvas

logger = logging.getLogger("frame_pdf")

REPO = Path(__file__).resolve().parent.parent
# Run as a plain script (scripts/ is not a package on sys.path), so `analysis.*` and `db.*`
# have to be made importable before any of them is touched -- module scope, not inside the
# first function that happens to need one.
sys.path.insert(0, str(REPO))

# ── Sampling ────────────────────────────────────────────────────────────────────
# One frame every 5s. Chosen against what the reader has to answer, not against what looks
# thorough: the unit of a sequence is a position change (a pass completing, a sweep landing,
# a back taken), and those hold for seconds, not frames. 5s keeps a 10-minute bout at 120
# frames -- 20 grid pages -- while still catching a guard pass between two samples. Faster
# events (a snapdown into a scramble) can fall between frames; that is a known ceiling, and
# the fix when it bites is a second pass at --step 2 over the interval in question, not a
# uniformly denser sheet that costs 3x the pages for the 95% of a bout that is static.
DEFAULT_STEP_SECONDS = 5
DEFAULT_GRID = (2, 3)      # cols x rows -> 6 frames per page

# Ceiling on frames per PDF. A full-event stream with no interval is hours long, and 5s over
# six hours is 4,300 frames -- a file nothing will read and a download that fills the disk.
# When an entry would exceed this the step is widened to fit and the context page reports the
# step it actually used, so a coarse locator pass is never mistaken for a dense one. The
# answer to a video that needs both is two entries: this one to find the bouts, then one
# per bout with a `start`/`end` and the normal step.
DEFAULT_MAX_FRAMES = 400

# Where --dump-library writes by default, and where the context page tells the reader the
# vocabulary lives. Versioned next to the manifests: the manifest says which fights, this
# says which words, and a sheet rendered against one vocabulary is read against the same one.
LIBRARY_PATH = REPO / "data" / "frame_pdf" / "node_library.json"
FRAME_WIDTH = 640          # px; readable body position without an unopenable file
JPEG_QUALITY = 72

PAGE_W, PAGE_H = 1654, 2339   # A4 at 200 dpi, portrait
MARGIN = 48
LABEL_H = 34

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")

# YouTube answers an anonymous download of these uploads with HTTP 403 -- measured, not
# assumed: --dump-json succeeds while the media request is refused, so metadata is not a
# proof that the bytes will come. A logged-in browser profile is what makes them come, and
# yt-dlp reads that profile's cookies directly rather than us handling any credential.
# Set to "" to force an anonymous attempt.
COOKIES_FROM_BROWSER = "firefox"


def _cookie_args() -> list[str]:
    return ["--cookies-from-browser", COOKIES_FROM_BROWSER] if COOKIES_FROM_BROWSER else []


def video_id(url: str) -> str:
    """The 11-char YouTube id, or the url itself when it is not a YouTube link. Identity is
    the id, never the full url: the same video arrives as watch?v=, youtu.be/ and with a
    ``&t=`` offset glued on, and three spellings of one video would each render their own
    PDF and each look like a fight nobody had processed yet."""
    m = _YT_ID.search(url)
    return m.group(1) if m else url.strip()


def hhmmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _fold(name: str) -> str:
    d = unicodedata.normalize("NFKD", name)
    return "".join(c for c in d if not unicodedata.combining(c)).strip().casefold()


# ── Manifest ────────────────────────────────────────────────────────────────────
@dataclass
class Entry:
    """One PDF to produce.

    ``start``/``end`` bound the interval inside the video, in video-absolute seconds. Both
    None means the whole file -- correct for a standalone bout upload, and correct for a
    highlight reel too: when nobody has established where the bouts are, rendering the whole
    reel and letting the reader find them is honest, while guessing an interval is the
    mislocalisation defect all over again.
    """
    url: str
    start: float | None = None
    end: float | None = None
    label: str = ""              # human hint: "Ana Lopez vs Maca Vicentini, IBJJF 2025"
    note: str = ""               # provenance from whoever sourced the link
    step: int | None = None      # per-entry override of --step
    kind: str = ""               # sourcing type: full_match / full_event / highlights
    skip: str = ""               # non-empty = do not render, and this says why

    @property
    def vid(self) -> str:
        return video_id(self.url)

    @property
    def slug(self) -> str:
        # The video id always terminates the name, and is never passed through the
        # sanitiser: ids are case-sensitive and mixed-case, so lowercasing one turns the
        # filename into something that no longer identifies the video it came from.
        span = f"-{int(self.start)}" if self.start else ""
        if not self.label:
            return f"{self.vid}{span}"
        base = re.sub(r"[^a-z0-9-]+", "-", _fold(self.label).replace(" ", "-")).strip("-")[:60]
        return f"{base or 'bout'}{span}-{self.vid}"


def load_manifest(path: Path) -> list[Entry]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["videos"] if isinstance(raw, dict) else raw
    seen: dict[tuple[str, float | None], Entry] = {}
    for r in rows:
        e = Entry(**r) if isinstance(r, dict) else Entry(url=str(r))
        # The source lists repeat a url once per bout it contains. Without a start offset
        # those rows are the same PDF, so collapse them instead of rendering the same reel
        # five times; rows that DO carry distinct offsets stay distinct.
        if e.skip:
            # Kept in the file rather than deleted: a reel that exists and was deliberately
            # not rendered is different from a fight nobody found footage for, and only the
            # first of those is worth re-reading when the sourcing rule changes.
            logger.info("manifest: skipping %s (%s)", e.vid, e.skip)
            continue
        key = (e.vid, e.start)
        if key in seen:
            logger.info("manifest: collapsing duplicate row for %s", e.vid)
            continue
        seen[key] = e
    return list(seen.values())


# ── What the database already knows ─────────────────────────────────────────────
@dataclass
class DbContext:
    processed: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    roster: dict[str, str] = field(default_factory=dict)   # folded name -> division

    def bouts_for(self, vid: str) -> list[dict[str, Any]]:
        return self.processed.get(vid, [])


def _engine() -> Any:
    """Prod, read-only. Deliberately NOT ``db_session()``: that context manager commits on a
    clean exit, and nothing in this module has any business writing."""
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    from db.base import get_engine

    return get_engine()


def load_db_context() -> DbContext:
    """Every final match that already carries a video link, keyed by video id, plus the
    scouting roster used to say which division a fight belongs to.

    Read-only -- see ``_engine``.
    """
    from sqlalchemy import text

    ctx = DbContext()
    with _engine().connect() as c:
        for r in c.execute(text("""
            select m.video_url, m.video_start_seconds, m.ts_origin, m.event, m.year,
                   a.name, b.name, coalesce(jsonb_array_length(m.sequence), 0)
              from matches m
              join athletes a on a.id = m.athlete_a_id
              join athletes b on b.id = m.athlete_b_id
             where m.status = 'final' and m.video_url is not null
        """)):
            ctx.processed.setdefault(video_id(r[0]), []).append({
                "start": r[1], "ts_origin": r[2], "event": r[3], "year": r[4],
                "a": r[5], "b": r[6], "events": r[7],
            })

    scouting = REPO / "data" / "scouting" / "adcc_2026_women.json"
    if scouting.exists():
        for div in json.loads(scouting.read_text(encoding="utf-8")).get("divisions", []):
            for ath in div.get("athletes", []):
                for nm in [ath["name"], *ath.get("aliases", [])]:
                    ctx.roster[_fold(nm)] = div["name"]
    return ctx


# ── yt-dlp / ffmpeg ─────────────────────────────────────────────────────────────
def probe(url: str) -> dict[str, Any]:
    out = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-warnings", "--skip-download", *_cookie_args(), url],
        capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    return {k: d.get(k) for k in ("title", "duration", "uploader", "upload_date", "description")}


def fetch(url: str, dest: Path, start: float | None, end: float | None) -> Path:
    """Download at <=480p, and only the requested section when one is given.

    480p is a deliberate ceiling: the reader has to tell mount from side control, not read
    a patch on a sleeve, and the disk on this machine is the binding constraint (a full
    ADCC stream at 1080p is tens of GB). ``--download-sections`` means a 4-minute bout
    inside a 6-hour broadcast costs 4 minutes of download, not 6 hours.

    Video-only, and the fallbacks matter. Asking for a separate audio stream to merge is
    both wasted bytes and a way to fail outright: several of these uploads only offer the
    combined 360p format 18, against which ``bv*+ba`` has no match at all. Nothing
    downstream of here ever opens an audio track.
    """
    cmd = ["yt-dlp", "--no-warnings", *_cookie_args(), "-f",
           "bv*[height<=480]/b[height<=480]/bv*[height<=720]/b",
           "-o", str(dest / "%(id)s.%(ext)s")]
    if start is not None:
        cmd += ["--download-sections", f"*{int(start)}-{int(end) if end else 999999}",
                "--force-keyframes-at-cuts"]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        # yt-dlp's own message is the only thing that says WHY (geo-block, members-only,
        # bot check, format withdrawn). Swallowing it turns every failure into "exit 1".
        raise RuntimeError(f"yt-dlp failed for {url}: {(r.stderr or r.stdout).strip()[-400:]}")
    files = [p for p in dest.iterdir() if p.suffix.lower() in (".mp4", ".mkv", ".webm")]
    if not files:
        raise RuntimeError(f"yt-dlp produced no video file for {url}")
    return max(files, key=lambda p: p.stat().st_size)


def extract(video: Path, out: Path, step: int, offset: float) -> list[tuple[float, Path]]:
    """Sample every ``step`` seconds. ``offset`` is added back to each frame's time so the
    stamp stays video-absolute even when only a section was downloaded -- the section starts
    at 0 in the local file, and forgetting that is precisely how a bout-relative clock gets
    manufactured."""
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps=1/{step},scale={FRAME_WIDTH}:-2", "-q:v", "4",
         str(out / "f_%05d.jpg")], check=True)
    frames = sorted(out.glob("f_*.jpg"))
    # ffmpeg's fps filter emits the first frame at t=0, then every `step` seconds.
    return [(offset + i * step, p) for i, p in enumerate(frames)]


# ── PDF ─────────────────────────────────────────────────────────────────────────
# reportlab, not Pillow. Pillow can only save a page as a *picture of* text: nothing in the
# sheet is selectable, searchable, or extractable, and a reader that ingests text separately
# from images sees an empty document. reportlab writes real text objects, and hands a JPEG
# straight through as DCTDecode rather than re-encoding it -- so the sheet gains search and
# does not gain weight. (matplotlib is already a dependency and was measured against: its PDF
# backend re-compresses every frame losslessly, which turns a 6 MB sheet into ~40 MB.)

FONT, FONT_B, FONT_M = "Helvetica", "Helvetica-Bold", "Courier"
PAGE = A4
PW, PH = PAGE
PAD = 40


def _text_block(c: Canvas, text: str, x: float, y: float, width: float,
                size: float, leading: float, font: str = FONT) -> float:
    """Wrapped paragraph as real text. Returns the new y."""
    c.setFont(font, size)
    for line in simpleSplit(text, font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y


def draw_context_page(c: Canvas, entry: Entry, meta: dict[str, Any], db: DbContext,
                      n_frames: int, step: int, first_ts: float, last_ts: float,
                      with_library: bool) -> None:
    """The first page: which fight, which clock, and what the answer must look like.

    It is written as a prompt because it IS the prompt -- the PDF is the whole message the
    reader gets, so anything left implicit here comes back as a guess.
    """
    y = PH - PAD
    title = entry.label or str(meta.get("title") or entry.vid)
    c.setFont(FONT_B, 15)
    for line in simpleSplit(title, FONT_B, 15, PW - 2 * PAD):
        c.drawString(PAD, y, line)
        y -= 19
    y -= 4
    c.setLineWidth(1)
    c.line(PAD, y, PW - PAD, y)
    y -= 20

    known = db.bouts_for(entry.vid)
    # Word-boundary, never substring: a roster name that happens to sit inside an unrelated
    # title would label the sheet with a division the fight is not in, and a wrong division
    # on the context page is a wrong premise the reader has no way to check.
    hay = f" {_fold(entry.label or str(meta.get('title') or ''))} "
    div = next((db.roster[k] for k in db.roster if f" {k} " in hay), None)

    rows: list[tuple[str, str]] = [
        ("Video", f"https://www.youtube.com/watch?v={entry.vid}"),
        ("Title", str(meta.get("title") or "-")),
        ("Channel", str(meta.get("uploader") or "-")),
        ("Uploaded", str(meta.get("upload_date") or "-")),
        ("Video length", hhmmss(meta["duration"]) if meta.get("duration") else "-"),
        ("Frames in this PDF", f"{n_frames}, one every {step}s"),
        ("Covers", f"{hhmmss(first_ts)} to {hhmmss(last_ts)} of the video"),
    ]
    if div:
        rows.append(("Division", f"{div} (ADCC 2026 scouting roster)"))
    if entry.kind:
        rows.append(("Footage type", entry.kind.replace("_", " ")))
    if entry.note:
        rows.append(("Source note", entry.note))
    if known:
        rows.append(("Already in the corpus",
                     "; ".join(f"{b['a']} vs {b['b']} ({b['event'] or 'no event'} {b['year']}), "
                               f"{b['events']} events" for b in known)))
    for k, v in rows:
        c.setFont(FONT_B, 9)
        c.drawString(PAD, y, k)
        y = _text_block(c, v, PAD + 130, y, PW - PAD - 130 - PAD, 9, 12) - 4

    y -= 12
    c.line(PAD, y, PW - PAD, y)
    y -= 24
    c.setFont(FONT_B, 14)
    c.drawString(PAD, y, "How to read this")
    y -= 22

    lib = ("The pages immediately after this one list every technique the library knows, "
           "grouped by kind and most-used first; take each `label` from them verbatim."
           if with_library else
           f"Take each `label` verbatim from {LIBRARY_PATH.name}, which lists every technique "
           "the library knows, most-used first.")

    paras: list[tuple[str, str]] = [
        ("p", f"Each frame below is stamped with its VIDEO-ABSOLUTE time: seconds from the "
              f"start of the video at the URL above, NOT seconds from the start of the bout. "
              f"Frame stamps run from {hhmmss(first_ts)} to {hhmmss(last_ts)}."),
        ("p", "Return every timestamp in that same video-absolute clock. If the bout starts "
              "partway into the video, say where it starts as a separate field -- do not "
              "rebase the event times to it. A bout-relative time reported as an absolute one "
              "places the whole fight in a different part of the broadcast, and the result "
              "looks plausible while being wrong."),
        ("p", "For each event you can see, give:"),
        ("f", "ts|integer seconds, video-absolute"),
        ("f", "label|a technique name, taken verbatim from the vocabulary pages"),
        ("f", "actor|which competitor did it, by name"),
        ("f", "successful|true if it landed, false if attempted and stopped"),
        ("f", "type|one of: control, submission, guard, takedown, pass, transition, "
              "sweep, escape"),
        ("f", "points|points the scoreboard awarded for THIS action; omit if you cannot "
              "read it"),
        ("p", "Points are the one thing the scoreboard is authoritative for, and reading "
              "footage is the first time they are visible at all -- a transcript only ever "
              "carried what a commentator said aloud. So: report `points` only when you can "
              "actually read the number change on the scoreboard between frames, attribute "
              "it to the action that caused it, and OMIT the field otherwise. Never write 0 "
              "to mean 'I could not tell' -- an action that scored nothing and an action "
              "nobody could score are different facts, and only an absent field holds the "
              "second. If the score jumps and you cannot see which action earned it, say so "
              "in `note` on the nearest event rather than guessing an attribution."),
        ("p", "Also return, once per bout: the two competitors' names, the event/card name, "
              "the year, who won and how, bout_start_seconds -- the video-absolute second "
              "the bout begins -- and final_score as \"a-b\" if the scoreboard shows one. "
              "If this video contains more than one bout, return one such object per bout."),
        ("p", f"LABELS ARE A CLOSED VOCABULARY. {lib} A label is normalised into a node key, "
              "and that key IS the node in every graph it reaches -- so a spelling the library "
              "does not have does not fail, it mints a second node, and one technique arrives "
              "split in two with no error anywhere. Where two listed names could both fit, "
              "take the one printed higher: that ordering is how often the corpus already "
              "uses it. If a frame genuinely shows something the list does not cover, use "
              "your own words and flag it as `new_label: true` rather than bending it into "
              "the nearest listed name."),
        ("p", "Only report what a frame actually shows. The scoreboard settles POINTS and "
              "nothing else: a technique you infer from a score change but never see is "
              "worth less than an omission, because a wrong label propagates into the "
              "athlete's graph and their rating and nothing downstream can tell it from an "
              "observed one. An omission is recoverable; an invented event is not."),
    ]
    for kind, body in paras:
        if kind == "f":
            # A field list is a table. Re-wrapping it as prose loses the one thing it
            # carries: which name goes with which description.
            name, desc = body.split("|", 1)
            c.setFont(FONT_M, 8.5)
            c.drawString(PAD + 14, y, name)
            y = _text_block(c, desc, PAD + 110, y, PW - PAD - 110 - PAD, 8.5, 11) - 1
        else:
            y = _text_block(c, body, PAD, y, PW - 2 * PAD, 8.5, 11) - 8
    c.showPage()


def draw_library_pages(c: Canvas, library: dict[str, Any]) -> None:
    """The label vocabulary, printed into the PDF as real, searchable text.

    A separate JSON file only helps a reader that was given the file. The sheet is the whole
    message, so the words it is allowed to use travel inside it -- otherwise the closed
    vocabulary the context page insists on is a rule with no list attached.

    Grouped by node_type and ordered most-used-first inside each group, which is the
    preference signal without a legend: where two names could fit, the one printed higher is
    the one the corpus already uses, and picking it keeps a technique in one node instead of
    splitting it across two spellings.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for n in library["nodes"]:
        groups.setdefault(str(n.get("node_type") or "other"), []).append(n)
    order = sorted(groups, key=lambda g: -sum(int(x["corpus_events"]) for x in groups[g]))

    ncols, col_w = 3, (PW - 2 * PAD) / 3
    line_h, size = 10.0, 7.5
    top = PH - PAD - 34
    bottom = PAD

    def header() -> None:
        c.setFont(FONT_B, 13)
        c.drawString(PAD, PH - PAD, "Allowed labels — use one of these, verbatim")
        c.setFont(FONT, 7.5)
        c.drawString(PAD, PH - PAD - 15,
                     f"{library['node_count']} techniques, grouped by kind, most-used first. "
                     f"Snapshot {library['generated']}.")

    header()
    col, y = 0, top

    def advance() -> tuple[int, float]:
        nonlocal col
        col += 1
        if col >= ncols:
            c.showPage()
            header()
            col = 0
        return col, top

    for g in order:
        rows = sorted(groups[g], key=lambda n: (-int(n["corpus_events"]), str(n["key"])))
        # A group header with no room for two of its entries belongs in the next column.
        if y - 3 * line_h < bottom:
            col, y = advance()
        c.setFont(FONT_B, 8.5)
        c.drawString(PAD + col * col_w, y, g.upper())
        y -= line_h + 3
        c.setFont(FONT, size)
        for n in rows:
            if y - line_h < bottom:
                col, y = advance()
                c.setFont(FONT, size)
            c.drawString(PAD + col * col_w, y, str(n["label"])[:36])
            y -= line_h
        y -= 5
    c.showPage()


def draw_grid_pages(c: Canvas, frames: list[tuple[float, Path]], grid: tuple[int, int]) -> None:
    """The frames, each with its video-absolute stamp as real text above it.

    ``drawImage`` on a JPEG path embeds the original DCTDecode stream, so a frame costs what
    ffmpeg already spent on it and nothing more.
    """
    cols, rows = grid
    per = cols * rows
    cell_w = (PW - 2 * PAD) / cols
    cell_h = (PH - 2 * PAD) / rows
    for i in range(0, len(frames), per):
        for j, (ts, path) in enumerate(frames[i:i + per]):
            x = PAD + (j % cols) * cell_w
            # reportlab's origin is bottom-left; rows read top-down.
            y_top = PH - PAD - (j // cols) * cell_h
            c.setFont(FONT_B, 8)
            c.drawString(x, y_top - 9, f"{hhmmss(ts)}   ({int(ts)}s)")
            c.drawImage(ImageReader(str(path)), x, y_top - cell_h + 6,
                        width=cell_w - 8, height=cell_h - 22,
                        preserveAspectRatio=True, anchor="n")
        c.showPage()


def build_pdf(entry: Entry, meta: dict[str, Any], db: DbContext,
              frames: list[tuple[float, Path]], step: int, grid: tuple[int, int],
              out_path: Path, library: dict[str, Any] | None = None) -> None:
    c = Canvas(str(out_path), pagesize=PAGE)
    c.setTitle(entry.label or str(meta.get("title") or entry.vid))
    c.setSubject(f"https://www.youtube.com/watch?v={entry.vid} — frames every {step}s, "
                 "timestamps are video-absolute")
    draw_context_page(c, entry, meta, db, len(frames), step,
                      frames[0][0], frames[-1][0], with_library=library is not None)
    if library:
        draw_library_pages(c, library)
    draw_grid_pages(c, frames, grid)
    c.save()


# ── Node library ────────────────────────────────────────────────────────────────
def dump_node_library(out_path: Path) -> str:
    """Every label a returned event is allowed to use, as JSON.

    A sequence event's ``label`` is not free text: it is normalised
    (``analysis.names._normalize_name`` then ``canonicalize``) into a ``node_key``, and that
    key IS the node in every graph the label reaches -- the athlete's, the ocean, the
    archetype centroids. A label that normalises to a key the library does not have does not
    fail; it silently mints a new node, and the athlete's game arrives split between "Heel
    Hook" and "Heelhook" with no error anywhere. Handing the reader the vocabulary up front
    is what stops that, and it is cheaper than repairing it after (see the six spelling
    merges in docs/analytics_audit).

    The whole library ships, not just the attested part, because a new label can be correct:
    an athlete really can do something no bout in the corpus has shown yet. ``corpus_events``
    is there to break ties toward the established spelling, not to forbid the rest.

    Read-only against prod, and a snapshot by design: it records the day it was taken, so a
    stale copy is visible rather than merely old.
    """
    from datetime import date

    from sqlalchemy import text

    from analysis.names import SYNONYMS

    with _engine().connect() as c:
        nodes = [
            {"key": r[0], "label": r[1], "type": r[2], "node_type": r[3] or None,
             "taxonomy_id": r[4]}
            for r in c.execute(text(
                "select node_key, label, type, node_type, taxonomy_id "
                "from technique_nodes order by node_key"))
        ]
        used = {r[0]: r[1] for r in c.execute(text(
            "select e->>'label', count(*) from matches m, jsonb_array_elements(m.sequence) e "
            "where m.status = 'final' and e->>'label' is not null group by 1"))}
        types = [{"type": r[0], "corpus_events": r[1]} for r in c.execute(text(
            "select e->>'type', count(*) from matches m, jsonb_array_elements(m.sequence) e "
            "where m.status = 'final' and e->>'type' is not null group by 1 order by 2 desc"))]
        alembic = c.execute(text("select version_num from alembic_version")).scalar()

    from analysis.names import _normalize_name, canonicalize
    counts: dict[str, int] = {}
    for label, n in used.items():
        counts[canonicalize(_normalize_name(label))] = counts.get(
            canonicalize(_normalize_name(label)), 0) + n
    for node in nodes:
        node["corpus_events"] = counts.get(node["key"], 0)
    nodes.sort(key=lambda n: (-n["corpus_events"], n["key"]))

    payload = {
        "generated": date.today().isoformat(),
        "source": f"technique_nodes on prod, alembic {alembic}",
        "node_count": len(nodes),
        "attested_in_corpus": sum(1 for n in nodes if n["corpus_events"]),
        "how_a_label_becomes_a_node": (
            "label -> analysis.names._normalize_name (lowercase, strip punctuation and "
            "accents, collapse whitespace) -> canonicalize (the `synonyms` map below) -> "
            "node_key. Prefer a `label` from this file verbatim. A label that normalises to "
            "a key not listed here mints a new node instead of failing, which is how one "
            "technique ends up split across two spellings."
        ),
        "event_types": types,
        "synonyms": SYNONYMS,
        "nodes": nodes,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return (f"{len(nodes)} nodes ({payload['attested_in_corpus']} attested in the corpus), "
            f"{len(types)} event types, {len(SYNONYMS)} synonyms -> {out_path}")


# ── Driver ──────────────────────────────────────────────────────────────────────
def _span_seconds(entry: Entry, meta: dict[str, Any]) -> float | None:
    """How much video this entry covers, or None when the duration is unknown."""
    if entry.start is not None and entry.end is not None:
        return max(0.0, entry.end - entry.start)
    if not meta.get("duration"):
        return None
    return float(meta["duration"]) - (entry.start or 0.0)


def process(entry: Entry, db: DbContext, out_dir: Path, step: int,
            grid: tuple[int, int], workdir: Path, force: bool,
            max_frames: int = DEFAULT_MAX_FRAMES,
            library: dict[str, Any] | None = None) -> str:
    known = db.bouts_for(entry.vid)
    if known and not force:
        with_seq = [b for b in known if b["events"]]
        if with_seq:
            return (f"skip {entry.vid}: already in the corpus with a reviewed sequence "
                    f"({with_seq[0]['a']} vs {with_seq[0]['b']}, {with_seq[0]['events']} events)")

    out_path = out_dir / f"{entry.slug}.pdf"
    if out_path.exists() and not force:
        return f"skip {entry.vid}: {out_path.name} already built"

    meta = probe(entry.url)
    step = entry.step or step
    span = _span_seconds(entry, meta)
    if span and span / step > max_frames:
        widened = int(-(-span // max_frames))          # ceil, so the count lands under the cap
        logger.info("%s: %s of video at %ds would be %d frames -- widening to %ds",
                    entry.vid, hhmmss(span), step, int(span / step), widened)
        step = widened
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        tmpd = Path(tmp)
        video = fetch(entry.url, tmpd, entry.start, entry.end)
        frames = extract(video, tmpd / "frames", step, entry.start or 0.0)
        video.unlink(missing_ok=True)          # the frames are the artefact; disk is tight
        if not frames:
            return f"FAIL {entry.vid}: ffmpeg produced no frames"
        build_pdf(entry, meta, db, frames, step, grid, out_path, library)
    mb = out_path.stat().st_size / 1e6
    return f"ok   {out_path.name}  {len(frames)} frames, {mb:.1f} MB"


def main() -> int:
    global COOKIES_FROM_BROWSER
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=Path,
                    help="videos to render; not needed with --dump-library")
    ap.add_argument("--dump-library", type=Path, nargs="?", const=LIBRARY_PATH,
                    help="write the label vocabulary a returned event may use, and exit")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "frame_pdf" / "out")
    ap.add_argument("--step", type=int, default=DEFAULT_STEP_SECONDS)
    ap.add_argument("--grid", default="2x3", help="cols x rows per page (default 2x3)")
    ap.add_argument("--workdir", type=Path, default=Path("/mnt/dados/bjjh/tmp"),
                    help="scratch for downloads; defaults off the small root volume")
    ap.add_argument("--cookies-from-browser", default=COOKIES_FROM_BROWSER,
                    help="browser profile yt-dlp reads cookies from; '' for anonymous "
                         "(anonymous currently gets HTTP 403 on these uploads)")
    ap.add_argument("--no-library", action="store_true",
                    help="do not print the label vocabulary into the sheet")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when the video already backs a reviewed sequence")
    ap.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES,
                    help="widen the step rather than exceed this many frames in one PDF")
    ap.add_argument("--limit", type=int, help="stop after N videos (a smoke run)")
    ap.add_argument("--dry-run", action="store_true", help="report the plan, download nothing")
    a = ap.parse_args()

    if a.dump_library:
        print(dump_node_library(a.dump_library))
        return 0
    if not a.manifest:
        ap.error("--manifest is required unless --dump-library is given")

    for tool in ("yt-dlp", "ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            logger.error("%s is not on PATH", tool)
            return 2

    COOKIES_FROM_BROWSER = a.cookies_from_browser
    cols, rows = (int(x) for x in a.grid.lower().split("x"))
    entries = load_manifest(a.manifest)
    db = load_db_context()
    a.out.mkdir(parents=True, exist_ok=True)
    a.workdir.mkdir(parents=True, exist_ok=True)

    library = None
    if not a.no_library:
        # Refresh rather than trust a copy on disk: the sheet asserts a closed vocabulary,
        # and a stale list makes that assertion false for anything added since. It is one
        # query and it is the difference between a rule and a wrong rule.
        logger.info("%s", dump_node_library(LIBRARY_PATH))
        library = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))

    if a.limit:
        entries = entries[:a.limit]
    logger.info("%d videos after collapsing duplicates; %d already linked in the corpus",
                len(entries), sum(1 for e in entries if db.bouts_for(e.vid)))

    if a.dry_run:
        for e in entries:
            known = db.bouts_for(e.vid)
            state = ("already reviewed" if any(b["events"] for b in known)
                     else "linked, no sequence" if known else "new")
            print(f"  {e.vid}  {state:20s}  {e.label or '(no label)'}")
        return 0

    results = []
    for e in entries:
        try:
            results.append(process(e, db, a.out, a.step, (cols, rows), a.workdir,
                                   a.force, a.max_frames, library))
        except Exception as exc:                                  # noqa: BLE001
            # One dead link must not cost the other 27 downloads. The reason is printed
            # next to the id, so the manifest row can be fixed and only that row re-run.
            results.append(f"FAIL {e.vid}: {exc}")
    for r in results:
        print(" ", r)
    fails = [r for r in results if r.startswith("FAIL")]
    print(f"\n{len(results) - len(fails)} ok, {len(fails)} failed -> {a.out}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
