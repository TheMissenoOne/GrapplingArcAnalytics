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

**Already-processed fights are skipped, per BOUT.** A multi-bout upload backs several DB rows
under one video id, so the skip is decided against the entry's OWN bout -- matched by
``video_start_seconds`` (and the athlete pair, when the label parses as one) via
``_own_bout()`` -- not "does ANY bout on this video have a sequence". That distinction is load-
bearing: it used to be per-video, and a multi-bout event with even one sequenced sibling would
silently skip every OTHER bout on the same upload regardless of that bout's own state (measured
2026-08-26 on the refinement queue: 104 of 108 entries self-skipped this way). Only an entry
with no ``start`` at all -- a whole-video render with nothing to disambiguate against -- still
falls back to the old "does any bout on this video have a sequence" rule. ``--force`` overrides
entirely, per video id; for a manifest whose every entry legitimately wants a re-render (e.g.
enrichment of an already-thin sequence — see ``docs/frame_pdf_reading.md``), running that
manifest with ``--force`` is scoped to exactly its own entries and touches nothing else.

Privacy class: **A, public competition data.** Published broadcasts of published bouts, the
same class as everything else in ``matches``. No user-fed data is read, written or implied.

Needs ``yt-dlp``, ``ffmpeg`` and ``ffprobe`` on PATH, and reportlab, which writes real text
objects and passes each JPEG through untouched — so the sheet is searchable and costs the
bytes of its frames and nothing more.
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
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
# Landscape default. NOT a 90-degree relabelling of the portrait grid (3x2 measures out
# smaller AND further from 16:9 -- 34,030pt^2 @ 1.04 vs portrait's 35,054pt^2 @ 1.08, since
# reducing rows barely changes the row-driven cell height when the column count still governs
# it). 2x2 is what's actually Pareto-better: fewer, wider columns give each cell's
# width-constrained image ~2.2x the area (~78,200pt^2) at a box aspect of 1.58, much nearer
# the 16:9 (1.78) a video frame actually is. Measured against A4 @ PAD=40, image inset 8/22.
DEFAULT_GRID_LANDSCAPE = (2, 2)   # cols x rows -> 4 frames per page, larger + closer to 16:9

# Ceiling on frames per PDF. A full-event stream with no interval is hours long, and 5s over
# six hours is 4,300 frames -- a file nothing will read and a download that fills the disk.
# When an entry would exceed this the step is widened to fit and the context page reports the
# step it actually used, so a coarse locator pass is never mistaken for a dense one. The
# answer to a video that needs both is two entries: this one to find the bouts, then one
# per bout with a `start`/`end` and the normal step.
DEFAULT_MAX_FRAMES = 400
# The strip is 12 KB a frame and exists to be scrubbed, so it earns a much higher ceiling than
# the sheet -- a 20-minute bout at one frame per second is still only ~14 MB of thumbnails.
DEFAULT_STRIP_FRAMES = 2000

# Where --dump-library writes by default, and where the context page tells the reader the
# vocabulary lives. Versioned next to the manifests: the manifest says which fights, this
# says which words, and a sheet rendered against one vocabulary is read against the same one.
LIBRARY_PATH = REPO / "data" / "frame_pdf" / "node_library.json"
# Resolved BJJ Heroes results, tracked. The scraped HTML lives under ``data/raw/`` and is
# gitignored, so without this a rebuild anywhere else would have to hit bjjheroes.com again
# just to reprint a line it already knew. It also records which bouts resolved and which did
# not, which is the half worth keeping: "no result found" is a fact about coverage.
BJJH_PATH = REPO / "data" / "frame_pdf" / "bjjh_results.json"
# Frame width and download ceiling, per footage kind. A single bout is the thing anyone
# actually reads events off, and 640x360 was measured to be too little: the competitors
# occupy roughly 3% of the frame (~110x70 px), and a reader could not tell top from bottom.
# A full-event reel is a locator -- you are finding WHERE a bout is, not reading it -- so it
# keeps the cheap ceiling, which is also what keeps a 6-hour broadcast off a 94%-full disk.
# The two numbers move together: raising the download ceiling while ffmpeg still rescales to
# 640 throws the pixels away a second time.
FRAME_WIDTH = 640          # px; the default, and what a reel gets
# The strip is an INDEX -- 31 of these are on screen at once and none is ever read for
# technique, only for "roughly where am I". 320px costs ~12 KB against ~85 KB, which is the
# difference between keeping the clip and not.
THUMB_WIDTH = 320
QUALITY: dict[str, tuple[int, str]] = {
    "full_match": (1280, "bv*[height<=720]/b[height<=720]/bv*[height<=1080]/b"),
    "full_event": (640, "bv*[height<=480]/b[height<=480]/bv*[height<=720]/b"),
}
_DEFAULT_QUALITY = QUALITY["full_event"]
JPEG_QUALITY = 72

