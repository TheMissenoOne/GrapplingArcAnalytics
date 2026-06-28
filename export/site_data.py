"""Generate the public *design* site's data + detail pages from the DB.

The static design bundle (``../GrapplingArc/site``) renders client-side from three
generated data files plus one detail page per match / qualifying fighter:

    site/breakdowns-data.js   window.GA_BREAKDOWNS  (all sequence bouts, graph.js-shaped)
    site/fighters-data.js     window.GA_FIGHTERS    (>=3-bout dossiers, card graphs)
    site/elo-data.js          window.GA_ELO         (rank_elo leaderboard rows)
    site/breakdown-<slug>.html   per-bout article  (stats + momentum + graph + prose)
    site/grapple-<slug>.html     per-fighter dossier (career graph + signature + prose)

It adapts the app-shaped ``{nodes,edges}`` the breakdown/career exporters emit into the
``{nodes:[{id,label,cat,size,fighter}],links:[{from,to,fighter,weight}]}`` shape
``site/graph.js`` consumes, and uses ``export.narrative`` for the editorial copy — so the
words and the numbers come from the same source.

    uv run python -m export.site_data            # -> ../GrapplingArc/site
    uv run python -m export.site_data --out /tmp/site
"""
# ruff: noqa: E501  (HTML/JS template strings are content, not wrappable code)

from __future__ import annotations

import argparse
import html
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.names import _normalize_name
from analysis.style_profile import build_style_profile, qualifies
from db.models import Archetype, Athlete
from export.match_breakdown import (
    _final_matches,
    _headline,
    build_match_breakdown,
    export_fighter_graph,
    match_slug,
    slugify,
)
from export.narrative import match_narrative, profile_narrative

logger = logging.getLogger(__name__)

_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "GrapplingArc" / "site"
_CATS = {"guard", "pass", "sweep", "takedown", "control", "submission", "escape", "transition"}


# ── small helpers ────────────────────────────────────────────────────────────
def _clamp3(n: int) -> int:
    return 1 if n <= 1 else (2 if n == 2 else 3)


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (name[:2]).upper()


def _name_break(name: str) -> str:
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]}<br/>{' '.join(parts[1:])}"
    return name


def _result_short(meta: dict[str, Any]) -> str:
    wt = (meta.get("win_type") or "").upper()
    if wt == "SUBMISSION" and meta.get("submission"):
        return f"SUB · {meta['submission']}"
    if wt == "SUBMISSION":
        return "SUB"
    if wt:
        return wt[:3]
    return "N/C"


def _archetype(athlete: Athlete | None, session: Session) -> str | None:
    if athlete is None or athlete.archetype_id is None:
        return None
    arch = session.get(Archetype, athlete.archetype_id)
    return arch.name if arch else None


def _to_graphview(app_graph: dict[str, Any], default_side: str = "a") -> dict[str, Any]:
    """app-shaped ``{nodes,edges}`` → the ``graph.js`` ``{nodes,links}`` contract."""
    nodes = []
    for n in app_graph.get("nodes", []):
        d = n.get("data", {})
        typ = str(d.get("type", ""))
        nodes.append({
            "id": n["id"], "label": n.get("label", n["id"]),
            "cat": typ if typ in _CATS else "control",
            "size": _clamp3(int(d.get("usageCount", 1))),
            "fighter": d.get("side", default_side),
        })
    links = []
    for e in app_graph.get("edges", []):
        d = e.get("data", {})
        links.append({
            "from": e["source"], "to": e["target"],
            "fighter": d.get("side", default_side),
            "weight": _clamp3(int(d.get("count", 1))),
        })
    return {"nodes": nodes, "links": links}


def _truncate_graph(g: dict[str, Any], limit: int) -> dict[str, Any]:
    """Keep the ``limit`` busiest nodes (+ their links) so cards/dossiers stay legible."""
    nodes = sorted(g["nodes"], key=lambda n: n["size"], reverse=True)[:limit]
    keep = {n["id"] for n in nodes}
    links = [lk for lk in g["links"] if lk["from"] in keep and lk["to"] in keep]
    return {"nodes": nodes, "links": links}


