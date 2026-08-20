"""Register match events against video frames, by hand, at the keyboard. Localhost only.

The reader in front of the frames is a person, not a model. This is the bench they work at:
the still on the left, the technique library under one permanently-focused search field, and
every other control on a key so the hands never leave home row.

**Why the search never loses focus.** Registering a bout is a few hundred repetitions of the
same loop -- look, name, commit -- and the cost of a loop is dominated by the hand leaving
the keyboard. So letters always go to the search, and digits and arrows are commands. That
costs one thing: four library labels contain digits ("50/50 Guard" and friends). They are
reachable by their letters, which is why the search matches anywhere in the name, not just
at the start.

**What it writes.** ``events.json`` in the bout folder, validated by
``scripts/frame_answer.py``. Autosaved as you work -- a tool you have to remember to save is
a tool that loses an hour of someone's evening.

    uv run python scripts/frame_registrar.py            # http://127.0.0.1:8765
    uv run python scripts/frame_registrar.py --open

Binds 127.0.0.1 and nothing else: it serves local image files and takes unauthenticated
writes, so it must not be reachable from the network.

Privacy class: **A, public competition data.**
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OUT = REPO / "data" / "frame_pdf" / "out"
LIBRARY = REPO / "data" / "frame_pdf" / "node_library.json"
BJJH = REPO / "data" / "frame_pdf" / "bjjh_results.json"
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def bouts() -> list[Path]:
    return sorted(d for d in OUT.iterdir() if (d / "frames.jsonl").exists())


def _title(d: Path) -> str:
    readme = d / "README.md"
    if readme.exists():
        first = readme.read_text(encoding="utf-8").splitlines()[0]
        return first.lstrip("# ").strip()
    return d.name


def _facts(d: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    readme = d / "README.md"
    if readme.exists():
        for line in readme.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^- \*\*(.+?):\*\* (.*)$", line)
            if m:
                out.setdefault(m.group(1), m.group(2))
    return out


def _athletes(title: str) -> tuple[str, str]:
    """"A vs B, Event Year" -> ("A", "B"). Pre-filled, never assumed: the operator can
    correct either, and the corrected names are what every event's actor is bound to."""
    m = re.match(r"^(?P<a>.+?)\s+vs\.?\s+(?P<b>[^,]+?)\s*(?:,.*)?$", title.strip(), re.I)
    return (m.group("a").strip(), m.group("b").strip()) if m else ("", "")


def library() -> dict[str, Any]:
    """The technique vocabulary, plus the synonym aliases that collapse onto a canonical
    node. An alias is a search key -- typing it must find the node it collapses to, or the
    operator types a name the corpus knows and gets told it is unknown."""
    if not LIBRARY.exists():
        return {"nodes": [], "aliases": {}}
    lib = json.loads(LIBRARY.read_text(encoding="utf-8"))
    by_key = {str(n["key"]): n for n in lib["nodes"]}
    aliases: dict[str, list[str]] = {}
    for alias, canon in (lib.get("synonyms") or {}).items():
        if canon in by_key:
            aliases.setdefault(str(by_key[canon]["label"]), []).append(str(alias))
    return {"nodes": [{"label": n["label"], "type": n.get("node_type") or n.get("type") or "",
                       "uses": n.get("corpus_events") or 0} for n in lib["nodes"]],
            "aliases": aliases}


def payload(d: Path) -> dict[str, Any]:
    frames = [json.loads(x) for x in
              (d / "frames.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    saved = json.loads((d / "events.json").read_text(encoding="utf-8")) \
        if (d / "events.json").exists() else {}
    title = _title(d)
    a, b = _athletes(title)
    bout = saved.get("bout") or {}
    bout.setdefault("athlete_a", a)
    bout.setdefault("athlete_b", b)
    facts = _facts(d)
    return {"slug": d.name, "title": title, "facts": facts, "frames": frames,
            "bout": bout, "events": saved.get("events", []),
            "clip": (d / "clip.mp4").exists(),
            # the strip's own span is the honest bound; the clip may run a hair longer
            "span": [frames[0]["ts"], frames[-1]["ts"]] if frames else [0, 0]}


def still(d: Path, t: float) -> bytes:
    """The full-resolution frame at ``t`` seconds, materialised from the bout's own clip.

    This is why the clip is kept. A folder of JPEGs fixes the sampling interval at the moment
    it is built, and the interval is exactly the thing that decides whether a transition is
    visible -- every unresolved event in this corpus so far fell between two samples. Seeking
    the clip makes the interval a choice at read time: 1s, 0.25s, or a single video frame,
    without rebuilding anything.

    Cached on disk because the operator steps back and forth over the same seconds
    constantly, and a second visit must be free. ~200ms cold, instant after.
    """
    clip = d / "clip.mp4"
    if not clip.exists():
        raise FileNotFoundError(f"{d.name} has no clip.mp4 -- rebuild it with --format frames")
    cache = d / "cache"
    cache.mkdir(exist_ok=True)
    out = cache / f"{t:09.2f}.jpg"
    if not out.exists():
        # -ss BEFORE -i is the fast seek (keyframe index, no decode of the preceding stream).
        r = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t}", "-i", str(clip),
             "-frames:v", "1", "-q:v", "3", "-y", str(out)],
            capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"ffmpeg could not seek {t}s: {(r.stderr or '').strip()[-200:]}")
    return out.read_bytes()