PAGE_W, PAGE_H = 1654, 2339   # A4 at 200 dpi, portrait
MARGIN = 48
LABEL_H = 34

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
# FloGrappling page urls carry a numeric id right after /video/ (the slug after it is
# cosmetic and unstable). Without this the fallback returned the whole url, and a slug
# with "https:/..." in it is a path with directories nobody created.
_FLO_ID = re.compile(r"flograppling\.com/video/(\d+)")

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
    m = _YT_ID.search(url) or _FLO_ID.search(url)
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
    step: float | None = None    # per-entry override of --step
    kind: str = ""               # sourcing type: full_match / full_event / highlights
    skip: str = ""               # non-empty = do not render, and this says why
    transcript: str = ""         # path to a transcript .md (see parse_transcript); empty = none

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


# ── Transcript alignment ─────────────────────────────────────────────────────────
# A YouTube auto-caption export (the "show transcript" panel, copy-pasted). Its clock is the
# same video-absolute axis as everything else here -- ffmpeg's, --download-sections', the
# frame stamps' -- so a caption is placed next to a frame by second, no bout-relative
# conversion involved.
def _parse_hms(ts: str) -> int | None:
    """``H:MM:SS`` or ``M:SS`` -> seconds, or None. Same shape as ``hhmmss`` in reverse."""
    parts = ts.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        m, s = nums
        return m * 60 + s
    if len(nums) == 3:
        h, m, s = nums
        return h * 3600 + m * 60 + s
    return None


