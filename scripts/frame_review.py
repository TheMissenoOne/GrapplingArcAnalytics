"""Human review of frame-read events, against the frames they were read from. Localhost only.

A reader's answer is a claim about what a frame shows. The only thing that settles it is
looking at the frame, so this puts the two side by side and records a verdict per event.

**What it writes.** ``review.json`` next to ``answer.json``, holding the machine's event AND
the human's verdict -- never the corrected version alone. A rejected event stays in the file
marked ``wrong`` rather than being deleted, because "the reader saw an armbar here and there
was none" is a measurement of the reader, and deleting it throws that measurement away. It is
also what makes an agreement rate computable later, which no amount of clean output would.

    uv run python scripts/frame_review.py            # http://127.0.0.1:8765
    uv run python scripts/frame_review.py --port 9000 --open

Binds 127.0.0.1 and nothing else: it serves local image files and takes unauthenticated
writes, so it must not be reachable from the network.

Privacy class: **A, public competition data.**
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OUT = REPO / "data" / "frame_pdf" / "out"
LIBRARY = REPO / "data" / "frame_pdf" / "node_library.json"
_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def bouts() -> list[Path]:
    return sorted(d for d in OUT.iterdir() if (d / "frames.jsonl").exists())


def _facts(d: Path) -> dict[str, str]:
    """The README's bullet list, back into fields. The README is written for a reader, not
    parsed by one -- but re-deriving these from the DB here would be a second source of truth
    for the same facts, and the one that drifts is always the copy."""
    out: dict[str, str] = {}
    readme = d / "README.md"
    if readme.exists():
        for line in readme.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^- \*\*(.+?):\*\* (.*)$", line)
            if m:
                out.setdefault(m.group(1), m.group(2))
    return out


def bout_payload(d: Path) -> dict[str, Any]:
    frames = [json.loads(x) for x in
              (d / "frames.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    answer = json.loads((d / "answer.json").read_text(encoding="utf-8")) \
        if (d / "answer.json").exists() else None
    review = json.loads((d / "review.json").read_text(encoding="utf-8")) \
        if (d / "review.json").exists() else None
    labels: list[str] = []
    if LIBRARY.exists():
        lib = json.loads(LIBRARY.read_text(encoding="utf-8"))
        labels = [str(n["label"]) for n in lib["nodes"]]
    return {"slug": d.name, "facts": _facts(d), "frames": frames,
            "answer": answer, "review": review, "labels": labels,
            "title": (d / "README.md").read_text(encoding="utf-8").splitlines()[0].lstrip("# ")
            if (d / "README.md").exists() else d.name}


def summary() -> list[dict[str, Any]]:
    rows = []
    for d in bouts():
        a = (d / "answer.json")
        r = (d / "review.json")
        n_events = 0
        if a.exists():
            try:
                n_events = len(json.loads(a.read_text(encoding="utf-8")).get("events", []))
            except json.JSONDecodeError:
                n_events = -1
        done = 0
        if r.exists():
            try:
                done = sum(1 for e in json.loads(r.read_text(encoding="utf-8"))["events"]
                           if e.get("_verdict"))
            except (json.JSONDecodeError, KeyError):
                done = 0
        rows.append({"slug": d.name, "title": bout_payload(d)["title"],
                     "events": n_events, "reviewed": done,
                     "frames": sum(1 for _ in (d / "frames.jsonl").open())})
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:      # one line per click is noise, not a log
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _folder(self) -> Path | None:
        """A slug from the query string is untrusted input that becomes a path. Matching it
        against the folders that exist is the check -- not sanitising the string, which is
        the version that gets a case wrong."""
        from urllib.parse import parse_qs, urlparse
        slug = parse_qs(urlparse(self.path).query).get("slug", [""])[0]
        return next((d for d in bouts() if d.name == slug), None)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, INDEX.encode(), "text/html; charset=utf-8")
        elif path == "/api/bouts":
            self._json(summary())
        elif path == "/api/bout":
            d = self._folder()
            self._json(bout_payload(d) if d else {"error": "no such bout"}, 200 if d else 404)
        elif path == "/frame":
            from urllib.parse import parse_qs, urlparse
            d = self._folder()
            name = parse_qs(urlparse(self.path).query).get("file", [""])[0]
            if not d or not _SAFE.match(name) or not (d / name).exists():
                self._send(404, b"", "text/plain")
                return
            self._send(200, (d / name).read_bytes(), "image/jpeg")
        else:
            self._send(404, b"", "text/plain")

    def do_POST(self) -> None:
        if self.path.split("?")[0] != "/api/bout":
            self._send(404, b"", "text/plain")
            return
        d = self._folder()
        if not d:
            self._json({"error": "no such bout"}, 404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        body["reviewed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        (d / "review.json").write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
        self._json({"saved": str(d / "review.json")})


INDEX = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Frame review</title><style>
:root{--bg:#0e1013;--pane:#16191e;--line:#262b33;--fg:#dfe3ea;--dim:#828a98;
--ok:#4ac07a;--bad:#e2585c;--fix:#e0a63c;--sel:#3d7de0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:13px/1.45 ui-sans-serif,system-ui,sans-serif;height:100vh;display:flex}
#rail{width:250px;flex:none;border-right:1px solid var(--line);overflow:auto;background:var(--pane)}
#rail h1{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--dim);
margin:0;padding:14px 12px 8px}
.b{padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.b:hover{background:#1c2027}.b.on{background:#1f2937;box-shadow:inset 3px 0 var(--sel)}
.b .t{font-size:12px}.b .m{font-size:11px;color:var(--dim);margin-top:2px}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
#head{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;
justify-content:space-between;align-items:center;gap:16px}
#head h2{font-size:14px;margin:0;font-weight:600}
#facts{font-size:11px;color:var(--dim);margin-top:3px}
#body{flex:1;display:flex;min-height:0}
#stage{flex:1;display:flex;flex-direction:column;min-width:0;padding:12px;gap:8px}
#big{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;
background:#000;border:1px solid var(--line);border-radius:4px;position:relative}
#big img{max-width:100%;max-height:100%;object-fit:contain}
#bigts{position:absolute;left:8px;top:8px;background:#000c;padding:2px 7px;border-radius:3px;
font-variant-numeric:tabular-nums;font-size:12px}
#strip{height:74px;display:flex;gap:4px;overflow-x:auto;align-items:center}
#strip img{height:64px;border:2px solid transparent;border-radius:3px;cursor:pointer;flex:none}
#strip img.on{border-color:var(--sel)}
#strip img.ev{border-color:var(--fix)}
#side{width:400px;flex:none;border-left:1px solid var(--line);display:flex;flex-direction:column}
#evs{flex:1;overflow:auto}
.e{padding:7px 10px;border-bottom:1px solid var(--line);cursor:pointer;display:flex;gap:8px}
.e:hover{background:#1c2027}.e.on{background:#1f2937}
.e .ts{color:var(--dim);font-variant-numeric:tabular-nums;flex:none;width:46px}
.e .l{flex:1;min-width:0}.e .who{font-size:11px;color:var(--dim)}
.e.v-ok{border-left:3px solid var(--ok)}.e.v-wrong{border-left:3px solid var(--bad);opacity:.55}
.e.v-fixed{border-left:3px solid var(--fix)}.e.v-added{border-left:3px solid var(--sel)}
#edit{border-top:1px solid var(--line);padding:10px;display:grid;
grid-template-columns:78px 1fr;gap:6px 8px;align-items:center}
#edit label{color:var(--dim);font-size:11px}
input,select,textarea{background:#0d0f13;border:1px solid var(--line);color:var(--fg);
border-radius:3px;padding:4px 6px;font:inherit;width:100%}
#bar{padding:8px 10px;border-top:1px solid var(--line);display:flex;gap:6px;flex-wrap:wrap}
button{background:#232833;border:1px solid var(--line);color:var(--fg);border-radius:3px;
padding:5px 10px;cursor:pointer;font:inherit}button:hover{background:#2c333f}
button.ok{border-color:var(--ok);color:var(--ok)}button.bad{border-color:var(--bad);color:var(--bad)}
button.pri{background:var(--sel);border-color:var(--sel);color:#fff}
#keys{font-size:11px;color:var(--dim);padding:6px 10px;border-top:1px solid var(--line)}
.empty{padding:30px;color:var(--dim);text-align:center}
</style></head><body>
<div id="rail"><h1>Bouts</h1><div id="list"></div></div>
<div id="main">
 <div id="head"><div><h2 id="title">—</h2><div id="facts"></div></div>
  <div><span id="prog" style="color:var(--dim);font-size:11px"></span>
   <button class="pri" onclick="save()">Save review</button></div></div>
 <div id="body">
  <div id="stage"><div id="big"><span id="bigts"></span><img id="img"></div>
   <div id="strip"></div></div>
  <div id="side"><div id="evs"></div>
   <div id="edit"></div>
   <div id="bar">
    <button class="ok" onclick="verdict('ok')">Correct (a)</button>
    <button class="bad" onclick="verdict('wrong')">Wrong (x)</button>
    <button onclick="verdict('fixed')">Fixed (f)</button>
    <button onclick="addEvent()">+ event here (n)</button></div>
   <div id="keys">j/k event &middot; &larr;/&rarr; frame &middot; a correct &middot; x wrong
    &middot; f fixed &middot; n add at current frame &middot; s save</div>
  </div></div></div>
<script>
let B=null, ei=0, fi=0, dirty=false;
const $=id=>document.getElementById(id);
const hhmm=t=>Math.floor(t/60)+':'+String(t%60).padStart(2,'0');

async function boot(){
  const rows=await (await fetch('/api/bouts')).json();
  $('list').innerHTML=rows.map(r=>`<div class="b" data-s="${r.slug}">
    <div class="t">${r.title}</div>
    <div class="m">${r.events<0?'invalid answer':r.events+' events'} &middot; ${r.frames} frames
    ${r.reviewed?' &middot; '+r.reviewed+' reviewed':''}</div></div>`).join('');
  [...document.querySelectorAll('.b')].forEach(el=>el.onclick=()=>open_(el.dataset.s));
  if(rows.length)open_(rows[0].slug);
}
async function open_(slug){
  if(dirty&&!confirm('Unsaved review on this bout. Discard?'))return;
  B=await (await fetch('/api/bout?slug='+encodeURIComponent(slug))).json();
  B.events=(B.review?.events)||(B.answer?.events||[]).map(e=>({...e}));
  dirty=false; ei=0; fi=0;
  [...document.querySelectorAll('.b')].forEach(el=>el.classList.toggle('on',el.dataset.s===slug));
  $('title').textContent=B.title;
  $('facts').textContent=Object.entries(B.facts).filter(([k])=>
    ['Result (BJJ Heroes)','Covers','Frames sampled','Division'].includes(k))
    .map(([k,v])=>k+': '+v).join('  ·  ');
  if(B.events.length)seek(B.events[0].ts);
  render();
}
function idxAt(ts){let b=0;B.frames.forEach((f,i)=>{if(Math.abs(f.ts-ts)<Math.abs(B.frames[b].ts-ts))b=i});return b}
function seek(ts){fi=idxAt(ts)}
function render(){
  if(!B){return}
  const evTs=new Set(B.events.map(e=>e.ts));
  $('evs').innerHTML=B.events.length?B.events.map((e,i)=>`<div class="e ${i===ei?'on':''} v-${e._verdict||''}"
     data-i="${i}"><span class="ts">${hhmm(e.ts)}</span><span class="l">${e.label}
     ${e.successful===false?'<span style="color:var(--dim)"> (attempt)</span>':''}
     ${e.points?'<span style="color:var(--fix)"> +'+e.points+'</span>':''}
     <div class="who">${e.actor} &middot; ${e.type}${e.confidence==='low'?' &middot; low confidence':''}</div>
     </span></div>`).join(''):'<div class="empty">No answer.json for this bout yet.</div>';
  [...document.querySelectorAll('.e')].forEach(el=>el.onclick=()=>{ei=+el.dataset.i;seek(B.events[ei].ts);render()});
  const f=B.frames[fi];
  $('img').src='/frame?slug='+encodeURIComponent(B.slug)+'&file='+f.file;
  $('bigts').textContent=hhmm(f.ts)+'  ('+f.ts+'s)';
  const lo=Math.max(0,fi-9),hi=Math.min(B.frames.length,fi+10);
  $('strip').innerHTML=B.frames.slice(lo,hi).map((x,j)=>
    `<img data-i="${lo+j}" class="${lo+j===fi?'on':evTs.has(x.ts)?'ev':''}" loading="lazy"
      src="/frame?slug=${encodeURIComponent(B.slug)}&file=${x.file}" title="${hhmm(x.ts)}">`).join('');
  [...$('strip').children].forEach(el=>el.onclick=()=>{fi=+el.dataset.i;render()});
  $('strip').children[fi-lo]?.scrollIntoView({inline:'center',block:'nearest'});
  const done=B.events.filter(e=>e._verdict).length;
  $('prog').textContent=done+' / '+B.events.length+' reviewed'+(dirty?'  • unsaved':'');
  editor();
}
function editor(){
  const e=B.events[ei];
  if(!e){$('edit').innerHTML='';return}
  const opt=(v,cur)=>`<option ${v===cur?'selected':''}>${v}</option>`;
  $('edit').innerHTML=`
   <label>ts</label><input id="f_ts" value="${e.ts}">
   <label>label</label><input id="f_label" list="labs" value="${e.label.replace(/"/g,'&quot;')}">
   <datalist id="labs">${B.labels.map(l=>`<option>${l}</option>`).join('')}</datalist>
   <label>actor</label><select id="f_actor">${
     [B.answer?.bout?.athlete_a,B.answer?.bout?.athlete_b].filter(Boolean)
      .map(n=>opt(n,e.actor)).join('')}</select>
   <label>type</label><select id="f_type">${
     ['control','submission','guard','takedown','pass','transition','sweep','escape']
      .map(t=>opt(t,e.type)).join('')}</select>
   <label>landed</label><select id="f_ok">${['true','false'].map(v=>opt(v,String(e.successful))).join('')}</select>
   <label>points</label><input id="f_pts" value="${e.points??''}" placeholder="blank = could not read">
   <label>note</label><input id="f_note" value="${(e.note||'').replace(/"/g,'&quot;')}">`;
  ['f_ts','f_label','f_actor','f_type','f_ok','f_pts','f_note'].forEach(id=>
    $(id).addEventListener('change',commit));
}
function commit(){
  const e=B.events[ei], pts=$('f_pts').value.trim();
  e.ts=parseInt($('f_ts').value,10); e.label=$('f_label').value.trim();
  e.actor=$('f_actor').value; e.type=$('f_type').value;
  e.successful=$('f_ok').value==='true'; e.note=$('f_note').value.trim()||undefined;
  // blank stays ABSENT, never 0 -- "the scoreboard gave nothing" and "I could not read it"
  // are different facts and only the missing field holds the second
  if(pts==='')delete e.points; else e.points=parseInt(pts,10);
  if(e._verdict!=='added')e._verdict='fixed';
  dirty=true; B.events.sort((a,b)=>a.ts-b.ts); render();
}
function verdict(v){
  if(!B.events[ei])return;
  B.events[ei]._verdict=v; dirty=true;
  if(ei<B.events.length-1){ei++;seek(B.events[ei].ts)}
  render();
}
function addEvent(){
  const b=B.answer?.bout||{};
  B.events.push({ts:B.frames[fi].ts,label:'',actor:b.athlete_a||'',type:'control',
                 successful:true,_verdict:'added'});
  B.events.sort((a,b)=>a.ts-b.ts);
  ei=B.events.findIndex(e=>e._verdict==='added'&&e.label==='');
  dirty=true; render(); $('f_label')?.focus();
}
async function save(){
  const body={bout:B.answer?.bout||{},events:B.events};
  const r=await fetch('/api/bout?slug='+encodeURIComponent(B.slug),
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(r.ok){dirty=false;render();boot_progress()}else alert('save failed');
}
async function boot_progress(){
  const rows=await (await fetch('/api/bouts')).json();
  rows.forEach(r=>{const el=document.querySelector(`.b[data-s="${r.slug}"] .m`);
    if(el)el.innerHTML=`${r.events} events &middot; ${r.frames} frames${r.reviewed?' &middot; '+r.reviewed+' reviewed':''}`});
}
addEventListener('keydown',ev=>{
  if(/^(INPUT|SELECT|TEXTAREA)$/.test(ev.target.tagName))return;
  const k=ev.key;
  if(k==='j'&&ei<B.events.length-1){ei++;seek(B.events[ei].ts);render()}
  else if(k==='k'&&ei>0){ei--;seek(B.events[ei].ts);render()}
  else if(k==='ArrowRight'&&fi<B.frames.length-1){fi++;render()}
  else if(k==='ArrowLeft'&&fi>0){fi--;render()}
  else if(k==='a')verdict('ok'); else if(k==='x')verdict('wrong');
  else if(k==='f')verdict('fixed'); else if(k==='n')addEvent();
  else if(k==='s'){ev.preventDefault();save()} else return;
  ev.preventDefault();
});
boot();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open a browser at the URL")
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