def summary() -> list[dict[str, Any]]:
    rows = []
    for d in bouts():
        n = 0
        f = d / "events.json"
        if f.exists():
            try:
                n = len(json.loads(f.read_text(encoding="utf-8")).get("events", []))
            except json.JSONDecodeError:
                n = -1
        rows.append({"slug": d.name, "title": _title(d), "events": n,
                     "frames": sum(1 for _ in (d / "frames.jsonl").open())})
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            # frames never change under a slug; without this every step re-fetches 75 KB
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _q(self, name: str) -> str:
        return parse_qs(urlparse(self.path).query).get(name, [""])[0]

    def _folder(self) -> Path | None:
        """A slug off the query string is untrusted input that becomes a path. It is matched
        against the folders that exist -- not sanitised, which is the version that gets an
        edge case wrong."""
        slug = self._q("slug")
        return next((d for d in bouts() if d.name == slug), None)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, INDEX.encode(), "text/html; charset=utf-8")
        elif path == "/api/bouts":
            self._json(summary())
        elif path == "/api/library":
            self._json(library())
        elif path == "/api/bout":
            d = self._folder()
            self._json(payload(d) if d else {"error": "no such bout"}, 200 if d else 404)
        elif path == "/frame":
            d, name = self._folder(), self._q("file")
            src = d / "strip" / name if d else None
            if not d or not _SAFE.match(name) or src is None or not src.exists():
                self._send(404, b"", "text/plain")
                return
            self._send(200, src.read_bytes(), "image/jpeg", cache=True)
        elif path == "/still":
            d = self._folder()
            try:
                t = round(float(self._q("t")), 2)
            except ValueError:
                self._send(400, b"", "text/plain")
                return
            if not d or t < 0:
                self._send(404, b"", "text/plain")
                return
            try:
                self._send(200, still(d, t), "image/jpeg", cache=True)
            except FileNotFoundError:
                self._send(404, b"", "text/plain")
            except RuntimeError as exc:
                self._send(500, str(exc).encode(), "text/plain")
        else:
            self._send(404, b"", "text/plain")

    def do_POST(self) -> None:
        if self.path.split("?")[0] != "/api/events":
            self._send(404, b"", "text/plain")
            return
        d = self._folder()
        if not d:
            self._json({"error": "no such bout"}, 404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        body["saved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        body["source"] = "frame_registrar (human)"
        (d / "events.json").write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
        self._json({"ok": True, "events": len(body.get("events", []))})


INDEX = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Event registrar</title><style>
/* Century Schoolbook (URW C059) for everything readable — a face drawn for legibility in
   cheaply printed schoolbooks, which is the same job as a caption beside a grainy still.
   JetBrains Mono ONLY where digits must align in a column. Both already on this machine, so
   the bench never depends on a network to look right. */
:root{
  --bg:oklch(0.164 0.008 255); --pit:oklch(0.118 0.006 255);
  --surface:oklch(0.208 0.009 255); --raised:oklch(0.246 0.010 255);
  --line:oklch(0.303 0.011 255); --line-soft:oklch(0.256 0.010 255);
  --ink:oklch(0.918 0.006 255); --ink-2:oklch(0.700 0.011 255); --ink-3:oklch(0.545 0.012 255);
  --signal:oklch(0.795 0.152 82);         /* playhead + focus. Never on a mat: no blue, no yellow-green */
  --landed:oklch(0.760 0.130 152); --attempt:oklch(0.680 0.165 26);
  --a1:oklch(0.740 0.120 210); --a2:oklch(0.760 0.140 330);   /* the two competitors */
  --sans:"C059","Century Schoolbook L","Liberation Serif",Georgia,serif;
  --mono:"JetBrains Mono","Adwaita Mono",ui-monospace,monospace;
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans);
  display:grid;grid-template-rows:auto 1fr auto;overflow:hidden}

/* ── bout strip ─────────────────────────────────────────────────────────── */
#top{display:flex;align-items:stretch;gap:var(--s5);padding:var(--s3) var(--s5);
  background:var(--surface);border-bottom:1px solid var(--line)}
