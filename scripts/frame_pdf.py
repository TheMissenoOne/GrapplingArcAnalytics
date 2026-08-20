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

Needs ``yt-dlp``, ``ffmpeg`` and ``ffprobe`` on PATH, and reportlab, which writes real text
objects and passes each JPEG through untouched — so the sheet is searchable and costs the
bytes of its frames and nothing more.
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
# Resolved BJJ Heroes results, tracked. The scraped HTML lives under ``data/raw/`` and is
# gitignored, so without this a rebuild anywhere else would have to hit bjjheroes.com again
# just to reprint a line it already knew. It also records which bouts resolved and which did
# not, which is the half worth keeping: "no result found" is a fact about coverage.
BJJH_PATH = REPO / "data" / "frame_pdf" / "bjjh_results.json"
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

# Set by _register_fonts(). The built-in names are the LAST resort: reportlab's standard
# fonts are Type1/WinAnsi, and a character outside that page does not raise -- it is drawn as
# a black box and extracts as one too. Silent mojibake in a sheet whose whole job is to be
# read back is the failure mode worth spending a font on.
FONT, FONT_B, FONT_M = "Helvetica", "Helvetica-Bold", "Courier"

# (regular, bold, mono) candidates, best coverage first. Liberation carries Latin Extended
# plus Greek and Cyrillic (2328 glyphs); Vera ships inside reportlab itself (283), so the
# chain always lands somewhere even on a machine with no system fonts installed.
_FONT_SETS = [
    ("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
     "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf"),
    ("/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",
     "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
     "/usr/share/fonts/liberation-mono-fonts/LiberationMono-Regular.ttf"),
]

# Typographic lookalikes an athlete name or a YouTube title picks up, mapped to the ASCII the
# fonts above all have. U+2011 is not academic: it is already in this corpus ("World No‑Gi"),
# and no candidate font carries it.
_ASCIIFY = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"',
    "\u2026": "...", "\u00a0": " ", "\u200b": "", "\u2212": "-",
})
_DRAWABLE: frozenset[int] | None = None
PAGE = A4
PW, PH = PAGE
PAD = 40


def _register_fonts() -> str:
    """Swap the Type1 defaults for a TrueType family with real Unicode coverage.

    Returns the family it settled on, for the log -- which font is in use decides which
    characters survive, so it is not a detail to leave implicit.
    """
    global FONT, FONT_B, FONT_M, _DRAWABLE
    import os

    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    vera = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    sets = [*_FONT_SETS, (f"{vera}/Vera.ttf", f"{vera}/VeraBd.ttf", f"{vera}/VeraMono.ttf")]
    for reg, bold, mono in sets:
        if not (os.path.exists(reg) and os.path.exists(bold)):
            continue
        name = os.path.splitext(os.path.basename(reg))[0]
        faces = {"sheet": reg, "sheet-b": bold}
        if os.path.exists(mono):
            faces["sheet-m"] = mono
        for alias, path in faces.items():
            pdfmetrics.registerFont(TTFont(alias, path))
        FONT, FONT_B = "sheet", "sheet-b"
        FONT_M = "sheet-m" if "sheet-m" in faces else "sheet"
        _DRAWABLE = frozenset(pdfmetrics.getFont(FONT).face.charToGlyph)
        return name
    logger.warning("no TrueType font found; falling back to Helvetica (Latin-1 only)")
    return "Helvetica"


def _txt(s: str) -> str:
    """Text as the chosen font can actually draw it.

    Two steps, in order: fold typographic variants onto their ASCII twin, then replace
    anything the font still has no glyph for with '?'. The second step exists because
    reportlab does not fail on a missing glyph -- it draws a filled box, and that box is what
    reaches both the page and the extracted text. A '?' is not better typography; it is an
    honest marker that a character was dropped, where the box pretends one was rendered.
    """
    s = str(s).translate(_ASCIIFY)
    if _DRAWABLE is None:
        return s
    return "".join(ch if ord(ch) in _DRAWABLE or ch in "\n\t" else "?" for ch in s)


