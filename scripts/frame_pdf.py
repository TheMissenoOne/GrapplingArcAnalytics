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

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("frame_pdf")

REPO = Path(__file__).resolve().parent.parent

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


def load_db_context() -> DbContext:
    """Every final match that already carries a video link, keyed by video id, plus the
    scouting roster used to say which division a fight belongs to.

    Read-only, and deliberately NOT through ``db_session()``: that context manager commits on
    a clean exit, and nothing here has any business writing.
    """
    sys.path.insert(0, str(REPO))
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    from sqlalchemy import text

    from db.base import get_engine

    ctx = DbContext()
    with get_engine().connect() as c:
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
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def context_page(entry: Entry, meta: dict[str, Any], db: DbContext,
                 n_frames: int, step: int, first_ts: float, last_ts: float) -> Image.Image:
    """The first page: which fight, which clock, and what the answer must look like.

    It is written as a prompt because it IS the prompt -- the PDF is the whole message the
    reader gets, so anything left implicit here comes back as a guess.
    """
    img = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    d = ImageDraw.Draw(img)
    h1, h2, body, mono = _font(52), _font(34), _font(28), _font(26)
    y = MARGIN

    d.text((MARGIN, y), entry.label or meta.get("title") or entry.vid, font=h1, fill="black")
    y += 74
    d.line([(MARGIN, y), (PAGE_W - MARGIN, y)], fill="black", width=3)
    y += 30

    known = db.bouts_for(entry.vid)
    div = next((db.roster[k] for k in db.roster if k in _fold(entry.label or meta.get("title") or "")), None)

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
    if entry.note:
        rows.append(("Source note", entry.note))
    if known:
        rows.append(("Already in the corpus",
                     "; ".join(f"{b['a']} vs {b['b']} ({b['event'] or 'no event'} {b['year']}), "
                               f"{b['events']} events" for b in known)))

    for k, v in rows:
        d.text((MARGIN, y), f"{k}", font=h2, fill="#444444")
        for line in _wrap(v, 74):
            d.text((MARGIN + 340, y), line, font=body, fill="black")
            y += 36
        y += 8

    y += 24
    d.line([(MARGIN, y), (PAGE_W - MARGIN, y)], fill="black", width=3)
    y += 30
    d.text((MARGIN, y), "How to read this", font=h1, fill="black")
    y += 74

    instructions = f"""Each frame below is stamped with its VIDEO-ABSOLUTE time: seconds from \
the start of the video at the URL above, NOT seconds from the start of the bout. Frame \
stamps run from {hhmmss(first_ts)} to {hhmmss(last_ts)}.

Return every timestamp in that same video-absolute clock. If the bout starts partway into \
the video, say where it starts as a separate field -- do not rebase the event times to it. \
A bout-relative time reported as an absolute one places the whole fight in a different part \
of the broadcast, and the result looks plausible while being wrong.

For each event you can see, give:
  ts            integer seconds, video-absolute
  label         what happened (e.g. "Guard Pass", "Back Take", "Heel Hook", "Sweep")
  actor         which competitor did it, by name
  successful    true if it landed, false if it was attempted and stopped
  type          one of: takedown, pass, sweep, submission, escape, control, guard_pull

Also return, once per bout: the two competitors' names, the event/card name, the year, who \
won and how, and bout_start_seconds -- the video-absolute second the bout begins.

If this video contains more than one bout, return one such object per bout.

Only report what a frame actually shows. An event you infer from the scoreboard but never \
see is worth less than an omission: a wrong label propagates into the athlete's graph and \
their rating, and nothing downstream can tell it from an observed one."""

    for para in instructions.split("\n\n"):
        for line in _wrap(" ".join(para.split()), 92):
            d.text((MARGIN, y), line, font=mono, fill="black")
            y += 34
        y += 16
    return img


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def grid_pages(frames: list[tuple[float, Path]], grid: tuple[int, int]) -> list[Image.Image]:
    cols, rows = grid
    per = cols * rows
    cell_w = (PAGE_W - 2 * MARGIN) // cols
    cell_h = (PAGE_H - 2 * MARGIN) // rows
    font = _font(30)
    pages: list[Image.Image] = []
    for i in range(0, len(frames), per):
        page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
        d = ImageDraw.Draw(page)
        for j, (ts, path) in enumerate(frames[i:i + per]):
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((cell_w - 16, cell_h - LABEL_H - 16))
                x = MARGIN + (j % cols) * cell_w
                y = MARGIN + (j // cols) * cell_h
                page.paste(im, (x + 8, y + LABEL_H))
                d.text((x + 8, y + 2), f"{hhmmss(ts)}   ({int(ts)}s)", font=font, fill="black")
        pages.append(page)
    return pages


def build_pdf(entry: Entry, meta: dict[str, Any], db: DbContext,
              frames: list[tuple[float, Path]], step: int, grid: tuple[int, int],
              out_path: Path) -> None:
    cover = context_page(entry, meta, db, len(frames), step,
                         frames[0][0], frames[-1][0])
    pages = grid_pages(frames, grid)
    cover.save(out_path, "PDF", save_all=True, append_images=pages,
               resolution=200.0, quality=JPEG_QUALITY)


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
            max_frames: int = DEFAULT_MAX_FRAMES) -> str:
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
        build_pdf(entry, meta, db, frames, step, grid, out_path)
    mb = out_path.stat().st_size / 1e6
    return f"ok   {out_path.name}  {len(frames)} frames, {mb:.1f} MB"


def main() -> int:
    global COOKIES_FROM_BROWSER
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=REPO / "data" / "frame_pdf" / "out")
    ap.add_argument("--step", type=int, default=DEFAULT_STEP_SECONDS)
    ap.add_argument("--grid", default="2x3", help="cols x rows per page (default 2x3)")
    ap.add_argument("--workdir", type=Path, default=Path("/mnt/dados/bjjh/tmp"),
                    help="scratch for downloads; defaults off the small root volume")
    ap.add_argument("--cookies-from-browser", default=COOKIES_FROM_BROWSER,
                    help="browser profile yt-dlp reads cookies from; '' for anonymous "
                         "(anonymous currently gets HTTP 403 on these uploads)")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even when the video already backs a reviewed sequence")
    ap.add_argument("--max-frames", type=int, default=DEFAULT_MAX_FRAMES,
                    help="widen the step rather than exceed this many frames in one PDF")
    ap.add_argument("--limit", type=int, help="stop after N videos (a smoke run)")
    ap.add_argument("--dry-run", action="store_true", help="report the plan, download nothing")
    a = ap.parse_args()

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
                                   a.force, a.max_frames))
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