#pick{font:13px/1 var(--sans);background:var(--raised);color:var(--ink);border:1px solid var(--line);
  border-radius:2px;padding:var(--s2) var(--s3);max-width:340px}
#pick:focus{outline:2px solid var(--signal);outline-offset:1px}
#who{display:flex;gap:var(--s5);align-items:center;flex:1;min-width:0}
.who{display:flex;align-items:baseline;gap:var(--s2);min-width:0}
.who .k{font:600 11px/1 var(--mono);color:var(--pit);background:var(--ink-2);
  padding:3px 5px;border-radius:2px}
.who.on .k{background:var(--sel)}
.who input{background:transparent;border:0;border-bottom:1px dotted var(--line);color:var(--ink);
  font:16px/1.3 var(--sans);padding:2px 0;min-width:60px;width:100%}
.who input:focus{outline:0;border-bottom-color:var(--sel)}
.who.a1{--sel:var(--a1)} .who.a2{--sel:var(--a2)}
.who.on input{color:var(--sel)}
#swap{background:var(--raised);border:1px solid var(--line);color:var(--ink-2);border-radius:2px;
  font:15px/1 var(--sans);padding:6px 9px;cursor:pointer;align-self:center}
#swap:hover{border-color:var(--signal);color:var(--signal)}
#warn{margin:0;align-self:center;max-width:190px;font:11px/1.35 var(--sans);color:var(--ink-3)}
#state{font:12px/1 var(--mono);color:var(--ink-3);white-space:nowrap;align-self:center}
#state.dirty{color:var(--signal)}

/* ── the bench ──────────────────────────────────────────────────────────── */
#bench{display:grid;grid-template-columns:minmax(0,1fr) 392px;min-height:0}
#left{display:grid;grid-template-rows:minmax(0,1fr) auto;min-width:0;background:var(--pit)}
#stage{position:relative;min-height:0;display:flex;align-items:center;justify-content:center;
  padding:var(--s4) var(--s5) var(--s2)}
#shot{height:100%;width:auto;max-width:100%;object-fit:contain;display:block;
  box-shadow:0 0 0 1px var(--line),0 18px 40px -24px oklch(0 0 0/0.9)}
#plate{margin:0;position:relative;display:flex;height:100%;max-width:100%;min-height:0}
#tc{position:absolute;left:var(--s3);bottom:var(--s3);background:oklch(0.118 0.006 255/0.82);
  border:1px solid var(--line);padding:3px 9px;border-radius:2px;
  font:600 15px/1.2 var(--mono);letter-spacing:0.02em;color:var(--signal)}
#of{position:absolute;right:var(--s3);bottom:var(--s3);
  background:oklch(0.118 0.006 255/0.82);padding:4px 8px;border-radius:2px;font:11px/1.2 var(--mono);color:var(--ink-3)}

/* the ribbon: the film IS the scrubber. Playhead is a fixed mark, the strip moves under it. */
#ribbon{position:relative;height:96px;border-top:1px solid var(--line-soft);background:var(--bg);
  overflow:hidden}
#reel{display:flex;gap:2px;align-items:center;height:100%;padding:var(--s3) 0;
  transition:transform 90ms cubic-bezier(.22,1,.36,1);will-change:transform}
#reel figure{margin:0;position:relative;flex:none;height:100%}
#reel img{height:100%;display:block;opacity:.42;transition:opacity 120ms}
#reel .cur img{opacity:1}
#reel .has img{opacity:.85}
#reel .has::after{content:"";position:absolute;left:0;right:0;bottom:0;height:3px;
  background:var(--pin)}