def parse_transcript(path: Path) -> list[tuple[int, str]]:
    """A YouTube transcript export -> ``[(second, caption_text)]``, in file order.

    The export repeats a triple per caption: a timestamp line (``H:MM:SS`` or ``M:SS``), a
    Portuguese long-form duration line ("3 horas, 45 minutos e 6 segundos") that says nothing
    the timestamp didn't, and the caption text. The duration line is discarded here rather
    than parsed -- it is redundant with the timestamp by construction, and it is the line most
    likely to change shape with a different UI locale.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        body = lines[lines.index("Transcrição") + 1:]
    except ValueError:
        body = lines
    if body and body[0].strip() == "Pesquisar transcrição":
        body = body[1:]
    while body and not body[-1].strip():
        body.pop()
    out: list[tuple[int, str]] = []
    n = len(body) - len(body) % 3
    for i in range(0, n, 3):
        ts = _parse_hms(body[i])
        if ts is None:
            continue                      # a triple whose first line isn't a timestamp
        out.append((ts, body[i + 2].strip()))
    return out


_BOUT_LINE = re.compile(
    r'(?P<label>.+?)\s*\((?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*'
    r'(?P<end>\d{1,2}:\d{2}(?::\d{2})?)\)')


def parse_bout_index(path: Path) -> list[tuple[str, int, int]]:
    """The header's partial bout list -> ``[(label, start_seconds, end_seconds)]``.

    Only rows shaped ``Name vs Name (H:MM:SS - H:MM:SS)`` become entries. A row with a single
    bare timestamp and no range (a highlight moment, not a bout span) has nothing to match
    the regex's two-timestamp group and is silently skipped -- correct, not a defect: it
    is not a usable window. Trailing junk after the closing paren (quote marks, "(Pode haver
    mais)") is never captured because the regex stops matching at the paren.
    """
    out: list[tuple[str, int, int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "Transcrição":
            break
        m = _BOUT_LINE.search(line)
        if not m:
            continue
        s, e = _parse_hms(m.group("start")), _parse_hms(m.group("end"))
        if s is None or e is None:
            continue
        out.append((m.group("label").strip(), s, e))
    return out


def transcript_window(times: list[int], texts: list[str], t: float, step: float) -> str:
    """Caption text for the frame at video-absolute second ``t``: every line whose timestamp
    falls in ``[t, t + step)``, joined in order. Empty when nothing was said in the window --
    that is a real answer (the reader renders a placeholder, not a shifted caption from a
    neighbouring frame). ``times`` must be sorted ascending, as ``parse_transcript`` returns.
    """
    lo = bisect.bisect_left(times, t)
    hi = bisect.bisect_left(times, t + step)
    return " ".join(x for x in texts[lo:hi] if x)


def build_windows(bouts: list[tuple[str, int, int]], total_seconds: float,
                  window: float = 600.0) -> list[tuple[float, float]]:
    """Fixed-size windows covering everything ``bouts`` does NOT already span, from 0 to
    ``total_seconds``. This is how a partial bout index still yields full coverage: the named
    ranges become fine-grained entries elsewhere, and this fills the gaps between and around
    them so no part of the video is dropped on the floor."""
    cur = 0.0
    gaps: list[tuple[float, float]] = []
    for _, s, e in sorted(bouts, key=lambda b: b[1]):
        if s > cur:
            gaps.append((cur, float(s)))
        cur = max(cur, float(e))
    if cur < total_seconds:
        gaps.append((cur, total_seconds))

    out: list[tuple[float, float]] = []
    for gs, ge in gaps:
        t = gs
        while t < ge:
            out.append((t, min(t + window, ge)))
            t += window
    return out


@lru_cache(maxsize=4)
def _load_transcript(path: str) -> tuple[tuple[int, str], ...]:
    """Cached: the same transcript backs dozens of manifest entries in one run, and re-reading
    a several-thousand-line file per entry is wasted work a dict lookup avoids."""
    return tuple(parse_transcript(Path(path)))


class DiskLowError(RuntimeError):
    """Raised instead of letting a download run a filesystem to zero."""


def _check_disk(*paths: Path, min_free_bytes: int) -> None:
    seen: set[int] = set()
    for p in paths:
        p = p if p.exists() else p.parent
        free = shutil.disk_usage(p).free
        st_dev = p.stat().st_dev
        if st_dev in seen:
            continue
        seen.add(st_dev)
        if free < min_free_bytes:
            raise DiskLowError(f"{free / 1e9:.2f} GB free on {p} -- below the "
                          f"{min_free_bytes / 1e9:.2f} GB floor; aborting rather than filling it")


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


def fetch(url: str, dest: Path, start: float | None, end: float | None,
          fmt: str = "") -> Path:
    """Download at the ceiling this footage kind earns, and only the requested section.

    The ceiling is deliberate: the reader has to tell mount from side control, not read
    a patch on a sleeve, and the disk on this machine is the binding constraint (a full
    ADCC stream at 1080p is tens of GB). ``--download-sections`` means a 4-minute bout
    inside a 6-hour broadcast costs 4 minutes of download, not 6 hours.

    Video-only, and the fallbacks matter. Asking for a separate audio stream to merge is
    both wasted bytes and a way to fail outright: several of these uploads only offer the
    combined 360p format 18, against which ``bv*+ba`` has no match at all. Nothing
    downstream of here ever opens an audio track.
    """
    fmt = fmt or _DEFAULT_QUALITY[1]
    cmd = ["yt-dlp", "--no-warnings", *_cookie_args(), "-f", fmt,
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


def extract(video: Path, out: Path, step: float, offset: float,
            width: int = FRAME_WIDTH, thumb: int | None = None) -> list[tuple[float, Path]]:
    """Sample every ``step`` seconds. ``offset`` is added back to each frame's time so the
    stamp stays video-absolute even when only a section was downloaded -- the section starts
    at 0 in the local file, and forgetting that is precisely how a bout-relative clock gets
    manufactured."""
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps=1/{step},scale={thumb or width}:-2", "-q:v", "4",
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


def page_size(orientation: str) -> tuple[float, float]:
    """(width, height) in points for ``--orientation``. Landscape is A4 rotated, not a
    different sheet -- same points, swapped axes."""
    return (A4[1], A4[0]) if orientation == "landscape" else A4


def set_page_size(orientation: str) -> None:
    """Point PAGE/PW/PH at ``orientation``'s dims, called once before any page is drawn.

    draw_context_page/draw_library_pages/draw_grid_pages/build_pdf all read PW/PH/PAGE as
    module globals rather than a threaded parameter (the same pattern _register_fonts uses for
    FONT/FONT_B) -- so mutating them here is what makes every page respect --orientation
    without duplicating three drawing functions for a rotated sheet.
    """
    global PAGE, PW, PH
    PAGE = page_size(orientation)
    PW, PH = PAGE


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


def _text_block_capped(c: Canvas, text: str, x: float, y: float, width: float,
                       size: float, leading: float, max_h: float, font: str = FONT) -> None:
    """Wrapped text, hard-capped to ``max_h`` -- for a caption block glued to a fixed-size grid
    cell, where ``_text_block``'s "keep drawing until the string ends" would run the block
    into the frame below it. Truncates with an ellipsis rather than silently dropping the
    tail, so a long caption reads as cut off, not as a caption that was fully shown."""
    c.setFont(font, size)
    lines = simpleSplit(_txt(text), font, size, width)
    max_lines = max(1, int(max_h // leading))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and c.stringWidth(last + "...", font, size) > width:
            last = last[:-1]
        lines[-1] = last + "..."
    for line in lines:
        c.drawString(x, y, line)
        y -= leading


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
                  step: float, first_ts: float, last_ts: float,
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
        ("Frames sampled", f"{n_frames}, one every {step:g}s"),
        ("Covers", f"{hhmmss(first_ts)} to {hhmmss(last_ts)} of the video"),
    ]
    if div:
        # NOT "Division". The roster gives the athlete's ADCC 2026 CATEGORY; this bout may
        # be in any bracket, and the first reader hit exactly that -- a sheet tagged "+65 kg"
        # whose every frame reads OPEN CLASS. A wrong premise on the context page is the one
        # kind of error the reader cannot check, so the row says what it actually knows.
        rows.append(("Athlete's ADCC 2026 category",
                     f"{div} — the athlete's bracket for ADCC 2026, NOT necessarily this "
                     "bout's division; read the broadcast banner for that"))
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


def context_prose(step: float, with_library: bool, first_ts: float, last_ts: float,
                  lib_file: str = "", ts_source: str = "",
                  with_transcript: bool = False) -> list[tuple[str, str]]:
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
    ]
    if with_transcript:
        paras.append(
            ("p", "THE CAPTION TEXT BESIDE EACH FRAME IS A GUIDE, NOT GROUND TRUTH. It is an "
                  "auto-caption of live commentary: the commentator misnames athletes, talks "
                  "about one bout while another is on screen, anticipates and speculates, and "
                  "the text lags the picture. When the caption and the frame disagree, THE "
                  "FRAME WINS. Use the caption to orient where to look and to break a tie on a "
                  "label -- never to record an event you did not see in the frame itself."))
    paras += [
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
        ("p", "NOT EVERY FRAME IS LIVE ACTION. A broadcast cuts to slow-motion replays, "
              "usually right after a finish or a scoring exchange, and a replay looks exactly "
              "like the thing it replays. Read one as new action and the same event is "
              "recorded twice, seconds apart, which downstream reads as a repeated technique "
              "rather than one shown twice. Tells: a graphics bug or logo appears, the clock "
              "stops or disappears, the camera angle changes, the action repeats something "
              "you already saw. Skip those frames rather than logging them, and say in the "
              "note which range you treated as replay."),
        ("p", "AND NOT EVERY FRAME IS THIS BOUT. A broadcast cuts away -- to another mat, to "
              "a different match's scorecard, to a crowd shot or an ad -- and it may show two "
              "bouts in sequence with a gap between rounds. A reader found exactly that in "
              "this batch: a scorecard from an unrelated match sitting in the middle of the "
              "sampled frames. Events read off a cutaway land on the wrong athletes entirely, "
              "which is worse than missing them. Before logging any event, confirm the frame "
              "shows the two competitors you identified, and say in the note which ranges you "
              "excluded as not-this-bout."),
        ("p", "THE SCOREBOARD'S NAME ORDER IS NOT A MAT POSITION. A lower-third graphic puts "
              "one name left and one right by broadcast convention; which competitor stands "
              "where on the mat is unrelated and changes shot to shot. Binding a name to a "
              "body through that order is the single highest-cost error available here, "
              "because `actor` is on every event -- get it backwards and the whole answer "
              "inverts at once, while still looking internally consistent. Bind identity to "
              "something carried ON the athlete instead: a name printed on the back of the "
              "rashguard, kit colour, sleeve length, an ankle band, hair. Then check that "
              "discriminator against a frame where the scoreboard and the bodies are visible "
              "together, and say in `identity_verified_by` which frame did it."),
        ("p", "REPORT ONLY WHAT YOU ARE CONFIDENT OF -- but report uncertainty by getting "
              "COARSER, not by staying silent and not by guessing. If the frames show that "
              "something happened and you cannot tell exactly which technique it was, use the "
              "generic label the library already carries -- `Guard Pass` rather than a named "
              "pass, `Takedown` rather than a named entry, `Submission` rather than a named "
              "finish, and likewise `Sweep`, `Escape`, `Guard`. A generic label is the honest "
              "answer at the resolution these frames give you; a specific one you inferred is "
              "a claim about a technique nobody saw, and once it is in the athlete's graph "
              "nothing downstream can tell it from an observed one."),
        ("p", f"This matters most across the sampling gap. At one frame every {step:g}s a "
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
                      n_frames: int, step: float, first_ts: float, last_ts: float,
                      with_library: bool, bjjh: dict[str, Any] | None = None,
                      with_transcript: bool = False) -> None:
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

    paras = context_prose(step, with_library, first_ts, last_ts, with_transcript=with_transcript)

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


def draw_grid_pages(c: Canvas, frames: list[tuple[float, Path]], grid: tuple[int, int],
                    captions: dict[float, str] | None = None) -> None:
    """The frames, each with its video-absolute stamp as real text above it, and -- when
    ``captions`` was supplied -- the transcript text for that frame's window as a wrapped,
    height-capped block beneath the image. A frame with nothing said in its window still gets
    the block, carrying a placeholder rather than being left out: the layout must not shift
    depending on whether a caption exists.

    ``drawImage`` on a JPEG path embeds the original DCTDecode stream, so a frame costs what
    ffmpeg already spent on it and nothing more.
    """
    cols, rows = grid
    per = cols * rows
    cell_w = (PW - 2 * PAD) / cols
    cell_h = (PH - 2 * PAD) / rows
    # A caption block takes a fixed slice of the cell's bottom, shrinking the image rather
    # than growing the page -- so a sheet with a transcript still has a predictable size.
    cap_h = cell_h * 0.26 if captions is not None else 0.0
    for i in range(0, len(frames), per):
        for j, (ts, path) in enumerate(frames[i:i + per]):
            x = PAD + (j % cols) * cell_w
            # reportlab's origin is bottom-left; rows read top-down.
            y_top = PH - PAD - (j // cols) * cell_h
            c.setFont(FONT_B, 8)
            c.drawString(x, y_top - 9, f"{hhmmss(ts)}   ({int(ts)}s)")
            c.drawImage(ImageReader(str(path)), x, y_top - cell_h + 6 + cap_h,
                        width=cell_w - 8, height=cell_h - 22 - cap_h,
                        preserveAspectRatio=True, anchor="n")
            if captions is not None:
                text = captions.get(ts) or "(no narration in this window)"
                _text_block_capped(c, text, x, y_top - cell_h + cap_h - 4,
                                   cell_w - 8, 6.5, 7.6, cap_h - 4)
        c.showPage()


def write_frames_dir(entry: Entry, meta: dict[str, Any], db: DbContext,
                     frames: list[tuple[float, Path]], step: float, out_dir: Path,
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
    for stale in (*d.glob("t*.jpg"), *(d / "strip").glob("*.jpg")):
        stale.unlink()                  # a re-run must not leave last run's frames behind
    (d / "strip").mkdir(exist_ok=True)

    index = []
    for ts, src in frames:
        name = f"t{int(ts):05d}.jpg"
        shutil.copyfile(src, d / "strip" / name)
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
              f"`strip/t*.jpg` — {len(frames)} thumbnails, one every {step:g}s, named by their "
              "VIDEO-ABSOLUTE second (`t00125.jpg` is 125s into the video). `frames.jsonl` "
              "carries the same index as one JSON object per line, so the timestamp is a "
              "field to read rather than a filename to parse.", "",
              "`clip.mp4` is the bout's own section of the broadcast, kept on purpose. The "
              "thumbnails are an index at a fixed interval; the clip holds EVERY frame, and "
              "at 720p it costs less than one JPEG per second of the same footage. Any "
              "instant can be materialised from it with a seek, which is what makes the "
              "sampling interval a choice at read time instead of a decision baked in when "
              "the folder was built.", "",
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
              frames: list[tuple[float, Path]], step: float, grid: tuple[int, int],
              out_path: Path, library: dict[str, Any] | None = None,
              bjjh: dict[str, Any] | None = None,
              transcript: tuple[tuple[int, str], ...] | None = None) -> None:
    c = Canvas(str(out_path), pagesize=PAGE)
    c.setTitle(_txt(entry.label or str(meta.get("title") or entry.vid)))
    c.setSubject(f"https://www.youtube.com/watch?v={entry.vid} — frames every {step}s, "
                 "timestamps are video-absolute")
    captions = None
    if transcript is not None:
        times = [t for t, _ in transcript]
        texts = [x for _, x in transcript]
        captions = {ts: transcript_window(times, texts, ts, step) for ts, _ in frames}
    draw_context_page(c, entry, meta, db, len(frames), step,
                      frames[0][0], frames[-1][0], with_library=library is not None, bjjh=bjjh,
                      with_transcript=captions is not None)
    if library:
        draw_library_pages(c, library)
    draw_grid_pages(c, frames, grid, captions=captions)
    c.save()


# ── BJJ Heroes results ──────────────────────────────────────────────────────────
_VS = re.compile(r"^(?P<a>.+?)\s+vs\.?\s+(?P<b>[^,]+?)\s*(?:,\s*(?P<ev>.+))?$", re.I)
_YEAR = re.compile(r"\b(20\d{2})\b")


def _bout_names(label: str) -> tuple[str, str] | None:
    """"A vs B, Event Year" -> ("A", "B"). None when the label is not a bout label."""
    m = _VS.match(label.strip())
    return (m.group("a").strip(), m.group("b").strip()) if m else None


def _own_bout(entry: Entry, known: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Which row of ``known`` (DB bouts already backed by this video id) is THIS entry's own
    bout, not a sibling that happens to share the video. Matched on ``video_start_seconds`` --
    the one field an ``Entry`` and a DB row share unambiguously -- with the athlete pair as a
    sanity check when the label parses as one. ``None`` when the entry carries no start (a
    whole-video render with nothing to disambiguate against) or nothing in ``known`` lines up;
    the caller falls back to the old whole-video rule in that case.
    """
    if entry.start is None:
        return None
    wanted = _bout_names(entry.label) if entry.label else None
    wanted_folded = {_fold(n) for n in wanted} if wanted else None
    for b in known:
        if b["start"] is None or abs(float(b["start"]) - entry.start) > 1.0:
            continue
        if wanted_folded is not None and {_fold(b["a"]), _fold(b["b"])} != wanted_folded:
            continue
        return b
    return None


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