def _career_graphview(athlete: Athlete, profile: dict[str, Any], session: Session,
                      limit: int = 12) -> dict[str, Any]:
    """Fighter's career graph (adapted + truncated), falling back to signature transitions."""
    g = export_fighter_graph(athlete, session)
    if g and g.get("nodes"):
        return _truncate_graph(_to_graphview(g, "a"), limit)
    nodes: dict[str, dict[str, Any]] = {}
    links = []
    for t in profile.get("signature_transitions", []):
        for lb in (t["from"], t["to"]):
            key = _normalize_name(lb)
            nodes.setdefault(key, {"id": key, "label": lb, "cat": "control",
                                   "size": 2, "fighter": "a"})
        links.append({"from": _normalize_name(t["from"]), "to": _normalize_name(t["to"]),
                      "fighter": "a", "weight": _clamp3(int(t["count"]))})
    return {"nodes": list(nodes.values()), "links": links}


# ── data files ───────────────────────────────────────────────────────────────
def _featured_stats(bd: dict[str, Any]) -> list[dict[str, Any]]:
    sa, sb = bd["stats"]["a"], bd["stats"]["b"]
    return [
        {"k": "Positional conversion", "va": _pct(sa["positional_conversion"]),
         "vb": _pct(sb["positional_conversion"])},
        {"k": "Control positions", "va": sa["controls"], "vb": sb["controls"]},
        {"k": "Sub attempts", "va": sa["submission_attempts"], "vb": sb["submission_attempts"]},
        {"k": "Transitions", "va": sa["transitions"], "vb": sb["transitions"]},
    ]


def build_breakdowns(
    session: Session,
) -> tuple[list[dict[str, Any]], list[tuple[str, dict[str, Any]]], dict[str, Any] | None]:
    """Returns (GA_BREAKDOWNS rows, [(slug, full breakdown)], GA_FEATURED) for sequence bouts.

    The featured bout = the decided match with the highest combined opponent rank_elo, so the
    homepage spotlight is real (names, method, mini-stats) and auto-updates with the data.
    """
    rows: list[dict[str, Any]] = []
    full: list[tuple[str, dict[str, Any]]] = []
    featured: dict[str, Any] | None = None
    best_score = -1.0
    for match in _final_matches(session):
        a = session.get(Athlete, match.athlete_a_id)
        b = session.get(Athlete, match.athlete_b_id)
        if a is None or b is None:
            continue
        slug = match_slug(a, b, match.year)
        bd = build_match_breakdown(match, a, b)
        gv = _to_graphview(bd["transition_graph"])
        rows.append({
            "id": slug, "href": f"breakdown-{slug}.html",
            "event": bd["meta"]["event"] or "Match",
            "result": _result_short(bd["meta"]), "date": str(match.year or ""),
            "title_en": _headline(bd), "title_pt": _headline(bd),
            "a": {"name": a.name, "code": _initials(a.name), "record": "",
                  "style": _archetype(a, session) or "—"},
            "b": {"name": b.name, "code": _initials(b.name), "record": "",
                  "style": _archetype(b, session) or "—"},
            "graph": gv,
        })
        full.append((slug, bd))
        score = (a.rank_elo or 0.0) + (b.rank_elo or 0.0)
        if bd["meta"]["winner"] and score > best_score:
            best_score = score
            win = bd["meta"]["winner"]
            featured = {
                "slug": slug, "href": f"breakdown-{slug}.html",
                "event": bd["meta"]["event"] or "Match", "method": bd["meta"]["method"],
                "winner": win["name"],
                "a": {"name": a.name, "code": _initials(a.name),
                      "style": _archetype(a, session) or "—"},
                "b": {"name": b.name, "code": _initials(b.name),
                      "style": _archetype(b, session) or "—"},
                "headline": _headline(bd), "stats": _featured_stats(bd),
            }
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows, full, featured