#head{position:absolute;left:50%;top:0;bottom:0;width:0;pointer-events:none;
  border-left:1px solid var(--signal)}
#head::before,#head::after{content:"";position:absolute;left:-4px;border-left:4px solid transparent;
  border-right:4px solid transparent}
#head::before{top:0;border-top:5px solid var(--signal)}
#head::after{bottom:0;border-bottom:5px solid var(--signal)}

/* ── right column ───────────────────────────────────────────────────────── */
#right{display:grid;grid-template-rows:auto auto minmax(0,1fr);border-left:1px solid var(--line);
  background:var(--surface);min-height:0}

#pending{padding:var(--s4) var(--s4) var(--s3);border-bottom:1px solid var(--line-soft)}
#q{width:100%;background:var(--pit);border:1px solid var(--line);border-radius:2px;
  color:var(--ink);font:17px/1.3 var(--sans);padding:9px var(--s3)}
#q:focus{outline:0;border-color:var(--signal);box-shadow:0 0 0 3px oklch(0.795 0.152 82/0.14)}
#q::placeholder{color:var(--ink-3)}
#slip{display:flex;flex-wrap:wrap;gap:var(--s2) var(--s3);align-items:baseline;
  margin-top:var(--s3);font:12px/1 var(--mono);color:var(--ink-3)}
#slip b{font-weight:500;color:var(--ink-2)}
#slip .on{color:var(--ink)}
#slip .landed{color:var(--landed)} #slip .attempt{color:var(--attempt)}
#slip .act1{color:var(--a1)} #slip .act2{color:var(--a2)}

#hits{max-height:236px;overflow:auto;border-bottom:1px solid var(--line-soft)}
.hit{display:flex;align-items:baseline;gap:var(--s3);padding:6px var(--s4);cursor:pointer}
.hit .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hit .t{font:10px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.hit .u{font:11px/1 var(--mono);color:var(--ink-3);min-width:34px;text-align:right}
.hit.sel{background:var(--raised);box-shadow:inset 3px 0 0 var(--signal)}
.hit.sel .n{color:var(--signal)}
.hit em{font-style:normal;color:var(--ink);text-decoration:underline;text-underline-offset:2px}
#hits .none{padding:var(--s4);color:var(--ink-3);font-size:13px}

#log{overflow:auto;min-height:0}
#log h2{margin:0;padding:var(--s3) var(--s4) var(--s2);font:500 11px/1 var(--mono);
  letter-spacing:.1em;text-transform:uppercase;color:var(--ink-3);
  position:sticky;top:0;background:var(--surface)}
.row{display:grid;grid-template-columns:58px 16px 1fr auto;gap:var(--s3);align-items:baseline;
  padding:6px var(--s4);cursor:pointer;border-top:1px solid var(--line-soft)}
.row:hover{background:var(--raised)}
.row.at{background:var(--raised)}
.row .ts{font:12px/1.4 var(--mono);color:var(--ink-3)}
.row.at .ts{color:var(--signal)}
.row .lb{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .meta{font:11px/1.4 var(--mono);white-space:nowrap}
.row .who-k{font:600 10px/1.6 var(--mono);text-align:center;border-radius:2px}
.row.a1 .who-k{color:var(--a1);background:oklch(0.740 0.120 210/0.14)}
.row.a2 .who-k{color:var(--a2);background:oklch(0.760 0.140 330/0.14)}
.row .att{color:var(--attempt)} .row .pt{color:var(--signal)}
#log .none{padding:var(--s5) var(--s4);color:var(--ink-3);font-size:13px;line-height:1.6}

/* ── key legend ─────────────────────────────────────────────────────────── */
#keys{display:flex;flex-wrap:wrap;gap:var(--s1) var(--s4);padding:var(--s2) var(--s5);
  background:var(--surface);border-top:1px solid var(--line);
  font:11px/1.8 var(--mono);color:var(--ink-3)}
#keys kbd{font:inherit;background:var(--raised);border:1px solid var(--line);border-radius:2px;
  padding:1px 5px;color:var(--ink-2)}
#toast{position:fixed;left:50%;bottom:54px;transform:translate(-50%,10px);opacity:0;
  background:var(--raised);border:1px solid var(--line);border-radius:2px;
  padding:7px var(--s4);font:13px/1 var(--sans);pointer-events:none;
  transition:opacity 140ms,transform 140ms cubic-bezier(.22,1,.36,1)}