def _capped(step: float, span: float | None, cap: int, vid: str, what: str) -> float:
    """Widen the step rather than blow past a frame cap, and say so."""
    if not span or span / step <= cap:
        return step
    widened = -(-span // cap)
    logger.info("%s: %s of video at %gs would be %d %s frames -- widening to %gs",
                vid, hhmmss(span), step, int(span / step), what, widened)
    return float(widened)


def process(entry: Entry, db: DbContext, out_dir: Path, step: float,
            grid: tuple[int, int], workdir: Path, force: bool,
            max_frames: int = DEFAULT_MAX_FRAMES,
            library: dict[str, Any] | None = None,
            bjjh: dict[str, Any] | None = None, fmt: str = "pdf",
            pdf_step: float = DEFAULT_STEP_SECONDS,
            strip_cap: int = DEFAULT_STRIP_FRAMES,
            min_free_bytes: int = 0) -> str:
    """One download, up to two artefacts.

    **They do not share a sampling interval, and that is not an oversight.** A sheet is a
    single file handed to a reader that gets nothing else, so its density is bounded by what
    stays uploadable and legible -- a 15-minute bout at one frame per second is 900 frames and
    150 pages. The folder is scrubbed by a person against a clip that can materialise any
    instant, so its strip wants to be fine. `--step` sets the strip; `--pdf-step` sets the
    sheet; each is capped independently.
    """
    wants_pdf = fmt in ("pdf", "both")
    wants_dir = fmt in ("frames", "both")
    pdf_path, dir_path = out_dir / f"{entry.slug}.pdf", out_dir / entry.slug

    known = db.bouts_for(entry.vid)
    if known and not force:
        own = _own_bout(entry, known)
        # A multi-bout upload backs several DB rows for one video id -- own is which ONE of
        # them this entry is. `own is None` with entry.start set means no DB row lines up
        # (a bout not yet in the corpus): render it, siblings' sequences are irrelevant. Only
        # when the entry carries no start at all (a whole-video render, nothing to
        # disambiguate against) do we fall back to the old "any sibling has a sequence" rule.
        if own is not None:
            with_seq = [own] if own["events"] else []
        elif entry.start is None:
            with_seq = [b for b in known if b["events"]]
        else:
            with_seq = []
        if with_seq:
            return (f"skip {entry.vid}: already in the corpus with a reviewed sequence "
                    f"({with_seq[0]['a']} vs {with_seq[0]['b']}, {with_seq[0]['events']} events)")

    missing = [n for n, want, path in (("sheet", wants_pdf, pdf_path),
                                       ("folder", wants_dir, dir_path))
               if want and not path.exists()]
    if not force and not missing:
        return f"skip {entry.vid}: both artefacts already built"

    if min_free_bytes:
        _check_disk(out_dir, workdir, min_free_bytes=min_free_bytes)

    meta = probe(entry.url)
    span = _span_seconds(entry, meta)
    strip_step = _capped(entry.step or step, span, strip_cap, entry.vid, "strip")
    sheet_step = _capped(entry.step or pdf_step, span, max_frames, entry.vid, "sheet")
    transcript = _load_transcript(entry.transcript) if entry.transcript else None

    made: list[str] = []
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        tmpd = Path(tmp)
        # NOT `fmt` -- that name already means the output format, and shadowing it here sent
        # every --format frames run down the PDF branch instead.
        width, dl_fmt = QUALITY.get(entry.kind, _DEFAULT_QUALITY)
        # The clip we already keep IS a valid source for any frame -- that is the whole reason
        # it is kept. Re-downloading a bout whose clip is on disk costs bandwidth and, on a
        # tight disk, room for two copies of the same video at once.
        held = dir_path / "clip.mp4"
        reused = held.exists() and not (wants_dir and force)
        video = held if reused else fetch(entry.url, tmpd, entry.start, entry.end, dl_fmt)
        if reused:
            logger.info("%s: extracting from the clip already on disk", entry.vid)

        # The sheet is built FIRST, while the clip is still in the temp dir: the folder branch
        # moves that file out, and extracting from a path that has just been moved is the kind
        # of ordering bug that only shows up when both formats are asked for at once.
        if wants_pdf and (force or not pdf_path.exists()):
            sheet = extract(video, tmpd / "sheet", sheet_step, entry.start or 0.0, width)
            if not sheet:
                return f"FAIL {entry.vid}: ffmpeg produced no frames for the sheet"
            build_pdf(entry, meta, db, sheet, sheet_step, grid, pdf_path, library, bjjh,
                     transcript)
            made.append(f"{pdf_path.name} ({len(sheet)} fr @{sheet_step:g}s, "
                        f"{pdf_path.stat().st_size / 1e6:.1f} MB)")

        if wants_dir and (force or not dir_path.exists()):
            strip = extract(video, tmpd / "strip", strip_step, entry.start or 0.0, width,
                            thumb=THUMB_WIDTH)
            if not strip:
                return f"FAIL {entry.vid}: ffmpeg produced no frames for the strip"
            d = write_frames_dir(entry, meta, db, strip, strip_step, out_dir, library, bjjh)
            # The clip IS the artefact, not the frames. Moved rather than copied: it is already
            # the only heavy thing in the temp dir and it is about to be deleted.
            if not reused:
                shutil.move(str(video), str(d / "clip.mp4"))
            mb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6
            made.append(f"{d.name}/ ({len(strip)} thumbs @{strip_step:g}s, {mb:.1f} MB)")

    return f"ok   {entry.slug}  " + " + ".join(made) if made else f"skip {entry.vid}: nothing to do"


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
    ap.add_argument("--orientation", choices=("portrait", "landscape"), default="portrait",
                    help="portrait (default): A4. landscape: A4 rotated, with a wider default "
                         "grid so each cell holds a 16:9 video frame at higher resolution")
    ap.add_argument("--grid", default=None,
                    help="cols x rows per page (default 2x3 portrait, 2x2 landscape)")
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
    ap.add_argument("--format", choices=("pdf", "frames", "both"), default="both",
                    help="pdf: one contact sheet per bout, for an upload that takes one file. "
                         "frames: a directory per bout -- thumbnail strip, clip.mp4, README "
                         "and labels -- for a person at the registrar. both (default): one "
                         "download, both artefacts, each at its own sampling step")
    ap.add_argument("--pdf-step", type=float, default=DEFAULT_STEP_SECONDS,
                    help="seconds between frames ON THE SHEET (default %(default)gs). Separate "
                         "from --step because a sheet dense enough to scrub is too long to read")
    ap.add_argument("--limit", type=int, help="stop after N videos (a smoke run)")
    ap.add_argument("--no-bjjh", action="store_true",
                    help="do not print the BJJ Heroes result line or profile links")
    ap.add_argument("--refresh-bjjh", action="store_true",
                    help="re-scrape BJJ Heroes instead of reading the cached resolution")
    ap.add_argument("--dry-run", action="store_true", help="report the plan, download nothing")
    ap.add_argument("--min-free-gb", type=float, default=0.0,
                    help="abort the whole run (not just one entry) when free space on --out "
                         "or --workdir's filesystem drops below this many GB. 0 (default) "
                         "checks nothing -- opt in for a long batch on a tight disk")
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
    set_page_size(a.orientation)
    logger.info("sheet font: %s", _register_fonts())
    if a.grid:
        cols, rows = (int(x) for x in a.grid.lower().split("x"))
    else:
        cols, rows = DEFAULT_GRID_LANDSCAPE if a.orientation == "landscape" else DEFAULT_GRID
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
            own = _own_bout(e, known)
            if own is not None:
                state = "already reviewed" if own["events"] else "linked, no sequence"
            elif e.start is not None:
                state = "new"  # own bout not yet in the corpus -- siblings don't count
            else:
                state = ("already reviewed" if any(b["events"] for b in known)
                         else "linked, no sequence" if known else "new")
            print(f"  {e.vid}  {state:20s}  {e.label or '(no label)'}")
        return 0

    min_free_bytes = int(a.min_free_gb * 1e9)
    results = []
    for e in entries:
        try:
            results.append(process(e, db, a.out, a.step, (cols, rows), a.workdir,
                                   a.force, a.max_frames, library, bjjh.get(e.slug),
                                   a.format, a.pdf_step, DEFAULT_STRIP_FRAMES, min_free_bytes))
        except DiskLowError as exc:
            # Loud and whole-run: every remaining entry would hit the same wall, and letting
            # them try one by one just fails 30 times slower and messier than stopping once.
            logger.error("aborting after %d entries: %s", len(results), exc)
            results.append(f"FAIL {e.vid}: {exc}")
            break
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