def _text_block(c: Canvas, text: str, x: float, y: float, width: float,
                size: float, leading: float, font: str = FONT) -> float:
    """Wrapped paragraph as real text. Returns the new y."""
    c.setFont(font, size)
    for line in simpleSplit(_txt(text), font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y


def _break(c: Canvas, y: float, need: float = 46) -> float:
    """Start a new page when ``need`` points of room are gone.

    The context page outgrew one sheet the moment it started carrying a result line: without
    this the last paragraphs were still written, just below the bottom edge where nothing --
    a reader, an extractor, a person -- ever sees them. Silent truncation of the instructions
    is worse than a second page, because the sheet still looks complete."""
    if y >= PAD + need:
        return y
    c.showPage()
    return float(PH - PAD)


def context_facts(entry: Entry, meta: dict[str, Any], db: DbContext, n_frames: int,
                  step: int, first_ts: float, last_ts: float,
                  bjjh: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The header table: which fight, which video, which clock, what BJJ Heroes says."""
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
        ("Frames sampled", f"{n_frames}, one every {step}s"),
        ("Covers", f"{hhmmss(first_ts)} to {hhmmss(last_ts)} of the video"),
    ]
    if div:
        rows.append(("Division", f"{div} (ADCC 2026 scouting roster)"))
    if entry.kind:
        rows.append(("Footage type", entry.kind.replace("_", " ")))
    if entry.note:
        rows.append(("Source note", entry.note))
    if bjjh and bjjh.get("result"):
        rows.append(("Result (BJJ Heroes)",
                     f"{bjjh['result']}   (W/L read from {bjjh['perspective']}'s record)"))
        for extra in bjjh.get("other_meetings", []):
            rows.append(("Same pair, also", extra))
        if bjjh.get("year_note"):
            rows.append(("Year check", bjjh["year_note"]))
    if known:
        rows.append(("Already in the corpus",
                     "; ".join(f"{b['a']} vs {b['b']} ({b['event'] or 'no event'} {b['year']}), "
                               f"{b['events']} events" for b in known)))
    return rows


def context_prose(step: int, with_library: bool, first_ts: float, last_ts: float,
                  lib_file: str = "", ts_source: str = "") -> list[tuple[str, str]]:
    """What the reader is being asked for. ``("p", text)`` is a paragraph; ``("f", "name|desc")``
    is one row of the field table.

    Lives apart from the drawing because the PDF is no longer the only way this leaves the
    building -- ``--format frames`` writes the same words as Markdown. Two copies of a prompt
    is two prompts, and the one nobody edits is the one that stays wrong.
    """
    lib = ("The pages immediately after this one list every technique the library knows, "
           "grouped by kind and most-used first; take each `label` from them verbatim."
           if with_library else
           f"Take each `label` verbatim from {lib_file or LIBRARY_PATH.name}, which lists every "
           "technique the library knows, most-used first.")

    paras: list[tuple[str, str]] = [
        ("p", f"Every frame carries a VIDEO-ABSOLUTE time -- seconds from the start of the "
              f"video at the URL above, NOT seconds from the start of the bout -- "
              f"{ts_source or 'printed as text directly above it'}. Those times run from "
              f"{hhmmss(first_ts)} to {hhmmss(last_ts)}."),
        ("p", "Return every timestamp in that same video-absolute clock. If the bout starts "
              "partway into the video, say where it starts as a separate field -- do not "
              "rebase the event times to it. A bout-relative time reported as an absolute one "
              "places the whole fight in a different part of the broadcast, and the result "
              "looks plausible while being wrong."),
        ("p", "For each event you can see, give:"),
        ("f", "ts|integer seconds, video-absolute"),
        ("f", "label|a technique name, taken verbatim from the label list"),
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
        ("p", "REPORT ONLY WHAT YOU ARE CONFIDENT OF -- but report uncertainty by getting "
              "COARSER, not by staying silent and not by guessing. If the frames show that "
              "something happened and you cannot tell exactly which technique it was, use the "
              "generic label the library already carries -- `Guard Pass` rather than a named "
              "pass, `Takedown` rather than a named entry, `Submission` rather than a named "
              "finish, and likewise `Sweep`, `Escape`, `Guard`. A generic label is the honest "
              "answer at the resolution these frames give you; a specific one you inferred is "
              "a claim about a technique nobody saw, and once it is in the athlete's graph "
              "nothing downstream can tell it from an observed one."),
        ("p", f"This matters most across the sampling gap. At one frame every {step}s a "
              "technique can start and finish between two samples, leaving only its result "
              "visible -- a competitor who was on top now underneath, a grip that is now a "
              "finish. Recording that result generically is correct. Naming the entry you "
              "did not see is not, however obvious it feels. The same rule covers `actor`: "
              "if you cannot tell which competitor did it, say so in `note` rather than "
              "picking one, because a right technique on the wrong athlete is two errors."),
        ("p", "The result line at the top, when there is one, comes from the athlete's BJJ "
              "Heroes record. It is there so you can tell the two competitors apart and know "
              "the bout ended -- it is NOT evidence of anything you cannot see. Do not report "
              "a finish, a technique or a score because that line names it: an armbar listed "
              "as the method and an armbar visible in the frames are two different claims, "
              "and only the second belongs in your answer."),
        ("p", "Only report what a frame actually shows. The scoreboard settles POINTS and "
              "nothing else: a technique you infer from a score change but never see is "
              "worth less than an omission, because a wrong label propagates into the "
              "athlete's graph and their rating and nothing downstream can tell it from an "
              "observed one. An omission is recoverable; an invented event is not."),
    ]
    return paras


def draw_context_page(c: Canvas, entry: Entry, meta: dict[str, Any], db: DbContext,
                      n_frames: int, step: int, first_ts: float, last_ts: float,
                      with_library: bool, bjjh: dict[str, Any] | None = None) -> None:
    """The first page: which fight, which clock, and what the answer must look like.

    It is written as a prompt because it IS the prompt -- the PDF is the whole message the
    reader gets, so anything left implicit here comes back as a guess.
    """
    y = PH - PAD
    title = _txt(entry.label or str(meta.get("title") or entry.vid))
    c.setFont(FONT_B, 15)
    for line in simpleSplit(title, FONT_B, 15, PW - 2 * PAD):
        c.drawString(PAD, y, line)
        y -= 19
    y -= 4
    c.setLineWidth(1)
    c.line(PAD, y, PW - PAD, y)
    y -= 20

    rows = context_facts(entry, meta, db, n_frames, step, first_ts, last_ts, bjjh)

    for k, v in rows:
        y = _break(c, y)
        c.setFont(FONT_B, 9)
        c.drawString(PAD, y, k)
        y = _text_block(c, v, PAD + 130, y, PW - PAD - 130 - PAD, 9, 12) - 4

    # Drawn by hand rather than as another row: the URL has to be a real annotation for a
    # human clicking it AND plain text for a reader that only gets the extracted characters.
    for i, (name, url) in enumerate((bjjh or {}).get("athletes", {}).items()):
        y = _break(c, y)
        c.setFont(FONT_B, 9)
        c.drawString(PAD, y, "BJJ Heroes" if i == 0 else "")
        c.setFont(FONT, 9)
        c.setFillColorRGB(0.0, 0.0, 0.55)
        text = _txt(f"{name} - {url}")
        c.drawString(PAD + 130, y, text)
        c.linkURL(url, (PAD + 130, y - 2, PAD + 130 + c.stringWidth(text, FONT, 9), y + 9),
                  relative=0)
        c.setFillColorRGB(0, 0, 0)
        y -= 12
    if (bjjh or {}).get("athletes"):
        y -= 4

    y -= 12
    c.line(PAD, y, PW - PAD, y)
    y -= 24
    c.setFont(FONT_B, 14)
    c.drawString(PAD, y, "How to read this")
    y -= 22

    paras = context_prose(step, with_library, first_ts, last_ts)

    for kind, body in paras:
        y = _break(c, y)
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
            c.drawString(PAD + col * col_w, y, _txt(str(n["label"]))[:36])
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


def write_frames_dir(entry: Entry, meta: dict[str, Any], db: DbContext,
                     frames: list[tuple[float, Path]], step: int, out_dir: Path,
                     library: dict[str, Any] | None, bjjh: dict[str, Any] | None) -> Path:
    """The same sheet, unpacked: one JPEG per frame plus the context as Markdown.

    For a reader that opens files rather than being handed one. The PDF exists because a chat
    upload takes a single file and a page render is the only way frames reach a vision model
    through it; neither constraint applies to an agent with a filesystem, and there the grid
    only costs resolution -- six frames share one page's worth of pixels.

    Nothing here is a second prompt. The words come from ``context_facts``/``context_prose``,
    the same two functions the PDF draws, so the two formats cannot drift apart.
    """
    d = out_dir / entry.slug
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("t*.jpg"):        # a re-run must not leave last run's frames behind
        old.unlink()

    index = []
    for ts, src in frames:
        name = f"t{int(ts):05d}.jpg"
        shutil.copyfile(src, d / name)
        index.append({"file": name, "ts": int(ts)})
    (d / "frames.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in index), encoding="utf-8")

    lines = [f"# {entry.label or meta.get('title') or entry.vid}", ""]
    for k, v in context_facts(entry, meta, db, len(frames), step,
                              frames[0][0], frames[-1][0], bjjh):
        lines.append(f"- **{k}:** {v}")
    for name, url in (bjjh or {}).get("athletes", {}).items():
        lines.append(f"- **BJJ Heroes:** [{name}]({url})")
    lines += ["",
              f"`t*.jpg` — {len(frames)} frames, one every {step}s, named by their "
              "VIDEO-ABSOLUTE second (`t00125.jpg` is 125s into the video). `frames.jsonl` "
              "carries the same index as one JSON object per line, so the timestamp is a "
              "field to read rather than a filename to parse.", "",
              "## How to read this", ""]

    fields: list[tuple[str, str]] = []
    # never "the pages after this one" -- there are no pages here; the vocabulary is a file,
    # and it is only named when it was actually written
    for kind, bodytext in context_prose(
            step, False, frames[0][0], frames[-1][0],
            lib_file="labels.md" if library else "",
            ts_source="in its filename and in `frames.jsonl` (the JPEG itself is unmarked)"):
        if kind == "f":
            fields.append(tuple(bodytext.split("|", 1)))    # type: ignore[arg-type]
            continue
        if fields:                                          # flush the table before the prose
            lines += ["| field | what it means |", "|---|---|"]
            lines += [f"| `{n}` | {desc} |" for n, desc in fields] + [""]
            fields = []
        lines += [bodytext, ""]
    if fields:
        lines += ["| field | what it means |", "|---|---|"]
        lines += [f"| `{n}` | {desc} |" for n, desc in fields] + [""]
    (d / "README.md").write_text("\n".join(lines), encoding="utf-8")

    if library:
        out = ["# Allowed labels — use one of these, verbatim", "",
               f"{library['node_count']} techniques, grouped by kind, most-used first "
               f"inside each group. Snapshot {library['generated']}.", ""]
        groups: dict[str, list[dict[str, Any]]] = {}
        for n in library["nodes"]:
            groups.setdefault(str(n.get("node_type") or "other"), []).append(n)
        for g in sorted(groups, key=lambda g: -sum(int(x["corpus_events"]) for x in groups[g])):
            out += [f"## {g}", ""]
            out += [f"- {n['label']}" for n in
                    sorted(groups[g], key=lambda n: (-int(n["corpus_events"]), str(n["key"])))]
            out.append("")
        (d / "labels.md").write_text("\n".join(out), encoding="utf-8")
    return d


def build_pdf(entry: Entry, meta: dict[str, Any], db: DbContext,
              frames: list[tuple[float, Path]], step: int, grid: tuple[int, int],
              out_path: Path, library: dict[str, Any] | None = None,
              bjjh: dict[str, Any] | None = None) -> None:
    c = Canvas(str(out_path), pagesize=PAGE)
    c.setTitle(_txt(entry.label or str(meta.get("title") or entry.vid)))
    c.setSubject(f"https://www.youtube.com/watch?v={entry.vid} — frames every {step}s, "
                 "timestamps are video-absolute")
    draw_context_page(c, entry, meta, db, len(frames), step,
                      frames[0][0], frames[-1][0], with_library=library is not None, bjjh=bjjh)
    if library:
        draw_library_pages(c, library)
    draw_grid_pages(c, frames, grid)
    c.save()


# ── BJJ Heroes results ──────────────────────────────────────────────────────────
_VS = re.compile(r"^(?P<a>.+?)\s+vs\.?\s+(?P<b>[^,]+?)\s*(?:,\s*(?P<ev>.+))?$", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b")


def _bout_names(label: str) -> tuple[str, str] | None:
    """"A vs B, Event Year" -> ("A", "B"). None when the label is not a bout label."""
    m = _VS.match(label.strip())
    return (m.group("a").strip(), m.group("b").strip()) if m else None


def _same_person(a: str, b: str) -> bool:
    """Do these two spellings name the same competitor?

    BJJ Heroes' match table abbreviates the opponent -- "L. Bernales", "I. Goodman", "Aurelie
    Vern" for Aurelie Le Vern -- while the profile it links to spells the name out. Exact key
    equality therefore misses the athlete's OWN record and silently falls through to the
    opponent's page, which reports the same bout with the opposite letter. Correctly labelled
    and still the wrong answer to "how did she do".

    The rule: first token and last token must agree, where an initial agrees with the name it
    abbreviates. That accepts a dropped particle ("Le") and an abbreviated first name, and
    still refuses "Jon Hansen" vs "John Hansen" -- two spellings that are a REAL open question
    in this corpus and must not be answered by a contact sheet.
    """
    from analysis.names import athlete_key

    ta, tb = athlete_key(a).split(), athlete_key(b).split()
    if not ta or not tb:
        return False
    if ta == tb:
        return True

    def first_ok(x: str, y: str) -> bool:
        x, y = x.rstrip("."), y.rstrip(".")
        return x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))

    return ta[-1] == tb[-1] and first_ok(ta[0], tb[0])


def _result_line(r: Any) -> str:
    """The BJJ Heroes row as it reads on their page: W/L, method, competition, weight,
    stage, year. Kept in that order and verbatim -- it is their record, not our rendering
    of it, and a reworded method is a claim we did not verify."""
    return "  ".join(x for x in (r.wl, r.method, r.competition, r.weight, r.stage,
                                 str(r.year or "")) if x)


def resolve_bjjh(entries: list[Entry], *, refresh: bool = False) -> dict[str, Any]:
    """Per bout: the BJJ Heroes result row and both athletes' profile URLs.

    Cached in ``BJJH_PATH``; ``refresh`` re-scrapes. Network failure is not fatal -- the sheet
    loses a context row, which is worth less than the batch.

    **The result is never inverted.** When only the opponent's page carries the bout, the row
    is printed from THEIR record with the perspective named, rather than flipping W/L: a
    method reads one way from each side ("Pts: 2x0" is a win by two for whoever the page
    belongs to), and a silently mirrored score is a wrong fact that looks like a right one.
    """
    if BJJH_PATH.exists() and not refresh:
        return dict(json.loads(BJJH_PATH.read_text(encoding="utf-8")))

    from datetime import date

    from analysis.date_reconcile import fighter_pages, parse_match_history
    from analysis.names import athlete_key

    bouts = {e.slug: (_bout_names(e.label), _YEAR.search(e.label)) for e in entries if e.label}
    names = {n for names_, _ in bouts.values() if names_ for n in names_}
    try:
        pages = fighter_pages({athlete_key(n) for n in names})
    except Exception as exc:                       # offline, 403, DNS -- degrade, never fail
        logger.warning("BJJ Heroes lookup skipped: %s", exc)
        return {"generated": date.today().isoformat(), "bouts": {}, "error": str(exc)}

    parsed = {k: (url, parse_match_history(html)) for k, (url, html) in pages.items()}
    profiles = {n: parsed[athlete_key(n)][0] for n in names if athlete_key(n) in parsed}
    logger.info("BJJ Heroes: %d of %d athletes have a profile", len(profiles), len(names))

    out: dict[str, Any] = {}
    for slug, (pair, ym) in bouts.items():
        if not pair:
            continue
        a, b = pair
        year = int(ym.group(1)) if ym else None
        rec: dict[str, Any] = {"athletes": {n: profiles[n] for n in (a, b) if n in profiles}}
        for side, other in ((a, b), (b, a)):
            if athlete_key(side) not in parsed:
                continue
            rows = [r for r in parsed[athlete_key(side)][1] if _same_person(r.opponent, other)]
            if not rows:
                continue
            dated = [r for r in rows if year and r.year == year]
            hit = dated or rows
            rec["result"] = _result_line(hit[0])
            rec["perspective"] = side
            # A pair that met more than once is not a lookup failure, it is two facts. Naming
            # the others is what stops the reader treating the first as the only one.
            if len(hit) > 1:
                rec["other_meetings"] = [_result_line(r) for r in hit[1:]]
            if year and not dated:
                rec["year_note"] = (
                    f"the label says {year}; BJJ Heroes lists this pair in "
                    + ", ".join(str(r.year) for r in rows if r.year))
            break
        out[slug] = rec

    doc = {"generated": date.today().isoformat(), "source": "bjjheroes.com fighter match tables",
           "bouts": out}
    BJJH_PATH.parent.mkdir(parents=True, exist_ok=True)
    BJJH_PATH.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("wrote %s (%d bouts, %d with a result)", BJJH_PATH, len(out),
                sum(1 for r in out.values() if r.get("result")))
    return doc


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
            library: dict[str, Any] | None = None,
            bjjh: dict[str, Any] | None = None, fmt: str = "pdf") -> str:
    known = db.bouts_for(entry.vid)
    if known and not force:
        with_seq = [b for b in known if b["events"]]
        if with_seq:
            return (f"skip {entry.vid}: already in the corpus with a reviewed sequence "
                    f"({with_seq[0]['a']} vs {with_seq[0]['b']}, {with_seq[0]['events']} events)")

    out_path = out_dir / (entry.slug if fmt == "frames" else f"{entry.slug}.pdf")
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
        if fmt == "frames":
            d = write_frames_dir(entry, meta, db, frames, step, out_dir, library, bjjh)
            mb = sum(f.stat().st_size for f in d.iterdir()) / 1e6
            return f"ok   {d.name}/  {len(frames)} frames, {mb:.1f} MB"
        build_pdf(entry, meta, db, frames, step, grid, out_path, library, bjjh)
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
    ap.add_argument("--format", choices=("pdf", "frames"), default="pdf",
                    help="pdf: one contact sheet per bout, for an upload that takes one file. "
                         "frames: a directory per bout -- one JPEG per frame at full size, "
                         "plus README.md and labels.md -- for an agent that reads a filesystem")
    ap.add_argument("--limit", type=int, help="stop after N videos (a smoke run)")
    ap.add_argument("--no-bjjh", action="store_true",
                    help="do not print the BJJ Heroes result line or profile links")
    ap.add_argument("--refresh-bjjh", action="store_true",
                    help="re-scrape BJJ Heroes instead of reading the cached resolution")
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
    logger.info("sheet font: %s", _register_fonts())
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

    # Resolved over the WHOLE manifest before --limit trims it, so a smoke run and a full
    # run write the same cache instead of the smoke run truncating it.
    bjjh = {} if a.no_bjjh else resolve_bjjh(entries, refresh=a.refresh_bjjh).get("bouts", {})

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
                                   a.force, a.max_frames, library, bjjh.get(e.slug),
                                   a.format))
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