#toast.on{opacity:1;transform:translate(-50%,0)}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>

<header id="top">
  <select id="pick"></select>
  <div id="who">
    <div class="who a1" id="w1"><span class="k">1</span><input id="n1" spellcheck="false"></div>
    <div class="who a2" id="w2"><span class="k">2</span><input id="n2" spellcheck="false"></div>
    <button id="swap" title="Swap the two names, and every actor already logged (Ctrl+X)">⇄</button>
  </div>
  <p id="warn">order is from the video title —<br>confirm it against the footage</p>
  <div id="state">—</div>
</header>

<main id="bench">
  <div id="left">
    <div id="stage"><figure id="plate"><img id="shot" alt="">
      <span id="tc"></span><span id="of"></span></figure></div>
    <div id="ribbon"><div id="reel"></div><div id="head"></div></div>
  </div>
  <aside id="right">
    <div id="pending">
      <input id="q" placeholder="type a technique…" spellcheck="false" autocomplete="off"
             inputmode="search">
      <div id="slip"></div>
    </div>
    <div id="hits"></div>
    <div id="log"><h2>Logged</h2><div id="rows"></div></div>
  </aside>
</main>

<footer id="keys">
  <span><kbd>←</kbd><kbd>→</kbd> frame</span><span><kbd>⇧</kbd>+ 10</span>
  <span><kbd>↑</kbd><kbd>↓</kbd> pick / ±10 frames</span><span><kbd>⏎</kbd> log</span>
  <span><kbd>1</kbd><kbd>2</kbd> actor</span><span><kbd>3</kbd> landed·attempt</span>
  <span><kbd>4</kbd> points</span><span><kbd>5</kbd><kbd>6</kbd> finer / coarser step</span><span><kbd>0</kbd> no points</span>
  <span><kbd>⇥</kbd> next event</span><span><kbd>del</kbd> remove</span>
  <span><kbd>^Z</kbd> undo</span><span><kbd>^X</kbd> swap names</span><span><kbd>esc</kbd> clear</span>
</footer>
<div id="toast"></div>
<script>
const $=s=>document.querySelector(s);
let LIB={nodes:[],aliases:{}}, B=null, hits=[], hi=0, undo=[], timer=null;
let pos=0;                                   /* seconds, continuous — not a frame index */
/* The interval is a read-time choice because the clip holds every frame. 1s is the sweep;
   0.25 and 0.1 are for the two seconds where a scramble actually happens. */
const STEPS=[5,2,1,0.5,0.25,0.1], DEFAULT_STEP=2;
let si=DEFAULT_STEP;
const step=()=>STEPS[si];
let draft={actor:1, landed:true, points:null};

const clock=t=>{const m=Math.floor(t/60), sec=t-m*60;
  return `${m}:${(sec<10?'0':'')}${Number.isInteger(sec)?sec:sec.toFixed(2)}`;};
const snap=t=>Math.round(t*100)/100;
const norm=s=>(s||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'')
  .replace(/\s+/g,' ').trim();

/* Ranking is a port of the app's src/utils/nodeSearchUtils.ts, same numbers: exact 100,
   prefix 70, word-prefix 50, substring 25, plus a full-query bonus. Every token must match
   (AND), so "arm tri" finds Arm Triangle and not Armbar. Kept identical on purpose — the
   operator's muscle memory for what a half-typed query surfaces should transfer between the
   app and this bench. */
function scoreName(name,tok){
  if(!name||!tok)return 0;
  if(name===tok)return 100;
  if(name.startsWith(tok))return 70;
  if(name.split(' ').some(w=>w.startsWith(tok)))return 50;
  if(name.includes(tok))return 25;
  return 0;
}
function names(n){ return [n.label, ...(LIB.aliases[n.label]||[]), n.type].filter(Boolean).map(norm); }
function score(n,q){
  const nq=norm(q); if(!nq)return 0;
  const ns=names(n), toks=nq.split(' ').filter(Boolean);
  let total=0;
  for(const t of toks){
    let best=0; for(const nm of ns){const s=scoreName(nm,t); if(s>best)best=s;}
    if(!best)return 0; total+=best;
  }
  let bonus=0;
  for(const nm of ns){ if(nm===nq){bonus=40;break} if(nm.startsWith(nq))bonus=Math.max(bonus,20); }
  return total+bonus;
}
function rank(q){
  const t=(q||'').trim(); if(!t)return [];
  return LIB.nodes.map((n,i)=>({n,i,s:score(n,t)})).filter(x=>x.s>0)
    .sort((a,b)=>(b.s-a.s)||(b.n.uses-a.n.uses)||(a.i-b.i)).slice(0,40).map(x=>x.n);
}
function mark(label,q){
  const nq=norm(q); if(!nq)return label;
  const i=norm(label).indexOf(nq.split(' ')[0]);
  if(i<0)return label;
  const len=nq.split(' ')[0].length;
  return label.slice(0,i)+'<em>'+label.slice(i,i+len)+'</em>'+label.slice(i+len);
}