def build_fighters(
    session: Session,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Returns (GA_FIGHTERS card rows, {slug: {athlete, profile, career}}) for ≥3-bout
    fighters. The profile + career graph are computed once and reused for the dossier."""
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    for match in _final_matches(session):
        for aid in (match.athlete_a_id, match.athlete_b_id):
            if aid in seen:
                continue
            seen.add(aid)
            athlete = session.get(Athlete, aid)
            if athlete is None or not qualifies(aid, session):
                continue
            profile = build_style_profile(athlete, session)
            career = _career_graphview(athlete, profile, session, 12)
            card = _truncate_graph(career, 8)
            slug = slugify(athlete.name)
            rec = profile["fighter"]["record"]
            rank = profile["fighter"]["elo_rank"]
            sub = f"{rec['wins']}–{rec['losses']}"
            if rank:
                sub += f" · #{rank} ELO"
            elif athlete.weight_class:
                sub += f" · {athlete.weight_class}"
            arche = profile.get("archetype") or "Grappler"
            cards.append({
                "slug": slug, "name": _name_break(athlete.name),
                "arch_en": arche, "arch_pt": arche, "rec": sub,
                "href": f"grapple-{slug}.html",
                "nodes": card["nodes"], "links": card["links"],
                "_rank": rank or 9999,
            })
            details[slug] = {"athlete": athlete, "profile": profile, "career": career}
    cards.sort(key=lambda r: r["_rank"])
    for r in cards:
        del r["_rank"]
    return cards, details


def build_elo(session: Session, limit: int = 8) -> list[list[Any]]:
    rows = list(session.execute(
        select(Athlete.name, Athlete.rank_elo)
        .where(Athlete.rank_elo.isnot(None)).order_by(Athlete.rank_elo.desc()).limit(limit)
    ))
    if not rows:
        return []
    top = float(rows[0][1])
    return [[str(i + 1), name, round(float(score)),
             round(float(score) / top * 100)] for i, (name, score) in enumerate(rows)]


# ── HTML chrome ──────────────────────────────────────────────────────────────
def _nav(active: str) -> str:
    def cls(key: str) -> str:
        return ' class="on"' if key == active else ""
    return f"""<div class="beltline"></div>
<header class="site-head"><div class="wrap">
  <a class="brand" href="index.html"><span class="mark">GA</span>Grappling<span class="o">Arc</span></a>
  <nav class="site-nav">
    <a href="index.html"{cls('home')}>Home</a>
    <a href="breakdowns.html"{cls('breakdowns')}>Breakdowns</a>
    <a href="grapple-like.html"{cls('grapple')}>Grapple Like</a>
    <a href="the-data.html"{cls('data')}>The Data</a>
  </nav>
  <div class="head-right">
    <div class="lang"><button data-lang="en" class="on">EN</button><button data-lang="pt">PT</button></div>
    <a class="btn app sm" href="index.html#app">Get the App</a>
  </div>
</div></header>"""


_FOOTER = """<footer class="site-foot"><div class="wrap">
  <a class="brand" href="index.html"><span class="mark">GA</span>Grappling<span class="o">Arc</span></a>
  <nav class="links">
    <a href="breakdowns.html">Breakdowns</a><a href="grapple-like.html">Grapple Like</a>
    <a href="the-data.html">The Data</a>
  </nav>
  <p class="copy">© 2026 GrapplingArc · generated from match data</p>
</div></footer>"""

_HEAD = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title} — GrapplingArc</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800;900&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=Spline+Sans+Mono:wght@400;500;600&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="site.css"/></head><body>"""


def _prose_html(sections: list[tuple[str, list[str]]]) -> str:
    parts = []
    for heading, paras in sections:
        parts.append(f'<div class="sec-label">{html.escape(heading)}</div>')
        body = "".join(f"<p>{html.escape(p)}</p>" for p in paras)
        parts.append(f'<div class="editorial">{body}</div>')
    return "\n".join(parts)


# ── breakdown detail page ────────────────────────────────────────────────────
_BREAKDOWN_JS = """
// momentum timeline from the real running point-share (0..1 for A → signed −1..1)
(function(){
  const c=document.getElementById('momentum'); if(!c) return;
  const series=(BD.stats.momentum_series||[]).map(v=>v*2-1);
  function draw(){
    const x=c.getContext('2d'),dpr=Math.min(devicePixelRatio||1,2);
    const r=c.getBoundingClientRect();c.width=r.width*dpr;c.height=r.height*dpr;x.setTransform(dpr,0,0,dpr,0,0);
    const W=r.width,H=r.height,mid=H/2,pts=series.length?series:[0];
    const X=i=>pts.length<2?W/2:i/(pts.length-1)*W;
    x.clearRect(0,0,W,H);
    x.strokeStyle='#23232a';x.beginPath();x.moveTo(0,mid);x.lineTo(W,mid);x.stroke();
    x.beginPath();pts.forEach((p,i)=>{const py=mid-(p>0?p:0)*(mid-12);i?x.lineTo(X(i),py):x.moveTo(X(i),py);});
    x.lineTo(W,mid);x.lineTo(0,mid);x.closePath();x.fillStyle='rgba(77,134,255,.28)';x.fill();
    x.beginPath();pts.forEach((p,i)=>{const py=mid+(p<0?-p:0)*(mid-12);i?x.lineTo(X(i),py):x.moveTo(X(i),py);});
    x.lineTo(W,mid);x.lineTo(0,mid);x.closePath();x.fillStyle='rgba(255,95,162,.28)';x.fill();
    x.beginPath();pts.forEach((p,i)=>{const py=mid-p*(mid-12);i?x.lineTo(X(i),py):x.moveTo(X(i),py);});
    x.strokeStyle='#cfcfd6';x.lineWidth=2;x.stroke();
  }
  draw();new ResizeObserver(draw).observe(c);
})();
// decisive sequence graph
GAGraph.mount(document.getElementById('seqGraph'),{mode:'map',nodes:BD.graph.nodes,links:BD.graph.links});
const lc=[['takedown','Takedown'],['control','Control'],['guard','Guard'],['pass','Passing'],['sweep','Sweep'],['submission','Submission'],['escape','Escape'],['transition','Transition']];
const dot=c=>'<span class="dot" style="background:'+c+'"></span>';
document.getElementById('seqLegend').innerHTML=
  lc.filter(([k])=>BD.graph.nodes.some(n=>n.cat===k)).map(([k,l])=>'<span>'+dot(GAGraph.CAT[k])+l+'</span>').join('')
  +'<span style="margin-left:auto">'+dot('var(--blue)')+BD.a+'</span><span>'+dot('var(--pink)')+BD.b+'</span>';
"""


def _stat_row(k: str, va: Any, vb: Any) -> str:
    return (f'<div class="stat"><div class="k">{k}</div>'
            f'<div class="vrow"><span class="v a">{va}</span><span class="v b">{vb}</span></div></div>')


def render_breakdown_page(slug: str, bd: dict[str, Any]) -> str:
    meta, stats = bd["meta"], bd["stats"]
    a, b = bd["fighters"]["a"], bd["fighters"]["b"]
    sa, sb = stats["a"], stats["b"]
    arche_a = bd.get("_arch_a") or ""
    arche_b = bd.get("_arch_b") or ""
    sections = match_narrative(bd)
    winner = meta.get("winner")
    win_line = (f"{winner['name']} · {meta['method']}" if winner
                else meta["method"])
    pc = _pct
    stat_grid = "".join([
        _stat_row("Takedowns", sa["takedowns_landed"], sb["takedowns_landed"]),
        _stat_row("Positional conversion", pc(sa["positional_conversion"]),
                  pc(sb["positional_conversion"])),
        _stat_row("Transitions", sa["transitions"], sb["transitions"]),
        _stat_row("Sub attempts", sa["submission_attempts"], sb["submission_attempts"]),
        _stat_row("Control positions", sa["controls"], sb["controls"]),
    ])

    def sig_card(f: dict[str, Any], label: str) -> str:
        d = f.get("elo_delta")
        delta = ""
        if d is not None:
            cls = "up" if d >= 0 else "down"
            arrow = "▲" if d >= 0 else "▼"
            delta = f'<div class="delta {cls}">{arrow} {d:+.1f} post-bout</div>'
        return (f'<div class="sig-card"><div class="k">{label}</div>'
                f'<div class="v">{f["graph_elo"]}</div>{delta}</div>')

    payload = {
        "a": a["name"], "b": b["name"],
        "graph": bd["transition_graph_gv"],
        "stats": {"momentum_series": stats.get("momentum_series", [])},
    }
    body = f"""{_nav('breakdowns')}
<section class="art-hero"><div class="wrap">
  <div class="center"><a href="breakdowns.html" class="tag" style="text-decoration:none">← Breakdowns</a></div>
  <div class="bout">
    <div class="corner a"><span class="av">{_initials(a['name'])}</span>
      <span class="nm">{html.escape(_name_break(a['name']))}</span>
      <span class="rc">{html.escape(arche_a)}</span></div>
    <span class="vsbig">VS</span>
    <div class="corner b"><span class="av">{_initials(b['name'])}</span>
      <span class="nm">{html.escape(_name_break(b['name']))}</span>
      <span class="rc">{html.escape(arche_b)}</span></div>
  </div>
  <div class="result-bar">
    <span class="tag">{html.escape(meta['event'] or 'Match')}</span>
    {'<span class="tag">' + html.escape(meta['weight_class']) + '</span>' if meta.get('weight_class') else ''}
    <span class="tag" style="color:var(--cat-submission);border-color:#3a2020">{html.escape(win_line)}</span>
  </div>
  <h1 class="art-title">{html.escape(_headline(bd))}</h1>
  <div class="prose"><p class="lead art-sum">{html.escape(sections[0][1][0])}</p></div>
</div></section>
<article class="art">
  <section class="block"><div class="wrap viz"><div class="statgrid">{stat_grid}</div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><div class="sec-label">Momentum</div></div>
    <div class="wrap viz"><div class="mtl"><canvas id="momentum"></canvas></div>
      <div class="mtl-axis"><span>start</span><span>finish</span></div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><div class="sec-label">The decisive sequence</div>
      <p class="editorial">Each node is a position; each edge a transition, coloured by who initiated it. Hover any node to isolate its connections.</p></div>
    <div class="wrap viz"><div class="graph-card seq-card"><canvas id="seqGraph" class="graph-canvas"></canvas>
      <div class="graph-legend" id="seqLegend"></div></div></div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose">{_prose_html(sections[1:])}</div></section>
  <div class="divider"></div>
  <section class="block"><div class="wrap prose"><div class="sec-label">Rating &amp; significance</div></div>
    <div class="wrap viz"><div class="sig-cards">
      {sig_card(a, a['name'] + ' ELO')}{sig_card(b, b['name'] + ' ELO')}
      <div class="sig-card"><div class="k">Method</div><div class="v">{html.escape(meta['method'])}</div></div>
    </div></div></section>
</article>
{_FOOTER}
<script src="graph.js"></script><script src="i18n.js"></script>
<script>const BD = {json.dumps(payload, ensure_ascii=False)};
{_BREAKDOWN_JS}</script></body></html>"""
    return _HEAD.format(title=html.escape(meta["title"])) + body


# ── dossier detail page ──────────────────────────────────────────────────────
_PROFILE_JS = """
// radar fingerprint
(function(){
  const c=document.getElementById('radar'); if(!c) return; const x=c.getContext('2d');
  const F=P.fingerprint, axes=[['top','Top'],['back','Back'],['legs','Legs'],['guard','Guard'],['pace','Pace'],['scramble','Scramble']];
  const vals=axes.map(([k])=>Math.max(0.04,F[k]||0)), cx=160,cy=150,R=98;
  function poly(s,fill,stroke,lw){x.beginPath();axes.forEach((_,i)=>{const ang=-Math.PI/2+i/axes.length*Math.PI*2,r=R*s[i],
    px=cx+Math.cos(ang)*r,py=cy+Math.sin(ang)*r;i?x.lineTo(px,py):x.moveTo(px,py);});x.closePath();
    if(fill){x.fillStyle=fill;x.fill();}x.strokeStyle=stroke;x.lineWidth=lw;x.stroke();}
  [0.25,0.5,0.75,1].forEach(g=>poly(axes.map(()=>g),null,'#23232a',1));
  axes.forEach((_,i)=>{const ang=-Math.PI/2+i/axes.length*Math.PI*2;x.strokeStyle='#23232a';
    x.beginPath();x.moveTo(cx,cy);x.lineTo(cx+Math.cos(ang)*R,cy+Math.sin(ang)*R);x.stroke();});
  poly(vals,'rgba(77,134,255,.22)','#4d86ff',2);
  x.fillStyle='#a2a2ad';x.font="600 11px 'Spline Sans Mono',monospace";x.textAlign='center';x.textBaseline='middle';
  axes.forEach(([_,l],i)=>{const ang=-Math.PI/2+i/axes.length*Math.PI*2;x.fillText(l,cx+Math.cos(ang)*(R+18),cy+Math.sin(ang)*(R+14));});
})();
// career graph
GAGraph.mount(document.getElementById('careerGraph'),{mode:'map',nodes:P.graph.nodes,links:P.graph.links});
const lg=[['guard','Guard'],['pass','Passing'],['control','Control'],['submission','Submission'],['takedown','Takedown'],['transition','Transition']];
document.getElementById('legend').innerHTML=lg.map(([k,l])=>'<span><span class="dot" style="background:'+GAGraph.CAT[k]+'"></span>'+l+'</span>').join('');
// signature frequency
document.getElementById('sigFreq').innerHTML=P.signature.map(s=>{const pct=Math.round(s.pct*100);
  return '<div class="freq-row"><div class="top"><span class="name">'+s.label+'</span><span class="pct">'+pct+'%</span></div>'
    +'<div class="freq-track"><div class="freq-fill" style="width:'+Math.max(6,pct)+'%"></div></div></div>';}).join('');
// response tree
document.getElementById('tree').innerHTML=P.responses.map(b=>{
  const resp=b.moves.map(r=>'<div class="resp"><span class="pct">'+Math.round(r.pct*100)+'%</span>'
    +'<span class="rtxt">'+r.move+'</span><span class="rbar"><span class="rfill" style="width:'+Math.max(6,Math.round(r.pct*100))+'%"></span></span></div>').join('');
  return '<div class="branch"><div class="q"><span class="ic">'+b.icon+'</span><span class="qt">When '+b.situation+'…</span></div>'+resp+'</div>';}).join('') || '<p class="editorial">Not enough mapped exchanges yet.</p>';
// linked matches
document.getElementById('linked').innerHTML=P.bouts.slice(0,3).map(m=>
  '<a class="mcard" href="breakdown-'+m.slug+'.html"><div class="ev">'+(m.year||'')+'</div><div class="op">'+m.result+'</div><div class="rs">'+(m.win_type||'')+'</div></a>').join('');
if(document.body.classList.contains('lang-pt')) GALang.set('pt');
"""


def render_profile_page(profile: dict[str, Any]) -> str:
    f = profile["fighter"]
    fin = profile["finishing"]
    fam = fin.get("submission_family", {})
    sections = profile_narrative(profile)
    rec = f["record"]
    rank = f.get("elo_rank")
    icons = {"taken down": "T", "guard passed": "P", "back taken": "B", "swept": "S"}
    payload = {
        "fingerprint": profile["fingerprint"],
        "graph": profile["_career_gv"],
        "signature": profile["signature_techniques"],
        "responses": [
            {"situation": sit, "icon": icons.get(sit, "?"), "moves": data["moves"]}
            for sit, data in profile["responses"].items()
        ],
        "bouts": profile["bouts"],
    }
    sub_lines = []
    for k, v in fam.get("shares", {}).items():
        sub_lines.append(f"{k} {round(v * 100)}%")
    fincards = "".join([
        f'<div class="fincard"><div class="k">Finish rate</div><div class="v sub">{round(fin["finish_rate"] * 100)}%</div><div class="cap">of wins by submission</div></div>',
        f'<div class="fincard"><div class="k">Submission family</div><div class="v">{html.escape(fam.get("dominant") or "—")}</div><div class="cap">{html.escape(", ".join(sub_lines))}</div></div>',
        f'<div class="fincard"><div class="k">Decision rate</div><div class="v">{round(fin["decision_rate"] * 100)}%</div><div class="cap">of decided bouts</div></div>',
        f'<div class="fincard"><div class="k">vs Top-10 ELO</div><div class="v" style="color:var(--good)">{fin["record_vs_elite"]["wins"]}–{fin["record_vs_elite"]["losses"]}</div><div class="cap">elite opposition</div></div>',
    ])
    sub_meta = f"<span><b>{rec['wins']}–{rec['losses']}</b> record</span>"
    sub_meta += f"<span><b>{round(f['finish_rate'] * 100)}%</b> finish rate</span>"
    if rank:
        sub_meta += f"<span><b>#{rank}</b> ELO · {html.escape(f.get('weight_class') or '')}</span>"
    arche = profile.get("archetype") or "Grappler"
    bio = sections[0][1][0] if sections else ""
    body = f"""{_nav('grapple')}
<section class="dossier"><div class="wrap">
  <div class="flex ac g12" style="margin-bottom:22px">
    <a href="grapple-like.html" class="tag" style="text-decoration:none">← Grapple Like</a>
    <span class="kicker">Athlete dossier</span>
  </div>
  <div class="dhead">
    <div class="athlete">
      <span class="arch">● {html.escape(arche)}</span>
      <h1>{_name_break(html.escape(f['name']))}</h1>
      <div class="sub">{sub_meta}</div>
      <p class="editorial bio">{html.escape(bio)}</p>
    </div>
    <div class="radar-card"><div class="rt">Style fingerprint</div>
      <div class="radar-wrap"><canvas id="radar" width="320" height="300"></canvas></div></div>
  </div>
</div></section>
<section class="wrap career">
  <div class="sec-head" style="margin-bottom:14px">
    <span class="eyebrow">The system, not the match</span>
    <h2 class="h-lg mt16">One graph for an entire grappling game</h2></div>
  <div class="graph-card"><canvas id="careerGraph" class="graph-canvas" style="height:440px"></canvas>
    <div class="graph-legend" id="legend"></div></div>
  <p class="graph-hint">Drag nodes to explore · hover to isolate a pathway</p>
</section>
<section class="mod"><div class="wrap"><div class="mod-grid">
  <div class="mod-intro"><span class="eyebrow">Signature game</span>
    <h2 class="mt16">What he reaches for first</h2>
    <div class="editorial">{_prose_html([sections[1]]) if len(sections) > 1 else ''}</div></div>
  <div class="freq" id="sigFreq"></div>
</div></div></section>
<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow pink">Response patterns</span>
    <h2 class="h-lg mt16">How he reacts when the position changes</h2></div>
  <div class="tree" id="tree"></div>
</div></section>
<section class="mod"><div class="wrap">
  <div class="sec-head"><span class="eyebrow">Finishing profile</span>
    <h2 class="h-lg mt16">Where the matches end</h2></div>
  <div class="fingrid">{fincards}</div>
</div></section>
<section class="mod"><div class="wrap">
  <div class="sec-head flex jb ac wrap-fx" style="gap:14px"><div>
    <span class="eyebrow">From abstract to concrete</span>
    <h2 class="h-lg mt16">See the system in action</h2></div>
    <a class="btn" href="breakdowns.html">All breakdowns →</a></div>
  <div class="mgrid" id="linked"></div>
</div></section>
{_FOOTER}
<script src="graph.js"></script><script src="i18n.js"></script>
<script>const P = {json.dumps(payload, ensure_ascii=False)};
{_PROFILE_JS}</script></body></html>"""
    return _HEAD.format(title=html.escape("Grapple Like " + f["name"])) + body


# ── orchestration ────────────────────────────────────────────────────────────
def _js_file(var: str, data: Any) -> str:
    return f"/* generated by export.site_data — do not edit */\nwindow.{var} = {json.dumps(data, ensure_ascii=False)};\n"


def export_site(session: Session, out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    rows, full, featured = build_breakdowns(session)
    fighters, details = build_fighters(session)
    elo = build_elo(session)

    bd_js = _js_file("GA_BREAKDOWNS", rows)
    bd_js += f"window.GA_FEATURED = {json.dumps(featured, ensure_ascii=False)};\n"
    (out / "breakdowns-data.js").write_text(bd_js, encoding="utf-8")
    (out / "fighters-data.js").write_text(_js_file("GA_FIGHTERS", fighters), encoding="utf-8")
    (out / "elo-data.js").write_text(_js_file("GA_ELO", elo), encoding="utf-8")

    # per-match detail pages (attach archetypes + adapted graph for the template)
    for slug, bd in full:
        bd["_arch_a"] = next((r["a"]["style"] for r in rows if r["id"] == slug), "")
        bd["_arch_b"] = next((r["b"]["style"] for r in rows if r["id"] == slug), "")
        bd["transition_graph_gv"] = _to_graphview(bd["transition_graph"])
        (out / f"breakdown-{slug}.html").write_text(
            render_breakdown_page(slug, bd), encoding="utf-8")

    # per-fighter dossiers (reuse the profile + career graph computed above)
    for slug, d in details.items():
        profile = d["profile"]
        profile["_career_gv"] = d["career"]
        (out / f"grapple-{slug}.html").write_text(
            render_profile_page(profile), encoding="utf-8")

    return {"breakdowns": len(full), "fighters": len(details), "elo": len(elo)}


def run(out: Path) -> int:
    from db.base import db_session
    with db_session() as session:
        counts = export_site(session, out)
    logger.info("Generated %d breakdowns, %d dossiers, %d ELO rows → %s",
                counts["breakdowns"], counts["fighters"], counts["elo"], out)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Generate the design site's data + detail pages")
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="site output dir")
    args = ap.parse_args()
    return run(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