async function boot(){
  LIB=await (await fetch('/api/library')).json();
  const rows=await (await fetch('/api/bouts')).json();
  $('#pick').innerHTML=rows.map(r=>
    `<option value="${r.slug}">${r.title}  —  ${r.events>0?r.events+' logged':'empty'}, ${r.frames} frames</option>`).join('');
  $('#pick').onchange=e=>load(e.target.value);
  if(rows.length)load(rows[0].slug);
}
async function load(slug){
  B=await (await fetch('/api/bout?slug='+encodeURIComponent(slug))).json();
  B.events=B.events||[]; pos=B.span?B.span[0]:0; si=DEFAULT_STEP; undo=[]; hits=[]; hi=0;
  $('#n1').value=B.bout.athlete_a||''; $('#n2').value=B.bout.athlete_b||'';
  $('#q').value=''; setState('saved');
  draw(); focusQ();
}
function swapNames(){
  const a=$('#n1').value, b=$('#n2').value;
  $('#n1').value=b; $('#n2').value=a;
  B.events.forEach(e=>{ if(e.actor===a)e.actor=b; else if(e.actor===b)e.actor=a; });
  save(); draw(); toast(`swapped — ${B.events.length} logged actor(s) followed`);
}
function actorName(i){ return (i===1?$('#n1').value:$('#n2').value).trim(); }
function focusQ(){ $('#q').focus(); }

const stillURL=t=>`/still?slug=${encodeURIComponent(B.slug)}&t=${snap(t).toFixed(2)}`;
function draw(){
  if(!B)return;
  $('#shot').src=stillURL(pos);
  $('#tc').textContent=clock(snap(pos));
  $('#of').textContent=`${snap(pos).toFixed(2)}s absolute   ·   step ${step()}s`;
  // Each still costs a seek the first time. Walking the neighbours now means the arrow key
  // reads from cache, which is the difference between a bench and a slideshow.
  for(let k=1;k<=6;k++){
    [pos+k*step(), pos-k*step()].forEach(t=>{
      if(t>=B.span[0]&&t<=B.span[1]){const im=new Image(); im.src=stillURL(t);}
    });
  }
  reel(); slip(); rows(); paintWho();
}
function reel(){
  /* The strip is a coarse index (one thumbnail per second), the playhead is continuous, so
     the head sits BETWEEN thumbnails whenever the step is sub-second. Interpolating its
     offset is what keeps "where am I" honest at 0.1s — snapping it to the nearest thumb
     would show the operator a position they are not at. */
  const near=B.frames.reduce((b,f,i)=>Math.abs(f.ts-pos)<Math.abs(B.frames[b].ts-pos)?i:b,0);
  const W=15, lo=Math.max(0,near-W), hi2=Math.min(B.frames.length,near+W+1);
  const at=new Map(); B.events.forEach(e=>at.set(Math.round(e.ts),e));
  $('#reel').innerHTML=B.frames.slice(lo,hi2).map((f,j)=>{
    const i=lo+j, e=at.get(f.ts);
    const cls=(i===near?'cur ':'')+(e?'has':'');
    const pin=e?`--pin:var(--a${(actorName(1)&&e.actor===actorName(1))?1:2})`:'';
    return `<figure class="${cls}" style="${pin}" data-i="${i}" data-t="${f.ts}"><img
      loading="lazy" decoding="async"
      src="/frame?slug=${encodeURIComponent(B.slug)}&file=${f.file}"></figure>`;
  }).join('');
  const reelEl=$('#reel');
  [...reelEl.children].forEach(el=>el.onclick=()=>{pos=+el.dataset.t;draw();focusQ()});
  requestAnimationFrame(()=>{
    const cur=reelEl.querySelector('.cur'); if(!cur)return;
    const gap=cur.offsetWidth+2, drift=(pos-B.frames[near].ts)*gap;   // 1 thumb == 1 second
    const mid=$('#ribbon').clientWidth/2;
    reelEl.style.transform=`translateX(${mid-(cur.offsetLeft+cur.offsetWidth/2)-drift}px)`;
  });
}
function slip(){
  const a=actorName(draft.actor)||`athlete ${draft.actor}`;
  $('#slip').innerHTML=
    `<span><b>at</b> <span class="on">${clock(snap(pos))}</span></span>`+
    `<span><b>by</b> <span class="on act${draft.actor}">${a}</span></span>`+
    `<span class="${draft.landed?'landed':'attempt'}">${draft.landed?'landed':'attempt only'}</span>`+
    `<span><b>points</b> <span class="${draft.points?'on':''}">${draft.points??'—'}</span></span>`;
}
function rows(){
  const el=$('#rows');
  if(!B.events.length){ el.innerHTML=`<div class="none">Nothing logged yet.<br>
    Step with <b>←</b>/<b>→</b>, type a technique, press <b>⏎</b>.</div>`; return; }
  const ts=snap(pos);
  el.innerHTML=B.events.map((e,i)=>{
    const which=e.actor===actorName(1)?'a1':'a2';
    return `<div class="row ${which} ${e.ts===ts?'at':''}" data-i="${i}">
      <span class="ts">${clock(e.ts)}</span>
      <span class="who-k">${which==='a1'?'1':'2'}</span>
      <span class="lb">${e.label}</span>
      <span class="meta">${e.successful?'':'<span class="att">att</span> '}${
        e.points?`<span class="pt">+${e.points}</span>`:''}</span></div>`;
  }).join('');
  [...el.children].forEach(d=>d.onclick=()=>{
    pos=B.events[+d.dataset.i].ts; draw(); focusQ();
  });
  const at=el.querySelector('.at'); if(at)at.scrollIntoView({block:'nearest'});
}
function paintWho(){
  $('#w1').classList.toggle('on',draft.actor===1);
  $('#w2').classList.toggle('on',draft.actor===2);
}
function search(){
  const q=$('#q').value;
  hits=rank(q); hi=0;
  const el=$('#hits');
  if(!q.trim()){ el.innerHTML=''; return; }
  if(!hits.length){ el.innerHTML=`<div class="none">No library match for “${q}”.
    Nothing is logged for an unknown name — a spelling the library lacks would mint a second
    node for a technique it already has.</div>`; return; }
  el.innerHTML=hits.map((n,i)=>`<div class="hit ${i===hi?'sel':''}" data-i="${i}">
    <span class="n">${mark(n.label,q)}</span><span class="t">${n.type}</span>
    <span class="u">${n.uses||''}</span></div>`).join('');
  [...el.children].forEach(d=>d.onclick=()=>{hi=+d.dataset.i;commit()});
}
function selHit(d){
  if(!hits.length)return;
  hi=(hi+d+hits.length)%hits.length;
  [...$('#hits').children].forEach((el,i)=>el.classList.toggle('sel',i===hi));
  $('#hits').children[hi]?.scrollIntoView({block:'nearest'});
}
function commit(){
  if(!hits.length)return;
  const n=hits[hi], a=actorName(draft.actor);
  if(!a){ toast('Name both competitors first — every event is bound to one of them.'); return }
  const e={ts:snap(pos), label:n.label, actor:a, successful:draft.landed, type:n.type};
  if(draft.points!=null)e.points=draft.points;   // absent, never 0 — see frame_answer.py
  B.events.push(e); B.events.sort((x,y)=>x.ts-y.ts);
  undo.push(e);
  $('#q').value=''; hits=[]; $('#hits').innerHTML='';
  draft.points=null;                              // points do not carry to the next event
  save(); draw(); toast(`${n.label} · ${a} · ${clock(e.ts)}`);
}
function removeAt(){
  const ts=snap(pos), i=B.events.findIndex(e=>Math.abs(e.ts-ts)<0.005);
  if(i<0){ toast('No event on this frame.'); return }
  const [gone]=B.events.splice(i,1); undo.push({undo:gone});
  save(); draw(); toast(`removed ${gone.label}`);
}
function undoLast(){
  const last=undo.pop(); if(!last)return;
  if(last.undo){ B.events.push(last.undo); B.events.sort((a,b)=>a.ts-b.ts); toast('restored'); }
  else { const i=B.events.indexOf(last); if(i>=0)B.events.splice(i,1); toast('undone'); }
  save(); draw();
}
function jump(d){
  if(!B.events.length)return;
  const ts=snap(pos);
  const cand=d>0?B.events.filter(e=>e.ts>ts):B.events.filter(e=>e.ts<ts).reverse();
  pos=cand.length?cand[0].ts:(d>0?B.events[0].ts:B.events[B.events.length-1].ts); draw();
}
function move(n){ pos=Math.min(B.span[1],Math.max(B.span[0],snap(pos+n*step()))); draw(); }
function zoom(d){ si=Math.min(STEPS.length-1,Math.max(0,si+d)); draw();
  toast(`step ${step()}s`); }
function toast(m){ const t=$('#toast'); t.textContent=m; t.classList.add('on');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('on'),1500); }
function setState(s){ const el=$('#state');
  el.textContent={saved:'saved',saving:'saving…',dirty:'unsaved'}[s]||s;
  el.classList.toggle('dirty',s!=='saved'); }
function save(){
  setState('dirty'); clearTimeout(timer);
  timer=setTimeout(async()=>{
    setState('saving');
    B.bout.athlete_a=$('#n1').value.trim(); B.bout.athlete_b=$('#n2').value.trim();
    const r=await fetch('/api/events?slug='+encodeURIComponent(B.slug),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({bout:B.bout,events:B.events})});
    setState(r.ok?'saved':'dirty');
  },400);
}

/* Focus is the contract: letters always reach the search, so every other control is a key.
   Clicking anywhere hands focus straight back. */
document.addEventListener('mouseup',()=>{ if(!/^(INPUT|SELECT)$/.test(document.activeElement.tagName))focusQ(); });
$('#n1').addEventListener('input',()=>{save();draw()});
$('#n2').addEventListener('input',()=>{save();draw()});
$('#q').addEventListener('input',search);
$('#swap').onclick=()=>{swapNames();focusQ()};

addEventListener('keydown',ev=>{
  const inName=ev.target===$('#n1')||ev.target===$('#n2')||ev.target===$('#pick');
  if(inName){ if(ev.key==='Enter'||ev.key==='Escape'){ev.target.blur();focusQ()} return; }
  const k=ev.key;
  if(ev.ctrlKey&&(k==='z'||k==='Z')){ undoLast(); ev.preventDefault(); return; }
  if(ev.ctrlKey&&(k==='x'||k==='X')){ swapNames(); ev.preventDefault(); return; }
  if(ev.ctrlKey||ev.metaKey||ev.altKey)return;
  const q=$('#q').value.trim();
  switch(k){
    case 'ArrowRight': move(ev.shiftKey?10:1); break;
    case 'ArrowLeft':  move(ev.shiftKey?-10:-1); break;
    case 'ArrowDown':  hits.length?selHit(1):move(10); break;
    case 'ArrowUp':    hits.length?selHit(-1):move(-10); break;
    case 'Enter':      commit(); break;
    case 'Tab':        jump(ev.shiftKey?-1:1); break;
    case 'Delete':     removeAt(); break;
    case 'Home':       pos=B.span[0]; draw(); break;
    case 'End':        pos=B.span[1]; draw(); break;
    case '5': zoom(1); break;
    case '6': zoom(-1); break;
    case 'Escape':     $('#q').value=''; search(); break;
    // Digits are commands, never text. Four library labels contain digits ("50/50 Guard" and
    // friends) and stay reachable by their letters, because the search matches anywhere.
    case '1': draft.actor=1; slip(); paintWho(); break;
    case '2': draft.actor=2; slip(); paintWho(); break;
    case '3': draft.landed=!draft.landed; slip(); break;
    case '4': { const cyc=[null,2,3,4];
                draft.points=cyc[(cyc.indexOf(draft.points)+1)%cyc.length]; slip(); break; }
    case '0': draft.points=null; slip(); break;
    default: return;                                  // everything else is typing
  }
  ev.preventDefault();
});
boot();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    n = len(bouts())
    if not n:
        print(f"no frame folders under {OUT} -- run frame_pdf.py --format frames first")
        return 2
    url = f"http://127.0.0.1:{a.port}/"
    print(f"{n} bouts  →  {url}   (ctrl-c to stop)")
    if a.open:
        webbrowser.open(url)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
